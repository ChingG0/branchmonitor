import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,timedelta
import numpy as np
from backend import alpha_beta as a,alpha_beta_sources as s,db

class MathTest(unittest.TestCase):
 def setUp(self):
  rng=np.random.default_rng(129);self.x=rng.normal(0,.012,240);self.e=rng.normal(0,.006,240);self.y=.0004+1.4*self.x+self.e
  self.dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(240)]
 def test_beta_and_hac_match_statsmodels(self):
  import statsmodels.api as sm
  f=a.fit_market(self.y,self.x);ref=sm.OLS(self.y,sm.add_constant(self.x)).fit(cov_type='HAC',cov_kwds={'maxlags':5,'use_correction':True})
  self.assertAlmostEqual(f['beta'],ref.params[1],12);self.assertAlmostEqual(f['se'],ref.bse[1],12)
  self.assertAlmostEqual(f['r2'],ref.rsquared,12)
  self.assertAlmostEqual(f['intercept'],ref.params[0],12);self.assertAlmostEqual(f['alpha_se'],ref.bse[0],12)
  self.assertAlmostEqual(f['alpha_t'],ref.tvalues[0],12);self.assertTrue(np.allclose(f['alpha_ci'],[ref.params[0]-1.96*ref.bse[0],ref.params[0]+1.96*ref.bse[0]],atol=1e-12))
  self.assertEqual(f['alpha_hac_lags'],5)
 def test_future_evaluation_does_not_change_preperiod_beta_sigma(self):
  f=a.analytics(self.y,self.x,self.dates);changed=self.y.copy();changed[-60:]+=np.linspace(.01,.05,60);g=a.analytics(changed,self.x,self.dates)
  for key in ['beta','sigma']:self.assertEqual(f['fixed']['fit'][key],g['fixed']['fit'][key])
  ar=self.y[-60:]-f['fixed']['fit']['beta']*self.x[-60:]
  self.assertAlmostEqual(f['fixed']['car'],ar.sum()*100,12)
  self.assertAlmostEqual(f['fixed']['score'],ar.sum()/(f['fixed']['fit']['sigma']*np.sqrt(60)),12)
  self.assertGreater(g['fixed']['score'],f['fixed']['score']);self.assertNotEqual(g['current']['beta'],f['current']['beta'])
 def test_twenty_uses_own_preperiod(self):
  f=a.analytics(self.y,self.x,self.dates);ref=a.fit_market(self.y[-140:-20],self.x[-140:-20]);self.assertEqual(f['twenty']['fit']['beta'],ref['beta'])
  self.assertEqual(f['twenty']['estimate_end'],self.dates[-21]);self.assertEqual(f['fixed']['estimate_end'],self.dates[-61])
 def test_cash_ex_dividend_not_fake_loss(self):
  prices={'2026-01-01':dict(close=100),'2026-01-02':dict(close=95)};e={'2026-01-02':dict(cash=5,unsafe=False,pre_close=100)}
  self.assertEqual(a.total_returns(prices,list(prices),e),[0.])
  e['2026-01-02']['unsafe']=True
  with self.assertRaisesRegex(ValueError,'除權'):a.total_returns(prices,list(prices),e)
 def test_missing_price_and_bad_preclose_fail_closed(self):
  p={'2026-01-01':dict(close=100),'2026-01-02':dict(close=95)}
  with self.assertRaisesRegex(ValueError,'缺價'):a.total_returns(p,['2026-01-01','2026-01-03'],{})
  with self.assertRaisesRegex(ValueError,'前價'):a.total_returns(p,list(p),{'2026-01-02':dict(cash=5,unsafe=False,pre_close=110)})
 def test_flat_residual_is_not_infinite_score(self):
  f=a.analytics(self.x*2,self.x,self.dates);self.assertIsNone(f['fixed']['score'])
 def test_us_session_alignment_compounds_holiday_interval(self):
  inputs={'proxies':{'SPY':{'2026-01-02':100,'2026-01-05':110,'2026-01-06':120},'TWD=X':{'2026-01-02':30,'2026-01-05':31,'2026-01-06':32}}}
  levels,used=a.foreign_levels(inputs,'SPY',['2026-01-05','2026-01-07'])
  self.assertEqual(used,{'2026-01-05':'2026-01-02','2026-01-07':'2026-01-06'})
  self.assertEqual(levels['2026-01-07']/levels['2026-01-05'],120*32/(100*30))
 def test_not_price_index_and_wrong_month(self):
  with self.assertRaises(ValueError):s.parse_benchmark({'stat':'OK','date':'20260101','fields':['日期','發行量加權股價指數'],'data':[]},'20260101','TAIEX_TR')
 def test_foreign_and_domestic_groups(self):
  self.assertEqual(s.classify('2330',{})[0],'stocks')
  self.assertEqual(s.classify('00646',{'基金類型':'國外成分股','基金中文名稱':'標普500'})[0],'overseas')
  self.assertEqual(s.foreign_benchmark({'標的指數/追蹤指數名稱':'NASDAQ-100指數'}),'QQQ')
  self.assertIsNone(s.foreign_benchmark({'標的指數/追蹤指數名稱':'全球指數'}))
 def test_delta_variance_handles_overlapping_windows(self):
  z=a.beta_delta(self.y,self.x);self.assertTrue(np.isfinite(z['se']));self.assertGreater(z['se'],0)
  self.assertEqual(z['value'],a.fit_market(self.y[-120:],self.x[-120:])['beta']-a.fit_market(self.y[-180:-60],self.x[-180:-60])['beta'])

class PublicationTest(unittest.TestCase):
 def test_first_publication_cannot_be_rewritten(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'one.json';self.assertTrue(a.publish_once(path,{'one':1}));self.assertFalse(a.publish_once(path,{'two':2}));self.assertEqual(json.loads(path.read_text()),{'one':1})
 def test_get_does_not_create_any_files_or_fetch(self):
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)/'new'),patch.object(s,'fetch',side_effect=AssertionError('GET fetched')):
   self.assertEqual(a.report()['rows'],[]);self.assertFalse((Path(d)/'new').exists())
 def test_rank_ic_handles_ties(self):
  self.assertEqual(a.average_ranks([3,1,1,5]).tolist(),[2.,.5,.5,3.])
 def test_no_retrospective_validation(self):
  inp=dict(dates=['2026-10-28','2026-10-29'],prices=[],events=[],benchmarks={},unsafe=[])
  snap=dict(published_at='2026-10-29T10:00:00+08:00',as_of='2026-10-28',rows=[])
  self.assertEqual(a.validation(inp,[snap])['mature'],[])
 def test_forward_period_starts_after_publication_and_cost_is_separate(self):
  dates=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(23)]
  prices=[dict(symbol='2330',date=d,close=100*1.01**i) for i,d in enumerate(dates)]
  inp=dict(dates=dates,prices=prices,events=[],benchmarks={'TAIEX_TR':{d:100 for d in dates}},unsafe=[],proxies={})
  snap=dict(version=a.VERSION,protocol=a.PROTOCOL,published_at=dates[0]+'T19:00:00+08:00',as_of=dates[0],rows=[dict(symbol='2330',group='stocks',benchmark='TAIEX_TR',beta=1)],ranking_sets=[dict(key='stocks|TAIEX_TR|TWSE|exclude|'+mode,group='stocks',benchmark='TAIEX_TR',market='TWSE',mode=mode,disposition_policy='exclude_active_and_unknown',candidates=['2330'],selected=['2330'],ranks={'2330':1}) for mode in ['fixed','rolling']])
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)):
   rows=a.validation(inp,[snap])['mature']
  self.assertEqual(len(rows),2)
  self.assertEqual(rows[0]['start'],dates[1]);self.assertEqual(rows[0]['end'],dates[21])
  self.assertAlmostEqual(rows[0]['mean_forward_car'],20,9)
  self.assertAlmostEqual(rows[0]['mean_net_return'],(1.01**20-1)*100-.685,9)
  self.assertEqual(rows[0]['method_version'],a.VERSION)
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)):
   self.assertEqual(a.validation(inp,[{**snap,'published_at':'2025-12-01T10:00:00+08:00'}])['mature'],[])
  inp['dispositions']=[dict(symbol='2330',start=dates[0],end=dates[-1])]
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)):
   self.assertEqual(a.validation(inp,[snap])['mature'],rows)
 def test_incomplete_disposition_coverage_does_not_publish_signal(self):
  inputs=dict(dates=[],prices=[],events=[],benchmarks={},unsafe=[],disposition_coverage={'TWSE':True,'TPEX':False})
  value=dict(version=a.VERSION,as_of='2026-10-28',generated_at='2026-10-29T10:00:00+08:00',rows=[dict(status='ok')])
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)),patch.object(s,'collect',return_value=inputs),patch.object(a,'build',return_value=value):
   self.assertTrue(a._lock.acquire(False));a._run()
   self.assertEqual(a.job_status()['status'],'partial');self.assertEqual(list((s.folder()/'snapshots').glob('*.json')),[])
   self.assertFalse(a.read(s.folder()/'latest.json')['validation']['publication_ready'])
 def test_failure_keeps_last_report_and_signal(self):
  with tempfile.TemporaryDirectory() as d,patch.object(db,'DATA',Path(d)),patch.object(s,'collect',side_effect=ValueError('incomplete')):
   a.atomic(s.folder()/'latest.json',{'as_of':'2026-10-28','rows':[]});before=(s.folder()/'latest.json').read_bytes()
   self.assertTrue(a._lock.acquire(False))
   with patch.object(a.traceback,'print_exc'):a._run()
   self.assertEqual((s.folder()/'latest.json').read_bytes(),before);self.assertEqual(a.job_status()['status'],'failed')


class DispositionAndRankingTest(unittest.TestCase):
 def fixture(self):
  return dict(disposition_coverage={'TWSE':True,'TPEX':True},disposition_observed_at='2026-10-29T10:00:00+08:00',
   dispositions=[dict(symbol='3441',market='TPEX',published='2026-10-28',period='1151029~1151106',start='2026-10-29',end='2026-11-06')])
 def test_publication_date_and_price_date_differ(self):
  d=a.disposition_at(self.fixture(),'3441','TPEX','2026-10-29','2026-10-28')
  self.assertTrue(d['active']);self.assertFalse(d['active_on_price_date']);self.assertTrue(d['excluded_by_default'])
  self.assertEqual(d['announcements'][0]['published'],'2026-10-28')
 def test_ended_not_permanently_excluded_and_future_not_backfilled(self):
  self.assertFalse(a.disposition_at(self.fixture(),'3441','TPEX','2026-11-07','2026-11-06')['excluded_by_default'])
  d=a.disposition_at(self.fixture(),'3441','TPEX','2026-10-27','2026-10-26');self.assertEqual(d['announcements'],[]);self.assertFalse(d['excluded_by_default'])
 def test_unknown_is_not_silently_treated_as_safe(self):
  d=a.disposition_at({},'2330','TWSE','2026-10-29','2026-10-28');self.assertEqual(d['state'],'unknown');self.assertTrue(d['excluded_by_default'])
 def test_period_formats_and_truncated_source(self):
  for text in ['1151029~1151106','115/10/29～115/11/06','2026-10-29至2026-11-06']:
   self.assertEqual(s.period_dates(text),['2026-10-29','2026-11-06'])
  raw=dict(stat='OK',fields=['證券代號','公布日期','處置起迄時間'],data=[['3441','115/10/28','115/10/29~115/11/06']],total=1)
  self.assertEqual(s.parse_dispositions(raw,'TWSE','official')[0]['start'],'2026-10-29')
  raw['data'].append(['','','本日無處置資料']);raw['total']=2
  self.assertEqual(len(s.parse_dispositions(raw,'TWSE','official')),1)
  raw['total']=3
  with self.assertRaisesRegex(ValueError,'截斷'):s.parse_dispositions(raw,'TWSE','official')
 def test_views_share_pool_for_stats_and_freeze_excluded_rows(self):
  rows=[]
  for sym,market,score,active,alpha in [('1111','TWSE',3,False,.001),('2222','TWSE',2,False,-.001),('3333','TWSE',5,True,.003),('4444','TPEX',4,False,0.)]:
   for bench in ['TAIEX_TR','TPEX_TR']:
    ci=[alpha-.0001,alpha+.0001];f=dict(beta=1,intercept=alpha,alpha_se=.0001/1.96,alpha_ci=ci)
    rows.append(dict(symbol=sym,group='stocks',market=market,benchmark=bench,status='ok',continuation=True,rolling_continuation=True,
      disposition=dict(active=active,excluded_by_default=active,screening_date='2026-10-29'),metrics=dict(current=f,fixed=dict(score=score),rolling=dict(score=-score))))
  views=a.rank_views(rows);v=next(v for v in views if v['key']=='stocks|TAIEX_TR|TWSE|exclude')
  self.assertEqual(v['count'],2);self.assertEqual(v['ranks']['fixed'],{'1111':1,'2222':2});self.assertEqual(v['alpha_summary']['nonzero'],2)
  self.assertAlmostEqual(v['alpha_summary']['all_null_expected'],.1)
  included=next(v for v in views if v['key']=='stocks|TAIEX_TR|TWSE|include');self.assertEqual(included['ranks']['fixed']['3333'],1)
  snap=a.make_snapshot(dict(rows=rows,views=views,generated_at='2026-10-29T10:00:00+08:00',as_of='2026-10-28'))
  self.assertTrue(next(r for r in snap['rows'] if r['symbol']=='3333')['excluded_at_publication'])
  default=next(v for v in snap['ranking_sets'] if v['key']=='stocks|TAIEX_TR|TWSE|exclude|fixed')
  self.assertNotIn('3333',default['candidates']);self.assertEqual(snap['method_version'],a.VERSION)

if __name__=='__main__':unittest.main()
