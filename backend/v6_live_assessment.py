"""Chat-only live context. Never turn an unvalidated market move into a probability.
Raw quote vintages and timestamps are retained; reports only read frozen values.
"""
from datetime import datetime,timedelta,time
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,math,re,sqlite3,uuid
from urllib.parse import quote
import numpy as np
import requests
from . import db,v6_shared_distribution as D
TZ=ZoneInfo('Asia/Taipei')
VERSION='v6-live-context-1.0.0'
NIGHT_URL='https://mis.taifex.com.tw/futures/api/getQuoteList'
NIGHT_PAGE='https://mis.taifex.com.tw/futures/AfterHoursSession/EquityIndices/FuturesDomestic'
US={'spx':('^GSPC','標普500'),'nasdaq':('^IXIC','NASDAQ'),'sox':('^SOX','費半'),'tsm':('TSM','台積電ADR'),'vix':('^VIX','VIX')}

def finite(x):
    try:v=float(str(x).replace(',',''))
    except (TypeError,ValueError):return None
    return v if math.isfinite(v) else None

def _clock(value):
    stamp=datetime.fromisoformat(value) if isinstance(value,str) else value
    if stamp.tzinfo is None:raise ValueError('報價查核時間需含時區')
    return stamp.astimezone(TZ)

def _contract(symbol,asof):
    m=re.fullmatch(r'TXF([A-L])(\d)-M',str(symbol))
    if not m:return None
    current=int(asof[:4]);years=[y for y in range(current-1,current+3) if y%10==int(m[2])]
    if len(years)!=1:return None
    return f'{years[0]:04}{ord(m[1])-64:02}'

def parse_night(payload,asof,stamp,day_quotes,cash_close):
    stamp=_clock(stamp)
    if payload.get('RtCode')!='0':raise ValueError('期交所即時行情格式異常')
    rows=[];begin=datetime.fromisoformat(asof).replace(hour=15,tzinfo=TZ);end=begin+timedelta(hours=14)
    for r in (payload.get('RtData') or {}).get('QuoteList',[]):
        contract=_contract(r.get('SymbolID'),asof)
        if not contract or contract not in day_quotes:continue
        price=finite(r.get('CLastPrice'));reference=finite(r.get('CRefPrice'));day_close=finite(day_quotes[contract]);high=finite(r.get('CHighPrice'));low=finite(r.get('CLowPrice'))
        if any(x is None or x<=0 for x in [price,reference,day_close,high,low]):continue
        if not low<=price<=high:continue
        raw_date=str(r.get('CDate',''));raw_time=str(r.get('CTime','')).zfill(6)
        if not re.fullmatch(r'\d{8}',raw_date) or not re.fullmatch(r'\d{6}',raw_time):continue
        base=datetime.strptime(raw_date+raw_time,'%Y%m%d%H%M%S').replace(tzinfo=TZ)
        candidates=[base+timedelta(days=i) for i in [-1,0,1]]
        possible=[t for t in candidates if begin<=t<=end and t<=stamp and abs((t-base).days)<=1]
        # CDate may be the evening date or clearing trade date; choose only a
        # timestamp inside the known cash day's real overnight session.
        if not possible:continue
        observed=max(possible);age=(stamp-observed).total_seconds()/60
        open_session=begin<=stamp<=end
        state='ready' if age<=15 else 'closed' if stamp>end and observed>=end-timedelta(minutes=30) else 'stale'
        diff=finite(r.get('CDiff'))
        if diff is not None and abs(price-reference-diff)>.01:continue
        rows.append({'key':contract,'contract':contract,'symbol':r['SymbolID'],'label':f'台指期 {contract}',
          'price':price,'high':high,'low':low,'quote_at':observed.isoformat(timespec='seconds'),'raw_date':raw_date,'raw_time':raw_time,
          'state':state,'session':'夜盤盤中' if open_session else '夜盤已結束','age_minutes':round(age,2),
          'reference':reference,'day_close':day_close,'day_close_date':asof,
          'change_day_points':price-day_close,'change_day_pct':(price/day_close-1)*100,
          'change_reference_points':price-reference,'change_reference_pct':(price/reference-1)*100,
          'day_basis_points':day_close-cash_close,'vs_closed_cash_points':price-cash_close,
          'basis_change_points':price-day_close,'session_range_pct':(high-low)/day_close*100,
          'included':state in ('ready','closed'),'source':'臺灣期貨交易所','url':NIGHT_PAGE,
          'note':'相對加權收盤為非同步價差；夜盤成交時現貨休市，不把它當即時期現價差或直接套成隔日現貨跌點。'})
    return sorted(rows,key=lambda r:r['contract'])

def parse_yahoo(payload,key,asof,stamp):
    stamp=_clock(stamp);symbol,label=US[key];data=(payload.get('chart') or {}).get('result') or []
    if not data or data[0].get('meta',{}).get('symbol')!=symbol:raise ValueError('海外商品代號或欄位不符')
    x=data[0];meta=x['meta'];reference=finite(meta.get('previousClose'))
    if reference is None or reference<=0:raise ValueError('缺少已核對的前日收盤')
    points=[];arr=(x.get('indicators',{}).get('quote') or [{}])[0].get('close',[])
    for ts,price in zip(x.get('timestamp',[]),arr):
        price=finite(price)
        if price is not None and price>0:
            at=datetime.fromtimestamp(ts,TZ)
            if at<=stamp:points.append((at,price))
    rt=finite(meta.get('regularMarketTime'));rp=finite(meta.get('regularMarketPrice'))
    if rt and rp and rp>0:
        at=datetime.fromtimestamp(rt,TZ)
        if at<=stamp:points.append((at,rp))
    if not points:raise ValueError('沒有截點以前可用的海外報價')
    observed,price=max(points,key=lambda p:p[0]);cash_cutoff=datetime.fromisoformat(asof).replace(hour=13,minute=30,tzinfo=TZ)
    period=meta.get('currentTradingPeriod',{}).get('regular',{});market_start=finite(period.get('start'));market_end=finite(period.get('end'))
    if not market_start or not market_end or market_end<=market_start:raise ValueError('缺少可核對的海外交易時段')
    regular_open=datetime.fromtimestamp(market_start,TZ);regular_end=datetime.fromtimestamp(market_end,TZ)
    age=(stamp-observed).total_seconds()/60;open_session=regular_open<=stamp<=regular_end
    # Never treat an older regular day's previousClose as the correct reference
    # for a new premarket day. Validate quote belongs to advertised session date.
    zone=ZoneInfo(meta.get('exchangeTimezoneName','America/New_York'))
    if observed.astimezone(zone).date()!=regular_open.astimezone(zone).date():state='stale'
    elif observed<=cash_cutoff:state='already_reflected'
    elif age<=20:state='ready'
    elif stamp>=regular_end and observed>=regular_end-timedelta(minutes=20):state='closed'
    else:state='stale'
    return {'key':key,'symbol':symbol,'label':label,'price':price,'previous_close':reference,
      'change_pct':(price/reference-1)*100,'quote_at':observed.isoformat(timespec='seconds'),
      'source_date':observed.astimezone(zone).date().isoformat(),'source_timezone':str(zone),
      'age_minutes':round(age,2),'state':state,'included':state in ('ready','closed'),
      'session':'盤中／可能延遲' if open_session else '盤前／盤後或已收盤','source':'Yahoo Finance',
      'url':'https://finance.yahoo.com/quote/'+quote(symbol,safe='')+'/',
      'note':'比較來源前日收盤；盤中或延遲報價獨立保存，不混入已完成日線或歷史回測。'}

def volatility(official,asof):
    f=official.loc[:asof].copy();D._check_prices(f)
    if str(f.index[-1])!=asof or len(f)<61:raise ValueError('近期波動至少需要61筆完整日線')
    close=f.close.to_numpy(float);high=f.high.to_numpy(float);low=f.low.to_numpy(float)
    returns=close[1:]/close[:-1]-1;ranges=(high[1:]-low[1:])/close[:-1]*100
    tr=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    weights=.94**np.arange(59,-1,-1);sigma=math.sqrt(float(np.sum(weights*returns[-60:]**2)/weights.sum()))*100
    return {'as_of':asof,'close':float(close[-1]),'atr14_points':float(np.mean(tr[-14:])),
      'atr14_pct':float(np.mean(tr[-14:])/close[-1]*100),'std20_daily_pct':float(np.std(returns[-20:],ddof=1)*100),
      'ewma_daily_pct':sigma,'ewma_lambda':.94,'ewma_observations':60,
      'range5_pct':float(np.mean(ranges[-5:])),'range20_pct':float(np.mean(ranges[-20:])),
      'previous20_range_pct':float(np.mean(ranges[-25:-5])),'latest_range_pct':float(ranges[-1]),
      'source':'證交所官方完整日線','url':'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST',
      'note':'振幅=(當日高−低)/前收；ATR14為真實區間均值。EWMA為近60筆平方報酬加權波動、λ固定0.94，僅作描述，未用來調整機率。'}

def assess(night,overseas,vol):
    ready=[r for r in night if r.get('included')];primary=ready[0] if ready else None
    us={r['key']:r for r in overseas if r.get('included')};drivers=[];watch=[]
    if primary:
        drivers.append(f"{primary['contract']}夜盤較同合約日盤收盤{primary['change_day_points']:+,.0f}點（{primary['change_day_pct']:+.2f}%）；相對結算參考價{primary['change_reference_points']:+,.0f}點。")
        if vol:
            primary['move_in_atr14']=abs(primary['change_day_points'])/vol['atr14_points']
            drivers.append(f"夜盤變動約為加權近14日ATR的{primary['move_in_atr14']:.2f}倍；期貨／現貨尺度不同，僅作量級參考。夜盤當盤振幅{primary['session_range_pct']:.2f}%。")
        watch.append(f"觀察夜盤低點{primary['low']:,.0f}能否守住，以及反彈能否收復同合約日盤收盤{primary['day_close']:,.0f}；不是預測目標。")
    for key in ['spx','nasdaq','sox','tsm','vix']:
        if key in us:drivers.append(f"{us[key]['label']}相對來源前收{us[key]['change_pct']:+.2f}%，資料時間{us[key]['quote_at']}。")
    direction=[us[k]['change_pct'] for k in ['spx','tsm'] if k in us]
    if primary and primary['change_day_points']<0 and len(direction)==2 and all(x<0 for x in direction):state='夜盤與海外同步偏弱';reading='目前有夜盤與海外同步承壓的證據；後續仍可能反彈，但不能把長期歷史上漲比例當成已納入這些訊息的即時預測。'
    elif primary and primary['change_day_points']>0 and len(direction)==2 and all(x>0 for x in direction):state='夜盤與海外同步改善';reading='目前夜盤與海外方向一致改善；仍須看現貨開盤與盤中承接，不把它當必漲。'
    elif primary:state='夜盤偏弱，海外待確認' if primary['change_day_points']<0 else '夜盤改善，海外待確認';reading='夜盤已反映新價格，海外可能分歧或資料不足；依已核對項目列出觀點，不推定缺值為安全。'
    elif us:state='海外變動已取得，夜盤待核對';reading='夜盤缺少同合約、同時段或足夠新鮮度資料，尚不能形成完整的跨市場評估。'
    else:state='等待可用的盤後資料';reading='目前沒有可核對且晚於台股收盤的新報價；已被現貨反映的舊行情不當新訊號。'
    if vol:drivers.append(f"加權近5日日均振幅{vol['range5_pct']:.2f}%，近20日{vol['range20_pct']:.2f}%，前一段20日{vol['previous20_range_pct']:.2f}%；波動與方向分開觀察。")
    return {'state':state,'reading':reading,'drivers':drivers,'watch_next':watch,
      'probability_status':'not_calibrated','updates_probabilities':False,
      'note':'即時情勢評估與歷史基準分開。現有漲跌百分比沒有使用這些盤中訊息，不代表目前行情的條件機率；不主觀加減百分比。'}

def _fetch(url,*,body=None):
    kwargs={'headers':{'User-Agent':'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)'},'timeout':(8,20)}
    response=requests.get(url,**kwargs) if body is None else requests.post(url,json=body,**kwargs)
    if response.status_code in (401,403,418,428,429):raise ValueError(f'來源限制存取 HTTP {response.status_code}；停止本次讀取')
    response.raise_for_status()
    if 'json' not in response.headers.get('Content-Type','').lower():raise ValueError('來源回傳非JSON，停止本次讀取')
    return response.content

def collect(asof,*,data_dir=None,fetcher=None):
    """Network only here, invoked by explicit chat publication, never GET."""
    data_dir=data_dir or db.DATA;fetcher=fetcher or _fetch
    root=data_dir/'v6_live_inputs'/(datetime.now(TZ).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    result={'version':VERSION,'market_as_of':asof,'night':[],'overseas':[],'volatility':None,'errors':[],'sources':[],'raw_directory':str(root)}
    def read(key,url,body=None):
        raw=fetcher(url,body=body);at=datetime.now(TZ);path=root/(key+'.json');path.write_bytes(raw)
        result['sources'].append({'key':key,'url':url,'request':body,'observed_at':at.isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),'file':str(path)})
        return json.loads(raw),at
    try:
        with sqlite3.connect(f'file:{data_dir/"monitor.sqlite3"}?mode=ro',uri=True) as conn:
            day_quotes={r[0]:r[1] for r in conn.execute('SELECT contract,close FROM v9_tx_quotes WHERE date=?',(asof,))}
        official=D.prices(data_dir);result['volatility']=volatility(official,asof);cash_close=result['volatility']['close']
    except (OSError,ValueError,sqlite3.Error) as e:result['errors'].append({'key':'local_reference','reason':str(e)});day_quotes={};cash_close=None
    if cash_close and day_quotes:
        try:
            payload,stamp=read('night',NIGHT_URL,{'MarketType':'1','SymbolType':'F','KindID':'1','CID':'TXF','ExpireMonth':''})
            result['night']=parse_night(payload,asof,stamp,day_quotes,cash_close)
            if not any(r['included'] for r in result['night']):result['errors'].append({'key':'night','reason':'未取得目前可核對且新鮮的夜盤成交，舊值不當即時資料'})
        except (OSError,ValueError,requests.RequestException) as e:result['errors'].append({'key':'night','reason':str(e)[:180]})
    for key,(symbol,label) in US.items():
        try:
            url='https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol,safe='')+'?range=5d&interval=5m&includePrePost=true'
            payload,stamp=read(key,url);result['overseas'].append(parse_yahoo(payload,key,asof,stamp))
        except (OSError,ValueError,KeyError,TypeError,requests.RequestException) as e:result['errors'].append({'key':key,'reason':str(e)[:180]})
    result['assessment']=assess(result['night'],result['overseas'],result['volatility']);result['captured_at']=datetime.now(TZ).isoformat(timespec='seconds')
    result['status']='partial' if result['errors'] else 'complete'
    path=data_dir/'v6_crash_scale.json'
    if path.exists():
        crash=json.loads(path.read_text())
        if crash.get('as_of')==asof:result['weekly_amplitude']=crash.get('amplitude')
    (root/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2));return result
