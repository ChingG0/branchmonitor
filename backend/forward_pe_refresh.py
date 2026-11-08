"""Freeze saved observations explicitly, or once within a home update job.

Import licensed EPS using forward_pe_store first. This step includes all visible
saved EPS versions in a new snapshot and reprices on the newest saved quote day.
It does not download sources, invent source times, or publish trading strategies.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from . import forward_pe_store as S, forward_pe_sources as P, forward_pe_valuation as V, forward_pe_dashboard as D, margin_statistics


def newest_quote_day():
    path = margin_statistics.folder()/'sources.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as c:
        row = c.execute("SELECT MAX(day) FROM latest WHERE market='TWSE' AND kind='quotes' AND day<=?", (S.instant(S.now()).date().isoformat(),)).fetchone()
    if not row or not row[0]:
        raise ValueError('尚無已保存的上市收盤，請先手動補齊市場資料中心。')
    return row[0]


def prepare():
    """Validate saved sources before writes. No download and no fallback date."""
    day = newest_quote_day()
    cutoff = S.instant(S.now())
    with S.connect() as c:
        candidates = [dict(r) for r in c.execute("SELECT u.* FROM universes u JOIN sources s ON s.id=u.source_id WHERE u.kind='official_month_end' AND s.status='ok'")
                      if r['effective_date'] <= day and S.instant(r['known_at']) <= cutoff]
        if not candidates:
            raise ValueError('沒有該行情日可用的官方月底前50名單，請先匯入權重來源。')
        universe = max(candidates, key=lambda r:(r['effective_date'],S.instant(r['known_at']),r['id']))
        members = sorted(json.loads(universe['payload']),key=lambda r:r['rank'])[:50]
        symbols = {r['symbol'] for r in members}
        if len(symbols)!=50:
            raise ValueError('前50名單不足，停止保存。')
        # Include imported files and prior versions, without choosing or mixing
        # provider/basis/period here. The valuation engine separates these.
        estimates = [r['id'] for r in c.execute("SELECT e.id,e.symbol,e.known_at,e.published_at FROM estimates e JOIN sources s ON s.id=e.source_id WHERE s.status='ok' ORDER BY e.id")
                     if r['symbol'] in symbols and S.instant(r['known_at'])<=cutoff and S.instant(r['published_at'])<=cutoff]
    quote_day, rows, refs = P.local_quotes(sorted(symbols),day=day)
    if quote_day != day or len(rows)!=50 or {r['symbol'] for r in rows}!=symbols or any(r['market_date']!=day for r in rows):
        raise ValueError('最新行情日的50檔股價與股數未齊，不退用較早日期。')
    for kind in ('quotes','shares'):
        r=refs[kind]
        if r['market']!='TWSE' or r['kind']!=kind or r['day']!=day or r['row_count']<=0:
            raise ValueError('最新官方來源身分或狀態無法核對。')
        raw=(margin_statistics.folder()/r['raw_path']).read_bytes()
        if hashlib.sha256(gzip.decompress(raw)).hexdigest()!=r['sha256']:
            raise ValueError('官方原始檔SHA無法核對，停止保存。')
    rows=sorted(rows,key=lambda r:r['symbol'])
    fingerprint=S.digest({'version':'forward-pe-update.v1','evaluation_date':cutoff.date().isoformat(),
                          'universe_id':universe['id'],'market_date':day,'quotes':rows,
                          'parent_sha256':{k:r['sha256'] for k,r in refs.items()},'estimate_ids':estimates,
                          'policy_sha256':S.digest(V.policy()),'engine_sha256':hashlib.sha256(Path(V.__file__).read_bytes()).hexdigest()})
    return {'day':day,'universe_id':universe['id'],'weight_date':universe['effective_date'],'rows':rows,'refs':refs,'estimates':estimates,'fingerprint':fingerprint}


def save_prepared(prepared, *, trigger):
    day,rows,refs,estimates=(prepared[k] for k in ('day','rows','refs','estimates'))
    payload={'kind':'read_only_official_normalized_extract','market_date':day,'quotes':rows,'parent_snapshots':refs}
    source_id=S.source('prices_and_issued_shares','twse_saved_official','local://margin_statistics/sources.sqlite3',S.canonical(payload).encode(),
                       meta={'market_date':day,'parent_raw_sha256':{k:r['sha256'] for k,r in refs.items()},'parent_retrieved_at':{k:r['retrieved_at'] for k,r in refs.items()}})
    quote_ids=S.quote_batch(source_id,rows)
    snapshot_id=S.snapshot(prepared['universe_id'],quote_ids,estimates,notes=[
        ('首頁更新保存' if trigger=='home' else '對話人工保存')+'；只使用已入庫的行情、股數與預估版本，沒有下載新資料。',
        '所有來源以實際入庫截點為準；匯入預估仍須通過原本資格，不回填歷史。',
        '不同來源／期間／口徑分組；原始預估與舊快照保留。',
        '保存不等於Drive讀回驗證成功；不發布V3／V6／V10／V11。'])
    saved=V.save(snapshot_id=snapshot_id,update_context={'trigger':trigger,'input_fingerprint':prepared['fingerprint'],'network_downloaded':False})
    return {**saved,'snapshot_id':snapshot_id,'price_date':day,'estimate_versions':len(estimates),'network_downloaded':False}


def freeze():
    """Conversation command always creates a new immutable actual-time version."""
    return save_prepared(prepare(),trigger='conversation')


def home_status(*, target=None):
    """Read saved qualifications only; never prepares, saves or fetches sources."""
    name='預估本益比河流圖'
    try:
        report=V.read()
        if not report.get('id'):
            return {'name':name,'status':'partial','message':'尚無估值保存；請先補齊官方行情、權重及預估EPS，再手動更新。'}
        target=target or D.latest_market_day()
        parts=[f"行情 {report['price_date']} · {report['quote_names']}/50檔"]
        ready=bool(target and report['price_date']==target and report['quote_names']==50)
        year=str(S.instant(S.now()).year)
        for y in (year,str(int(year)+1)):
            period=next((p for p in report['periods'] if p['period_kind']=='calendar_year' and p['period']==y),{})
            groups=period.get('groups',[])
            qualified=max((g['qualified']['names'] for g in groups),default=0)
            parts.append(f"{y} EPS文件 {period.get('document_names',0)}/50、核對 {qualified}/50")
            ready=ready and any(g['qualified']['exact_top50_pe'] is not None for g in groups)
        if not D.weights_current(report['weight_date'],report['price_date']):
            ready=False;parts.append('月底權重已過期，請在對話補齊')
        if not target or report['price_date']!=target:
            parts.append('最新行情尚未核對，請先補齊市場資料中心')
        if not ready:
            parts.append('來源未齊，請在對話補齊EPS／權重；首頁不下載共識，也不自動重試')
        else:
            parts.append('當期估值已保存；歷史不足時頁面仍不給位階')
        return {'name':name,'status':'complete' if ready else 'partial','message':'；'.join(parts),
                'detail':{'report_id':report['id'],'saved_at':report['computed_at'],'price_date':report['price_date']}}
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):
        return {'name':name,'status':'partial','message':'估值來源尚無法核對，請手動補齊；已保存資料保留，不自動重試。'}


def update_home():
    """One bounded job step, after official quotes/shares. GET never calls it."""
    try:
        prepared=prepare()
        target=D.latest_market_day()
        if not target or prepared['day']!=target:
            raise ValueError('最新已核對交易日與行情／股數尚未對齊，請先補齊市場資料中心')
        if not D.weights_current(prepared['weight_date'],prepared['day']):
            raise ValueError('最新月底權重尚未匯入，請在對話補齊；未套用過期名單')
        old=V.read()
        unchanged=old.get('update_context',{}).get('input_fingerprint')==prepared['fingerprint']
        if unchanged:
            # Verify existing input evidence as well as the saved report hash.
            # This loads exact IDs, without calculating a new valuation.
            V._load(old['snapshot_id'],None)
            action='unchanged'
        else:
            save_prepared(prepared,trigger='home');action='saved'
        result=home_status(target=target)
        result['detail']={**result.get('detail',{}),'action':action,'checked_at':S.now(),'network_downloaded':False}
        return result
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as error:
        result=home_status()
        result.update(status='partial',message='本次來源未齊，保留上次保存：'+str(error)[:150]+'；請手動補齊，不自動重試。')
        result['detail']={**result.get('detail',{}),'action':'retained','checked_at':S.now(),'network_downloaded':False}
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['freeze']);p.parse_args()
    print(json.dumps(freeze(),ensure_ascii=False))
