import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,forward_pe_store as S,forward_pe_inventory as I

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(db,'DATA',Path(self.tmp.name));self.patch.start();S.initialize()
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def src(self,provider='cnyes_factset',published='2026-10-13T08:00:00+08:00',raw=b'public evidence'):
  return S.source('eps',provider,'https://news.example/report',raw,published_at=published)
 def eps(self,**kwargs):
  return {'symbol':'2330','period_kind':'calendar_year','period':'2026','statistic':'median','value':90.0,'currency':'TWD','eps_basis':'not_disclosed','share_basis':'not_disclosed','forecast_as_of':None,'published_at':'2026-10-13T08:00:00+08:00','provider':'FactSet via public news','estimate_type':'analyst_consensus','low':80.,'high':100.,**kwargs}
 def test_all_observation_tables_are_immutable(self):
  sid=self.src()
  with S.connect(True) as c:
   for statement in ["UPDATE sources SET provider='other'",'DELETE FROM sources']:
    with self.assertRaises(sqlite3.IntegrityError):c.execute(statement)
   for table in ('sources','universes','quotes','estimates','snapshots','audits'):
    self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchone()[0],2)
  with S.connect() as c:self.assertEqual(c.execute('SELECT id FROM sources').fetchone()[0],sid)
 def test_ingestion_time_cannot_be_backdated_by_original_download_timestamp(self):
  old='2026-10-13T10:00:00+08:00';sid=S.source('eps','cnyes_factset','https://news.example/report',b'original document',published_at='2026-10-13T08:00:00+08:00',retrieved_at=old)
  S.estimate_batch(sid,[self.eps()]);self.assertIsNone(S.select_estimate('2330','2026',cutoff='2026-10-14T00:00:00+08:00'))
  e=S.select_estimate('2330','2026');self.assertGreater(S.instant(e['known_at']),S.instant(old))
 def test_late_arriving_old_report_cannot_replace_newer_provider_version(self):
  new='2026-11-02T08:00:00+08:00';sid=self.src(published=new,raw=b'new');S.estimate_batch(sid,[self.eps(value=95,published_at=new)])
  sid=self.src(raw=b'old');S.estimate_batch(sid,[self.eps(value=90)])
  self.assertEqual(S.select_estimate('2330','2026')['value'],95)
  with S.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM estimates').fetchone()[0],2)
 def test_mean_median_year_provider_and_horizon_do_not_mix(self):
  sid=self.src();S.estimate_batch(sid,[self.eps(value=90),self.eps(value=91,statistic='mean'),self.eps(value=99,period='2027')])
  self.assertEqual(S.select_estimate('2330','2026')['value'],90)
  self.assertEqual(S.select_estimate('2330','2026',statistic='mean')['value'],91)
  self.assertEqual(S.select_estimate('2330','2027')['value'],99)
  self.assertIsNone(S.select_estimate('2330','2026',provider='licensed_export'))
  self.assertIsNone(S.select_estimate('2330','2026',period_kind='NTM'))
 def test_zero_and_negative_eps_are_retained_and_missing_is_rejected(self):
  sid=self.src();S.estimate_batch(sid,[self.eps(value=0,low=-1,high=1),self.eps(symbol='2454',value=-2,low=-4,high=-1)])
  self.assertEqual(S.select_estimate('2330','2026')['value'],0);self.assertEqual(S.select_estimate('2454','2026')['value'],-2)
  for value in [None,True,float('nan'),float('inf')]:
   with self.assertRaises((ValueError,TypeError)):S.estimate_batch(sid,[self.eps(value=value)])
 def test_bad_batch_is_atomic_and_same_batch_is_idempotent(self):
  sid=self.src();good=self.eps();bad=self.eps(symbol='2454',value=1000)
  with self.assertRaises(ValueError):S.estimate_batch(sid,[good,bad])
  with S.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM estimates').fetchone()[0],0)
  self.assertEqual(S.estimate_batch(sid,[good]),S.estimate_batch(sid,[good]))
  with S.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM estimates').fetchone()[0],1)
 def test_incompatible_or_unknown_basis_does_not_become_ready(self):
  sid=self.src();S.estimate_batch(sid,[self.eps()]);self.assertFalse(S.select_estimate('2330','2026')['calculation_ready'])
  row=self.eps(value=92,eps_basis='diluted',share_basis='current_ordinary_share_adjusted',forecast_as_of='2026-10-12',basis_evidence={'reference':'original export specification'})
  sid=self.src(raw=b'basis clarification document');S.estimate_batch(sid,[row]);self.assertTrue(S.select_estimate('2330','2026')['calculation_ready'])
 def test_multiple_eps_bases_require_an_explicit_choice(self):
  sid=self.src();S.estimate_batch(sid,[self.eps(eps_basis='basic'),self.eps(value=92,eps_basis='diluted')])
  with self.assertRaises(ValueError):S.select_estimate('2330','2026')
  self.assertEqual(S.select_estimate('2330','2026',eps_basis='diluted')['value'],92)
 def test_future_publication_and_forecast_date_are_rejected(self):
  with self.assertRaises(ValueError):self.src(published='2099-01-01T00:00:00+08:00')
  sid=self.src()
  with self.assertRaises(ValueError):S.estimate_batch(sid,[self.eps(forecast_as_of='2026-10-14')])
 def test_corrupt_raw_evidence_is_detected(self):
  sid=self.src();p=next((S.home()/'raw').glob('*.gz'));p.write_bytes(b'corrupt')
  with self.assertRaises((ValueError,OSError,EOFError)):self.src()
 def test_inventory_read_is_read_only_and_obeys_snapshot_cutoff(self):
  sid=self.src();rows=[{'rank':i+1,'symbol':str(1000+i),'name':'fixture','weight_pct':2.0} for i in range(50)]
  uid=S.universe(sid,'2026-10-01',rows)
  qids=S.quote_batch(sid,[{'symbol':r['symbol'],'market_date':'2026-10-01','close':100.,'shares':1000.,'currency':'TWD','price_basis':'raw_close'} for r in rows])
  eids=S.estimate_batch(sid,[self.eps(symbol='1000')]);S.snapshot(uid,qids,eids)
  path=S.home()/'observations.sqlite3';before=path.read_bytes()
  with patch.object(I.requests,'get',side_effect=AssertionError('display cannot fetch')):
   r=I.current_report();self.assertEqual(r['coverage']['quote_names'],50);self.assertEqual(r['coverage']['eps_document_names'],1);self.assertFalse(r['gates']['valuation_ready']);self.assertEqual(r['rows'][0]['eps']['source_url'],'https://news.example/report')
   self.assertEqual(I.current_report(cutoff='2026-10-14T00:00:00+08:00')['status'],'missing')
  self.assertEqual(path.read_bytes(),before)
 def test_report_read_creates_no_database(self):
  with patch.object(db,'DATA',Path(self.tmp.name)/'empty'):
   self.assertIsNone(S.read_snapshot());self.assertFalse(S.home().exists())

if __name__=='__main__':unittest.main()
