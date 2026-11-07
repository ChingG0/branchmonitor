import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from backend import db,service,sources
class RankingProbeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.tmp.name));self.p.start();db.initialize()
  with db.connect() as c:
   c.execute('INSERT INTO market_days VALUES (?,?,?,?)',('2026-11-04','ok','ok','official'))
   c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',('2026-11-04','TAIEX','指數',100,99,101,98,None,'TWSE'))
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_dated_published_rows_can_advance_stale_default(self):
  with patch.object(sources,'ranking',side_effect=[('2026-11-03',[{'old':1}]),('2026-11-04',[{'new':1}])]) as f:
   self.assertEqual(service.latest_ranking_probe({'broker':'A','branch':'B'}),('2026-11-04',[{'new':1}]));self.assertEqual(f.call_args.args,('A','B','2026-11-04'))
 def test_empty_or_unavailable_new_day_keeps_old_date(self):
  for result in [('2026-11-04',[]),sources.SourceError('not published')]:
   with patch.object(sources,'ranking',side_effect=[('2026-11-03',[{'old':1}]),result]):self.assertEqual(service.latest_ranking_probe({'broker':'A','branch':'B'}),('2026-11-03',[{'old':1}]))
 def test_current_default_needs_no_extra_request(self):
  with patch.object(sources,'ranking',return_value=('2026-11-04',[{'new':1}])) as f:
   self.assertEqual(service.latest_ranking_probe({'broker':'A','branch':'B'})[0],'2026-11-04');self.assertEqual(f.call_count,1)
