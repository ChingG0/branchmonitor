from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend import db,market_monitor as m

class ComparisonTests(unittest.TestCase):
    def fear(self,value,date):
        return dict(value=value,rating='fear',as_of=date+'T07:59:00+08:00',note='test')

    def test_fear_source_day_uses_us_day_and_deduplicates_intraday_rows(self):
        ms=lambda s:datetime.fromisoformat(s).timestamp()*1000
        raw=dict(fear_and_greed=dict(score=31.3714,rating='fear',timestamp='2026-10-21T23:59:57+00:00',previous_close=27.2),fear_and_greed_historical=dict(data=[
            dict(x=ms('2026-10-20T00:00:00+00:00'),y=26),
            dict(x=ms('2026-10-20T23:59:00+00:00'),y=27),
            dict(x=ms('2026-10-21T00:00:00+00:00'),y=31),
            dict(x=ms('2026-10-22T00:00:00+00:00'),y=100)]))
        result=m.parse_fear(json.dumps(raw))
        self.assertTrue(result['as_of'].startswith('2026-10-22'))
        self.assertEqual(result['source_date'],'2026-10-21')
        self.assertEqual(result['history'],[dict(date='2026-10-20',value=27.2),dict(date='2026-10-21',value=31.3714)])

    def test_updates_retain_distinct_days_not_previous_fetch(self):
        first=m._merge_history('fear',self.fear(27.2,'2026-10-21'))
        second=m._merge_history('fear',self.fear(31,'2026-10-22'),first)
        repeated=m._merge_history('fear',self.fear(31.37,'2026-10-22'),second)
        self.assertEqual(repeated['history'],[dict(date='2026-10-20',value=27.2),dict(date='2026-10-21',value=31.37)])
        revised=self.fear(32,'2026-10-22');revised['history']=[dict(date='2026-10-20',value=27.3)]
        self.assertEqual(m._merge_history('fear',revised,first)['history'][0]['value'],27.3)

    def test_backfill_keeps_latest_and_get_never_fetches_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATA',Path(tmp)):
            db.initialize()
            with patch.object(m,'load_card',return_value=self.fear(31.37,'2026-10-22')):self.assertTrue(m.update_one('fear'))
            with db.connect() as c:before=dict(c.execute("SELECT * FROM market_observations WHERE key='fear'").fetchone())
            m.save_comparison_history('fear',[dict(date='2026-10-20',value=27.2),dict(date='2026-10-21',value=0),dict(date='2026-10-22',value=100),dict(date='2026-10-19',value='NaN')])
            with db.connect() as c:after=dict(c.execute("SELECT * FROM market_observations WHERE key='fear'").fetchone())
            self.assertEqual(after['saved_at'],before['saved_at']);self.assertEqual(after['checked_at'],before['checked_at'])
            data=json.loads(after['payload']);self.assertEqual(data['value'],31.37);self.assertEqual(len(data['history']),2)
            with patch.object(m,'fetch',side_effect=AssertionError('GET must not fetch')):
                card=next(c for c in m.report()['cards'] if c['id']=='fear');self.assertEqual(card['data']['source_date'],'2026-10-21')
            with db.connect() as c:self.assertEqual(dict(c.execute("SELECT * FROM market_observations WHERE key='fear'").fetchone()),after)
            with patch.object(m,'load_card',return_value=self.fear(0,'2026-10-21')):self.assertFalse(m.update_one('fear'))
            with db.connect() as c:failed=dict(c.execute("SELECT * FROM market_observations WHERE key='fear'").fetchone())
            self.assertEqual(failed['payload'],after['payload']);self.assertIn('日期倒退',failed['error'])

    def test_flows_match_names_verify_amounts_and_skip_future_dates(self):
        rows=lambda f,t:[dict(name='投信',buy=max(t,0),sell=max(-t,0),net=t),dict(name='外資',buy=max(f,0),sell=max(-f,0),net=f)]
        data=dict(as_of='2026-10-21',rows=rows(100,20))
        merged=m._merge_history('twse',data,extra=[dict(date='2026-10-20',rows=rows(-300,30)),dict(date='2026-10-22',rows=rows(1000,1000)),dict(date='2026-10-19',rows=[dict(name='外資',buy=1,sell=0,net=5)])])
        self.assertEqual([r['date'] for r in merged['history']],['2026-10-20','2026-10-21'])
        self.assertEqual(merged['history'][0]['rows'][0]['name'],'外資')
        self.assertEqual(merged['history'][0]['rows'][0]['net'],-300)
        self.assertEqual(merged['rows'],data['rows'])

if __name__=='__main__':unittest.main()
