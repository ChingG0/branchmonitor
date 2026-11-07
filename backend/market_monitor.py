"""Independent market dashboard: validated public data and last-good local snapshots."""
from __future__ import annotations
import calendar
import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timedelta,timezone
from urllib.parse import quote,urljoin,urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from . import db, pe_river, liquidity_river, fed_rates, credit_spreads, market_ma20

TZ=ZoneInfo('Asia/Taipei')
CATALOG={
 'pe_river':('大盤價量河流','pe_river','臺灣證券交易所',liquidity_river.SOURCE),
 'ma20':('雙指數距離20MA','ma20','臺灣證券交易所／櫃買中心',market_ma20.SOURCE),
 'tw_news':('股市快訊','news','CMoney','https://cmnews.com.tw/twstock'),
 'us_news':('美股雷達','news','鉅亨網','https://news.cnyes.com/news/cat/wd_stock'),
 'fear':('恐懼與貪婪','fear','CNN','https://edition.cnn.com/markets/fear-and-greed'),
 'retail':('小台散戶多空比','retail','玩股網公式／期交所資料','https://www.wantgoo.com/futures/retail-indicator/wmt&'),
 'volume':('大盤成交金額','volume','臺灣證券交易所','https://www.twse.com.tw/zh/trading/historical/fmtqik.html'),
 'twse':('上市法人買賣超','institution','臺灣證券交易所','https://www.twse.com.tw/zh/trading/foreign/bfi82u.html'),
 'tpex':('上櫃法人買賣超','institution','櫃買中心','https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/summary/day.html'),
 'futures':('台指期未平倉','futures','Yahoo 股市','https://tw.stock.yahoo.com/future/futures_uncovered.html'),
 'rates':('美國利率與 FOMC','rates','Federal Reserve',fed_rates.SOURCE),
 'credit':('科技與金融公司債觀測','credit','comdirect／美國財政部',credit_spreads.TREASURY_PAGE),
 'twd':('台幣','quote','Yahoo Finance','https://finance.yahoo.com/quote/TWD=X/'),
 'jpy':('日圓','quote','Yahoo Finance','https://finance.yahoo.com/quote/JPY=X/'),
 'usd':('美元指數','quote','Yahoo Finance','https://finance.yahoo.com/quote/DX-Y.NYB/'),
 'gold':('臺銀金','quote','櫃買中心','https://www.tpex.org.tw/zh-tw/other/gold/statistics/info.html'),
 'btc':('Bitcoin','quote','Yahoo Finance','https://finance.yahoo.com/quote/BTC-USD/'),
 'oil':('布蘭特原油','quote','Yahoo Finance','https://hk.finance.yahoo.com/quote/BZ%3DF/'),
}
QUOTES={'twd':('TWD=X','TWD','台幣／1 美元'), 'jpy':('JPY=X','JPY','日圓／1 美元'),
        'usd':('DX-Y.NYB','USD','指數點'), 'btc':('BTC-USD','USD','美元／BTC'), 'oil':('BZ=F','USD','美元／桶')}
FED='https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
HISTORY_KEYS={'fear','twse','tpex','volume','retail','gold','futures',*QUOTES}
VALUE_KEYS=HISTORY_KEYS-{'twse','tpex','futures'}
_lock=threading.Lock()
_job={'status':'idle','running':False,'done':0,'total':0,'message':'讀取本機保存資料'}

def now():return datetime.now(TZ).isoformat(timespec='seconds')
def num(value):
    try:n=float(str(value).replace(',','').strip())
    except (ValueError,TypeError):raise ValueError('來源數值缺漏')
    if not math.isfinite(n):raise ValueError('來源數值無效')
    return n
def day(value):
    s=str(value).strip();parts=re.findall(r'\d+',s)
    if len(parts)==1 and len(s) in [7,8]:parts=[s[:-4],s[-4:-2],s[-2:]]
    if len(parts)!=3:raise ValueError('來源日期無法辨識')
    y,m,d=map(int,parts);result=date(y+1911 if y<1911 else y,m,d)
    if result>datetime.now(TZ).date():raise ValueError('來源日期晚於今天')
    return result.isoformat()
def fetch(url):
    r=requests.get(url,timeout=(8,22),headers={'User-Agent':'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)'})
    if r.status_code in (401,403,418,429):raise ValueError(f'來源限制自動讀取（HTTP {r.status_code}），可開啟原站查看')
    r.raise_for_status()
    return r.content
def soup(raw):return BeautifulSoup(raw,'html.parser')
def safe_link(value,base):
    url=urljoin(base,value);p=urlparse(url)
    if p.scheme!='https' or p.hostname!=urlparse(base).hostname:raise ValueError('新聞連結來源不符')
    return url

def parse_cm(raw):
    rows=[];seen=set()
    for card in soup(raw).select('.articleCard'):
        title=card.select_one('h2.articleCard__title');a=card.select_one('a[href^="/article/"]')
        if not title or not a:continue
        link=safe_link(a['href'],CATALOG['tw_news'][3])
        if link in seen:continue
        seen.add(link);rows.append({'title':title.get_text(' ',strip=True),'url':link})
        if len(rows)==5:break
    if len(rows)!=5:raise ValueError('無法取得完整的前五則台股新聞')
    return dict(items=rows,as_of=None,note='來源最新列表前五則；時間為本機擷取時間')

def parse_cnyes(raw):
    values=json.loads(raw)['items']['data'];rows=[];seen=set()
    for row in values:
        ident=str(row['newsId'])
        if not ident.isdigit() or ident in seen:continue
        seen.add(ident)
        stamp=datetime.fromtimestamp(num(row['publishAt']),TZ).isoformat(timespec='seconds')
        rows.append(dict(title=soup(row['title']).get_text(' ',strip=True),url='https://news.cnyes.com/news/id/'+ident,published_at=stamp))
    rows.sort(key=lambda r:r['published_at'],reverse=True)
    if len(rows)<5:raise ValueError('美股分類新聞不足五則')
    return dict(items=rows[:5],as_of=rows[0]['published_at'],note='美股分類最新五則，依發布時間排序')

def parse_institution(raw,market):
    data=json.loads(raw)
    if data.get('stat','').lower()!='ok':raise ValueError('法人資料尚未發布')
    as_of=day(data['date']);table=data if market=='twse' else data['tables'][0]
    expected=['單位名稱','買進金額','賣出金額','買賣差額'] if market=='twse' else ['單位名稱','買進金額(元)','賣出金額(元)','買賣超(元)']
    if table.get('fields')!=expected:raise ValueError('法人欄位格式改變')
    foreign='外資及陸資(不含外資自營商)' if market=='twse' else '外資及陸資(不含自營商)'
    result=[]
    for label,target in [('外資',foreign),('投信','投信')]:
        r=next((r for r in table['data'] if r[0].strip()==target),None)
        if r is None:raise ValueError('缺少外資或投信資料')
        buy,sell,net=map(num,r[1:4])
        if buy<0 or sell<0 or abs(buy-sell-net)>1:raise ValueError('法人買賣金額不符')
        result.append(dict(name=label,buy=buy,sell=sell,net=net))
    return dict(as_of=as_of,rows=result,unit='新台幣元',note='外資含陸資、不含外資自營商；顯示單位為億元')

def parse_futures(raw):
    s=soup(raw);text=s.get_text(' ',strip=True)
    m=re.search(r'法人未平倉口數\s+資料時間：?\s*(\d{4}/\d{2}/\d{2})',text)
    if not m:raise ValueError('未平倉資料日期無法驗證')
    rows=[]
    for label in ['外資及陸資','投信','自營商','三大法人合計']:
        node=s.find(string=lambda t:t and t.strip()==label)
        li=node.find_parent('li') if node else None
        values=[x.get_text(strip=True) for x in li.select('ul li')] if li else []
        if len(values)!=9:raise ValueError('未平倉表格欄位改變')
        long,short,net=map(num,values[:3])
        if long<0 or short<0 or abs(long-short-net)>0:raise ValueError('未平倉口數核對失敗')
        rows.append(dict(name=label,long=long,short=short,net=net))
    return dict(as_of=day(m[1]),rows=rows,unit='口',note='台股期貨法人多單、空單及淨額；不是全市場未平倉總量')

def parse_fed(raw,today=None):
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    months={name:i for i,name in enumerate(calendar.month_name) if name}
    months.update({name:i for i,name in enumerate(calendar.month_abbr) if name})
    meetings=[];statements=[]
    for panel in soup(raw).select('.panel'):
        heading=panel.select_one('.panel-heading');match=re.search(r'(20\d{2}) FOMC Meetings',heading.get_text() if heading else '')
        if not match:continue
        year=int(match[1])
        for row in panel.select('.fomc-meeting'):
            month=row.select_one('.fomc-meeting__month');dates=row.select_one('.fomc-meeting__date')
            if not month or not dates:continue
            ds=dates.get_text(strip=True)
            if 'notation' in ds:continue
            names=month.get_text(strip=True).split('/');numbers=re.findall(r'\d+',ds)
            if len(numbers) not in (1,2) or any(n not in months for n in names):continue
            begin=date(year,months[names[0]],int(numbers[0]));end=date(year,months[names[-1]],int(numbers[-1]))
            meetings.append((begin,end))
            if end<=today and row.find('a',href=re.compile(r'/newsevents/pressreleases/monetary\d+a.htm$')):statements.append(end)
    upcoming=sorted((a,b) for a,b in meetings if b>=today and b not in statements)
    if not upcoming or not statements:raise ValueError('FOMC 行事曆資料不足')
    return dict(next_start=upcoming[0][0].isoformat(),next_end=upcoming[0][1].isoformat(),statement_date=max(statements).isoformat())

def parse_rates(raw,calendar_raw):
    node=soup(raw).select_one('#description')
    text=node.get_text(' ',strip=True) if node else ''
    match=re.search(r'target range for the federal funds rate.{0,100}?(\d+(?:\.\d+)?)%?\s*[-–]\s*(\d+(?:\.\d+)?)%',text,re.I)
    if not match:raise ValueError('無法辨識現行聯邦基金利率區間')
    low,high=map(num,match.groups())
    if not 0<=low<high<30:raise ValueError('利率區間無效')
    info=parse_fed(calendar_raw)
    # The explanatory paragraph must refer to the latest published meeting.
    reference=datetime.fromisoformat(info['statement_date'])
    if not re.search(reference.strftime('%B')+r'\s+'+str(reference.year),text,re.I):raise ValueError('利率說明與最新 FOMC 決議月份不同')
    return dict(as_of=info['statement_date'],low=low,high=high,next_start=info['next_start'],next_end=info['next_end'],calendar_url=FED,note='聯邦基金利率目標區間；FOMC 日期為美東會議日期')

def parse_quote(raw,key):
    r=json.loads(raw)['chart']['result'][0];m=r['meta'];symbol,currency,unit=QUOTES[key]
    if m.get('symbol')!=symbol or m.get('currency')!=currency:raise ValueError('報價商品或幣別不符')
    value=num(m['regularMarketPrice'])
    if value<=0:raise ValueError('報價無效')
    stamp=datetime.fromtimestamp(num(m['regularMarketTime']),TZ)
    if stamp.timestamp()>datetime.now(TZ).timestamp()+300:raise ValueError('報價時間晚於現在')
    change=m.get('regularMarketChangePercent')
    change=num(change) if change is not None else None
    history=quote_history(r,stamp.date().isoformat())
    return dict(value=value,change_pct=change,as_of=stamp.isoformat(timespec='seconds'),symbol=symbol,unit=unit,history=history,note='來源最新可用報價，可能延遲；漲跌幅依來源提供。歷史比較採已取得的前一來源日數值')

def quote_history(result,cutoff):
    history={}
    closes=((result.get('indicators',{}).get('quote') or [{}])[0].get('close') or [])
    for t,close in zip(result.get('timestamp',[]),closes):
        try:
            stamp=datetime.fromtimestamp(num(t),TZ);value=num(close)
            if value>0 and stamp.date().isoformat()<=cutoff:history[stamp.date().isoformat()]=dict(date=stamp.date().isoformat(),value=value)
        except (ValueError,TypeError,OverflowError):continue
    return sorted(history.values(),key=lambda r:r['date'])


def parse_gold(raw):
    row=next((r for r in json.loads(raw) if r.get('GoldCode')=='AU9901'),None)
    if not row:raise ValueError('找不到臺銀金 AU9901')
    value=num(row['TradingLatestTradingInfo.LatestPrice']);buy=num(row['QuotedBuyingB.Price']);sell=num(row['QuotedSellingS.Price'])
    if min(value,buy,sell)<=0 or buy>sell:raise ValueError('臺銀金報價無效')
    stamp=day(row['Date']);t=str(row['Time']).zfill(6)
    if not re.fullmatch(r'\d{6}',t):raise ValueError('臺銀金時間缺漏')
    timestamp=datetime.fromisoformat(stamp+'T'+t[:2]+':'+t[2:4]+':'+t[4:]).replace(tzinfo=TZ)
    return dict(value=value,bid=buy,ask=sell,as_of=timestamp.isoformat(),unit='新台幣／台錢',symbol='AU9901',change_pct=None,previous_bid=num(row['QuotedBuyingPrePrice']) if row.get('QuotedBuyingPrePrice') else None,previous_ask=num(row['QuotedSellingPrePrice']) if row.get('QuotedSellingPrePrice') else None,note='最近成交價；1 台錢＝3.75 公克。另列造市商買／賣報價')

def parse_fear(raw):
    row=json.loads(raw)['fear_and_greed'];score=num(row['score'])
    if not 0<=score<=100:raise ValueError('情緒指數超出 0～100')
    stamp=datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('情緒指數時間缺少時區')
    result=dict(value=score,rating=str(row['rating']),as_of=stamp.astimezone(TZ).isoformat(),note='CNN 美股市場情緒指標；不是加密貨幣恐懼貪婪指數。前日依美股來源日期比較')
    current_day=stamp.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    history=fear_history(json.loads(raw))
    # CNN's previous_close is authoritative when the source also supplies its date.
    prior=[r for r in history if r['date']<current_day]
    if prior and row.get('previous_close') is not None:
        previous=num(row['previous_close'])
        if 0<=previous<=100:prior[-1]['value']=previous
    result['history']=history
    return _merge_history('fear',result)

def parse_retail(raw):
    rows=[]
    for tr in soup(raw).select('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.select('th,td')]
        if len(cells)<5 or not re.fullmatch(r'20\d{2}/\d{2}/\d{2}',cells[0]):continue
        try:
            stamp=day(cells[0]);close,long_,short_,ratio=map(num,cells[1:5])
        except ValueError:continue
        if min(close,long_,short_)<0 or not -100<=ratio<=100:continue
        rows.append({'date':stamp,'close':close,'retail_long':long_,'retail_short':short_,'ratio_pct':ratio})
    if not rows:raise ValueError('散戶多空比表格無有效資料')
    latest=max(rows,key=lambda r:r['date'])
    return dict(as_of=latest['date'],value=latest['ratio_pct'],retail_long=latest['retail_long'],retail_short=latest['retail_short'],rows=sorted(rows,key=lambda r:r['date'],reverse=True)[:20],unit='%',note='小台指散戶多空比；正值代表散戶淨偏多，風控採反向解讀')

def parse_retail_official(market_raw,institution_raw):
    market=json.loads(market_raw);institution=json.loads(institution_raw)
    contracts=[r for r in market if r.get('Contract')=='MTX' and r.get('TradingSession')=='一般' and str(r.get('OpenInterest','')).replace(',','').isdigit()]
    rows=[r for r in institution if r.get('ContractCode')=='小型臺指期貨' and r.get('Item') in ['自營商','投信','外資及陸資']]
    if not contracts or len(rows)!=3:raise ValueError('期交所小台未平倉資料不足')
    dates={str(r.get('Date')) for r in contracts+rows}
    if len(dates)!=1:raise ValueError('小台與法人資料日期不一致')
    total=sum(num(r['OpenInterest']) for r in contracts);institution_long=sum(num(r['OpenInterest(Long)']) for r in rows);institution_short=sum(num(r['OpenInterest(Short)']) for r in rows)
    retail_long=total-institution_long;retail_short=total-institution_short
    if total<=0 or min(retail_long,retail_short)<0:raise ValueError('散戶多空比計算結果無效')
    ratio=(retail_long-retail_short)/total*100
    return dict(as_of=day(next(iter(dates))),value=ratio,retail_long=retail_long,retail_short=retail_short,total_open_interest=total,unit='%',source_data_url='https://openapi.taifex.com.tw/',note='依玩股網公式：小台總未平倉扣除三大法人多／空方部位；正值代表散戶淨偏多，風控採反向解讀')

def parse_market_volume(raw):
    payloads=raw if isinstance(raw,(list,tuple)) else [raw]
    expected=['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數']
    by_date={}
    for payload in payloads:
        data=json.loads(payload)
        if data.get('stat','').lower()!='ok' or data.get('fields')!=expected:raise ValueError('大盤成交資訊格式改變')
        for row in data.get('data',[]):
            if len(row)!=6:continue
            stamp=day(row[0]);shares,amount,trades,index,change=map(num,row[1:])
            if min(shares,amount,trades,index)<0:continue
            by_date[stamp]={'date':stamp,'shares':shares,'amount':amount,'amount_100m':amount/100000000,'trades':trades,'index':index,'change':change}
    rows=list(by_date.values())
    if not rows:raise ValueError('大盤成交資訊沒有有效資料')
    rows.sort(key=lambda r:r['date']);latest=rows[-1];base=rows[-21:-1]
    average=sum(r['amount_100m'] for r in base)/len(base) if base else None
    return dict(as_of=latest['date'],value=latest['amount_100m'],amount=latest['amount'],average_20=average,ratio=latest['amount_100m']/average if average else None,rows=rows[-21:],unit='億元',note='上市市場每日成交金額；例如 8,000 億元顯示為 8000')

def load_card(key):
    if key=='ma20':
        result=market_ma20.report()
        if not result['available']:raise ValueError('請先完成加權與櫃買行情更新')
        return result
    if key=='pe_river':return pe_river.refresh(fetch)
    if key=='tw_news':return parse_cm(fetch(CATALOG[key][3]))
    if key=='us_news':return parse_cnyes(fetch('https://api.cnyes.com/media/api/v1/newslist/category/wd_stock?limit=5'))
    if key=='fear':return parse_fear(fetch('https://production.dataviz.cnn.io/index/fearandgreed/graphdata'))
    if key=='retail':
        from . import market_futures_sources as dated
        target=dated.cash_day()
        try:
            result=parse_retail_official(fetch('https://openapi.taifex.com.tw/v1/DailyMarketReportFut'),fetch('https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate'))
            if result['as_of']<target:raise ValueError('小台OpenAPI尚未到最新收盤日')
            return result
        except (ValueError,requests.RequestException):return dated.retail(target)
    if key=='volume':
        month=datetime.now(TZ).date().replace(day=1);months=[]
        for _ in range(3):
            months.append(month);month=(month-timedelta(days=1)).replace(day=1)
        url='https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json&date='
        return parse_market_volume([fetch(url+stamp.strftime('%Y%m01')) for stamp in reversed(months)])
    if key=='twse':return parse_institution(fetch('https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json'),'twse')
    if key=='tpex':return parse_institution(fetch('https://www.tpex.org.tw/www/zh-tw/insti/summary?response=json'),'tpex')
    if key=='futures':
        from . import market_futures_sources as dated
        target=dated.cash_day()
        try:
            result=parse_futures(fetch(CATALOG[key][3]))
            if result['as_of']<target:raise ValueError('Yahoo法人資料尚未到最新收盤日')
            return result
        except (ValueError,requests.RequestException):return dated.futures(target)
    if key=='rates':return fed_rates.load(fetch,parse_fed,FED)
    if key=='credit':
        with db.connect() as conn:
            saved=conn.execute("SELECT payload FROM market_observations WHERE key='credit'").fetchone()
        prior=json.loads(saved['payload']) if saved and saved['payload'] else None
        return credit_spreads.load(fetch,prior)
    if key=='gold':return parse_gold(fetch('https://www.tpex.org.tw/openapi/v1/tpex_gold_latest'))
    if key in QUOTES:return parse_quote(fetch('https://query1.finance.yahoo.com/v8/finance/chart/'+quote(QUOTES[key][0],safe='')+'?interval=1d&range=5d'),key)
    raise ValueError('無效市場卡片')

def observation_day(key,data):
    """Use the observation's market date, never the local fetch date."""
    if key=='fear':
        stamp=datetime.fromisoformat(data['as_of'].replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('情緒指數時間缺少時區')
        return stamp.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    return day(data['as_of'][:10])


def fear_history(payload):
    # The graph uses UTC calendar labels for daily observations; its final
    # intra-day timestamp can repeat the same source day. Keep one row per day.
    rows={}
    for row in payload.get('fear_and_greed_historical',{}).get('data',[]):
        try:
            value=num(row['y']);stamp=datetime.fromtimestamp(num(row['x'])/1000,timezone.utc)
            if 0<=value<=100:rows[stamp.date().isoformat()]=dict(date=stamp.date().isoformat(),value=value)
        except (ValueError,TypeError,KeyError,OverflowError):continue
    return sorted(rows.values(),key=lambda r:r['date'])


def _merge_history(key,data,prior=None,extra=None):
    if key not in HISTORY_KEYS:return data
    current=observation_day(key,data)
    history={}
    # Later source revisions take precedence, but the current saved reading
    # always wins for its own date. Repeated fetches never create a new day.
    def compact(item):
        return dict(date=observation_day(key,item),**({'value':item['value']} if key in VALUE_KEYS else {'rows':item['rows']}))
    combined=list((prior or {}).get('history',[]))
    if prior:combined.append(compact(prior))
    embedded=[]
    if key in ('volume','retail'):
        for row in data.get('rows',[]):
            metric='amount_100m' if key=='volume' else 'ratio_pct'
            if metric in row:embedded.append(dict(date=row['date'],value=row[metric]))
    combined+=list(extra or [])+embedded+list(data.get('history',[]))+[compact(data)]
    for row in combined:
        try:
            d=day(row['date'])
            if d>current:continue
            if key in VALUE_KEYS:
                value=num(row['value'])
                if key=='fear' and not 0<=value<=100:continue
                if key not in ('fear','retail') and value<=0:continue
                history[d]=dict(date=d,value=value)
            elif key=='futures':
                checked=[]
                for label in ('外資及陸資','投信','自營商','三大法人合計'):
                    item=next(r for r in row['rows'] if r['name']==label)
                    long_,short,net=(num(item[k]) for k in ('long','short','net'))
                    if min(long_,short)<0 or abs(long_-short-net)>.1:raise ValueError('未平倉歷史口數不符')
                    checked.append(dict(name=label,long=long_,short=short,net=net))
                history[d]=dict(date=d,rows=checked)
            else:
                checked=[]
                for label in ('外資','投信'):
                    item=next(r for r in row['rows'] if r['name']==label)
                    buy,sell,net=(num(item[k]) for k in ('buy','sell','net'))
                    if buy<0 or sell<0 or abs(buy-sell-net)>1:raise ValueError('法人歷史金額不符')
                    checked.append(dict(name=label,buy=buy,sell=sell,net=net))
                history[d]=dict(date=d,rows=checked)
        except (ValueError,KeyError,TypeError,StopIteration):continue
    result=dict(data,source_date=current,history=sorted(history.values(),key=lambda r:r['date'])[-30:])
    return result


def save_comparison_history(key,rows):
    """Explicit backfill of already parsed matching-source rows; no latest-price update."""
    if key not in HISTORY_KEYS:raise ValueError('此卡片沒有歷史比較')
    with db.connect() as c:
        saved=c.execute('SELECT payload FROM market_observations WHERE key=?',(key,)).fetchone()
        if not saved or not saved['payload']:raise ValueError('請先保存該卡片最新資料')
        data=json.loads(saved['payload']);result=_merge_history(key,data,extra=rows)
        c.execute('UPDATE market_observations SET payload=? WHERE key=?',(json.dumps(result,ensure_ascii=False,allow_nan=False),key))
    return len(result['history'])


def report():
    with db.connect() as c:saved={r['key']:dict(r) for r in c.execute('SELECT * FROM market_observations')}
    cards=[]
    for key,(title,kind,source,url) in CATALOG.items():
        row=saved.get(key,{})
        data=json.loads(row['payload']) if row.get('payload') else None
        if data and key in HISTORY_KEYS:data=_merge_history(key,data)
        error=row.get('error','')
        if key=='ma20':
            data=market_ma20.report()  # Local completed OHLC only; never fetch or publish models.
            row={};error=''
        if key=='pe_river' and data and data.get('liquidity'):
            current=liquidity_river.report()
            if current['available'] and (current['as_of'] or '') >= (data['liquidity'].get('as_of') or ''):
                data['liquidity']=current
        cards.append(dict(id=key,title=title,kind=kind,source=(data or {}).get('source_name',source),url=(data or {}).get('source_url',url),data=data,
            saved_at=row.get('saved_at'),checked_at=row.get('checked_at'),error=error))
    return dict(cards=cards,job=dict(_job))

def update_one(key):
    try:
        result=load_card(key);stamp=now()
        warning=result.get('warning','') if key in ('pe_river','credit') else ''
        if key=='pe_river':
            liq=result.get('liquidity') or {}
            warning='；'.join(dict.fromkeys(x for x in [warning,liq.get('warning',''),liq.get('margin_warning',''),liq.get('maintenance_warning','')] if x))
            tail=(liq.get('points') or [None])[-1]
            if tail and any(tail.get(k) is None for k in ('financing_units','short_units','maintenance_pct')):warning+='；'+tail['date']+'融資融券或維持率未齊，請手動更新'
        with db.connect() as c:
            if key in HISTORY_KEYS:
                old=c.execute('SELECT payload FROM market_observations WHERE key=?',(key,)).fetchone()
                prior=json.loads(old['payload']) if old and old['payload'] else None
                if prior and observation_day(key,result)<observation_day(key,prior):raise ValueError('來源日期倒退，保留較新資料')
                if prior and 'T' in result['as_of'] and 'T' in prior['as_of'] and datetime.fromisoformat(result['as_of'])<datetime.fromisoformat(prior['as_of']):raise ValueError('來源時間倒退，保留較新資料')
                result=_merge_history(key,result,prior)
            encoded=json.dumps(result,ensure_ascii=False,allow_nan=False)
            c.execute('INSERT OR REPLACE INTO market_observations VALUES (?,?,?,?,?)',(key,encoded,stamp,stamp,warning))
        return not bool(warning)
    except Exception as e:
        message=str(e) if isinstance(e,ValueError) else '來源連線或格式異常，請稍後重試'
        with db.connect() as c:
            c.execute("INSERT INTO market_observations VALUES (?,NULL,NULL,?,?) ON CONFLICT(key) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error",(key,now(),message[:180]))
        return False

def refresh(key=None):
    if key is not None and (not isinstance(key,str) or key not in CATALOG):raise ValueError('無效市場卡片')
    if not _lock.acquire(blocking=False):return dict(_job)
    keys=[key] if key else list(CATALOG)
    _job.update(status='running',running=True,done=0,total=len(keys),message='正在更新市場資料')
    def worker():
        failed=0
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for task in as_completed([pool.submit(update_one,k) for k in keys]):
                    if not task.result():failed+=1
                    _job['done']+=1
            _job['status']='partial' if failed else 'complete'
            _job['message']=f'更新結束 · {len(keys)-failed}/{len(keys)} 張成功'+(f'，{failed} 張來源未齊，請手動更新' if failed else '')
        except Exception:
            _job['status']='failed'
            _job['message']='更新未完成，已保留成功資料，請手動更新'
        finally:_job['running']=False;_lock.release()
    try:threading.Thread(target=worker,daemon=True).start()
    except Exception:_job['running']=False;_lock.release();raise
    return dict(_job)
