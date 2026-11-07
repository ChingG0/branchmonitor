"""Saved V3 market research. Explicit CLI generation only; HTTP/report are read-only."""
from __future__ import annotations
import argparse
from datetime import datetime
import fcntl
import json
import math
from pathlib import Path
import sqlite3
from . import db

VERSION = 'v3-market-study-v1'


def study_path(account=None):
    return Path(account).with_name('v3-market-study.json') if account else db.DATA / 'v3-market-study.json'


def read_study(path=None):
    path = Path(path) if path else study_path()
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get('version') != VERSION:
        raise ValueError('V3 市場研究版本不符；保留原檔')
    return value


def _levels(closes):
    """Use close-only ranges: some historical index rows have no OHLC highs/lows."""
    current = closes[0]
    n = len(closes)
    refs = [('近5日收盤低點', min(closes[:5])), ('近5日收盤高點', max(closes[:5])),
            ('近10日收盤低點', min(closes[:10])), ('近10日收盤高點', max(closes[:10])),
            ('近20日收盤低點', min(closes[:20])), ('近20日收盤高點', max(closes[:20])),
            ('20日均線', sum(closes[:20]) / 20)]
    if n >= 60:
        refs += [('近60日收盤低點', min(closes[:60])), ('近60日收盤高點', max(closes[:60])),
                 ('60日均線', sum(closes[:60]) / 60)]

    def pick(below):
        candidates = sorted(((value, label) for label, value in refs
                             if 0 < abs(value / current - 1) <= .10 and
                             (value < current if below else value > current)),
                            key=lambda x: abs(x[0] - current))
        selected = []
        for value, label in candidates:
            if all(abs(value - row['value']) / current > .002 for row in selected):
                selected.append({'value': round(value, 2), 'basis': label})
            if len(selected) == 2:
                break
        return selected

    return {'support': pick(True), 'resistance': pick(False)}


def index_research(source, day, symbol):
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as con:
        rows = con.execute('SELECT date,close FROM prices WHERE symbol=? AND date<=? AND close IS NOT NULL '
                           'ORDER BY date DESC LIMIT 61', (symbol, day)).fetchall()
    if len(rows) < 20 or rows[0][0] != day:
        raise ValueError(f'{symbol} 缺少研究日或20筆收盤，不能產生位階')
    closes = [float(row[1]) for row in rows]
    if any(not math.isfinite(x) or x <= 0 for x in closes):
        raise ValueError(f'{symbol} 收盤價無效')
    threshold = math.ceil((sum(closes[:19]) / 19) * 100 - 1e-9) / 100
    levels = _levels(closes)
    return {'close': round(closes[0], 2), 'sma20': round(sum(closes[:20]) / 20, 2),
            'sma60': round(sum(closes[:60]) / 60, 2) if len(closes) >= 60 else None,
            'change_1d_pct': round((closes[0] / closes[1] - 1) * 100, 2) if len(closes) > 1 else None,
            'change_5d_pct': round((closes[0] / closes[5] - 1) * 100, 2) if len(closes) > 5 else None,
            'next_close_to_sma20': threshold,
            **levels}


def build_snapshot(decision, source, context, old=None, now=None):
    """Build a complete study in memory so a manual V3 review can save it atomically."""
    from . import v3
    source = Path(source)
    ctx = context
    now = (now or datetime.now(v3.TZ)).astimezone(v3.TZ)
    day = v3._date(decision.get('as_of'))
    if day != ctx['as_of'] or day > now.date().isoformat():
        raise ValueError('只能研究最新可用的收盤日，不能事後回填')
    if not isinstance(decision.get('analysis'), str) or len(decision['analysis'].strip()) < 20:
        raise ValueError('市場研究需寫明方向及依據')
    if not isinstance(decision.get('buy_condition'), str) or len(decision['buy_condition'].strip()) < 10:
        raise ValueError('需寫明下次買入的其他必要條件')
    if not isinstance(decision.get('bond_reason'), str) or len(decision['bond_reason'].strip()) < 10:
        raise ValueError('需寫明債券配置理由')
    stock = v3._number(decision.get('stock_target_pct'), '股票建議比例', 0, 100)
    bond = v3._number(decision.get('bond_target_pct'), '債券建議比例', 0, 100)
    if stock + bond > 100:
        raise ValueError('股票與債券建議比例合計不可超過100%')
    sources = decision.get('sources', [])
    if not isinstance(sources, list) or any(not isinstance(row, dict) or
        not isinstance(row.get('title'), str) or not isinstance(row.get('url'), str)
        for row in sources):
        raise ValueError('來源格式不正確')
    expected = old['revision'] if old else 0
    if decision.get('expected_revision') != expected:
        raise ValueError('V3 市場研究版號已改變，請重新讀取後再產生')
    if old and day < old['as_of']:
        raise ValueError('不能用較舊資料覆蓋市場研究')
    indices = {symbol: index_research(source, day, symbol) for symbol in ('TAIEX', 'TPEX')}
    return {'version': VERSION, 'revision': expected + 1, 'as_of': day,
            'recorded_at': now.isoformat(timespec='seconds'), 'ready': bool(ctx['ready']),
            'new_buy_allowed': bool(ctx['ready'] and not ctx['blocked']),
            'breadth_pct': ctx.get('breadth_pct'), 'indices': indices,
            'analysis': decision['analysis'].strip(),
            'buy_condition': decision['buy_condition'].strip(),
            'allocation': {'stock_pct': stock, 'bond_pct': bond,
                           'cash_pct': round(100 - stock - bond, 2)},
            'bond_reason': decision['bond_reason'].strip(), 'sources': sources,
            'method': '僅用研究日以前的指數收盤價，取近5/10/20/60日收盤區間與20/60MA作觀察位階；只列距收盤10%內的最近兩級。下一交易日重站20MA門檻＝最近19筆收盤均值，向上取至0.01點。這是試算，不是盤中支撐、保證買點或股票買單。'}


def generate(decision, source=None, path=None, context=None):
    """Save a dated research snapshot without touching the V3 paper account."""
    from . import v3
    source = Path(source or db.DATA / 'monitor.sqlite3')
    path = Path(path) if path else study_path()
    ctx = context if context is not None else v3.market_context(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_study(path)
        value = build_snapshot(decision, source, ctx, old)
        v3._atomic(path, value)
    return value


def main():
    parser = argparse.ArgumentParser(description='V3 市場研究僅依明確要求手動產生')
    parser.add_argument('command', choices=['generate', 'report'])
    parser.add_argument('--source')
    parser.add_argument('--path')
    parser.add_argument('--decision')
    args = parser.parse_args()
    if args.command == 'report':
        print(json.dumps(read_study(args.path), ensure_ascii=False, allow_nan=False))
    else:
        if not args.decision:
            parser.error('generate 需提供 --decision 決策檔')
        result = generate(json.loads(Path(args.decision).read_text()), args.source, args.path)
        print(json.dumps({'revision': result['revision'], 'as_of': result['as_of'],
                          'allocation': result['allocation']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
