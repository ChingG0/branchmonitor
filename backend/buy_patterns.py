"""Buying patterns based only on sessions and histories known at the cutoff."""
from collections import defaultdict


def buying_patterns(flows, prices, calendar, as_of, calendar_complete, history_intervals, verified_symbols):
    days = sorted(set(d for d in calendar if d <= as_of))
    result = {'streak_buys': [], 'fresh_buys': [], 'buy_pattern_status': {
        'calendar_ready': bool(calendar_complete and days and days[-1] == as_of),
        'prior_sessions': max(0, min(20, len(days)-1)), 'unverified_candidates': 0}}
    if not result['buy_pattern_status']['calendar_ready']:
        return result
    lookup = {(r['date'], r['symbol']): r for r in flows if r['date'] <= as_of}
    quotes = {(r['date'], r['symbol']): r['close'] for r in prices if r['date'] <= as_of}
    intervals = defaultdict(list)
    for interval in history_intervals or []:
        intervals[interval['symbol']].append(interval)

    def covered(symbol, sessions):
        if history_intervals is None:
            return verified_symbols is not None and symbol in verified_symbols
        return all(any(r['start'] <= day <= r['end'] for r in intervals[symbol]) for day in sessions)

    for (day, symbol), row in lookup.items():
        if day != as_of or row['net'] <= 0:
            continue
        close = quotes.get((day, symbol))
        base = dict(date=day, symbol=symbol, name=row['name'], buy=row['buy'], sell=row['sell'],
                    net=row['net'], close=close, buy_value=row['buy']*1000*close if close is not None else None)
        run = []
        for session in reversed(days):
            trade = lookup.get((session, symbol))
            if trade is None or trade['net'] <= 0:
                break
            run.append(trade)
        if len(run) >= 2:
            net_value = [r['net']*1000*quotes[(r['date'], symbol)]
                         if quotes.get((r['date'], symbol)) is not None else None for r in run]
            result['streak_buys'].append(dict(base, streak=len(run), start_date=run[-1]['date'],
                streak_net=sum(r['net'] for r in run),
                streak_value=sum(net_value) if all(v is not None for v in net_value) else None,
                streak_at_least=len(run) == len(days)))
        prior = days[-21:-1]
        if len(prior) < 20:
            continue
        if not covered(symbol, prior+[as_of]):
            result['buy_pattern_status']['unverified_candidates'] += 1
            continue
        # An absent row means zero only inside verified stock-history intervals.
        if any(lookup.get((d, symbol), {}).get('buy', 0) > 0 for d in prior):
            continue
        result['fresh_buys'].append(dict(base, quiet_start=prior[0], quiet_end=prior[-1], quiet_days=20))
    result['streak_buys'].sort(key=lambda r: (r['streak'], r['buy_value'] or 0), reverse=True)
    result['fresh_buys'].sort(key=lambda r: (r['buy_value'] is not None, r['buy_value'] or 0), reverse=True)
    return result
