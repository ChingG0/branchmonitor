"""Descriptive, gross-price statistics. No order placement or invented opening holdings."""
from __future__ import annotations

from collections import defaultdict, deque
from .buy_patterns import buying_patterns


def analyze(flows, prices, calendar, as_of, calendar_complete=True, verified_symbols=None, history_intervals=None):
    flows = sorted((r for r in flows if r['date'] <= as_of), key=lambda r: (r['date'], r['symbol']))
    calendar = sorted(d for d in calendar if d <= as_of)
    day_index = {d: i for i, d in enumerate(calendar)}
    calendar_complete = calendar_complete and all(r['date'] in day_index for r in flows)
    price_map = {(r['date'], r['symbol']): r for r in prices if r['date'] <= as_of}
    coverage_by_symbol = defaultdict(list)
    for interval in history_intervals or []:
        coverage_by_symbol[interval['symbol']].append(interval)

    def verified_until(symbol, end):
        if history_intervals is None:
            return verified_symbols is None or symbol in verified_symbols
        first = flows[0]['date'] if flows else as_of
        return any(r['start'] <= first and r['end'] >= end for r in coverage_by_symbol[symbol])
    by_day = defaultdict(list)
    for r in flows:
        by_day[r['date']].append(r)
    lots = defaultdict(deque)
    names = {r['symbol']: r['name'] for r in flows}
    invested = 0.0
    realized_known = 0.0
    missing_cost = False
    missing_realized = False
    unmatched = 0.0
    closed = []
    active_cycles = {}
    cycle_records = []
    series = []
    cumulative_net = 0.0

    def price(day, symbol):
        record = price_map.get((day, symbol))
        return record['close'] if record else None

    for day in sorted(set(calendar) | set(by_day)):
        for r in by_day[day]:
            symbol, quantity = r['symbol'], r['net']
            unit = price(day, symbol)
            cumulative_net += quantity
            if quantity > 1e-9:
                if symbol not in active_cycles:
                    active_cycles[symbol] = {'symbol':symbol,'name':r['name'],'start_date':day,
                        'end_date':None,'bought_lots':0.0,'sold_lots':0.0,'known_cost':0.0,
                        'cost_missing':False,'known_realized':0.0,'realized_missing':False,
                        'unmatched_lots':0.0}
                cycle = active_cycles[symbol]
                cycle['bought_lots'] += quantity
                if unit is None:
                    cycle['cost_missing'] = True
                else:
                    cycle['known_cost'] += quantity * 1000 * unit
                lots[symbol].append({'date': day, 'quantity': quantity, 'price': unit})
                if unit is None:
                    missing_cost = True
                else:
                    invested += quantity * 1000 * unit
            elif quantity < -1e-9:
                remaining, matched, pnl, event_cost = -quantity, 0.0, 0.0, 0.0
                weighted_days, invalid = 0.0, False
                while remaining > 1e-9 and lots[symbol]:
                    lot = lots[symbol][0]
                    take = min(remaining, lot['quantity'])
                    matched += take
                    weighted_days += take * (day_index.get(day, 0) - day_index.get(lot['date'], 0))
                    if unit is None or lot['price'] is None:
                        invalid = True
                        missing_realized = True
                    else:
                        pnl += take * 1000 * (unit - lot['price'])
                        event_cost += take * 1000 * lot['price']
                    lot['quantity'] -= take
                    remaining -= take
                    if lot['quantity'] < 1e-9:
                        lots[symbol].popleft()
                unmatched += remaining
                realized_known += pnl
                if matched > 0:
                    cycle = active_cycles[symbol]
                    cycle['sold_lots'] += matched
                    cycle['known_realized'] += pnl
                    cycle['realized_missing'] |= invalid
                    cycle['unmatched_lots'] += remaining
                    if not lots[symbol]:
                        cycle['end_date'] = day
                        cycle_records.append(active_cycles.pop(symbol))
                complete = not invalid and remaining < 1e-9 and matched > 0
                closed.append({'date':day,'symbol':symbol,'name':r['name'],'quantity':-quantity,
                    'matched':matched,'unmatched':remaining,'price':unit,
                    'pnl':pnl if complete else None,'known_pnl':pnl,
                    'return_pct':pnl/event_cost*100 if complete and event_cost else None,
                    'holding_days':weighted_days/matched if matched and calendar_complete else None,'complete':complete})
        marked = 0.0
        marked_valid = True
        marked_observed = False
        has_open_lots = False
        for symbol, queue in lots.items():
            quote = price(day, symbol)
            for lot in queue:
                has_open_lots = True
                if quote is None or lot['price'] is None:
                    marked_valid = False
                else:
                    marked_observed = True
                    marked += lot['quantity'] * 1000 * (quote-lot['price'])
        valid = not missing_cost and not missing_realized and marked_valid
        series.append({'date':day,'net':sum(r['net'] for r in by_day[day]),
            'cumulative_net':cumulative_net,'pnl':realized_known+marked if valid else None,
            'return_pct':(realized_known+marked)/invested*100 if valid and invested else None,
            # Unknown opening sales add no profit; later priced FIFO matches still do.
            'realized_pnl':realized_known,
            'unrealized_pnl':marked if marked_observed or not has_open_lots else None,
            'unrealized_complete':marked_valid})

    positions = []
    unrealized_known, market_value_known, unpriced_positions = 0.0, 0.0, 0
    for symbol, queue in lots.items():
        quantity = sum(lot['quantity'] for lot in queue)
        if quantity <= 1e-9:
            continue
        quote = price(as_of, symbol)
        known = all(lot['price'] is not None for lot in queue)
        cost = sum(lot['quantity']*1000*lot['price'] for lot in queue if lot['price'] is not None)
        pnl = quantity*1000*quote-cost if quote is not None and known else None
        if quote is not None:
            market_value_known += quantity*1000*quote
        if pnl is None:
            unpriced_positions += 1
        else:
            unrealized_known += pnl
        weighted = sum(lot['quantity']*(day_index.get(as_of, len(calendar)-1)-day_index.get(lot['date'],0)) for lot in queue)/quantity
        positions.append({'symbol':symbol,'name':names[symbol],'quantity':quantity,
            'cost':cost if known else None,'avg_cost':cost/quantity/1000 if known else None,
            'close':quote,'value':quantity*1000*quote if quote is not None else None,
            'pnl':pnl,'return_pct':pnl/cost*100 if pnl is not None and cost else None,
            'holding_days':weighted if calendar_complete else None,'first_date':queue[0]['date'],
            'oldest_days':day_index.get(as_of,len(calendar)-1)-day_index.get(queue[0]['date'],0) if calendar_complete else None})
    positions.sort(key=lambda r: r['value'] or 0, reverse=True)
    position_map = {r['symbol']:r for r in positions}
    cycles = []
    for record in cycle_records + list(active_cycles.values()):
        symbol = record['symbol']
        is_closed = record['end_date'] is not None
        position = position_map.get(symbol) if not is_closed else None
        history_verified = verified_until(symbol, record['end_date'] or as_of)
        reasons = []
        if not history_verified:
            reasons.append('個股歷史未完整回補')
        if record['unmatched_lots'] > 1e-9:
            reasons.append('清倉日含期初未知流出')
        if record['cost_missing'] or record['realized_missing']:
            reasons.append('買賣日價格不足')
        cash_valid = not reasons
        realized = record['known_realized'] if cash_valid else None
        unrealized = (0.0 if is_closed else position['pnl']) if cash_valid else None
        if not is_closed and cash_valid and unrealized is None:
            reasons.append('評價日價格不足')
        pnl = realized + unrealized if realized is not None and unrealized is not None else None
        cost = record['known_cost'] if not record['cost_missing'] else None
        end = record['end_date'] or as_of
        cycles.append({'symbol':symbol,'name':record['name'],'start_date':record['start_date'],
            'end_date':record['end_date'],'as_of':as_of,'status':'closed' if is_closed else 'open',
            'bought_lots':record['bought_lots'],'sold_lots':record['sold_lots'],
            'quantity':0.0 if is_closed else position['quantity'],'cost':cost,
            'realized_pnl':realized,'unrealized_pnl':unrealized,'pnl':pnl,
            'return_pct':pnl/cost*100 if pnl is not None and cost else None,
            'holding_days':day_index[end]-day_index[record['start_date']]
                if calendar_complete and end in day_index else None,
            'complete':is_closed and pnl is not None,'history_verified':history_verified,
            'unmatched_lots':record['unmatched_lots'],'reason':'；'.join(reasons)})
    cycles.sort(key=lambda r:(r['end_date'] or as_of,r['start_date'],r['symbol']),reverse=True)
    evaluated_cycles = [r for r in cycles if r['complete']]
    cycle_wins = sum(r['pnl'] > 1e-7 for r in evaluated_cycles)
    cycle_stats = {'win_rate':cycle_wins/len(evaluated_cycles)*100 if evaluated_cycles else None,
        'wins':cycle_wins,'closed_count':len(evaluated_cycles),
        'unscored_count':sum(r['status']=='closed' and not r['complete'] for r in cycles),
        'open_count':sum(r['status']=='open' for r in cycles)}
    total_valid = not missing_cost and not missing_realized and unpriced_positions == 0
    evaluated = [r for r in closed if r['complete']]
    wins = sum(r['pnl'] > 0 for r in evaluated)
    stats = {'positions':len(positions),'invested':invested if not missing_cost else None,
        'realized':realized_known if not missing_realized else None,'unrealized':unrealized_known if not unpriced_positions else None,
        'pnl':realized_known+unrealized_known if total_valid else None,
        'return_pct':(realized_known+unrealized_known)/invested*100 if total_valid and invested else None,
        'known_pnl':realized_known+unrealized_known,'market_value':market_value_known,
        'unpriced_positions':unpriced_positions,'unmatched_lots':unmatched,
        'win_rate':wins/len(evaluated)*100 if evaluated else None,'wins':wins,
        'closed_count':len(evaluated),'unscored_events':len(closed)-len(evaluated)}

    # Observation statistic, not an executable after-close trading strategy.
    signals = []
    window_start = calendar[-30] if len(calendar) >= 30 else (calendar[0] if calendar else as_of)
    for r in flows:
        if r['net'] <= 0 or r['date'] < window_start or r['date'] not in day_index:
            continue
        i = day_index[r['date']]
        outcome = dict(symbol=r['symbol'],name=r['name'],date=r['date'],net=r['net'],
                       maturity=None,status='pending',return_pct=None,excess=None,hit=None)
        if not calendar_complete:
            outcome['status']='missing'
            signals.append(outcome)
            continue
        if i+10 < len(calendar):
            end = calendar[i+10]
            outcome['maturity'] = end
            p0, p1 = price(r['date'],r['symbol']), price(end,r['symbol'])
            b0, b1 = price(r['date'],'TAIEX'), price(end,'TAIEX')
            if all(x is not None and x > 0 for x in [p0,p1,b0,b1]):
                ret = (p1/p0-1)*100
                excess = ret-(b1/b0-1)*100
                outcome.update(status='scored',return_pct=ret,excess=excess,hit=excess > 0)
            else:
                outcome['status'] = 'missing'
        signals.append(outcome)
    scored = [s for s in signals if s['status']=='scored']
    hit_wins = sum(s['hit'] for s in scored)
    hit_stats = {'rate':hit_wins/len(scored)*100 if scored else None,'wins':hit_wins,
        'scored':len(scored),'pending':sum(s['status']=='pending' for s in signals),
        'missing':sum(s['status']=='missing' for s in signals),'total':len(signals),
        'window_start':window_start,'window_end':as_of,'stocks':len({s['symbol'] for s in signals}),
        'calendar_incomplete':not calendar_complete}

    cycles_by_symbol, signals_by_symbol = defaultdict(list), defaultdict(list)
    for row in cycles:
        cycles_by_symbol[row['symbol']].append(row)
    for row in scored:
        signals_by_symbol[row['symbol']].append(row)
    stock_stats = []
    for symbol, name in names.items():
        history = cycles_by_symbol[symbol]
        finished = [r for r in history if r['status']=='closed']
        evaluated = [r for r in finished if r['complete']]
        wins = sum(r['pnl'] > 1e-7 for r in evaluated)
        losses = sum(r['pnl'] < -1e-7 for r in evaluated)
        signal_scores = signals_by_symbol[symbol]
        position = position_map.get(symbol)
        row = {'symbol':symbol,'name':name,'win_rate':wins/len(evaluated)*100 if evaluated else None,
            'wins':wins,'losses':losses,'breakeven':len(evaluated)-wins-losses,
            'closed_count':len(evaluated),'total_closed':len(finished),
            'unscored_count':len(finished)-len(evaluated),
            'closed_pnl':sum(r['pnl'] for r in evaluated) if evaluated and len(evaluated)==len(finished) else None,
            'quantity':position['quantity'] if position else 0.0,
            'holding_days':position['holding_days'] if position else None,
            'unrealized_pnl':position['pnl'] if position else 0.0,
            'hit_rate':sum(r['hit'] for r in signal_scores)/len(signal_scores)*100 if signal_scores else None,
            'hit_samples':len(signal_scores),'history_verified':verified_symbols is None or symbol in verified_symbols}
        stock_stats.append(row)
        if position:
            position.update(win_rate=row['win_rate'],wins=wins,closed_count=len(evaluated),history_verified=row['history_verified'])
    stock_stats.sort(key=lambda r:(r['win_rate'] is not None,r['win_rate'] or 0,r['closed_count']),reverse=True)
    stock_map = {r['symbol']:r for r in stock_stats}

    flow_lookup = {(r['date'],r['symbol']):r for r in flows}
    daily = []
    for r in by_day.get(as_of, []):
        quote = price_map.get((as_of,r['symbol']), {})
        pct = r['net']*1000/quote['volume']*100 if quote.get('volume') else None
        stock = stock_map[r['symbol']]
        daily.append(dict(r,close=quote.get('close'),volume_share=pct,
            win_rate=stock['win_rate'],wins=stock['wins'],closed_count=stock['closed_count'],
            net_value=r['net']*1000*quote['close'] if quote.get('close') else None))
    daily.sort(key=lambda r: abs(r['net_value'] or 0), reverse=True)
    watch = []
    for r in daily:
        if not calendar_complete or (verified_symbols is not None and r['symbol'] not in verified_symbols):
            continue
        if r['net'] <= 0 or len(r['symbol']) != 4 or r['symbol'].startswith('0'):
            continue
        recent = [flow_lookup.get((d,r['symbol']), {}).get('net',0) for d in calendar[-5:]]
        streak = 0
        for d in reversed(calendar):
            if flow_lookup.get((d,r['symbol']),{}).get('net',0) > 0:
                streak += 1
            else:
                break
        signal_scores = [s for s in scored if s['symbol']==r['symbol']]
        watch.append(dict(symbol=r['symbol'],name=r['name'],net=r['net'],close=r['close'],
            recent_net=sum(recent),streak=streak,volume_share=r['volume_share'],
            score_value=sum(recent)*1000*r['close'] if r['close'] else 0,
            hit_rate=sum(s['hit'] for s in signal_scores)/len(signal_scores)*100 if len(signal_scores)>=5 else None,
            samples=len(signal_scores)))
    watch = sorted((r for r in watch if r['recent_net']>0),key=lambda r:r['score_value'],reverse=True)
    patterns = buying_patterns(flows, prices, calendar, as_of, calendar_complete, history_intervals, verified_symbols)
    return {**patterns,'stats':stats,'hit_stats':hit_stats,'daily':daily,'positions':positions,
        'cycles':cycles,'cycle_stats':cycle_stats,'stock_stats':stock_stats,
        'closed':list(reversed(closed)),'series':series,'signals':list(reversed(signals)),
        'watchlist':watch[:8],'dates':calendar}
