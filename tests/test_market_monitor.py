import json
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from backend import db,market_monitor as m

class MarketTests(unittest.TestCase):
    def test_news_only_five_unique_safe_links_and_latest_first(self):
        html=''.join(f'<div class="articleCard"><a href="/article/{i}"><h2 class="articleCard__title">新聞{i}</h2></a></div>' for i in [0,0,1,2,3,4,5])
        self.assertEqual([r['title'] for r in m.parse_cm(html)['items']],[f'新聞{i}' for i in range(5)])
        with self.assertRaises(ValueError):m.safe_link('//evil.example/test','https://cmnews.com.tw')
        payload={'items':{'data':[{'newsId':i,'title':f'<b>News {i}</b>','publishAt':1700000000+i} for i in range(7)]}}
        self.assertEqual([r['title'] for r in m.parse_cnyes(json.dumps(payload))['items']],[f'News {i}' for i in range(6,1,-1)])

    def test_institution_amounts_dates_and_foreign_exclusion(self):
        for market,foreign,fields in [('twse','外資及陸資(不含外資自營商)',['單位名稱','買進金額','賣出金額','買賣差額']),('tpex','外資及陸資(不含自營商)',['單位名稱','買進金額(元)','賣出金額(元)','買賣超(元)'])]:
            table={'fields':fields,'data':[['\u3000'+foreign,'100,000,000','300,000,000','-200,000,000'],['投信','20','5','15'],['合計','999','0','999']]}
            data={'date':'20260916','stat':'ok',**(table if market=='twse' else {'tables':[table]})}
            r=m.parse_institution(json.dumps(data),market)
            self.assertEqual(r['as_of'],'2026-09-16');self.assertEqual(r['rows'][0]['net'],-200000000)
            table['data'][0][3]='0'
            with self.assertRaises(ValueError):m.parse_institution(json.dumps(data),market)
        self.assertEqual(m.day('1150916'),'2026-09-16')
        with self.assertRaises(ValueError):m.day('29990101')

    def test_futures_uses_first_contract_and_verifies_net(self):
        rows=''.join('<li>'+name+'<ul>'+''.join(f'<li>{v}</li>' for v in [100,80,20,999,999,0,88,88,0])+'</ul></li>' for name in ['外資及陸資','投信','自營商','三大法人合計'])
        html='法人未平倉口數 資料時間： 2026/09/16<ul>'+rows+'</ul>'
        r=m.parse_futures(html);self.assertEqual(r['rows'][0]['net'],20)
        with self.assertRaises(ValueError):m.parse_futures(html.replace('<li>20</li>','<li>21</li>'))

    def test_calendar_cross_month_year_and_completed_meeting(self):
        def panel(year,month,days,statement=False):
            return f'<div class="panel"><h4 class="panel-heading">{year} FOMC Meetings</h4><div class="fomc-meeting"><div class="fomc-meeting__month">{month}</div><div class="fomc-meeting__date">{days}</div>'+ ('<a href="/newsevents/pressreleases/monetary20261209a.htm">Statement</a>' if statement else '')+'</div></div>'
        raw=panel(2026,'December','8-9',True)+panel(2027,'January','26-27')+panel(2027,'Apr/May','30-1')
        r=m.parse_fed(raw,date(2026,12,9));self.assertEqual(r['next_start'],'2027-01-26');self.assertEqual(r['statement_date'],'2026-12-09')
        r=m.parse_fed(raw,date(2027,4,20));self.assertEqual(r['next_end'],'2027-05-01')
        with patch.object(m,'parse_fed',return_value={'statement_date':'2026-12-09','next_start':'2027-01-26','next_end':'2027-01-27'}):
            r=m.parse_rates('<p id="description">The target range for the federal funds rate to 3.75%-4.00% in December 2026.</p>',raw)
            self.assertEqual((r['low'],r['high']),(3.75,4))
            with self.assertRaises(ValueError):m.parse_rates('<p id="description">target range for the federal funds rate to 3.75%-4.00% in June 2026.</p>',raw)

    def test_quotes_do_not_treat_five_day_previous_close_as_daily_change(self):
        meta={'symbol':'TWD=X','currency':'TWD','regularMarketPrice':31.8,'regularMarketTime':1700000000,'chartPreviousClose':20}
        encode=lambda:json.dumps({'chart':{'result':[{'meta':meta}]}})
        self.assertIsNone(m.parse_quote(encode(),'twd')['change_pct'])
        meta['currency']='USD'
        with self.assertRaises(ValueError):m.parse_quote(encode(),'twd')
        meta.update(currency='TWD',regularMarketPrice='NaN')
        with self.assertRaises(ValueError):m.parse_quote(encode(),'twd')

    def test_gold_uses_last_price_per_tai_qian_not_previous_average(self):
        raw=json.dumps([{'GoldCode':'AU9901','Date':'1150916','Time':'163005','TradingLatestTradingInfo.LatestPrice':'16632','QuotedBuyingB.Price':'16632','QuotedSellingS.Price':'16690','TradingPreAvg':'16526.5'}])
        r=m.parse_gold(raw);self.assertEqual(r['value'],16632);self.assertEqual(r['unit'],'新台幣／台錢');self.assertIsNone(r['change_pct'])
        self.assertEqual(r['as_of'],'2026-09-16T16:30:05+08:00')

    def test_retail_sentiment_and_market_turnover(self):
        html='<table><tr><th>日期</th><th>收盤價</th><th>散戶做多</th><th>散戶做空</th><th>散戶多空比(%)</th></tr><tr><td>2026/09/22</td><td>48243</td><td>27027</td><td>24115</td><td>8.94</td></tr></table>'
        retail=m.parse_retail(html);self.assertEqual(retail['as_of'],'2026-09-22');self.assertEqual(retail['value'],8.94)
        market=[{'Date':'20260922','Contract':'MTX','TradingSession':'一般','OpenInterest':'32,581'}]
        institution=[{'Date':'20260922','ContractCode':'小型臺指期貨','Item':name,'OpenInterest(Long)':long_,'OpenInterest(Short)':short_} for name,long_,short_ in [('自營商','1,304','7,356'),('投信','88','104'),('外資及陸資','4,162','1,006')]]
        official=m.parse_retail_official(json.dumps(market),json.dumps(institution));self.assertEqual(official['retail_long'],27027);self.assertEqual(official['retail_short'],24115);self.assertAlmostEqual(official['value'],8.9377,places=3)
        payload={'stat':'OK','fields':['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數'],'data':[['115/09/21','1,000','800,000,000,000','500','47,000','100'],['115/09/22','2,000','1,000,000,000,000','600','48,000','1,000']]}
        volume=m.parse_market_volume(json.dumps(payload));self.assertEqual(volume['value'],10000);self.assertEqual(volume['ratio'],1.25)

    def test_market_turnover_merges_prior_month_for_twenty_day_average(self):
        fields=['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數']
        start=date(2026,9,1)
        prior={'stat':'OK','fields':fields,'data':[[(start+timedelta(days=i)).isoformat(),'1,000','800,000,000,000','500','47,000','100'] for i in range(20)]}
        current={'stat':'OK','fields':fields,'data':[['2026-10-01','2,000','1,000,000,000,000','600','48,000','1,000']]}
        volume=m.parse_market_volume([json.dumps(prior),json.dumps(current)])
        self.assertEqual(volume['as_of'],'2026-10-01');self.assertEqual(len(volume['rows']),21)
        self.assertEqual(volume['average_20'],8000);self.assertEqual(volume['ratio'],1.25)

    def test_snapshot_is_persistent_read_only_and_retained_on_failure(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(db,'DATA',Path(temp)):
            db.initialize()
            with patch.object(m,'load_card',return_value={'value':31.8,'as_of':'2026-09-16'}) as loader:
                self.assertTrue(m.update_one('twd'));first=next(r for r in m.report()['cards'] if r['id']=='twd')
                db.initialize();self.assertEqual(len(m.report()['cards']),len(m.CATALOG));loader.assert_called_once_with('twd')
            with patch.object(m,'load_card',side_effect=ValueError('來源暫不可用')):
                self.assertFalse(m.update_one('twd'));second=next(r for r in m.report()['cards'] if r['id']=='twd')
            self.assertEqual(first['data'],second['data']);self.assertEqual(first['saved_at'],second['saved_at']);self.assertEqual(second['error'],'來源暫不可用')
            with patch.object(m,'fetch',side_effect=AssertionError('GET must not fetch')):m.report()

    def test_refresh_rejects_unknown_card_and_deduplicates_running_job(self):
        with self.assertRaises(ValueError):m.refresh('unknown')
        with m._lock,patch.object(m.threading,'Thread') as thread:
            m.refresh();thread.assert_not_called()

    def test_v6_outlook_moved_to_manual_weather_page(self):
        self.assertNotIn('v6_outlook', m.CATALOG)

if __name__=='__main__':unittest.main()
