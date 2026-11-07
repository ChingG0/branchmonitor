import json
import gzip
import hashlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from backend import sources
from backend.sources import SourceError,parse_ranking,parse_history,parse_market,parse_tpex_openapi_market,validate_identity

FIXTURES=Path(__file__).parent/'fixtures'


class MoneyDJTests(unittest.TestCase):
    def setUp(self):
        self.html=(FIXTURES/'ranking_lots.html').read_text()

    def test_real_ranking_with_stock_scripts_and_fund_links(self):
        day,rows=parse_ranking(self.html,'2026-09-07','E','9300','9339')
        self.assertEqual(day,'2026-09-07')
        self.assertEqual(len(rows),100)
        self.assertEqual(next(r for r in rows if r['symbol']=='00664R')['net'],400)
        self.assertEqual(next(r for r in rows if r['symbol']=='3189')['name'],'景碩')

    def test_big5_bytes(self):
        self.assertEqual(len(parse_ranking(self.html.encode('cp950',errors='replace'),'2026-09-07')[1]),100)

    def test_rejects_wrong_query_day(self):
        with self.assertRaises(SourceError):parse_ranking(self.html,'2026-09-04')

    def test_rejects_wrong_actual_data_day(self):
        with self.assertRaises(SourceError):parse_ranking(self.html.replace('資料日期：20260907','資料日期：20260904'),'2026-09-07')

    def test_rejects_wrong_branch(self):
        with self.assertRaises(SourceError):parse_ranking(self.html,'2026-09-07','E','9800','9800')

    def test_encoded_branch_hex_case_only_is_equivalent(self):
        page="GenFundCorpCombo('9B00','003900420032005A','frm')"
        validate_identity(page,'9B00','003900420032005a')
        for broker,branch in [('9B01','003900420032005a'),('9B00','003900420032006a'),('9B00','9b2Z')]:
            with self.assertRaises(SourceError):validate_identity(page,broker,branch)
        with self.assertRaises(SourceError):validate_identity("GenFundCorpCombo('9B00','9b2Z','frm')",'9B00','9B2Z')

    def test_history_preserves_independently_rounded_net(self):
        html=(FIXTURES/'history.html').read_text()
        rows=parse_history(html,'2330','台積電','2026-08-01','2026-09-07','9300','9339')
        row=next(r for r in rows if r['date']=='2026-09-03')
        self.assertEqual((row['buy'],row['sell'],row['net']),(5,1,3))
        self.assertEqual(len(rows),26)

    def test_parse_error_cannot_become_zero_activity(self):
        with self.assertRaises(SourceError):parse_ranking('<html>maintenance</html>','2026-09-07')
        with self.assertRaises(SourceError):parse_history('<html>maintenance</html>','2330','台積電','2026-08-01','2026-09-07')


class ExchangeTests(unittest.TestCase):
    def test_cached_maintenance_page_is_replaced_from_official_twse_host(self):
        url=('https://www.twse.com.tw/rwd/zh/exRight/TWT49U?'
             'startDate=20251231&endDate=20261013&response=json')
        primary=Mock(content=b'<html>website maintenance</html>')
        primary.raise_for_status=Mock()
        fallback=Mock(content=b'{"stat":"OK","tables":[]}')
        fallback.raise_for_status=Mock()
        with tempfile.TemporaryDirectory() as folder,patch.object(sources,'DATA',Path(folder)),\
             patch.object(sources.requests,'get',side_effect=[primary,fallback]) as request,\
             patch.object(sources.time,'sleep'):
            cache=Path(folder)/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.gz')
            cache.parent.mkdir()
            cache.write_bytes(gzip.compress(primary.content))
            self.assertEqual(sources.get(url),fallback.content)
            self.assertEqual(sources.get(url),fallback.content)
            self.assertEqual(request.call_count,2)
            self.assertTrue(request.call_args_list[-1].args[0].startswith('https://wwwc.twse.com.tw/'))
            self.assertEqual(gzip.decompress(cache.read_bytes()),fallback.content)

    def test_tpex_download_falls_back_to_verified_warm_session(self):
        failed=Mock(side_effect=sources.requests.exceptions.SSLError('temporary'))
        warm=Mock(content=b'warm');warm.raise_for_status=Mock()
        result=Mock(content=b'{"stat":"ok"}');result.raise_for_status=Mock()
        session=Mock();session.get.side_effect=[warm,result]
        with tempfile.TemporaryDirectory() as folder,patch.object(sources,'DATA',Path(folder)),\
             patch.object(sources.requests,'get',failed),patch.object(sources.requests,'Session',return_value=session),\
             patch.object(sources.time,'sleep'):
            raw=sources.get('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?response=json&date=2026%2F01%2F02',True)
        self.assertEqual(raw,result.content)
        self.assertEqual(failed.call_count,3);self.assertEqual(session.get.call_count,2)
        self.assertNotIn('verify',session.get.call_args_list[0].kwargs)

    def test_non_tpex_failure_does_not_use_fallback(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(sources,'DATA',Path(folder)),\
             patch.object(sources.requests,'get',side_effect=sources.requests.exceptions.ConnectionError('temporary')),\
             patch.object(sources.requests,'Session') as session,patch.object(sources.time,'sleep'):
            with self.assertRaises(SourceError):sources.get('https://example.invalid/data',True)
        session.assert_not_called()

    def test_exchange_event_endpoints_also_use_verified_warm_session(self):
        for url in ['https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?response=json',
                    'https://www.twse.com.tw/rwd/zh/split/TWTCAU?response=json']:
            response=Mock(content=b'{"stat":"ok"}');response.raise_for_status=Mock()
            session=Mock();session.get.return_value=response
            with tempfile.TemporaryDirectory() as folder,patch.object(sources,'DATA',Path(folder)),\
                 patch.object(sources.requests,'get',side_effect=sources.requests.exceptions.ChunkedEncodingError('temporary')),\
                 patch.object(sources.requests,'Session',return_value=session),patch.object(sources.time,'sleep'):
                self.assertEqual(sources.get(url,True),response.content)
            self.assertEqual(session.get.call_args_list[-1].args[0],url)
            self.assertNotIn('verify',session.get.call_args_list[-1].kwargs)

    def test_tpex_empty_weekend_is_not_trading_data(self):
        data={'stat':'ok','date':'20260906','tables':[{'fields':['代號','名稱','收盤'],'data':[]}]}
        self.assertEqual(parse_market(data,'2026-09-06','TPEX'),[])

    def test_missing_price_is_not_zero_and_warrants_are_excluded(self):
        data={'stat':'ok','date':'20260907','tables':[{'fields':['代號','名稱','收盤','成交股數'],
              'data':[['2330','測試','--','1,000'],['6488','環球晶','971.00','10,000'],['700001','權證','1.00','2,000']]}]}
        result=parse_market(data,'2026-09-07','TPEX')
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['symbol'],'6488')
        self.assertEqual(result[0]['volume'],10000)

    def test_wrong_exchange_date_rejected(self):
        with self.assertRaises(SourceError):parse_market({'stat':'OK','date':'20260904','tables':[]},'2026-09-07','TWSE')

    def test_tpex_openapi_snapshot_requires_exact_date(self):
        payload=[{'Date':'1151002','SecuritiesCompanyCode':'6488','CompanyName':'環球晶',
                  'Close':'450.5','Open':'445','High':'455','Low':'443','TradingShares':'1234000'}]
        # The parser's production completeness guard is tested with a realistic
        # universe while preserving one row whose field mapping is asserted.
        payload += [dict(payload[0],SecuritiesCompanyCode=str(code)) for code in range(1000,1300)]
        rows=parse_tpex_openapi_market(payload,'2026-10-02')
        row=next(item for item in rows if item['symbol']=='6488')
        self.assertEqual((row['close'],row['volume'],row['market']),(450.5,1234000,'TPEX'))
        with self.assertRaises(SourceError):parse_tpex_openapi_market(payload,'2026-10-01')

    def test_tpex_market_uses_official_openapi_only_after_primary_failure(self):
        payload=[{'Date':'1151002','SecuritiesCompanyCode':str(code),'CompanyName':'測試',
                  'Close':'10','Open':'9','High':'11','Low':'9','TradingShares':'1000'}
                 for code in range(1000,1301)]
        with patch.object(sources,'get',side_effect=[SourceError('HTTP 520'),json.dumps(payload).encode()] ) as fetch:
            rows=sources.market_prices('2026-10-02','TPEX',refresh=True)
        self.assertEqual(len(rows),301)
        self.assertEqual(fetch.call_args_list[-1].args[0],'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes')
        self.assertTrue(fetch.call_args_list[-1].kwargs['refresh'])

    def test_tpex_market_reuses_date_verified_openapi_after_reconnect_failure(self):
        payload=[{'Date':'1151002','SecuritiesCompanyCode':str(code),'CompanyName':'測試',
                  'Close':'10','Open':'9','High':'11','Low':'9','TradingShares':'1000'}
                 for code in range(1000,1301)]
        with patch.object(sources,'get',side_effect=[SourceError('HTTP 520'),SourceError('temporary'),json.dumps(payload).encode()]) as fetch:
            rows=sources.market_prices('2026-10-02','TPEX',refresh=True)
        self.assertEqual(len(rows),301)
        self.assertFalse(fetch.call_args_list[-1].kwargs['refresh'])


if __name__=='__main__':unittest.main()
