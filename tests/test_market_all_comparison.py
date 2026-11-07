from datetime import date,datetime,timezone
import json
import unittest
from backend import fed_rates,market_monitor as m

class MarketAllTests(unittest.TestCase):
    def test_fed_effective_dates_and_change_validation(self):
        raw='<h4>2024</h4><table><tr><td>September 19</td><td>0</td><td>50</td><td>4.75-5.00</td></tr><tr><td>November 8</td><td>0</td><td>25</td><td>4.50-4.75</td></tr></table><h4>2026</h4><table><tr><td>September 17</td><td>25</td><td>0</td><td>4.75-5.00</td></tr><tr><td>December 10</td><td>25</td><td>0</td><td>5.00-5.25</td></tr></table>'
        rows=fed_rates.parse_history(raw,date(2026,10,22))
        self.assertEqual([r['date'] for r in rows],['2024-09-19','2024-11-08','2026-09-17'])
        self.assertEqual(rows[-1]['change_bp'],25)
        with self.assertRaises(ValueError):fed_rates.parse_history(raw.replace('<td>25</td><td>0</td>','<td>50</td><td>0</td>'),date(2026,10,22))
        with self.assertRaises(ValueError):fed_rates.parse_history('<h4>2026</h4><table></table>')

    def test_fed_statement_fraction_and_unchanged_meeting(self):
        from unittest.mock import patch
        raw='<p>The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent.</p>'
        self.assertEqual(fed_rates.parse_statement(raw),(3.75,4))
        unchanged='<p>The Committee decided to maintain the target range for the federal funds rate at 3-3/4 to 4 percent.</p>'
        history=[dict(date='2026-09-17',low=3.75,high=4,change_bp=25)]
        calendar=lambda data:dict(statement_date='2026-10-28',next_start='2026-12-08',next_end='2026-12-09')
        with patch.object(fed_rates,'parse_history',return_value=history):
            result=fed_rates.load(lambda url:unchanged,calendar,'https://example.com')
            self.assertEqual(result['as_of'],'2026-09-17');self.assertEqual(result['decision_date'],'2026-10-28')
            with self.assertRaises(ValueError):fed_rates.load(lambda url:unchanged.replace('3-3/4 to 4','4 to 4-1/4'),calendar,'https://example.com')

    def test_quote_null_and_future_history_are_not_previous_close(self):
        ts=lambda d:int(datetime.fromisoformat(d+'T00:00:00+00:00').timestamp())
        raw={'chart':{'result':[{'meta':{'symbol':'TWD=X','currency':'TWD','regularMarketPrice':32.48,'regularMarketTime':ts('2026-10-21')},'timestamp':[ts('2026-10-19'),ts('2026-10-20'),ts('2026-10-21'),ts('2026-10-22')],'indicators':{'quote':[{'close':[32,None,33,999]}]}}]}}
        result=m.parse_quote(json.dumps(raw),'twd');merged=m._merge_history('twd',result)
        self.assertEqual(merged['history'],[{'date':'2026-10-19','value':32},{'date':'2026-10-21','value':32.48}])
        self.assertIsNone(result['change_pct'])

    def test_daily_volume_rows_and_negative_retail_are_compared_as_values(self):
        volume={'as_of':'2026-10-21','value':8000,'rows':[{'date':'2026-10-20','amount_100m':9000},{'date':'2026-10-21','amount_100m':8000}]}
        self.assertEqual(m._merge_history('volume',volume)['history'][0]['value'],9000)
        retail={'as_of':'2026-10-21','value':-10,'rows':[{'date':'2026-10-20','ratio_pct':0}]}
        self.assertEqual(m._merge_history('retail',retail)['history'],[dict(date='2026-10-20',value=0),dict(date='2026-10-21',value=-10)])

    def test_position_history_checks_all_names_and_long_minus_short(self):
        rows=[dict(name=n,long=2,short=3,net=-1) for n in ['外資及陸資','投信','自營商','三大法人合計']]
        data=dict(as_of='2026-10-21',rows=rows)
        bad=[dict(r) for r in rows];bad[0]['net']=100
        result=m._merge_history('futures',data,extra=[dict(date='2026-10-20',rows=rows),dict(date='2026-10-19',rows=bad)])
        self.assertEqual([r['date'] for r in result['history']],['2026-10-20','2026-10-21'])

if __name__=='__main__':unittest.main()
