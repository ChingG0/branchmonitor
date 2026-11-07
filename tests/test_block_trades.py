import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import block_trades as b,db

class BlockTradesTest(unittest.TestCase):
 def payload(self):return json.dumps({'stat':'OK','date':'20260922','fields':['證券代號','證券名稱','交易別','成交價','成交股數','成交金額'],'data':[['3189','景碩','逐筆交易','900.00','400,000','360,000,000'],['3189','景碩','逐筆交易','890.00','100,000','89,000,000'],['2330','台積電','配對交易','1,500','20,000','30,000,000']]})
 def test_parse_aggregates_symbol_and_keeps_trade_type(self):
  result=b.parse_payload(self.payload(),'2026-09-22');row=result['rows'][0]
  self.assertEqual(row['symbol'],'3189');self.assertEqual(row['count'],2);self.assertEqual(row['amount'],449000000);self.assertAlmostEqual(row['vwap'],898)
  self.assertEqual(result['trade_count'],3)
 def test_rejects_wrong_date_and_amount(self):
  with self.assertRaises(ValueError):b.parse_payload(self.payload(),'2026-09-21')
  data=json.loads(self.payload());data['data'][0][-1]='1'
  result=b.parse_payload(json.dumps(data),'2026-09-22');self.assertEqual(result['rows'][0]['symbol'],'3189');self.assertEqual(result['rows'][0]['count'],1)
 def test_report_compares_saved_history(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(db,'DATA',Path(temp)):
   db.initialize()
   old=b.parse_payload(self.payload(),'2026-09-22');new=json.loads(json.dumps(old));new['date']='2026-09-23';new['rows'][0]['amount']*=2
   with db.connect() as c:b.init(c);c.execute('INSERT INTO block_trade_days VALUES (?,?,?)',('2026-09-22',json.dumps(old),'x'));c.execute('INSERT INTO block_trade_days VALUES (?,?,?)',('2026-09-23',json.dumps(new),'x'))
   report=b.report();self.assertEqual(report['snapshot']['rows'][0]['amount_ratio'],2)

if __name__=='__main__':unittest.main()
