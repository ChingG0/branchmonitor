"""Explicit offline build from pinned source versions, never called by GET."""
from pathlib import Path
from contextlib import closing
from itertools import groupby
from collections import deque
import gzip,hashlib,json,sqlite3,os
from . import db,margin_statistics as src,margin_analytics as a

AUDIT=db.ROOT/'research/margin_statistics_p345_20261105'
P2=db.ROOT/'research/margin_statistics_p2_20261105'
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def ro(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
def dump(path,v):path.write_text(json.dumps(v,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
def pin_reference():
    path=AUDIT/'reference.sqlite3'
    if path.exists():raise RuntimeError('Reference already pinned; use replay, do not overwrite')
    with closing(ro(P2/'inputs.sqlite3')) as frozen,closing(ro(src.folder()/'sources.sqlite3')) as source,closing(ro(db.DATA/'monitor.sqlite3')) as monitor:
        ids={(r['market'],r['kind'],r['day']):r['id'] for r in frozen.execute('SELECT * FROM source_manifest')}
        days=[r[0] for r in frozen.execute('SELECT day FROM calendar ORDER BY day')]
        temp=path.with_suffix('.tmp');c=sqlite3.connect(temp)
        try:
            c.executescript('CREATE TABLE quotes(market TEXT,day TEXT,symbol TEXT,name TEXT,close REAL,shares REAL,quote_source TEXT,share_source TEXT,PRIMARY KEY(day,market,symbol)); CREATE TABLE rs(day TEXT PRIMARY KEY,payload BLOB,saved_at TEXT);')
            count=0
            for market in ('TWSE','TPEX'):
                for day in days:
                    sid=ids.get((market,'quotes',day));ssid=ids.get((market,'shares',day))
                    if not sid:continue
                    shares={r['symbol']:r['shares'] for r in source.execute('SELECT symbol,shares FROM shares WHERE snapshot_id=?',(ssid,))} if ssid else {}
                    quotes=list(source.execute('SELECT symbol,name,close,shares FROM quotes WHERE snapshot_id=?',(sid,)))
                    if not quotes:raise ValueError('Pinned quote snapshot missing')
                    c.executemany('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?)',[(market,day,r['symbol'],r['name'],r['close'],shares.get(r['symbol']) if market=='TWSE' else r['shares'],sid,ssid) for r in quotes]);count+=len(quotes)
            rs_count=0
            for r in monitor.execute('SELECT date,payload,fetched_at FROM rs_days WHERE date BETWEEN ? AND ? ORDER BY date',(days[0],days[-1])):
                if r['date'] not in days:continue
                v=json.loads(r['payload'])
                if v.get('as_of')!=r['date']:raise ValueError('RS payload date mismatch')
                # Exact saved bytes, not rankings recreated with today's universe.
                c.execute('INSERT INTO rs VALUES(?,?,?)',(r['date'],gzip.compress(r['payload'].encode(),mtime=0),r['fetched_at']));rs_count+=1
            c.commit()
        finally:c.close()
        temp.replace(path)
    dump(AUDIT/'reference-manifest.json',{'created_at':src.now(),'sha256':sha(path),'quote_rows':count,'rs_dates':rs_count,'source_ids':'exact Package2 source_manifest ids, no latest-version substitution','vintage':'retrospective snapshot; saved RS fetched_at retained, not assumed original publication'})

def grouped_ledger(market,cutoff):
    with gzip.open(P2/(market.lower()+'-security-diagnostics.jsonl.gz'),'rt') as f:
        rows=(json.loads(line) for line in f)
        for day,group in groupby(rows,key=lambda r:r['date']):
            if day>cutoff:break
            yield day,[dict(r,market=market) for r in group]

def event_map(c):
    periods={}
    for r in c.execute('SELECT * FROM events ORDER BY retrieved_at'):periods[(r['market'],r['period'])]=json.loads(r['payload'])
    result={}
    for v in periods.values():
        for e in v['data']['events']:result.setdefault(e['date'],[]).append(e)
    return result

def replay(cutoff=None):
    manifest=json.loads((P2/'manifest.json').read_text());reference=json.loads((AUDIT/'reference-manifest.json').read_text())
    assert sha(P2/'inputs.sqlite3')==manifest['inputs_sha256']
    assert sha(P2/'frozen-engine.py')==manifest['engine_sha256']
    assert sha(AUDIT/'reference.sqlite3')==reference['sha256']
    # Ledgers and grades are pinned too, not just their generating input.
    protected=json.loads((AUDIT/'protected-before.json').read_text())
    for market in ('twse','tpex'):
        for name in (market+'-daily.json.gz',market+'-security-diagnostics.jsonl.gz'):
            path=P2/name;assert sha(path)==protected[str(path.relative_to(db.ROOT))]
    with closing(ro(P2/'inputs.sqlite3')) as c,closing(ro(AUDIT/'reference.sqlite3')) as refs:
        dates=[r[0] for r in c.execute('SELECT day FROM calendar ORDER BY day') if cutoff is None or r[0]<=cutoff];end=dates[-1]
        grades={}
        for market in ('TWSE','TPEX'):
            with gzip.open(P2/(market.lower()+'-daily.json.gz'),'rt') as f:grades[market]={r['date']:r for r in json.load(f) if r['date']<=end}
        events=event_map(c);iterators={m:iter(grouped_ledger(m,end)) for m in grades};pending={m:next(it,None) for m,it in iterators.items()}
        history=deque(maxlen=21);frames={}
        for i,day in enumerate(dates):
            allrows={}
            for m in grades:
                if pending[m] and pending[m][0]==day:
                    allrows[m]=pending[m][1];pending[m]=next(iterators[m],None)
                else:allrows[m]=[]
                if bool(allrows[m]) == bool(grades[m].get(day,{}).get('source_missing')):raise ValueError('Ledger/source grade calendar mismatch')
            universe=[dict(r) for r in refs.execute('SELECT market,symbol,name,close,shares FROM quotes WHERE day=? ORDER BY market,symbol',(day,))]
            # Add credit-only names with missing cap: never drop these from denominator coverage.
            seen={(r['market'],r['symbol']) for r in universe}
            for m,rows in allrows.items():
                universe += [dict(market=m,symbol=r['symbol'],name=r['name'],close=r['close'],shares=None) for r in rows if (m,r['symbol']) not in seen]
            # history before current append has at most21; exact date uses index difference.
            if len(history)>=20:base20=history[-20]
            else:base20=None
            prior5=history[-5] if len(history)>=5 else None
            g={m:grades[m].get(day) for m in grades}
            baskets=[];allflat=[r for rows in allrows.values() for r in rows]
            for key in a.LABELS:
                required=('TWSE',) if key=='listed_stock' else ('TPEX',) if key=='otc_stock' else ('TWSE','TPEX')
                rows=[r for r in allflat if a.classify(r['symbol'],r['name'],r['market'])==key]
                quotes=[r for r in universe if a.classify(r['symbol'],r['name'],r['market'])==key]
                old=[r for m in required for r in (base20['rows'][m] if base20 else []) if a.classify(r['symbol'],r['name'],m)==key] if base20 else None
                baskets.append(a.public_basket(key,rows,quotes,{m:g[m] for m in required},old,{m:grades[m].get(base20['date']) for m in required} if base20 else None,events.get(day,())))
            r=refs.execute('SELECT payload,saved_at FROM rs WHERE day=?',(day,)).fetchone();snapshot=json.loads(gzip.decompress(r['payload'])) if r else None
            subsets={m:a.public_subsets(m,day,allrows[m],base20['rows'][m] if base20 else None,prior5['rows'][m] if prior5 else None,g[m],grades[m].get(base20['date']) if base20 else None,grades[m].get(prior5['date']) if prior5 else None,snapshot) for m in grades}
            frames[day]={'date':day,'baskets':baskets,'subsets':subsets,'official':[{'market':m,'loan_amount':g[m].get('official') if g[m] else None,'source_complete':bool(g[m] and not g[m].get('source_missing'))} for m in grades],
                'baseline20':base20['date'] if base20 else None,'baseline5':prior5['date'] if prior5 else None,'rs_saved_at':r['saved_at'] if r else None,'rs_anchors':snapshot.get('anchors') if snapshot else None}
            # Avoid computing or exposing any package3–5 gated numbers in this frozen run.
            assert all(b['status']=='blocked' and all(v is None for v in b['metrics'].values()) for b in baskets)
            history.append({'date':day,'rows':allrows})
            if (i+1)%150==0:print('已完成',i+1,'交易日',flush=True)
    return {'version':a.VERSION,'created_at':src.now(),'status':'gated','model_ready':False,'dates':dates,'source_as_of':end,'input_sha256':manifest['inputs_sha256'],'reference_sha256':reference['sha256'],
        'classification_version':'margin-baskets-3-5-1','classification':'依當日名稱與固定規則初分；非歷史商品成員認證，反向與其他另列；98/99字頭普通股不當作DR','vintage':'本次回溯來源與當日既有RS保存版本；不是當年發布時點回測','frames':frames}

def main():
    if not (AUDIT/'reference.sqlite3').exists():pin_reference()
    result=replay();target=src.folder()/'analytics.json.gz';raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode();temp=target.with_suffix('.tmp');temp.write_bytes(gzip.compress(raw,mtime=0));temp.replace(target)
    dump(AUDIT/'manifest.json',{'created_at':src.now(),'artifact_sha256':sha(target),'engine_sha256':sha(Path(a.__file__)),'source_classifier_sha256':sha(Path(src.__file__)),'builder_sha256':sha(Path(__file__)),'protocol_sha256':sha(AUDIT/'implementation-protocol.json'),'dates':len(result['dates']),'input_sha256':result['input_sha256'],'reference_sha256':result['reference_sha256']})
    print(json.dumps({'dates':len(result['dates']),'as_of':result['source_as_of'],'model_ready':False,'artifact_bytes':target.stat().st_size},ensure_ascii=False))
if __name__=='__main__':main()
