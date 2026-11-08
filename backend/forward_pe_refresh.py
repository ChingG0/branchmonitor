"""Explicit manual freeze of saved observations; never called by GET or schedules.

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
from contextlib import closing
from . import forward_pe_store as S, forward_pe_sources as P, forward_pe_valuation as V, margin_statistics


def newest_quote_day():
    path = margin_statistics.folder()/'sources.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as c:
        row = c.execute("SELECT MAX(day) FROM latest WHERE market='TWSE' AND kind='quotes' AND day<=?", (S.instant(S.now()).date().isoformat(),)).fetchone()
    if not row or not row[0]:
        raise ValueError('尚無已保存的上市收盤，請先手動補齊市場資料中心。')
    return row[0]


def freeze():
    """Append actual-time snapshot; failed validation never replaces latest report."""
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
    payload={'kind':'read_only_official_normalized_extract','market_date':day,'quotes':rows,'parent_snapshots':refs}
    source_id=S.source('prices_and_issued_shares','twse_saved_official','local://margin_statistics/sources.sqlite3',S.canonical(payload).encode(),
                       meta={'market_date':day,'parent_raw_sha256':{k:r['sha256'] for k,r in refs.items()},'parent_retrieved_at':{k:r['retrieved_at'] for k,r in refs.items()}})
    quote_ids=S.quote_batch(source_id,rows)
    snapshot_id=S.snapshot(universe['id'],quote_ids,estimates,notes=[
        '包6人工保存；只使用已入庫的行情、股數與預估版本，沒有下載新資料。',
        '所有來源以實際入庫截點為準；匯入預估仍須通過原本資格，不回填歷史。',
        '不同來源／期間／口徑分組；原始預估與舊快照保留。',
        '保存不等於Drive讀回驗證成功；不發布V3／V6／V10／V11。'])
    saved=V.save(snapshot_id=snapshot_id)
    return {**saved,'snapshot_id':snapshot_id,'price_date':day,'estimate_versions':len(estimates),'network_downloaded':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['freeze']);p.parse_args()
    print(json.dumps(freeze(),ensure_ascii=False))
