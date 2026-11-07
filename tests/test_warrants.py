import json
import unittest

from backend import warrants


class WarrantAnalysisTest(unittest.TestCase):
    def test_detail_recomputes_normalized_greeks_and_score(self):
        payload = {'Data': {'WarrantInfo': {
            'CommKey': '081988', 'CommName': '騰輝電群益63購02', 'SalePr': 5.9, 'SaleQty': 105,
            'BuyPr1': 2.4, 'SellPr1': 2.42, 'BuyQty1': 4, 'SellQty1': 8,
            'LimitDe': '2027-03-23T00:00:00', 'LastTransDe': '2027-03-19T00:00:00',
            'UserRate': .05, 'StrikePr': 400, 'CallorPut': 1, 'PrOutIn': 9.75,
            'LastDays': 169, 'BIV': '83.60', 'SIV': '84.00', 'AorE': '美式',
            'StockName': '騰輝電子-KY', 'StockCommkey': '6672',
            'StockInfo': {'CommKey': '6672', 'CommName': '騰輝電子-KY', 'SalePr': 439, 'CFPersent': 8.26},
        }, 'HistoryTicks': [{'Date': '2026-10-02T00:00:00'}]}}
        detail, day = warrants.parse_warrant_detail(json.dumps(payload).encode(), '081988')
        self.assertEqual(day, '2026-10-02')
        self.assertEqual(detail['underlying']['code'], '6672')
        self.assertGreater(detail['effective_leverage'], 3)
        self.assertLess(detail['greeks']['theta'], 0)
        self.assertEqual(detail['score']['spread'], .83)

    def test_stock_list_filters_under_60_days_and_penalizes_missing_quotes(self):
        payload = {'Data': {'Stock': {'CommKey': '6672', 'CommName': '騰輝電子-KY', 'SalePr': '439', 'CFPersent': 8.26},
            'Warrants': {'Call': [
                {'CommKey': 'SHORT', 'CommName': '短天期', 'RemainDays': '59'},
                {'CommKey': 'GOOD1', 'CommName': '完整報價', 'BuyPr': '2.00', 'SellPr': '2.02', 'SalePr': '2.01', 'SIV': '45', 'PrOutIn': '5', 'RemainDays': '120', 'ExecutiveRatio': '.05', 'StrikePrice': '420'},
                {'CommKey': 'BAD01', 'CommName': '沒有賣價', 'BuyPr': '2.00', 'SellPr': '--', 'SalePr': '2.00', 'SIV': '--', 'PrOutIn': '5', 'RemainDays': '120', 'ExecutiveRatio': '.05', 'StrikePrice': '420'},
            ], 'Put': []}}}
        quick = {'Data': {'BasicInfo': [], 'OtherInfo': [
            {'CommKey': 'GOOD1', 'ExpDate': '2027-02-02T00:00:00'}], 'PrQtyInfo': [
            {'CommKey': 'GOOD1', 'SaleQty': 88}]}}
        result = warrants.parse_stock_warrants(json.dumps(payload).encode(), '6672', json.dumps(quick).encode())
        self.assertEqual(result['eligible'], 2)
        self.assertEqual(result['rows'][0]['code'], 'GOOD1')
        self.assertEqual(result['rows'][0]['volume'], 88)
        self.assertEqual(result['rows'][0]['expiry'], '2027-02-02')
        self.assertIsNotNone(result['rows'][0]['spread_leverage'])
        self.assertGreater(result['rows'][0]['score']['value'], result['rows'][1]['score']['value'])

    def test_parse_weekly_broker_flow_and_estimated_amount(self):
        html = '''<div>騰輝電群益63購02(081988) 最後更新日：2026/10/02</div><table id="oMainTable">
          <tr><td>買超券商</td><td>買進</td><td>賣出</td><td>買超</td><td>佔比</td><td>賣超券商</td><td>買進</td><td>賣出</td><td>賣超</td><td>佔比</td></tr>
          <tr><td>永豐金-潮州</td><td>100</td><td>50</td><td>50</td><td>13.33%</td><td>國票證券</td><td>32</td><td>78</td><td>46</td><td>12.27%</td></tr>
          <tr><td>合計買超張數</td><td>57</td><td>合計賣超張數</td><td>57</td></tr>
          <tr><td>平均買超成本</td><td>5.06</td><td>平均賣超成本</td><td>3.10</td></tr></table>'''
        result = warrants.parse_weekly_flow(html, '081988')
        self.assertEqual(result['date'], '2026-10-02')
        self.assertEqual(result['buys'][0]['estimated_amount'], 253000)
        self.assertEqual(result['sells'][0]['net'], 46)

    def test_market_making_warnings_are_objective_signals(self):
        info = {'Invertory': 500, 'OutSideQty': 80, 'HasShow': True}
        warnings, inventory, outstanding = warrants._market_warnings(
            info, {'spread': 12}, 40, 55, 44,
            [{'Date': '2026-10-01', 'BIV': 52}, {'Date': '2026-10-02', 'BIV': 40}])
        codes = {row['code'] for row in warnings}
        self.assertTrue({'wide_spread', 'iv_gap', 'ask_iv_up', 'bid_iv_down',
                         'low_inventory', 'low_outstanding'}.issubset(codes))
        self.assertEqual(inventory, 500)
        self.assertEqual(outstanding, 80)

    def test_valid_null_data_means_no_related_warrants(self):
        result = warrants.parse_stock_warrants(b'{"Data":null}', '1234')
        self.assertTrue(result['no_warrants'])
        self.assertEqual(result['message'], '沒有相關權證')
        self.assertEqual(result['rows'], [])

    def test_missing_data_key_is_still_a_format_error(self):
        with self.assertRaises(ValueError):
            warrants.parse_stock_warrants(b'{}', '1234')

    def test_empty_quick_filter_does_not_hide_valid_stock_without_warrants(self):
        primary = {'Data': {'Warrants': {'Call': [], 'Put': []},
                            'Stock': {'CommKey': '3150', 'CommName': '鈺寶-創', 'SalePr': '15.20'}}}
        quick = {'ErrorMessage': '無符合條件的資料'}
        result = warrants.parse_stock_warrants(json.dumps(primary).encode(), '3150',
                                                json.dumps(quick, ensure_ascii=False).encode())
        self.assertTrue(result['no_warrants'])
        self.assertEqual(result['name'], '鈺寶-創')
        self.assertEqual(result['message'], '沒有相關權證')


if __name__ == '__main__':
    unittest.main()
