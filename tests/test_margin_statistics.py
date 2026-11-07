import copy,gzip,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import margin_statistics as m,margin_statistics_sources as s
FIX=Path(__file__).parent/'fixtures/margin_statistics'
def fixture(name):return json.loads(gzip.decompress((FIX/(name+'.json.gz')).read_bytes()))
class SourceTests(unittest.TestCase):
 def test_official_market_totals_and_offset_not_double_subtracted(self):
  for market,name,expected in [('TWSE','listed-margin',596945870000),('TPEX','tpex-margin',190919219000)]:
   rows,total=m.parse_credit(fixture(name),market,'2026-11-04')
   self.assertEqual(total['loan_amount'],expected);self.assertEqual(sum(r[6] for r in rows),total['units_balance']);self.assertTrue(all(r[-1]==0 for r in rows));self.assertTrue(any(r[12]>0 for r in rows))
 def test_short_buy_sell_mapping(self):
  v=fixture('listed-margin');r=m.parse_credit(v,'TWSE','2026-11-04')[0][0];t=next(t for t in v['tables'] if t.get('fields',[None])[0]=='代號')['data'][0]
  self.assertEqual(r[8],m.number(t[9],True));self.assertEqual(r[9],m.number(t[8],True))
 def test_wrong_date_and_failed_source_rejected(self):
  with self.assertRaises(ValueError):m.parse_credit(fixture('listed-margin'),'TWSE','2026-11-03')
  with self.assertRaises(ValueError):m.parse_credit({'stat':'error','date':'20261104'},'TWSE','2026-11-04')
 def test_thousand_unit_rounding_only(self):
  v=fixture('listed-margin');loan=next(r for r in v['tables'][0]['data'] if r[0]=='融資金額(仟元)');loan[5]=str(m.number(loan[5],True)+1)
  m.parse_credit(v,'TWSE','2026-11-04');loan[5]=str(m.number(loan[5],True)+5)
  with self.assertRaises(ValueError):m.parse_credit(v,'TWSE','2026-11-04')
 def test_detail_total_missing_and_duplicate_rejected(self):
  v=fixture('listed-margin');t=next(t for t in v['tables'] if t.get('fields',[None])[0]=='代號');t['data'].pop()
  with self.assertRaises(ValueError):m.parse_credit(v,'TWSE','2026-11-04')
  v=fixture('tpex-margin');v['tables'][0]['data'].append(v['tables'][0]['data'][0])
  with self.assertRaises(ValueError):m.parse_credit(v,'TPEX','2026-11-04')
 def test_share_and_price_exact_dates_null_not_zero(self):
  v=fixture('listed-prices');t=next(t for t in v['tables'] if '每日收盤行情' in t.get('title','') and t.get('data'));idx=t['fields'].index('收盤價');t['data'][0][idx]='--'
  self.assertIsNone(m.parse_quotes(v,'TWSE','2026-11-04')[0][2])
  self.assertGreater(len(m.parse_shares(fixture('listed-shares'),'2026-11-04')),1000)
  with self.assertRaises(ValueError):m.parse_shares(fixture('listed-shares'),'2026-11-03')
 def test_history_calendar_validation(self):
  v=fixture('calendar-twse');rows=s.calendar_rows(v,'2023-01-01','2023-01-01','2023-01-31');self.assertEqual(rows[0],'2023-01-03');self.assertEqual(len(rows),13)
  with self.assertRaises(ValueError):s.calendar_rows(v,'2023-02-01','2023-01-01','2023-03-01')
 def test_append_revisions_and_readonly_report(self):
  with tempfile.TemporaryDirectory() as tmp:
   m.initialize(tmp);v=fixture('listed-margin');raw=json.dumps(v).encode();id1,_=m.save_snapshot(v,'TWSE','credit','2026-11-04','official',raw,m.now(),store=tmp)
   t=next(t for t in v['tables'] if t.get('fields',[None])[0]=='代號');t['data'][0][-1]+='測試版本';id2,_=m.save_snapshot(v,'TWSE','credit','2026-11-04','official',json.dumps(v).encode(),m.now(),store=tmp)
   self.assertNotEqual(id1,id2)
   with m.connect(tmp) as c:
    self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],2);self.assertEqual(c.execute('SELECT snapshot_id FROM latest').fetchone()[0],id2)
   r=m.report(store=tmp);self.assertFalse(r['model_ready']);self.assertEqual(r['source_versions'],2);self.assertTrue(all(i['close'] is None and i['market_cap'] is None for i in r['items']));self.assertFalse(any('maintenance' in i for i in r['items']))
   with m.connect(tmp) as c:self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],2)
   self.assertTrue(all((Path(tmp)/x).exists() for x in ['sources.sqlite3']))
 def test_missing_report_never_creates_database(self):
  with tempfile.TemporaryDirectory() as tmp:
   self.assertFalse(m.report(store=tmp)['model_ready']);self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_official_protection_stops_host_without_more_requests(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m,'folder',return_value=Path(tmp)),patch.object(s.db,'DATA',Path(tmp)),patch.object(s.requests,'get') as get:
   s._BLOCKED.clear();get.return_value.status_code=428
   try:
    with self.assertRaises(s.SourcePaused):s.fetch('https://www.twse.com.tw/test-source',lambda v:None)
    with self.assertRaises(s.SourcePaused):s.fetch('https://www.twse.com.tw/test-other-day',lambda v:None)
    self.assertEqual(get.call_count,1)
   finally:s._BLOCKED.clear()
 def test_three_year_scope_does_not_count_archived_older_sources(self):
  with tempfile.TemporaryDirectory() as tmp:
   m.initialize(tmp);v=fixture('listed-margin')
   for day in ['2023-01-03','2026-11-04']:
    v['date']=day.replace('-','');m.save_snapshot(v,'TWSE','credit',day,'official',json.dumps(v).encode(),m.now(),store=tmp)
   m.atomic(Path(tmp)/'job.json',{'status':'complete','start':'2023-11-01','end':'2026-11-04'})
   r=m.report(store=tmp);self.assertEqual(r['dates'],['2026-11-04']);self.assertEqual(r['source_versions'],1)
   with m.connect(tmp) as c:self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],2)
 def test_other_not_forced_into_etf_baskets(self):
  for symbol,name in [('00980A','主動台股'),('0052','科技'),('00679B','美債'),('01001T','REIT')]:self.assertEqual(m.classify(symbol,name,'TWSE'),'other')
  self.assertEqual(m.classify('0050','台灣50','TWSE'),'etf_cap');self.assertEqual(m.classify('0056','高股息','TWSE'),'etf_dividend');self.assertEqual(m.classify('00632R','反1','TWSE'),'etf_leverage')
if __name__=='__main__':unittest.main()
