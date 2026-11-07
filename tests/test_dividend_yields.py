import unittest
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend import dividend_yields as dy
from backend import db


class DividendYieldTests(unittest.TestCase):
    def test_official_history_is_split_and_dates_remain_strict(self):
        from unittest.mock import Mock
        calls=[]
        def get(url,params):
            calls.append((params['startDate'],params['endDate']))
            begin=date.fromisoformat(params['startDate'][:4]+'-'+params['startDate'][4:6]+'-'+params['startDate'][6:])
            payload=dict(stat='OK',strDate=params['startDate'],endDate=params['endDate'],fields=['資料日期','股票代號'],
                         data=[[f'{begin.year-1911}年{begin.month}月{begin.day}日','2330']])
            return Mock(json=lambda:payload)
        with patch.object(dy,'_get',side_effect=get):
            result=dy._twse_ex_history_chunks(date(2026,1,15),date(2026,3,5))
        self.assertEqual(sorted(calls),[('20260115','20260131'),('20260201','20260228'),('20260301','20260305')])
        self.assertEqual(len(result['data']),3)
        bad=Mock(json=lambda:dict(stat='OK',strDate='20260115',endDate='20260131',fields=[],data=[['115年2月1日','2330']]))
        with patch.object(dy,'_get',return_value=bad),self.assertRaises(ValueError):
            dy._twse_ex_history_chunks(date(2026,1,15),date(2026,1,31))

    @staticmethod
    def calendar(year, day, cash, payment=None, quarters=None):
        return dict(year=year, ex_date=day, cash=cash,
                    payment_date=payment or day, quarters=quarters or [1, 2, 3, 4])

    def calculate(self, symbol, close, events, calendar, official=5, changes=(), gaps=()):
        stock = dict(symbol=symbol, close=close, yield_pct=official, dividend_year='114')
        dy._stock_cash_estimates([stock], events, date(2026, 10, 15), gaps,
                                 {symbol:calendar}, changes)
        return stock

    def test_tsmc_fiscal_year_does_not_mix_trailing_quarters(self):
        dates = ['2025-09-16','2025-12-11','2026-03-17','2026-06-11','2026-09-16']
        amounts = [5.000017,5.000011,6.000035,6.000035,7.000001]
        events = [('2330',date.fromisoformat(d),a,0) for d,a in zip(dates,amounts)]
        calendar = [self.calendar(2025 if i<4 else 2026,d,a,quarters=[i+1 if i<4 else 1])
                    for i,(d,a) in enumerate(zip(dates,amounts))]
        stock = self.calculate('2330',2440,events,calendar,official=.90)
        self.assertEqual(stock['yield_pct'],.90)
        self.assertAlmostEqual(stock['cash_per_share'],22.000098)
        self.assertEqual(stock['cash_dividend_year'],2025)
        self.assertEqual(len(stock['yield_components']),4)
        self.assertNotIn('last_ex_yield_pct',stock)

    def test_stock_dividend_is_not_cash_and_prior_fiscal_year_is_excluded(self):
        events = [('5314',date(2025,10,28),.27436643,0),
                  ('5314',date(2026,8,14),0,3157.02936097),
                  ('5314',date(2026,9,21),.04852674,0)]
        cal = [self.calendar(2024,'2025-10-28',.27436643),
               self.calendar(2025,'2026-08-14',0),
               self.calendar(2025,'2026-09-21',.04852674,'2026-10-13')]
        stock=self.calculate('5314',24.8,events,cal,official=127.5)
        self.assertEqual(stock['yield_pct'],.20)
        self.assertEqual(stock['yield_method'],'annual')

    def test_cash_waiting_for_payment_uses_prior_completed_year(self):
        events=[('1442',date(2025,8,21),4.5,0),('1442',date(2026,9,23),2.4,0)]
        cal=[self.calendar(2024,'2025-08-21',4.5),self.calendar(2025,'2026-09-23',2.4,'2026-10-23')]
        stock=self.calculate('1442',26.75,events,cal)
        self.assertEqual(stock['yield_pct'],16.82)
        self.assertEqual(stock['yield_method'],'previous_year')

    def test_announced_zero_is_not_replaced_by_last_year_high_dividend(self):
        stock=self.calculate('4909',35.4,[('4909',date(2025,7,21),6,0)],
                             [self.calendar(2024,'2025-07-21',6)],official=0)
        self.assertEqual(stock['yield_pct'],0)
        self.assertEqual(stock['cash_dividend_year'],2025)

    def test_split_and_stock_dividend_use_current_share_basis(self):
        events=[('5904',date(2026,7,16),25.5,0)]
        cal=[self.calendar(2025,'2026-07-16',25.5)]
        changes=[dict(symbol='5904',date='2026-09-01',kind='par',factor=10)]
        stock=self.calculate('5904',73.1,events,cal,changes=changes)
        self.assertEqual(stock['yield_pct'],3.49)
        stock=self.calculate('1111',50,[('1111',date(2026,7,1),2,1000)],
                             [self.calendar(2025,'2026-07-01',2)])
        self.assertEqual(stock['yield_pct'],2)

    def test_unknown_capital_change_does_not_publish_unadjusted_yield(self):
        changes=[dict(symbol='1111',date='2026-09-01',kind='capital',factor=None)]
        stock=self.calculate('1111',50,[('1111',date(2026,7,1),2,0)],
                             [self.calendar(2025,'2026-07-01',2)],changes=changes)
        self.assertIsNone(stock['yield_pct'])

    def test_unknown_payment_or_mismatched_cash_is_not_assumed_paid(self):
        for payment,cash in [(None,2),('2026-08-01',20)]:
            event=self.calendar(2025,'2026-07-01',cash)
            event['payment_date']=payment
            stock=self.calculate('1111',50,[('1111',date(2026,7,1),2,0)],[event])
            self.assertIsNone(stock['yield_pct'])

    def test_old_unknown_rights_do_not_hide_new_cash_but_later_unknown_rights_do(self):
        event=self.calendar(2025,'2026-09-01',2)
        events=[('1111',date(2026,9,1),2,0)]
        stock=self.calculate('1111',50,events,[event],gaps={'1111':{date(2024,12,1)}})
        self.assertEqual(stock['yield_pct'],4)
        stock=self.calculate('1111',50,events,[event],gaps={'1111':{date(2026,10,1)}})
        self.assertIsNone(stock['yield_pct'])

    def test_partial_year_can_only_be_labelled_last_paid_estimate(self):
        stock=self.calculate('1111',50,[('1111',date(2026,7,1),2,0)],
                             [self.calendar(2025,'2026-07-01',2,quarters=[4])],official=6)
        self.assertEqual(stock['yield_pct'],4)
        self.assertEqual(stock['yield_method'],'last_paid')
        self.assertIn('非全年',stock['yield_basis'])

    def test_etf_split_and_reverse_split_adjust_cash_units(self):
        changes=[dict(symbol='0050',date='2026-05-01',factor=4),
                 dict(symbol='00673R',date='2026-05-01',factor=.25)]
        self.assertEqual(dy._adjust_etf_cash('0050',date(2026,3,1),8,date(2026,10,15),changes),2)
        self.assertEqual(dy._adjust_etf_cash('00673R',date(2026,3,1),1,date(2026,10,15),changes),4)
        self.assertEqual(dy._adjust_etf_cash('0050',date(2026,7,1),8,date(2026,10,15),changes),8)

    def test_calendar_ignores_provider_yield_and_deduplicates_events(self):
        feed=[dict(stockNo=str(i),date='2026/07/01',cashDividend=2,cashYield=127.5,
                   yearSeasons=[20251,20252,20253,20254],paymentDate='2026-08-01T00:00:00')
              for i in range(1000,2500)]*4
        cal=dy.parse_dividend_calendar(feed)
        self.assertEqual(len(cal['1111']),1)
        self.assertNotIn('cashYield',cal['1111'][0])
        self.assertEqual(cal['1111'][0]['year'],2025)

    def test_fill_rate_excludes_unfinished_events_and_reports_fill_days(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            db.initialize()
            with db.connect() as conn:
                conn.executemany('INSERT INTO prices(date,symbol,name,close,market) VALUES (?,?,?,?,?)', [
                    ('2026-01-02', '1111', '測試', 8.0, 'TWSE'),
                    ('2026-06-01', '1111', '測試', 9.0, 'TWSE'),
                    ('2026-06-02', '1111', '測試', 9.5, 'TWSE'),
                    ('2026-06-03', '1111', '測試', 10.0, 'TWSE'),
                    ('2026-10-14', '1111', '測試', 8.0, 'TWSE'),
                    ('2026-10-15', '1111', '測試', 8.1, 'TWSE'),
                ])
            stock = {'symbol':'1111'}
            start = dy._fill_metrics([stock], [
                ('1111', date(2026, 6, 1), 1.0, 0, 10.0),
                ('1111', date(2026, 10, 14), 1.0, 0, 10.0),
            ], date(2026, 10, 15))
            self.assertEqual(start, '2026-01-02')
            self.assertEqual(stock['fill_sample'], 1)
            self.assertEqual(stock['fill_successes'], 1)
            self.assertEqual(stock['fill_probability_pct'], 100.0)
            self.assertEqual(stock['fill_days_avg'], 3.0)

    def test_etf_fill_baseline_and_empty_sample_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            db.initialize()
            rows = [{'symbol':'0056'}, {'symbol':'00999A'}, {'symbol':'00631L'}]
            baseline = {'0056':dict(fill_sample=10,fill_successes=8,fill_days_total=120,wantgoo_last_ex_date='2026-07-01'),
                        '00999A':dict(fill_sample=1,fill_successes=0,fill_days_total=0,wantgoo_last_ex_date='2026-10-01')}
            dy._etf_fill_metrics(rows, [], date(2026,10,21), baseline)
            self.assertEqual((rows[0]['fill_probability_pct'],rows[0]['fill_days_avg']), (80,15))
            self.assertEqual((rows[1]['fill_probability_pct'],rows[1]['fill_days_avg']), (0,None))
            self.assertIsNone(rows[2]['fill_probability_pct'])
            self.assertEqual(rows[2]['fill_source'],'無歷史樣本')

    def test_etf_new_fill_uses_high_and_calendar_days_without_duplicate_or_future_events(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            db.initialize()
            with db.connect() as conn:
                conn.executemany('INSERT INTO prices(date,symbol,name,close,high,market) VALUES (?,?,?,?,?,?)', [
                    ('2026-10-15','0056','測試',30,30,'TWSE'),
                    ('2026-10-16','0056','測試',29,29.5,'TWSE'),
                    ('2026-10-19','0056','測試',29.8,30.1,'TWSE')])
            row={'symbol':'0056'}
            events=[('0056',date(2026,10,16),1)]*2+[('0056',date(2026,10,22),1)]
            base={'0056':dict(fill_sample=4,fill_successes=2,fill_days_total=20,wantgoo_last_ex_date='2026-07-01')}
            dy._etf_fill_metrics([row],events,date(2026,10,21),base)
            self.assertEqual((row['fill_sample'],row['fill_successes'],row['fill_new_events']), (5,3,1))
            self.assertEqual(row['fill_days_avg'],8)
            changes=[dict(symbol='0056',date='2026-10-19',factor=4)]
            dy._etf_fill_metrics([row],events,date(2026,10,21),base,changes)
            self.assertEqual(row['fill_sample'],4)
            base['0056']=dict(fill_sample=5,fill_successes=3,fill_days_total=24,wantgoo_last_ex_date='2026-10-16')
            dy._etf_fill_metrics([row],events,date(2026,10,21),base)
            self.assertEqual((row['fill_sample'],row['fill_new_events']),(5,0))

    def test_wantgoo_fill_baseline_matches_its_calendar_day_method(self):
        def event(symbol, day, recovered=None):
            return {'stockNo':symbol, 'date':day, 'cashDividend':1,
                    'recoveryDate':f'{recovered}T00:00:00' if recovered else None}
        feed = [event(f'{symbol:04d}', '2025/07/01') for symbol in range(1000, 2500) for _ in range(4)]
        feed += [event('6596', '2023/01/01', '2023-08-30'),
                 event('6596', '2024/01/01', '2024-01-22'),
                 event('6596', '2025/01/01', '2025-02-06'),
                 event('6596', '2026/03/26')]
        stats = dy.parse_wantgoo_fill_data(feed, date(2026, 10, 16))['6596']
        self.assertEqual(stats['fill_sample'], 4)
        self.assertEqual(stats['fill_successes'], 3)
        self.assertEqual(stats['fill_days_total'], 242 + 22 + 37)
        self.assertEqual(stats['wantgoo_last_ex_date'], '2026-03-26')

    def test_new_local_ex_dividend_is_added_once_then_handed_to_wantgoo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            db.initialize()
            with db.connect() as conn:
                conn.executemany('INSERT INTO prices(date,symbol,name,close,high,market) VALUES (?,?,?,?,?,?)', [
                    ('2026-10-13', '1111', '測試', 9.0, 9.5, 'TWSE'),
                    ('2026-10-14', '1111', '測試', 9.7, 10.2, 'TWSE'),
                ])
            row = {'symbol':'1111'}
            official = [('1111', date(2026, 10, 13), 1, 0, 10.0)]
            baseline = {'1111': {'fill_sample':5, 'fill_successes':3,
                                 'fill_days_total':100, 'wantgoo_last_ex_date':'2026-06-01'}}
            dy._merge_wantgoo_fills([row], official, date(2026, 10, 15), baseline)
            self.assertEqual((row['fill_sample'], row['fill_successes']), (6, 4))
            self.assertEqual(row['fill_days_avg'], 25.5)
            self.assertEqual(row['fill_new_events'], 1)
            baseline['1111'] = {'fill_sample':6, 'fill_successes':4,
                                'fill_days_total':102, 'wantgoo_last_ex_date':'2026-10-13'}
            dy._merge_wantgoo_fills([row], official, date(2026, 10, 15), baseline)
            self.assertEqual((row['fill_sample'], row['fill_successes']), (6, 4))
            self.assertEqual(row['fill_new_events'], 0)


if __name__ == '__main__':
    unittest.main()
