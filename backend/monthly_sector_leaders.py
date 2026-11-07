"""Month leaders using the application's own multi-label sector dictionary.

All calculations are explicit-refresh only. Historical membership is a current
snapshot, not a historical point-in-time universe or an investable backtest.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,date,timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import argparse,gzip,hashlib,json,math,os,uuid
from . import db,sector_flow

TZ=ZoneInfo('Asia/Taipei');VERSION='own-sector-month-leaders-1'
FILE='sector-leaders.json';HISTORY='sector-history.json.gz'
MIN_MEMBERS=2;MIN_COVERAGE=.8


def _prior(month):return (date.fromisoformat(month+'-01')-timedelta(days=1)).strftime('%Y-%m')
def _price(v):
    if isinstance(v,bool):return None
    try:x=float(v)
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) and x>0 else None


def parse_monthly(value,symbol,before_month):
    """Yahoo timestamps label month starts; never treat this as a close date.

    Some responses include a live month even with an earlier period2. Exclude
    it explicitly. Price close, not adjusted-close/average/return index.
    """
    rows=value.get('chart',{}).get('result')
    if not rows:raise ValueError('個股沒有可用月線')
    r=rows[0];meta=r['meta']
    if meta.get('symbol')!=symbol or meta.get('dataGranularity')!='1mo':raise ValueError('個股代碼或月線頻率不符')
    if meta.get('currency')!='TWD' or meta.get('exchangeTimezoneName')!='Asia/Taipei':raise ValueError('台股月線幣別或時區不符')
    ts=r.get('timestamp',[]);close=r['indicators']['quote'][0].get('close',[])
    if len(ts)!=len(close):raise ValueError('個股月線長度不符')
    result={}
    for stamp,value in zip(ts,close):
        month=datetime.fromtimestamp(stamp,TZ).strftime('%Y-%m')
        if month>=before_month:continue
        v=_price(value)
        if v is None:continue
        if month in result and abs(result[month]-v)>1e-8:raise ValueError('同月個股收盤衝突')
        result[month]=v
    if not result:raise ValueError('沒有已結束月份的個股收盤')
    return result


def atomic(path,value,*,compressed=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    content=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
    with tmp.open('xb') as f:f.write(gzip.compress(content) if compressed else content);f.flush();os.fsync(f.fileno())
    tmp.replace(path)


def cached(*,store=None):
    try:
        v=json.loads(((store or db.DATA/'monthly_statistics')/FILE).read_text())
        if v.get('version')!=VERSION or not isinstance(v.get('months'),dict):raise ValueError('族群月排行版本不符')
        return v
    except (OSError,ValueError,TypeError):return {'version':VERSION,'months':{},'metadata':{},'warning':'尚未補齊自訂族群月資料'}


def load_history(store):
    try:
        v=json.loads(gzip.decompress((store/HISTORY).read_bytes()))
        if v.get('version')!=VERSION:raise ValueError('個股月線快取版本不符')
        return v
    except FileNotFoundError:return {'version':VERSION,'prices':{},'metadata':{}}


def local_endpoints(timeline):
    from prototype import sectors
    dates=sorted({r['date'] for r in timeline});prices={};proof=[]
    if not dates:return prices,{'rows':0}
    marks=','.join('?'*len(dates))
    if not sectors.PRICES.exists():raise ValueError('本機個股行情尚未建立')
    with sectors.quotes() as c:
        c.execute('BEGIN')
        for row in c.execute(f'SELECT date,symbol,close FROM prices WHERE date IN ({marks})',dates):
            v=_price(row['close'])
            if v is not None:prices[(row['date'],row['symbol'])]=v
        # RS cache includes the previous year-end baseline not yet in prices.
        for row in c.execute(f'SELECT date,payload FROM rs_quotes WHERE date IN ({marks})',dates):
            for q in json.loads(row['payload']):
                v=_price(q.get('close'))
                if v is not None and q.get('date')==row['date']:
                    prices.setdefault((row['date'],q['symbol']),v)
    proof=sorted((d,s,v) for (d,s),v in prices.items())
    return prices,{'rows':len(proof),'sha256':hashlib.sha256(json.dumps(proof).encode()).hexdigest()}


def calculate(classification,history,timeline,local_prices,*,now=None):
    """Equal mean of each member's monthly endpoint price return.

    Existing sector-board period-return convention; no compounding of daily
    breadth/mean or substitution of a 20-day return for a calendar month.
    """
    if classification.get('error'):raise ValueError(classification['error'])
    groups={g['name']:set() for g in classification['groups']}
    for symbol,names in classification['memberships'].items():
        for name in names:
            if name in groups:groups[name].add(symbol)
    if not groups:raise ValueError('自訂族群字典沒有成員')
    stamp=now or datetime.now(TZ);current=stamp.astimezone(TZ).strftime('%Y-%m')
    indexed={r['month']:r for r in timeline};hist=history.get('prices',{});records={}
    def period_result(r,baseline):
        member_returns={};source_counts=defaultdict(int)
        if baseline:
            for symbol in classification['memberships']:
                h=hist.get(symbol,{});start=_price(h.get(baseline['month']));end=_price(h.get(r['month']));kind='yahoo-monthly'
                # Match both dates and price basis. Current month never mixes
                # a provider-adjusted old close with an unadjusted local close.
                if r['month']==current or start is None or end is None:
                    start=_price(local_prices.get((baseline['date'],symbol)));end=_price(local_prices.get((r['date'],symbol)));kind='local-exact-endpoints'
                if start is not None and end is not None:
                    member_returns[symbol]=(end/start-1)*100;source_counts[kind]+=1
        valid=[]
        for name,symbols in groups.items():
            values=[member_returns[s] for s in sorted(symbols) if s in member_returns];n=len(values);total=len(symbols)
            if n<MIN_MEMBERS or not total or n/total<MIN_COVERAGE:continue
            valid.append({'name':name,'return_pct':sum(values)/n,'quoted':n,'members':total,'coverage_pct':n/total*100})
        valid.sort(key=lambda x:(-x['return_pct'],x['name']))
        leaders=[x for x in valid if x['return_pct']>1e-10][:3]
        return {'month':r['month'],'date':r['date'],'previous_date':baseline['date'] if baseline else None,
          'partial':r.get('complete') is not True,'status':'ready' if leaders else 'no_risers' if valid else 'missing',
          'leaders':leaders,'eligible_groups':len(valid),'total_groups':len(groups),'positive_groups':sum(x['return_pct']>1e-10 for x in valid),
          'usable_symbols':len(member_returns),'return_sources':dict(source_counts)}
    for month,r in sorted(indexed.items()):
        if month>current or r['date']>stamp.astimezone(TZ).date().isoformat():continue
        monthly=period_result(r,indexed.get(_prior(month)))
        baseline_month=f"{int(month[:4])-1}-12"
        monthly['ytd']={**period_result(r,indexed.get(baseline_month)),'baseline_month':baseline_month}
        records[month]=monthly
    supplied=[m for m,v in records.items() if v['eligible_groups']]
    ytd_supplied=[m for m,v in records.items() if v['ytd']['eligible_groups']]
    fingerprint=hashlib.sha256(json.dumps(classification,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'version':VERSION,'saved_at':stamp.isoformat(),'months':records,
      'metadata':{'classification_source':'prototype/sectors.sqlite3','dictionary_snapshot':classification.get('snapshot'),
       'dictionary_sha256':fingerprint,'group_count':len(groups),'symbol_count':len(classification['memberships']),
       'first_month':min(supplied) if supplied else None,'last_month':max(supplied) if supplied else None,
       'ytd_first_month':min(ytd_supplied) if ytd_supplied else None,'ytd_last_month':max(ytd_supplied) if ytd_supplied else None,
       'ranking_modes':['monthly','ytd'],'ranking_method':'endpoint-equal-mean-monthly-and-ytd-1','min_members':MIN_MEMBERS,'min_coverage_pct':MIN_COVERAGE*100,'history':history.get('metadata',{}),
       'note':'依目前自訂族群成員回看，非當年成員名單；含存活者偏差。各月只在至少2檔且成員價格涵蓋率達80%的族群中，按成員月價格報酬等權平均排序。族群可重疊，不是對大盤的貢獻排名。歷史月線採資料商拆併股調整價格、不含現金股利；本月使用與台股格子相同起訖日的本機收盤。'}}


def refresh_sync(timeline,*,store=None,now=None):
    target=store or db.DATA/'monthly_statistics';cl=sector_flow.classification()
    if cl.get('error'):raise ValueError(cl['error'])
    hist=load_history(target)
    prices,proof=local_endpoints(timeline);result=calculate(cl,hist,timeline,prices,now=now)
    result['metadata']['endpoint_proof']=proof;atomic(target/FILE,result);return result


def attach(report,sector):
    """Attach to TAIEX only and only when both endpoint dates match exactly."""
    tw=report.get('markets',{}).get('taiex')
    if not tw:return report
    months=sector.get('months',{});indexed={r['month']:r for r in tw['rows']}
    for r in tw['rows']:
        row=months.get(r['month'])
        if row and row['date']==r['date'] and row.get('previous_date')==r.get('previous_date'):
            r['sector_leaders']=row['leaders'];r['sector_status']=row['status'];r['sector_eligible_groups']=row['eligible_groups'];r['sector_total_groups']=row['total_groups']
        else:r.update(sector_leaders=[],sector_status='stale' if row else 'missing')
        ytd=row.get('ytd') if row else None;baseline=indexed.get(f"{int(r['month'][:4])-1}-12")
        if ytd and baseline and ytd['date']==r['date'] and ytd.get('previous_date')==baseline['date']:
            r['sector_ytd']=ytd
        else:r['sector_ytd']={'leaders':[],'status':'stale' if ytd else 'missing'}
    report['sector_metadata']=sector.get('metadata',{});report['sector_saved_at']=sector.get('saved_at')
    return report


def import_raw(raw,universe,*,store,before_month,manifest=None,now=None):
    """Explicit long-history bootstrap from the chat-downloaded raw files."""
    prices={};errors=[]
    for item in universe:
        path=raw/(item['symbol']+'.json.gz')
        try:prices[item['symbol']]=parse_monthly(json.loads(gzip.decompress(path.read_bytes())),item['yahoo'],before_month)
        except (OSError,ValueError,KeyError,TypeError,IndexError) as e:errors.append({'symbol':item['symbol'],'error':'來源無可用歷史' if isinstance(e,FileNotFoundError) else str(e)[:200]})
    if not prices:raise ValueError('個股月線全部不可用，保留舊快取')
    earliest=min(m for values in prices.values() for m in values)
    result={'version':VERSION,'prices':prices,'metadata':{'source':'Yahoo Finance individual monthly price closes','source_url':'https://finance.yahoo.com/',
       'imported_at':(now or datetime.now(TZ)).isoformat(),'complete_before_month':before_month,'first_close_month':earliest,'symbols':len(prices),'requested_symbols':len(universe),
       'failed_symbols':errors,'source_manifest_sha256':hashlib.sha256(json.dumps(manifest or [],sort_keys=True).encode()).hexdigest()}}
    atomic(store/HISTORY,result,compressed=True);return result
