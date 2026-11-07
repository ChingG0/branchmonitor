import unittest

from backend.insider_changes import _with_price_history


def change(month, shares, holder):
    return {'year': 2026, 'month': month, 'market': 'sii', 'symbol': '2330',
            'holder': holder, 'change_shares': shares}


class InsiderPriceHistoryTest(unittest.TestCase):
    def test_same_company_month_is_one_trial_and_never_uses_later_months(self):
        rows = [change(1, 1000, '甲'), change(1, 2000, '乙'), change(2, 3000, '甲'),
                change(2, -1000, '丙'), change(3, 5000, '甲'), change(3, -2000, '丙')]
        prices = {('2026-03', '2330'): {'start_open': 100, 'start_date': '2026-03-02',
                                      'end_close': 110, 'end_date': '2026-03-31'},
                  ('2026-04', '2330'): {'start_open': 110, 'start_date': '2026-04-01',
                                      'end_close': 105, 'end_date': '2026-04-30'}}
        final_days = {'2026-03': '2026-03-31', '2026-04': '2026-04-30'}
        _with_price_history(rows, prices, final_days)

        self.assertEqual(rows[0]['next_month_price']['direction'], 'up')
        self.assertEqual(rows[0]['next_month_price']['start_date'], '2026-03-02')
        self.assertEqual(rows[0]['next_month_price']['start_open'], 100)
        self.assertEqual(rows[1]['history']['total'], 0)
        self.assertEqual(rows[2]['history'], {'wins': 1, 'total': 1, 'rate_pct': 100.0})
        self.assertEqual(rows[4]['history'], {'wins': 1, 'total': 2, 'rate_pct': 50.0})
        self.assertEqual(rows[4]['next_month_price']['status'], 'pending')
        self.assertEqual(rows[5]['history'], {'wins': 1, 'total': 1, 'rate_pct': 100.0})

    def test_unlisted_or_missing_prices_do_not_enter_denominator(self):
        rows = [change(1, 1000, '甲'), change(2, 1000, '甲')]
        rows.append({**change(1, 1000, '乙'), 'market': 'pub'})
        _with_price_history(rows, {('2026-03', '2330'): {'start_open': 100, 'start_date': '2026-03-02'}},
                            {'2026-03': '2026-03-31', '2026-04': '2026-04-30'})
        self.assertEqual(rows[0]['next_month_price']['status'], 'missing')
        self.assertEqual(rows[1]['history']['total'], 0)
        self.assertEqual(rows[2]['next_month_price']['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
