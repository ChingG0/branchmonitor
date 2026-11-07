"""Explicit, offline Package6/7 build. Reuses immutable prior packages."""
from pathlib import Path
from contextlib import closing
from collections import deque
import json,gzip,hashlib,sqlite3
from . import db,margin_statistics as src,margin_analytics as a,margin_events as e,margin_analytics_build as p345

AUDIT=db.ROOT/'research/margin_statistics_p67_20261105'
P2=db.ROOT/'research/margin_statistics_p2_20261105'
P345=db.ROOT/'research/margin_statistics_p345_20261105'
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
def ro(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

def pin_indexes():
    target=AUDIT/'indexes.json'
    if target.exists():raise ValueError('Indexes already frozen; do not overwrite')
    # This function only validates and reads saved prices. It does not call refresh_prices.
    from . import v6_shared_distribution as existing
    official=existing.prices();meta=json.loads((db.DATA/'v6_distribution_prices/manifest.json').read_text())
    with closing(ro(P2/'inputs.sqlite3')) as c:dates=[r[0] for r in c.execute('SELECT day FROM calendar ORDER BY day')]
    first,last=dates[0],dates[-1]
    tw={str(d):float(r.close) for d,r in official.iterrows() if first<=str(d)<=last}
    with closing(ro(db.DATA/'monitor.sqlite3')) as c:
        tp={r['date']:r['close'] for r in c.execute("SELECT date,close FROM prices WHERE symbol='TPEX' AND market='TPEX' AND date BETWEEN ? AND ? ORDER BY date",(first,last))}
    if any(not e.positive(x) for v in (tw,tp) for x in v.values()):raise ValueError('Invalid saved index close')
    result={'created_at':src.now(),'cutoff':last,'markets':{'TWSE':tw,'TPEX':tp},'sources':{
        'TWSE':{'label':'既有已核對官方加權指數價格','verified_cache':True,'cache_sha256':meta['sha256'],'base_sha256':meta['base_sha256'],'last_observed_at':meta['observed_at'],'url':'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'},
        'TPEX':{'label':'既有本機櫃買指數收盤；未新增網路核驗','verified_cache':False,'table':'monitor.sqlite3/prices, symbol=TPEX','first':min(tp) if tp else None,'last':max(tp) if tp else None}},
        'note':'只保存當時既有來源版本；缺櫃買行情不以加權指數補足，這不是原始發布時點回測'}
    dump(target,result);dump(AUDIT/'indexes-manifest.json',{'created_at':src.now(),'sha256':sha(target),'coverage':{m:len(v) for m,v in result['markets'].items()}})

def replay(cutoff=None):
    if sha(AUDIT/'indexes.json')!=json.loads((AUDIT/'indexes-manifest.json').read_text())['sha256']:raise ValueError('Pinned indexes differ')
    protected=json.loads((AUDIT/'protected-before.json').read_text())
    for rel in ['research/margin_statistics_p2_20261105/inputs.sqlite3','research/margin_statistics_p345_20261105/reference.sqlite3','data/margin_statistics/analytics.json.gz','docs/MARGIN_STATISTICS_PROTOCOL.json']:
        if sha(db.ROOT/rel)!=protected[rel]:raise ValueError('Protected prerequisite changed: '+rel)
    indexes=json.loads((AUDIT/'indexes.json').read_text())
    with gzip.open(src.folder()/'analytics.json.gz','rt') as f:analytics=json.load(f)
    if analytics['model_ready'] is not False:raise ValueError('Unexpected Package2 promotion')
    dates=[d for d in analytics['dates'] if cutoff is None or d<=cutoff]
    if not dates:raise ValueError('Empty event calendar')
    grades={};series={};breadths={m:[] for m in ('TWSE','TPEX')};history={m:deque(maxlen=19) for m in breadths};price_maps={m:[] for m in breadths}
    for m in breadths:
        path=P2/(m.lower()+'-daily.json.gz');rel=str(path.relative_to(db.ROOT))
        if sha(path)!=protected[rel]:raise ValueError('Pinned grades differ')
        with gzip.open(path,'rt') as f:grades[m]={r['date']:r for r in json.load(f)}
    with closing(ro(P345/'reference.sqlite3')) as c:
        for day in dates:
            quotes=[dict(r) for r in c.execute('SELECT market,symbol,name,close FROM quotes WHERE day=? ORDER BY market,symbol',(day,))]
            for m in breadths:
                current={r['symbol']:r['close'] for r in quotes if r['market']==m and a.classify(r['symbol'],r['name'],m) in ('listed_stock','otc_stock')}
                breadths[m].append(e.breadth(current,list(history[m])));history[m].append(current);price_maps[m].append(current)
    for m in breadths:
        loans=[grades[m].get(d,{}).get('official') for d in dates]
        close=[indexes['markets'][m].get(d) for d in dates]
        bucket='listed_stock' if m=='TWSE' else 'otc_stock'
        eligible=[not a.gate_reasons(grades[m].get(d)) and next(b for b in analytics['frames'][d]['baskets'] if b['key']==bucket)['status']=='point' for d in dates]
        models={k:[{} for _ in dates] for k in e.METHODS}
        # No conditional proxy decomposition is published while Package2 is blocked.
        # The complete calculation is still supported for qualified research input.
        if any(eligible):
            book=dict(p345.grouped_ledger(m,dates[-1]));previous=[]
            for i,day in enumerate(dates):
                current=[r for r in book.get(day,[]) if a.classify(r['symbol'],r['name'],m) in ('listed_stock','otc_stock')]
                if eligible[i]:
                    for k in e.METHODS:
                        stat=a.metrics(current,k);models[k][i]={'maintenance':stat['maintenance'],'loan':stat['loan'],'below130_share':stat['below130_share']}
                        if i and eligible[i-1]:models[k][i].update(e.decomposition(previous,current,price_maps[m][i],k))
                previous=current
        series[m]=e.analyze(dates,loans,close,breadths[m],models,eligible)
        if any(r['eligible'] or r['cleaning'] is not None or r['divergence'] is not None for r in series[m]):raise ValueError('This release cannot promote model signals')
    frames={}
    for i,day in enumerate(dates):
        frames[day]={'date':day,'markets':[e.frame(series[m][:i+1],m) for m in ('TWSE','TPEX')]}
        if (i+1)%150==0:print('已封存事件觀測',i+1,'交易日',flush=True)
    return {'version':e.VERSION,'created_at':src.now(),'status':'research_gated','model_ready':False,'dates':dates,'source_as_of':dates[-1],
        'quality_version':'margin-statistics-quality-1','protocol_sha256':sha(AUDIT/'implementation-protocol.json'),'indexes_sha256':sha(AUDIT/'indexes.json'),
        'index_sources':indexes['sources'],'vintage':'本次回溯封存，不是原始發布時點回測；早日截止檢查不會消除來源修訂偏誤','frames':frames}

def main():
    if not (AUDIT/'indexes.json').exists():pin_indexes()
    result=replay();target=src.folder()/'events.json.gz';raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode();temp=target.with_suffix('.tmp');temp.write_bytes(gzip.compress(raw,mtime=0));temp.replace(target)
    dump(AUDIT/'manifest.json',{'created_at':src.now(),'artifact_sha256':sha(target),'engine_sha256':sha(Path(e.__file__)),'builder_sha256':sha(Path(__file__)),'protocol_sha256':result['protocol_sha256'],'indexes_sha256':result['indexes_sha256'],'days':len(result['dates'])})
    print(json.dumps({'dates':len(result['dates']),'as_of':result['source_as_of'],'markets':[{'market':m['market'],'source_episodes':len(m['observation']['episodes']),'model_confirmed_count':m['primary']['cleaning']['confirmed_count']} for m in result['frames'][result['dates'][-1]]['markets']]},ensure_ascii=False))
if __name__=='__main__':main()
