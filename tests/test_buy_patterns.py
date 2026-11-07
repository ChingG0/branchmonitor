import unittest
from datetime import date, timedelta
from backend.buy_patterns import buying_patterns


class BuyPatternsTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2026, 1, 2)+timedelta(days=i)).isoformat()
                     for i in range(40) if (date(2026, 1, 2)+timedelta(days=i)).weekday()<5]
        self.days = self.days[:22]
        self.coverage = [dict(symbol='A', start=self.days[0], end=self.days[-1])]

    def row(self, i, buy=100, sell=0, net=None):
        return dict(date=self.days[i], symbol='A', name='測試', buy=buy, sell=sell,
                    net=buy-sell if net is None else net)

    def run_pattern(self, rows, coverage=None, days=None, complete=True):
        days = days or self.days
        prices = [dict(date=d, symbol='A', close=10) for d in self.days]
        return buying_patterns(rows, prices, days, days[-1], complete,
                               self.coverage if coverage is None else coverage, None)

    def test_three_sessions_across_weekend_and_future_excluded(self):
        days = self.days[:3]  # Friday, Monday, Tuesday
        r = self.run_pattern([self.row(i) for i in range(4)], days=days)
        row = r['streak_buys'][0]
        self.assertEqual(row['streak'], 3)
        self.assertEqual(row['streak_net'], 300)
        self.assertEqual(row['streak_value'], 3000000)
        self.assertTrue(row['streak_at_least'])

    def test_zero_net_or_missing_day_breaks_streak(self):
        for middle in [[], [self.row(-2, buy=10, sell=10)]]:
            r = self.run_pattern([self.row(-3), *middle, self.row(-1)])
            self.assertEqual(r['streak_buys'], [])

    def test_twenty_verified_quiet_days_can_contain_sales(self):
        r = self.run_pattern([self.row(0), self.row(10, buy=0, sell=15), self.row(-1)])
        self.assertEqual(len(r['fresh_buys']), 1)
        self.assertEqual(r['fresh_buys'][0]['quiet_start'], self.days[1])
        self.assertEqual(r['fresh_buys'][0]['buy_value'], 1000000)

    def test_gross_buy_during_net_sale_disqualifies_quiet_window(self):
        r = self.run_pattern([self.row(1, buy=1, sell=100), self.row(-1)])
        self.assertEqual(r['fresh_buys'], [])

    def test_unverified_missing_history_is_never_zero(self):
        r = self.run_pattern([self.row(-1)], coverage=[])
        self.assertEqual(r['fresh_buys'], [])
        self.assertEqual(r['buy_pattern_status']['unverified_candidates'], 1)

    def test_adjacent_intervals_cover_sessions_but_one_gap_fails(self):
        intervals = [dict(symbol='A', start=self.days[0], end=self.days[10]),
                     dict(symbol='A', start=self.days[11], end=self.days[-1])]
        self.assertEqual(len(self.run_pattern([self.row(-1)], coverage=intervals)['fresh_buys']), 1)
        intervals[1]['start'] = self.days[12]
        self.assertEqual(self.run_pattern([self.row(-1)], coverage=intervals)['fresh_buys'], [])

    def test_short_or_incomplete_calendar_never_claims_twenty_days(self):
        r = self.run_pattern([self.row(19)], days=self.days[:20])
        self.assertEqual(r['fresh_buys'], [])
        self.assertEqual(r['buy_pattern_status']['prior_sessions'], 19)
        r = self.run_pattern([self.row(-2), self.row(-1)], complete=False)
        self.assertEqual(r['streak_buys'], [])
        self.assertEqual(r['fresh_buys'], [])
