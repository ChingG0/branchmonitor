import unittest,csv,io
from pathlib import Path
from unittest.mock import patch
from backend import market_futures_sources as F,market_monitor as M
P=Path(__file__).parent/'fixtures'/'taifex_market_20261104'
class DatedFuturesTests(unittest.TestCase):
    def test_official_positions_are_exactly_dated_and_net_verified(self):
        a=F.parse_positions((P/'txf.csv').read_bytes(),'2026-11-04','臺股期貨');self.assertEqual(a[0]['net'],-93647)
        with self.assertRaisesRegex(ValueError,'日期'):F.parse_positions((P/'txf.csv').read_bytes(),'2026-11-03','臺股期貨')
        with self.assertRaisesRegex(ValueError,'商品'):F.parse_positions((P/'txf.csv').read_bytes(),'2026-11-04','小型臺指期貨')
    def test_missing_and_duplicate_position_rows_are_rejected(self):
        raw=(P/'txf.csv').read_bytes();txt=raw.decode('cp950')
        with self.assertRaises(ValueError):F.parse_positions(('\n'.join(txt.splitlines()[:2])).encode('cp950'),'2026-11-04','臺股期貨')
        with self.assertRaises(ValueError):F.parse_positions((txt+txt.splitlines()[1]+'\n').encode('cp950'),'2026-11-04','臺股期貨')
    def test_retail_uses_only_day_session_and_unique_expiry_interest(self):
        market=(P/'mtx.csv').read_bytes();inst=(P/'mxf.csv').read_bytes();r=F.parse_retail(market,inst,'2026-11-04')
        a=F.parse_positions(inst,'2026-11-04','小型臺指期貨')
        self.assertGreater(r['total_open_interest'],0);self.assertEqual(r['retail_long'],r['total_open_interest']-sum(x['long'] for x in a));self.assertAlmostEqual(r['value'],(r['retail_long']-r['retail_short'])/r['total_open_interest']*100)
        with self.assertRaises(ValueError):F.parse_retail(market,inst,'2026-11-03')
    def test_blank_yahoo_falls_back_to_dated_official_and_totals(self):
        with patch.object(F,'cash_day',return_value='2026-11-04'),patch.object(M,'fetch',side_effect=ValueError('blank')),patch.object(F,'download',return_value=(P/'txf.csv').read_bytes()):
            r=M.load_card('futures');self.assertEqual(r['source_name'],'臺灣期貨交易所');self.assertEqual(r['as_of'],'2026-11-04');self.assertEqual(r['rows'][-1]['net'],sum(x['net'] for x in r['rows'][:-1]))
    def test_stale_openapi_uses_dated_csv_without_relabeling_old_day(self):
        with patch.object(F,'cash_day',return_value='2026-11-04'),patch.object(M,'fetch',return_value=b'{}'),patch.object(M,'parse_retail_official',return_value={'as_of':'2026-11-03'}),patch.object(F,'download',side_effect=lambda path,*args:(P/('mtx.csv' if path=='futDataDown' else 'mxf.csv')).read_bytes()):
            r=M.load_card('retail');self.assertEqual(r['as_of'],'2026-11-04');self.assertIn('非三大法人',r['source_name'])
if __name__=='__main__':unittest.main()
