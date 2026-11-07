import unittest
from unittest.mock import patch

from backend import v9_tx


class V9TxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.futures, cls.index = v9_tx._seed()
        cls.history = v9_tx.TXHistory(cls.futures, cls.index)

    def test_frozen_research_results_match_official_history(self):
        ytd = self.history.simulate('2026-01-01', '2026-10-07')
        hold = self.history.simulate('2026-01-01', '2026-10-07', defensive=False)
        bear = self.history.simulate('2022-01-01', '2022-12-31')
        self.assertAlmostEqual(ytd['return_pct'], 74.402, places=3)
        self.assertAlmostEqual(ytd['taiex_pct'], 71.6474, places=3)
        self.assertAlmostEqual(hold['return_pct'], 68.4813, places=3)
        self.assertAlmostEqual(bear['return_pct'], -25.6295, places=3)

    def test_close_signal_only_trades_at_following_opens(self):
        self.assertEqual(self.history.signals['2026-07-27'], 1)
        self.assertEqual(self.history.signals['2026-07-28'], -1)
        self.assertEqual(self.history.signals['2026-07-29'], 1)
        actions = {row['date']: row for row in self.history.simulate(
            '2026-01-01', '2026-10-07')['activity']}
        self.assertEqual(actions['2026-07-29']['signal_date'], '2026-07-28')
        self.assertEqual(actions['2026-07-29']['action'], '多單平倉／建立空單')
        self.assertEqual(actions['2026-07-30']['action'], '空單回補／建立多單')

    def test_new_index_bar_without_tx_cannot_be_skipped(self):
        index = {**self.index, '2026-10-08': dict(self.index['2026-10-07'])}
        history = v9_tx.TXHistory(self.futures, index)
        self.assertEqual(history.days[-1], '2026-10-07')
        self.assertEqual(history.missing_dates, ['2026-10-08'])
        with patch.object(v9_tx, '_loaded', return_value=(self.futures, index)):
            self.assertEqual(v9_tx.latest_date(), '2026-10-07')


if __name__ == '__main__':
    unittest.main()
