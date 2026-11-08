import unittest
from backend import forward_pe_sources as P

class Parsers(unittest.TestCase):
 def test_weight_date_and_two_column_table(self):
  raw='<p>資料日期：2026/10/30</p><table><tr><th>市值佔 大盤比重</th></tr><tr><td>1</td><td>2330</td><td>台積電</td><td>42%</td><td>2</td><td>2454</td><td>聯發科</td><td>4%</td></tr></table>'
  day,rows=P.parse_weights(raw);self.assertEqual(day,'2026-10-30');self.assertEqual([r['symbol'] for r in rows],['2330','2454'])
  with self.assertRaises(ValueError):P.parse_weights(raw.replace('資料日期：2026/10/30',''))
 def document(self):
  return '''<meta property="article:published_time" content="2026-11-02T00:11:12Z"><h1>...</h1><h1>鉅亨速報 - Factset 最新調查：聯發科(2454-TW)EPS預估上修至70.87元</h1><p>共27位分析師，對聯發科做出2026年EPS預估</p><p>市場預估EPS</p><table><tr><td>預估值</td><td>2026年(前值)</td><td>2027年</td></tr><tr><td>最高值</td><td>77.9(77.9)</td><td>197.7</td></tr><tr><td>最低值</td><td>60.43(60.43)</td><td>68.56</td></tr><tr><td>平均值</td><td>70.49(69.53)</td><td>146.99</td></tr><tr><td>中位數</td><td>70.87(69.4)</td><td>146.97</td></tr></table><p>歷史獲利表現</p><table><tr><td>EPS</td><td>105319255</td></tr></table>'''
 def test_only_forecast_eps_table_is_read_and_prior_not_backdated(self):
  stamp,rows=P.parse_cnyes_eps(self.document());self.assertEqual(len(rows),4);self.assertEqual(stamp,'2026-11-02T08:11:12+08:00')
  one=next(r for r in rows if r['period']=='2026' and r['statistic']=='median');self.assertEqual(one['value'],70.87);self.assertEqual(one['reported_prior_value'],69.4);self.assertIsNone(one['forecast_as_of']);self.assertEqual(one['analyst_count'],27)
  two=next(r for r in rows if r['period']=='2027' and r['statistic']=='median');self.assertIsNone(two['analyst_count']);self.assertEqual(two['value'],146.97)
 def test_ambiguous_year_missing_value_and_missing_publication_rejected(self):
  for a,b in [('2027年','未來12個月'),('70.87(69.4)','--'),('article:published_time','other')]:
   with self.assertRaises(ValueError):P.parse_cnyes_eps(self.document().replace(a,b))
if __name__=='__main__':unittest.main()
