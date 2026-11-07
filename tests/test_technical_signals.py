import unittest
from backend import technical_signals as t


class TechnicalSignalTests(unittest.TestCase):
    def rows(self,rising=True,count=70):
        values=[]
        for index in range(count):
            close=50+index*.6 if rising else 100-index*.6
            values.append({'date':f'2026-{1+index//28:02d}-{1+index%28:02d}','high':close+1,'low':close-1,'close':close})
        return values

    def test_uptrend_passes_dmi_and_sar_confirms_without_future_data(self):
        result=t.calculate(self.rows())
        self.assertTrue(result['available']);self.assertTrue(result['dmi_pass'])
        self.assertGreater(result['plus_di'],result['minus_di']);self.assertGreaterEqual(result['adx'],20)
        self.assertEqual(result['status'],'bullish_continuation');self.assertTrue(result['sar']['bullish'])
        self.assertLess(result['sar']['value'],result['close'])

    def test_downtrend_fails_required_dmi_but_sar_remains_descriptive(self):
        result=t.calculate(self.rows(False))
        self.assertTrue(result['available']);self.assertFalse(result['dmi_pass'])
        self.assertLessEqual(result['plus_di'],result['minus_di']);self.assertEqual(result['status'],'bearish')
        self.assertFalse(result['sar']['bullish'])

    def test_early_turn_and_confirmation_are_distinct(self):
        early=t.classify(18,17,12,15,16,13,1,0)
        self.assertEqual(early['status'],'early_turn');self.assertTrue(early['cross_up_today'])
        confirmed=t.classify(20,15,14,19,16,13,2,1)
        self.assertEqual(confirmed['status'],'early_confirmed');self.assertFalse(confirmed['cross_up_today'])

    def test_short_or_discontinuous_history_is_not_treated_as_neutral(self):
        short=t.calculate(self.rows(count=20));self.assertFalse(short['available']);self.assertIn('不足',short['reason'])
        rows=self.rows();rows[-1]={**rows[-1],'high':200,'low':190,'close':195}
        broken=t.calculate(rows);self.assertFalse(broken['available']);self.assertIn('不連續',broken['reason'])


if __name__=='__main__':unittest.main()
