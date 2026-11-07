"""族群字典與廣度指標（原型）。

字典存在 prototype/sectors.sqlite3，與正式的 data/monitor.sqlite3 完全分離。
行情一律以唯讀連線讀取正式庫，絕不寫入。指標全部在本機重算，不連外部來源。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = Path(__file__).resolve().parent / 'sectors.sqlite3'
SEED = Path(__file__).resolve().parent / 'sector-seed.json'
PRICES = ROOT / 'data' / 'monitor.sqlite3'
WINDOW_START = '2026-01-02'
# 共動只在成員數落在此區間時計算：2 檔算相關沒有意義，過大的族群本來就不會齊漲齊跌。
CORR_MIN, CORR_MAX = 3, 30


@contextmanager
def store():
    conn = sqlite3.connect(STORE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def quotes():
    """正式資料庫唯讀，另把原型自有行情併進來。

    mode=ro 由 SQLite 保證不可寫。正式系統尚未同步的交易日，改用原型自己抓的收盤價；
    只要正式系統補上該日，合併檢視就一律以正式資料為準，原型那份自動退居備援。
    暫存檢視（TEMP VIEW）會遮蔽同名的主資料表，因此既有查詢不需要改寫。
    """
    if not PRICES.exists():
        raise SystemExit(f'找不到行情資料庫：{PRICES}')
    conn = sqlite3.connect(f'file:{PRICES}?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if STORE.exists():
            conn.execute("ATTACH DATABASE ? AS ext", (f'file:{STORE}?mode=ro',))
            tables = {r['name'] for r in conn.execute(
                "SELECT name FROM ext.sqlite_master WHERE type='table'")}
            if {'own_prices', 'own_market_days'} <= tables:
                conn.executescript("""CREATE TEMP VIEW market_days AS\n  SELECT date,twse_status,tpex_status,message FROM main.market_days\n  UNION ALL\n  SELECT date,twse_status,tpex_status,message FROM ext.own_market_days\n   WHERE date NOT IN (SELECT date FROM main.market_days);\nCREATE TEMP VIEW prices AS\n  SELECT date,symbol,name,close,open,high,low,volume,market FROM main.prices\n  UNION ALL\n  SELECT date,symbol,name,close,open,high,low,volume,market FROM ext.own_prices\n   WHERE date NOT IN (SELECT date FROM main.market_days);""")
        yield conn
    finally:
        conn.close()


def initialize():
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS sector_groups (
          name TEXT PRIMARY KEY, parent TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sector_members (
          name TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(name,symbol));
        CREATE INDEX IF NOT EXISTS idx_members_symbol ON sector_members(symbol);
        CREATE TABLE IF NOT EXISTS chain_links (
          partner TEXT NOT NULL, partner_code TEXT NOT NULL, direction TEXT NOT NULL,
          category TEXT NOT NULL, symbol TEXT NOT NULL,
          PRIMARY KEY(partner,direction,category,symbol));
        CREATE INDEX IF NOT EXISTS idx_chain_symbol ON chain_links(symbol);
        CREATE TABLE IF NOT EXISTS sector_days (
          date TEXT NOT NULL, name TEXT NOT NULL,
          members INTEGER NOT NULL, quoted INTEGER NOT NULL,
          comovement REAL, corr_members INTEGER NOT NULL DEFAULT 0,
          r1 REAL, r5 REAL, r20 REAL, breadth REAL,
          advancers INTEGER NOT NULL DEFAULT 0, decliners INTEGER NOT NULL DEFAULT 0,
          computed_at TEXT NOT NULL, PRIMARY KEY(date,name));
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')


def seed():
    """字典是靜態資料，重匯時整批替換；不影響已算好的 sector_days。"""
    data = json.loads(SEED.read_text(encoding='utf-8'))
    initialize()
    with store() as conn:
        conn.execute('DELETE FROM sector_groups')
        conn.execute('DELETE FROM sector_members')
        conn.execute('DELETE FROM chain_links')
        conn.executemany('INSERT INTO sector_groups VALUES (?,?)',
                         [(g['name'], g['parent']) for g in data['groups']])
        conn.executemany('INSERT INTO sector_members VALUES (?,?)',
                         [(g['name'], s) for g in data['groups'] for s in g['members']])
        conn.executemany('INSERT INTO chain_links VALUES (?,?,?,?,?)',
                         [(c['partner'], c['code'], l['direction'], l['category'], s)
                          for c in data['chains'] for l in c['links'] for s in l['symbols']])
        conn.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [
            ('dictionary_source', data['source']),
            ('dictionary_snapshot', data['snapshot_date']),
            ('seeded_at', datetime.now().isoformat(timespec='seconds'))])
        counts = [conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in ['sector_groups', 'sector_members', 'chain_links']]
    return dict(zip(['groups', 'members', 'chain_links'], counts))


def calendar(conn, end):
    return [r['date'] for r in conn.execute(
        "SELECT date FROM market_days WHERE twse_status='ok' AND date<=? ORDER BY date", (end,))]


def latest_date():
    with quotes() as conn:
        days = calendar(conn, '9999')
    if not days:
        raise SystemExit('行情資料庫沒有已驗證的交易日')
    return days[-1]


def closes(conn, symbols, days):
    """只取需要的日期與股票，避免把整張 prices 表載進記憶體。"""
    marks = ','.join('?' * len(days))
    series = {}
    rows = conn.execute(
        f'SELECT date,symbol,close FROM prices WHERE date IN ({marks})', days)
    wanted = set(symbols)
    for row in rows:
        if row['symbol'] in wanted:
            series.setdefault(row['symbol'], {})[row['date']] = row['close']
    return series


def average_correlation(series, days, min_overlap=20):
    """成員日報酬的平均兩兩相關係數。

    逐對取雙方都有報酬的交易日，而不是要求整段視窗完整——停牌或資料缺口的成員
    若被整檔剔除，剩下的少數成員會把相關係數大幅高估。
    """
    returns = {}
    for symbol, prices in series.items():
        daily = {b: prices[b] / prices[a] - 1
                 for a, b in zip(days, days[1:]) if a in prices and b in prices}
        if len(daily) >= min_overlap:
            returns[symbol] = daily
    codes = sorted(returns)
    if len(codes) < 2:
        return None, len(codes)
    total = pairs = 0.0
    for i, left in enumerate(codes):
        for right in codes[i + 1:]:
            shared = returns[left].keys() & returns[right].keys()
            if len(shared) < min_overlap:
                continue
            x = [returns[left][d] for d in shared]
            y = [returns[right][d] for d in shared]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            dx = [v - mx for v in x]
            dy = [v - my for v in y]
            nx = math.sqrt(sum(v * v for v in dx))
            ny = math.sqrt(sum(v * v for v in dy))
            if nx > 1e-12 and ny > 1e-12:
                total += sum(a * b for a, b in zip(dx, dy)) / (nx * ny)
                pairs += 1
    return (total / pairs, len(codes)) if pairs else (None, len(codes))


def horizon_return(prices, days, span):
    """以交易日回推 span 日的收盤價格報酬；兩端缺價則不推估。

    未還原除權息與公司事件，與 branch-monitor 其他統計同口徑。期間停牌的個股
    兩端相除會把缺口期間一併計入，屬已知限制（見 README 的驗證結果）。
    """
    if len(days) <= span:
        return None
    start, end = prices.get(days[-1 - span]), prices.get(days[-1])
    if not start or not end:
        return None
    return (end / start - 1) * 100


def compute(date=None, window_start=WINDOW_START, verbose=True):
    date = date or latest_date()
    initialize()
    with store() as conn:
        groups = {r['name']: r['parent'] for r in conn.execute('SELECT * FROM sector_groups')}
        members = {}
        for row in conn.execute('SELECT * FROM sector_members'):
            members.setdefault(row['name'], []).append(row['symbol'])
    if not groups:
        raise SystemExit('字典尚未匯入，請先執行 seed')

    with quotes() as conn:
        days = [d for d in calendar(conn, date) if d >= window_start]
        if not days or days[-1] != date:
            raise SystemExit(f'{date} 不是本機已驗證的交易日')
        universe = {s for group in members.values() for s in group}
        series = closes(conn, universe, days)

    stamp = datetime.now().isoformat(timespec='seconds')
    rows = []
    for name, parent in groups.items():
        symbols = members.get(name, [])
        member_series = {s: series[s] for s in symbols if s in series}
        daily = {s: horizon_return(p, days, 1) for s, p in member_series.items()}
        scored = [v for v in daily.values() if v is not None]
        advancers = sum(v > 0 for v in scored)
        decliners = sum(v < 0 for v in scored)

        def mean_return(span):
            values = [horizon_return(p, days, span) for p in member_series.values()]
            values = [v for v in values if v is not None]
            return sum(values) / len(values) if values else None

        comovement, corr_members = (None, 0)
        if CORR_MIN <= len(symbols) <= CORR_MAX:
            comovement, corr_members = average_correlation(member_series, days)
        rows.append((date, name, len(symbols), len(scored), comovement, corr_members,
                     mean_return(1), mean_return(5), mean_return(20),
                     advancers / len(scored) if scored else None,
                     advancers, decliners, stamp))
    with store() as conn:
        conn.executemany('INSERT OR REPLACE INTO sector_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                     ('last_computed', json.dumps({'date': date, 'window_start': days[0],
                                                   'sessions': len(days), 'at': stamp})))
    if verbose:
        print(f'{date}：{len(rows)} 個族群，視窗 {days[0]}～{date}（{len(days)} 個交易日）')
    return {'date': date, 'groups': len(rows), 'sessions': len(days), 'window_start': days[0]}


def report(date=None):
    """看板讀取用；只讀本機已算好的結果，不觸發重算。"""
    with store() as conn:
        date = date or (conn.execute('SELECT MAX(date) FROM sector_days').fetchone()[0])
        if not date:
            raise SystemExit('尚未計算任何日期')
        parents = {r['name']: r['parent'] for r in conn.execute('SELECT * FROM sector_groups')}
        members = {}
        for row in conn.execute('SELECT * FROM sector_members ORDER BY symbol'):
            members.setdefault(row['name'], []).append(row['symbol'])
        groups = [dict(r, parent=parents.get(r['name'], ''), members_list=members.get(r['name'], []))
                  for r in conn.execute('SELECT * FROM sector_days WHERE date=? ORDER BY name', (date,))]
        chains = {}
        for row in conn.execute('SELECT * FROM chain_links ORDER BY partner,direction,category,symbol'):
            partner = chains.setdefault(row['partner'], {'code': row['partner_code'], 'links': {}})
            partner['links'].setdefault(row['direction'], {}).setdefault(row['category'], []).append(row['symbol'])
        meta = {r['key']: r['value'] for r in conn.execute('SELECT * FROM meta')}

    universe = sorted({s for g in groups for s in g['members_list']} |
                      {s for p in chains.values() for cats in p['links'].values() for v in cats.values() for s in v})
    with quotes() as conn:
        days = [d for d in calendar(conn, date)][-260:]
        series = closes(conn, universe, days)
        names = {r['symbol']: r['name'] for r in conn.execute(
            'SELECT symbol,name FROM prices WHERE date=?', (date,)) if r['symbol'] in set(universe)}
    # 本機行情從 2026-01-02 起，沒有前一年最後交易日的收盤可當年初基期。
    # 這裡如實以本機最早一個交易日為基準，介面標明起算日，不冒充 YTD。
    since = days[0]
    stocks = {}
    for symbol in universe:
        prices = series.get(symbol)
        if not prices:
            continue
        base = prices.get(since)
        stocks[symbol] = {'n': names.get(symbol, symbol),
                          'r1': horizon_return(prices, days, 1), 'r5': horizon_return(prices, days, 5),
                          'r20': horizon_return(prices, days, 20),
                          'since': (prices[date] / base - 1) * 100 if base and date in prices else None}
    extra = reference(date, set(stocks) | {s for c in chains.values()
                                           for cats in c['links'].values() for v in cats.values() for s in v})
    with store() as conn:
        structure = {}
        for row in conn.execute('SELECT * FROM structure ORDER BY relation,category,party_name'):
            structure.setdefault(row['symbol'], []).append(
                [row['relation'], row['category'], row['party_name'], row['party_symbol']])
        partner_quotes = {r['partner']: dict(r) for r in conn.execute('SELECT * FROM partner_quotes')}
    board = leaders(date)
    return {'date': date, 'meta': meta, 'groups': groups, 'foreign': chains,
            'stocks': stocks, 'since': since, 'structure': structure,
            'partner_quotes': partner_quotes, 'leaders': board['groups'],
            'leader_coverage': board['coverage'], 'flows': daily_flows(date)['stocks'], **extra}


def reference(date, symbols):
    """個股的股本、市值、累計營收，以及在台股產業鏈中的上中下游位置。

    佔比是「同環節已上市櫃成員的合計佔比」，不是真實產品市占率：同環節只涵蓋
    有掛牌的公司，未含外商與未上市競爭者，且公司營收通常橫跨多項業務。
    """
    wanted = set(symbols)
    with store() as conn:
        profiles = {r['symbol']: dict(r) for r in conn.execute('SELECT * FROM company_profile')
                    if r['symbol'] in wanted}
        revenue = {r['symbol']: dict(r) for r in conn.execute('SELECT * FROM company_revenue')
                   if r['symbol'] in wanted}
        chains = {r['code']: {'code': r['code'], 'name': r['name'], 'segments': []}
                  for r in conn.execute('SELECT * FROM chains ORDER BY name')}
        segments = {}
        for row in conn.execute('SELECT * FROM chain_segments ORDER BY chain,seq'):
            entry = {'code': row['code'], 'name': row['name'], 'tier': row['tier'],
                     'seq': row['seq'], 'chain': row['chain'], 'symbols': []}
            segments[(row['chain'], row['code'])] = entry
            if row['chain'] in chains:
                chains[row['chain']]['segments'].append(entry)
        for row in conn.execute('SELECT * FROM chain_members'):
            entry = segments.get((row['chain'], row['segment']))
            if entry:
                entry['symbols'].append(row['symbol'])

    with quotes() as conn:
        closes = {r['symbol']: r['close'] for r in conn.execute(
            'SELECT symbol,close FROM prices WHERE date=?', (date,))}

    stocks = {}
    for symbol, profile in profiles.items():
        close = closes.get(symbol)
        shares = profile.get('shares') or None
        month = revenue.get(symbol, {})
        stocks[symbol] = {
            'capital': profile.get('capital'), 'shares': shares,
            'market': profile['market'], 'listed_on': profile.get('listed_on') or None,
            'market_cap': shares * close if shares and close else None,
            'revenue_ym': month.get('year_month'),
            'revenue_cum': month.get('cumulative'),
            'revenue_yoy': ((month['cumulative'] / month['last_year_cumulative'] - 1) * 100
                            if month.get('cumulative') and month.get('last_year_cumulative') else None)}

    # 環節內佔比：分母只加總同環節中該項目有值的成員，缺值不當零。
    positions = {}
    for entry in segments.values():
        peers = [s for s in entry['symbols'] if s in stocks]
        cap_total = sum(stocks[s]['market_cap'] for s in peers if stocks[s]['market_cap'])
        rev_total = sum(stocks[s]['revenue_cum'] for s in peers if stocks[s]['revenue_cum'])
        cap_peers = sum(1 for s in peers if stocks[s]['market_cap'])
        rev_peers = sum(1 for s in peers if stocks[s]['revenue_cum'])
        for symbol in entry['symbols']:
            record = stocks.get(symbol, {})
            positions.setdefault(symbol, []).append({
                'chain': entry['chain'], 'chain_name': chains.get(entry['chain'], {}).get('name', ''),
                'segment': entry['code'], 'segment_name': entry['name'], 'tier': entry['tier'],
                'seq': entry['seq'], 'members': len(entry['symbols']),
                'cap_share': (record.get('market_cap') / cap_total * 100
                              if record.get('market_cap') and cap_total else None),
                'cap_peers': cap_peers,
                'rev_share': (record.get('revenue_cum') / rev_total * 100
                              if record.get('revenue_cum') and rev_total else None),
                'rev_peers': rev_peers})
    return {'profiles': stocks, 'chains': chains, 'positions': positions}


def daily_flows(date, span=5):
    """當日個股層級的資金與人氣訊號，另附最近 span 個交易日的法人累計。全部只讀本機。"""
    with store() as conn:
        window = [r['date'] for r in conn.execute(
            'SELECT date FROM institutional_days WHERE date<=? ORDER BY date DESC LIMIT ?',
            (date, span))][::-1]
        institutional = {r['symbol']: dict(r) for r in conn.execute(
            'SELECT * FROM institutional WHERE date=?', (date,))}
        has_institutional = date in window
        history = [dict(r) for r in conn.execute(
            'SELECT * FROM institutional WHERE date>=? AND date<=?',
            (window[0] if window else date, date))] if window else []
    with quotes() as conn:
        closes = {r['symbol']: r['close'] for r in conn.execute(
            'SELECT symbol,close FROM prices WHERE date=?', (date,))}
        marks = ','.join('?' * len(window)) if window else "''"
        window_closes = {(r['date'], r['symbol']): r['close'] for r in conn.execute(
            f'SELECT date,symbol,close FROM prices WHERE date IN ({marks})', window)} if window else {}
        # 券商同步買超：目前仍在追蹤的分點中，當日淨買超的家數。
        branches = [(r['broker'], r['branch']) for r in conn.execute(
            'SELECT broker,branch FROM branches WHERE active=1')]
        clause = ' OR '.join(['(broker=? AND branch=?)'] * len(branches)) if branches else '0'
        has_branches = bool(conn.execute(
            'SELECT 1 FROM snapshots WHERE date=? AND status=? LIMIT 1',
            (date, 'observed')).fetchone())
        consensus, opposed = {}, {}
        for row in conn.execute(
                f'SELECT symbol,SUM(net>0) buyers,SUM(net<0) sellers FROM flows '
                f'WHERE date=? AND ({clause}) GROUP BY symbol',
                [date] + [v for pair in branches for v in pair]):
            consensus[row['symbol']] = row['buyers']
            opposed[row['symbol']] = row['sellers']
        row = conn.execute('SELECT payload FROM focus_turnover_days WHERE date=?', (date,)).fetchone()
    turnover = {}
    if row:
        for entry in json.loads(row['payload']).get('rows', []):
            turnover[entry['symbol']] = entry

    # 每日各自用當日收盤換算金額，不用最後一天的價格回推整段。
    cumulative = defaultdict(lambda: {'foreign': 0.0, 'trust': 0.0, 'days': 0, 'positive': 0})
    for record in history:
        close = window_closes.get((record['date'], record['symbol']))
        if close is None:
            continue
        entry = cumulative[record['symbol']]
        entry['foreign'] += record['foreign_net'] * close
        entry['trust'] += record['trust_net'] * close
        entry['days'] += 1
        entry['positive'] += (record['foreign_net'] + record['trust_net']) > 0

    stocks = {}
    for symbol, close in closes.items():
        record = institutional.get(symbol)
        foreign = record['foreign_net'] if record else None
        trust = record['trust_net'] if record else None
        window_entry = cumulative.get(symbol)
        stocks[symbol] = {
            'close': close,
            'foreign_shares': foreign, 'trust_shares': trust,
            'foreign_value': foreign * close if foreign is not None else None,
            'trust_value': trust * close if trust is not None else None,
            'inst_value': (foreign + trust) * close if foreign is not None else None,
            'foreign_value_5d': window_entry['foreign'] if window_entry else None,
            'trust_value_5d': window_entry['trust'] if window_entry else None,
            'inst_value_5d': (window_entry['foreign'] + window_entry['trust']) if window_entry else None,
            'inst_days': window_entry['days'] if window_entry else 0,
            'inst_positive_days': window_entry['positive'] if window_entry else 0,
            'buyers': consensus.get(symbol, 0) if has_branches else None,
            'sellers': opposed.get(symbol, 0) if has_branches else None,
            'turnover_rank': turnover.get(symbol, {}).get('rank'),
            'turnover_amount': turnover.get(symbol, {}).get('amount')}
    return {'stocks': stocks, 'has_institutional': has_institutional,
            'has_turnover': bool(turnover), 'branches': len(branches),
            'has_branches': has_branches, 'turnover_size': len(turnover), 'window': window}


def leaders(date=None, min_comovement=0.35, min_members=3, max_members=30):
    """當日主流族群：法人資金、成交重心入榜、券商同步買超合在族群層級。

    只納入共動夠緊的族群——共動低的族群成員本來就不一起動，把它們的資金加總
    當族群資金流會誤導。缺資料的個股不計為零，另列缺漏檔數。
    """
    with store() as conn:
        date = date or conn.execute('SELECT MAX(date) FROM sector_days').fetchone()[0]
        rows = [dict(r) for r in conn.execute('SELECT * FROM sector_days WHERE date=?', (date,))]
        members = {}
        for row in conn.execute('SELECT * FROM sector_members'):
            members.setdefault(row['name'], []).append(row['symbol'])
        parents = {r['name']: r['parent'] for r in conn.execute('SELECT * FROM sector_groups')}
    signals = daily_flows(date)
    stocks = signals['stocks']
    window = signals['window']

    result = []
    for row in rows:
        if row['comovement'] is None or row['comovement'] < min_comovement:
            continue
        if not min_members <= row['members'] <= max_members:
            continue
        codes = members.get(row['name'], [])
        priced = [stocks[c] for c in codes if c in stocks]
        inst = [s for s in priced if s['inst_value'] is not None]
        inst5 = [s for s in priced if s['inst_value_5d'] is not None]
        ranked = [s for s in priced if s['turnover_rank']]
        contributors = sorted(
            [dict(s, symbol=c) for c, s in ((c, stocks[c]) for c in codes if c in stocks)
             if s['inst_value'] is not None],
            key=lambda s: abs(s['inst_value']), reverse=True)[:5]
        result.append({
            'name': row['name'], 'parent': parents.get(row['name'], ''),
            'members': row['members'], 'comovement': row['comovement'],
            'breadth': row['breadth'], 'r1': row['r1'], 'r5': row['r5'], 'r20': row['r20'],
            'foreign_value': sum(s['foreign_value'] for s in inst) if inst else None,
            'trust_value': sum(s['trust_value'] for s in inst) if inst else None,
            'inst_value': sum(s['inst_value'] for s in inst) if inst else None,
            'foreign_value_5d': sum(s['foreign_value_5d'] for s in inst5) if inst5 else None,
            'trust_value_5d': sum(s['trust_value_5d'] for s in inst5) if inst5 else None,
            'inst_value_5d': sum(s['inst_value_5d'] for s in inst5) if inst5 else None,
            'inst_stocks': len(inst), 'inst_missing': len(codes) - len(inst),
            'turnover_hits': len(ranked),
            'turnover_amount': sum(s['turnover_amount'] for s in ranked) if ranked else 0.0,
            'consensus_stocks': (sum(1 for s in priced if s['buyers'] >= 2)
                                 if signals['has_branches'] else None),
            'consensus_branches': (sum(s['buyers'] for s in priced)
                                   if signals['has_branches'] else None),
            'contributors': [{'symbol': s['symbol'], 'inst_value': s['inst_value'],
                              'foreign_value': s['foreign_value'], 'trust_value': s['trust_value'],
                              'inst_value_5d': s['inst_value_5d'],
                              'buyers': s['buyers'], 'turnover_rank': s['turnover_rank']}
                             for s in contributors]})
    result.sort(key=lambda r: r['inst_value'] if r['inst_value'] is not None else -1e18, reverse=True)
    return {'date': date, 'groups': result, 'coverage': {
        'institutional': signals['has_institutional'], 'turnover': signals['has_turnover'],
        'branches': signals['branches'], 'turnover_size': signals['turnover_size'],
        'has_branches': signals['has_branches'],
        'window': window, 'window_days': len(window)}}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='族群字典與廣度指標（原型）')
    parser.add_argument('command', choices=['seed', 'compute', 'build'])
    parser.add_argument('--date', help='計算日期，預設為本機最新交易日')
    args = parser.parse_args()
    if args.command in ('seed', 'build'):
        print('字典已匯入：', seed())
    if args.command in ('compute', 'build'):
        compute(args.date)
