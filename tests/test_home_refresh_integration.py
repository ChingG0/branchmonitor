import copy, json, sqlite3, tempfile, unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from backend import db, full_update, service, margin_refresh as refresh, margin_statistics as src, margin_observation_build as builder
from tests.test_margin_statistics import fixture

TZ=ZoneInfo('Asia/Taipei')
class HomeRefreshIntegration(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.original=db.DATA;db.DATA=Path(self.tmp.name);db.initialize()
 def tearDown(self):
  db.DATA=self.original;self.tmp.cleanup()
 def tick(self,hour=18,minute=0,day=9):return datetime(2026,11,day,hour,minute,tzinfo=TZ)
 def test_all_nonmanual_pages_in_registry(self):
  expected={'追蹤分點','全市場主力','券商關注','個股 RS','每日跟單','ETF RS','Alpha／Beta 排行','主動 ETF 動向','內部人持股異動','殖利率排行','市場觀測','族群流向','每日鉅額交易','權證分析','券商風雲榜','V9 台指期','月份漲跌統計','融資維持率統計','景氣循環分析','預估本益比河流圖'}
  self.assertEqual(set(full_update.NAMES),expected);self.assertEqual(len(full_update.NAMES),len(expected))
  for version in ('V3','V6','V10','V11'):self.assertFalse(any(version in name for name in full_update.NAMES))
 def test_scheduler_runs_once_daily_and_persists_attempt_even_when_partial(self):
  db.update_settings({'schedule_enabled':True,'schedule_time':'18:00'})
  with patch.object(full_update,'status',return_value={'status':'partial'}),patch.object(full_update,'start') as start:
   self.assertFalse(service.scheduler_tick(self.tick(17,59),db.settings()))
   self.assertTrue(service.scheduler_tick(self.tick(),db.settings()))
   self.assertFalse(service.scheduler_tick(self.tick(18,30),db.settings()))
   self.assertFalse(service.scheduler_tick(self.tick(19),db.settings()))
   self.assertFalse(service.scheduler_tick(self.tick(23),db.settings()))
   self.assertTrue(service.scheduler_tick(self.tick(day=10),db.settings()))
   self.assertEqual(start.call_count,2)
 def test_dispatch_failure_is_not_automatically_retried(self):
  db.update_settings({'schedule_enabled':True,'schedule_time':'18:00'})
  with patch.object(full_update,'status',return_value={'status':'partial'}),patch.object(full_update,'start',side_effect=RuntimeError('dispatch failed')) as start:
   with self.assertRaises(RuntimeError):service.scheduler_tick(self.tick(),db.settings())
   self.assertFalse(service.scheduler_tick(self.tick(18,1),db.settings()))
   self.assertFalse(service.scheduler_tick(self.tick(22),db.settings()))
   start.assert_called_once()
 def test_weekend_disabled_complete_and_running_do_not_start(self):
  cfg={'schedule_enabled':True,'schedule_time':'18:00'}
  with patch.object(full_update,'status',return_value={'status':'running'}),patch.object(full_update,'start') as start:
   self.assertFalse(service.scheduler_tick(self.tick(),cfg));start.assert_not_called()
  with patch.object(full_update,'status',return_value={'status':'complete','finished_at':'2026-11-09T18:02:00+08:00'}),patch.object(full_update,'start') as start:
   self.assertFalse(service.scheduler_tick(self.tick(19),dict(cfg,home_full_success_date='2026-11-09')))
   self.assertFalse(service.scheduler_tick(self.tick(day=7),cfg))
   self.assertFalse(service.scheduler_tick(self.tick(),dict(cfg,schedule_enabled=False)));start.assert_not_called()
 def test_single_module_success_does_not_skip_the_daily_full_update(self):
  with patch.object(full_update,'status',return_value={'status':'complete','finished_at':'2026-11-09T18:01:00+08:00'}),patch.object(full_update,'start') as start:
   self.assertTrue(service.scheduler_tick(self.tick(19),{'schedule_enabled':True,'schedule_time':'18:00'}))
   start.assert_called_once()
 def test_added_modules_dispatch_to_their_real_updaters(self):
  with patch.object(full_update,'_wait',return_value='complete') as wait:
   self.assertEqual(full_update._run_one('融資維持率統計'),'complete')
   self.assertIs(wait.call_args.args[1],refresh.refresh)
   self.assertEqual(full_update._run_one('月份漲跌統計'),'complete')
   self.assertIs(wait.call_args.args[1],full_update.monthly_statistics.refresh)
   self.assertEqual(full_update._run_one('景氣循環分析'),'complete')
   self.assertIs(wait.call_args.args[1],full_update.business_cycle.refresh)
 def test_forward_pe_runs_after_official_sources_and_calls_once(self):
  self.assertGreater(full_update.NAMES.index('預估本益比河流圖'),full_update.NAMES.index('融資維持率統計'))
  with patch.object(full_update.forward_pe_refresh,'update_home',return_value={'status':'partial','message':'EPS待補','detail':{'action':'unchanged'}}) as update,patch.object(full_update,'_module') as report:
   self.assertEqual(full_update._run_one('預估本益比河流圖'),'partial')
   update.assert_called_once();self.assertEqual(report.call_args.args[2],'EPS待補')
 def load_sources(self,day,missing=None):
  src.initialize()
  for market,kind,name in [('TWSE','credit','listed-margin'),('TWSE','quotes','listed-prices'),('TWSE','shares','listed-shares'),('TPEX','credit','tpex-margin'),('TPEX','quotes','listed-prices')]:
   if (market,kind)==missing:continue
   value=copy.deepcopy(fixture(name));value['date']=day.replace('-','')
   if market=='TPEX' and kind=='quotes':
    t=next(t for t in value['tables'] if '每日收盤行情' in t.get('title','') and t.get('data'));t['title']='上櫃股票行情';t['fields']=[{'證券代號':'代號','證券名稱':'名稱','收盤價':'收盤','開盤價':'開盤','最高價':'最高','最低價':'最低','成交金額':'成交金額(元)'}.get(f,f) for f in t['fields']]
    value['tables']=[t]
   src.save_snapshot(value,market,kind,day,'https://official.invalid/'+name,json.dumps(value).encode(),src.now())
  with src.connect(write=True) as c:c.execute('INSERT OR REPLACE INTO calendar VALUES(?,?)',(day,'fixture'))
 def test_live_replay_handles_gaps_keeps_estimates_and_never_opens_frozen_research(self):
  self.load_sources('2026-11-04');self.load_sources('2026-11-05',missing=('TPEX','quotes'))
  with patch.object(builder,'INPUT',Path(self.tmp.name)/'missing-frozen.sqlite3'),patch.object(builder,'OUT',Path(self.tmp.name)/'missing-research'):
   meta=builder.build(live=True)
  self.assertFalse(meta['model_ready']);self.assertEqual(meta['scope'],'observations_not_trading_signals')
  self.assertEqual(refresh.latest_date(),'2026-11-04')
  with sqlite3.connect(src.folder()/'observations.sqlite3') as c:
   self.assertEqual(c.execute("SELECT available FROM days WHERE day='2026-11-05' AND market='TPEX'").fetchone()[0],0)
   self.assertGreater(c.execute('SELECT COUNT(*) FROM observations WHERE loan_a>0 AND loan_b>0').fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM observations WHERE day='2026-11-05' AND market='TPEX'").fetchone()[0],0)
  self.assertFalse((Path(self.tmp.name)/'missing-research').exists())

if __name__=='__main__':unittest.main()
