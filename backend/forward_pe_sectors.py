"""Package 5: date-aligned custom-sector price/turnover observations.

Read only the user's taxonomy and saved official TWSE/TPEX quote snapshots.
No top-turnover subset, close*volume proxy, external fetch, or strategy update.
"""
from __future__ import annotations

from datetime import date, timedelta
import math
import re
import sqlite3
from contextlib import closing

from . import db, margin_statistics, sector_flow, forward_pe_store as S

VERSION = 'forward-pe-sectors.v1'
MARKETS = ('TWSE', 'TPEX')


def number(v, *, positive=False):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) and (x > 0 if positive else x >= 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=15)


def previous_session(calendar, target):
    """Do not mistake the last *available quote* for the previous trading day."""
    current = calendar.get(target)
    if not current or any(current.get(m) != 'ok' for m in MARKETS):
        return None
    prior = max((d for d,r in calendar.items() if d < target and all(r.get(m)=='ok' for m in MARKETS)), default=None)
    if not prior:
        return None
    d = date.fromisoformat(prior) + timedelta(days=1)
    while d.isoformat() <= target:
        if d.weekday() < 5:
            row = calendar.get(d.isoformat(), {})
            if any(row.get(m) not in ('ok','no_data') for m in MARKETS):
                return None
        d += timedelta(days=1)
    return prior


def _pool(snapshots):
    valid, duplicates = {}, set()
    complete = all(m in snapshots and snapshots[m]['status']=='ok' for m in MARKETS)
    for market, source in snapshots.items():
        if source['status'] != 'ok':
            continue
        for row in source['rows']:
            symbol = row['symbol']
            if not re.fullmatch(r'[1-9]\d{3}', symbol):
                continue
            if symbol in valid:
                duplicates.add(symbol)
            valid[symbol] = {**row,'market':market}
    for symbol in duplicates:
        valid.pop(symbol)
    amounts = [number(r.get('turnover')) for r in valid.values()]
    complete = complete and not duplicates and bool(valid) and all(v is not None for v in amounts)
    total = sum(v for v in amounts if v is not None) if complete else None
    return valid, total, sorted(duplicates)


def calculate(taxonomy, current_sources, prior_sources):
    current, total, duplicates = _pool(current_sources)
    prior, previous_total, previous_duplicates = _pool(prior_sources)
    results = []
    for group in taxonomy['groups']:
        symbols = sorted(set(s for s,names in taxonomy['memberships'].items() if group['name'] in names))
        returns, amounts, old_amounts = [], [], []
        for symbol in symbols:
            now, old = current.get(symbol, {}), prior.get(symbol, {})
            p, b = number(now.get('close'),positive=True),number(old.get('close'),positive=True)
            if p is not None and b is not None and now['market']==old['market']:
                returns.append((p/b-1)*100)
            amount, previous_amount = number(now.get('turnover')),number(old.get('turnover'))
            if amount is not None: amounts.append(amount)
            if previous_amount is not None: old_amounts.append(previous_amount)
        n = len(symbols)
        amount = sum(amounts) if amounts else None
        previous_amount = sum(old_amounts) if old_amounts else None
        share = amount/total*100 if n and len(amounts)==n and total and amount is not None else None
        previous_share = previous_amount/previous_total*100 if n and len(old_amounts)==n and previous_total and previous_amount is not None else None
        results.append({
            'name':group['name'],'parent':group['parent'],'members':n,
            'quoted':len(returns),'return_coverage_pct':len(returns)/n*100 if n else None,
            'return_pct':sum(returns)/len(returns) if returns else None,
            'advancers':sum(v>0 for v in returns),'decliners':sum(v<0 for v in returns),'unchanged':sum(v==0 for v in returns),
            'amount_twd':amount,'amount_names':len(amounts),'amount_complete':bool(n and len(amounts)==n),
            'previous_amount_twd':previous_amount,'previous_amount_names':len(old_amounts),
            'amount_share_pct':share,'previous_amount_share_pct':previous_share,
            'share_change_pp':share-previous_share if share is not None and previous_share is not None else None,
        })
    results.sort(key=lambda r:(r['amount_twd'] is None,-(r['amount_twd'] or 0),r['name']))
    return {'rows':results,'market_turnover_twd':total,'previous_market_turnover_twd':previous_total,
            'duplicate_symbols':duplicates,'previous_duplicate_symbols':previous_duplicates,
            'market_names':len(current),'previous_market_names':len(prior)}


def _quotes(c, day):
    result = {}
    if not day:
        return result
    for market in MARKETS:
        row = c.execute("SELECT s.* FROM latest l JOIN snapshots s ON s.id=l.snapshot_id WHERE l.market=? AND l.kind='quotes' AND l.day=?", (market,day)).fetchone()
        if not row:
            result[market] = {'status':'missing','rows':[],'day':day}
            continue
        source = dict(row)
        identity_ok = source['day']==day and source['market']==market and source['kind']=='quotes'
        rows = [dict(r) for r in c.execute('SELECT symbol,name,close,turnover FROM quotes WHERE snapshot_id=? ORDER BY symbol',(source['id'],))] if identity_ok else []
        status = 'snapshot_identity_mismatch' if not identity_ok else 'ok' if len(rows)==source['row_count'] and rows else 'row_count_mismatch'
        result[market] = {'status':status,
                          'rows':rows,'day':day,'snapshot_day':source['day'],'snapshot_market':source['market'],
                          'id':source['id'],'sha256':source['sha256'],
                          'url':source['url'],'retrieved_at':source['retrieved_at'],'quality':source['quality']}
    return result


def report(day=None):
    if day is not None:
        day = date.fromisoformat(day).isoformat()
    taxonomy = sector_flow.classification()
    out = {'version':VERSION,'status':'missing','date':day,'previous_date':None,'dates':[], 'rows':[],
           'warnings':[],'classification':{'snapshot':taxonomy['snapshot'],'source':taxonomy['source'],'groups':len(taxonomy['groups']),
                                          'sha256':S.digest({'groups':taxonomy['groups'],'memberships':taxonomy['memberships']})},
           'scope':'上市櫃四碼股票（含四碼存託憑證，不含ETF、權證及六碼證券）',
           'notes':[
               '使用我們的多標籤族群；同股可同屬多個族群，比重不能相加，也不正規化成100%。',
               '漲跌為同一市場兩個相鄰交易日均有收盤價者的等權平均；不是官方產業指數。未還原除權息及公司事件。',
               '成交金額直接用官方成交金額（元），不是收盤乘成交量，也不是成交值前幾名加總。',
               '比重分母是當日所有上市櫃四碼股票的去重成交金額，包含未分類股票；不是前50檔或所有族群加總。',
               '比重及較前日百分點變化需整個市場分母及該族群成員金額齊全；不足則僅列已知金額與涵蓋檔數。',
               '過去日期套用目前的自訂族群字典，並非當時成分還原；不納入策略回測或自動交易。',
               '預設至少3檔且兩日價格涵蓋80%，避免單檔或少數缺價股票主導排行；可切換查看全部。',
               '只讀現有保存，來源不足提示手動更新，不在此頁下載、重算原族群看板或更新策略。',
           ]}
    if taxonomy['error']:
        out['warnings'].append(taxonomy['error'])
        return out
    try:
        with closing(readonly(margin_statistics.folder()/'sources.sqlite3')) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN')
            dates = [r[0] for r in c.execute("SELECT DISTINCT day FROM latest WHERE kind='quotes' AND day<=? ORDER BY day DESC LIMIT 60",(S.instant(S.now()).date().isoformat(),))]
            target = day or (dates[0] if dates else None)
            out.update(date=target,dates=dates)
            if not target:
                out['warnings'].append('尚無官方收盤成交資料，請手動補齊市場資料中心。')
                return out
            calendar = {}
            try:
                with closing(readonly(db.DATA/'monitor.sqlite3')) as m:
                    start = (date.fromisoformat(target)-timedelta(days=21)).isoformat()
                    calendar = {d:{'TWSE':a,'TPEX':b} for d,a,b in m.execute('SELECT date,twse_status,tpex_status FROM market_days WHERE date BETWEEN ? AND ?',(start,target))}
            except sqlite3.Error:
                out['warnings'].append('已驗證交易日曆無法讀取，未猜前一交易日。')
            prior = previous_session(calendar,target)
            current_sources, prior_sources = _quotes(c,target),_quotes(c,prior)
            c.rollback()
    except (sqlite3.Error,OSError) as e:
        out['warnings'].append('官方成交資料尚未齊全，請手動更新；未改用其他日期或成交榜代替。')
        return out
    out.update(calculate(taxonomy,current_sources,prior_sources))
    out['previous_date'] = prior
    out['sources'] = {'current':{k:{x:v for x,v in r.items() if x!='rows'} for k,r in current_sources.items()},
                      'previous':{k:{x:v for x,v in r.items() if x!='rows'} for k,r in prior_sources.items()}}
    if not prior:
        out['warnings'].append('前一交易日未核對，漲跌幅及前日比重變化暫不顯示。')
    for label,key,dup_key,sources in [('當日','market_turnover_twd','duplicate_symbols',current_sources),('前日','previous_market_turnover_twd','previous_duplicate_symbols',prior_sources)]:
        if all(sources.get(m,{}).get('status')=='ok' for m in MARKETS) and out[key] is None and not out[dup_key]:
            out['warnings'].append(label+'四碼股票成交金額含缺值或無法核對總額；比重不計算，請手動核對市場資料中心。')
    for label,sources in [('當日',current_sources),('前日',prior_sources)]:
        missing = [m for m in MARKETS if sources.get(m,{}).get('status')!='ok']
        if missing:
            out['warnings'].append(label+'官方來源未齊：'+'、'.join(missing)+'；請手動補齊市場資料中心，不自動重試。')
    if out['duplicate_symbols'] or out['previous_duplicate_symbols']:
        out['warnings'].append('部分代碼跨市場重複，已排除，不用其中一筆覆蓋另一筆。')
    out['status'] = 'ok' if not out['warnings'] and out['market_turnover_twd'] is not None else 'partial'
    return out
