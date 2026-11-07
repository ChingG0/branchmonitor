import json
import unittest
import numpy as np
from backend import v1, v2
import test_v8 as fixtures

class V1Tests(unittest.TestCase):
    def test_defense_activation_never_loosen_and_no_today_high(self):
        pos=dict(peak=100.,entry_price=100.,stop=92.)
        # Only the already completed close can activate the regime. Today's
        # intraday high is intentionally not an input to this calculation.
        self.assertEqual(v2.next_defense(pos,107.,5.,True),92.)
        self.assertEqual(v2.next_defense(pos,108.,5.,True),98.)
        self.assertEqual(v2.next_defense(pos,108.,5.),93.)
        protected=dict(peak=110.,entry_price=100.,stop=100.)
        self.assertEqual(v2.next_defense(protected,105.,4.,True),102.)
        self.assertEqual(v2.next_defense(protected,105.,15.,True),100.)
        self.assertEqual(v2.next_defense(protected,105.,np.nan,True),100.)

    def test_prefix_accounting_and_next_session_stop(self):
        raw,flows,rs=fixtures.V8Tests.fixture()
        result=v1.calculate(raw,flows,rs)
        self.assertEqual(result['key'],'v1')
        self.assertTrue(result['trades'])
        activated=False
        for day,s in result['days'].items():
            self.assertGreaterEqual(s['cash'],0)
            self.assertLessEqual(len(s['holdings']),3)
            self.assertEqual(len({x['symbol'] for x in s['holdings']}),len(s['holdings']))
            self.assertAlmostEqual(s['equity'],v1.INITIAL+s['realized_pnl']+s['unrealized_pnl'])
            for p in s['holdings']:
                self.assertGreaterEqual(p['next_stop'],p['stop'])
                activated |= p['next_protection_active']
        self.assertTrue(activated)
        cut=list(result['days'])[35]
        prefix=v1.calculate(raw,flows,rs,cut)
        self.assertEqual(prefix['days'],{d:s for d,s in result['days'].items() if d<=cut})
        self.assertEqual(prefix['curve'],[r for r in result['curve'] if r['date']<=cut])
        json.dumps(result,allow_nan=False)

    def test_gap_execution_costs_no_same_day_reentry(self):
        raw,flows,rs=fixtures.V8Tests.fixture()
        base=v1.calculate(raw,flows,rs)
        d,s=next((d,s) for d,s in base['days'].items() if s['holdings'])
        pos=s['holdings'][0]; days=list(base['days']);nextday=days[days.index(d)+1]
        mask=(raw.date==nextday)&(raw.symbol==pos['symbol']);price=pos['next_stop']*.75
        for f,v in [('open',price),('high',price*1.01),('low',price*.99),('close',price)]:raw.loc[mask,f]=v
        result=v1.calculate(raw,flows,rs)
        trade=next(t for t in result['trades'] if t['symbol']==pos['symbol'] and t['entry_date']==pos['entry_date'])
        self.assertEqual(trade['exit_price'],price)
        self.assertEqual(trade['exit_date'],nextday)
        self.assertAlmostEqual(trade['pnl'],price*trade['qty']*(1-v1.SELL)-trade['cost'])
        self.assertNotIn(pos['symbol'],result['days'][nextday]['buy'])

    def test_new_position_stop_and_missing_rs(self):
        raw,flows,rs=fixtures.V8Tests.fixture()
        first=v1.calculate(raw,flows,rs)['entry_events'][0]
        mask=(raw.date==first['date'])&(raw.symbol==first['symbol'])
        raw.loc[mask,'low']=raw.loc[mask,'open']*.5
        result=v1.calculate(raw,flows,rs)
        t=next(t for t in result['trades'] if t['symbol']==first['symbol'] and t['entry_date']==first['date'])
        self.assertEqual(t['exit_date'],first['date'])
        self.assertEqual(t['exit_price'],t['stop'])
        missing=v1.calculate(raw,flows,rs[:-1])
        self.assertEqual(missing['days'][missing['as_of']]['candidates'],[])
