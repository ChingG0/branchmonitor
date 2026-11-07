import unittest
from backend import box_strategy as box

class BoxStrategyTests(unittest.TestCase):
    def test_roc_date_parser(self):
        self.assertEqual(box.roc_day('1150922'),'2026-09-22')
        self.assertEqual(box.roc_day('115/09/22'),'2026-09-22')
    def test_percentile_uses_midrank(self):
        self.assertEqual(box.percentile([1,2,2,4],2),50)

    def test_short_history_is_near_miss_without_invented_levels(self):
        rows=[]
        for i in range(20):
            rows.append({'date':f'2026-01-{i+1:02d}','open':100,'high':102,'low':99,'close':101,'volume':1000,'adj':101,'adj_open':100,'adj_high':102,'adj_low':99})
        result=box.inspect({'symbol':'2330','name':'台積電'}, {'rows':rows},'2026-01-20',{60:[],120:[]})
        self.assertFalse(result['signal']);self.assertIn('日線不足',result['failures'][0]);self.assertNotIn('take_profit',result)

if __name__=='__main__':unittest.main()
