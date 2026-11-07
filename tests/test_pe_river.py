import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from backend import pe_river as p, db, market_monitor as m


class PeRiverTests(unittest.TestCase):
    def test_official_month_workbook_and_wrong_month(self):
        raw=(Path(__file__).parent/'fixtures/pe_202609.zip').read_bytes()
        row=p.parse_month(raw,'2026-09')
        self.assertEqual(row['date'],'2026-09-30')
        self.assertAlmostEqual(row['pe'],26.68)
        with self.assertRaises(ValueError):p.parse_month(raw,'2026-08')

    def test_month_alignment_no_future_anchor_and_stale_cutoff(self):
        state={'months':{'2026-08':{'month':'2026-08','date':'2026-08-31','pe':20},'2026-09':{'month':'2026-09','date':'2026-09-30','pe':25}},
               'closes':{'2026-08-28':19000,'2026-08-31':20000,'2026-09-29':24000,'2026-09-30':30000,'2026-10-01':33000,'2026-12-10':35000},'price_months':{}}
        points={v['date']:v for v in p.build(state,date(2026,12,10))['points']}
        self.assertNotIn('2026-08-28',points)
        self.assertEqual(points['2026-09-29']['estimated_pe'],24)
        self.assertEqual(points['2026-09-30']['estimated_pe'],25)
        self.assertEqual(points['2026-10-01']['estimated_pe'],27.5)
        self.assertEqual(points['2026-10-01']['bands'],[4800,9600,14400,19200,24000,28800])
        self.assertNotIn('2026-12-10',points)

    def test_close_schema_date_and_nan(self):
        payload={'stat':'OK','fields':['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數'],'data':[['115/09/30','1','2','3','47,940.13','0']]}
        self.assertEqual(p.parse_closes(json.dumps(payload),'2026-09'),{'2026-09-30':47940.13})
        with self.assertRaises(ValueError):p.parse_closes(json.dumps(payload),'2026-10')
        payload['data'][0][4]='NaN'
        with self.assertRaises(ValueError):p.parse_closes(json.dumps(payload),'2026-09')

    def test_read_is_offline_and_failed_update_retains_payload(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
            db.initialize()
            with patch.object(m,'fetch',side_effect=AssertionError('No network on read')):
                self.assertFalse(p.report()['available'])
            with patch.object(m,'load_card',return_value={'available':True,'warning':''}):self.assertTrue(m.update_one('pe_river'))
            with patch.object(m,'load_card',side_effect=ValueError('月報來源暫不可用')):self.assertFalse(m.update_one('pe_river'))
            card=next(c for c in m.report()['cards'] if c['id']=='pe_river')
            self.assertTrue(card['data']['available'])
            self.assertIn('月報來源',card['error'])

    def test_partial_update_preserves_good_data_and_reports_warning(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
            db.initialize()
            with patch.object(m,'load_card',return_value={'available':True,'warning':'指數待補'}):self.assertFalse(m.update_one('pe_river'))
            card=next(c for c in m.report()['cards'] if c['id']=='pe_river')
            self.assertTrue(card['data']['available'])
            self.assertEqual(card['error'],'指數待補')
        self.assertEqual(p.shift_month(date(2026,5,31),-3),date(2026,2,28))
        self.assertEqual(p.shift_month(date(2024,2,29),-12),date(2023,2,28))

    def test_cached_history_refresh_only_checks_manifest(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)),patch.object(p,'cached') as cache:
            db.initialize()
            today=p.datetime.now(p.TZ).date()
            months=[p.shift_month(today.replace(day=1),i).strftime('%Y-%m') for i in range(-13,1)]
            cache.return_value={'months':{m:{'month':m,'date':m+'-01','pe':20} for m in months},'closes':{m+'-01':20000 for m in months},'price_months':{m:today.isoformat() for m in months},'complete_price_months':months[:-1]}
            def fetch(url):
                if '/FMTQIK?' in url:
                    y=today.year-1911
                    return json.dumps({'stat':'OK','fields':['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數'],'data':[[f'{y}/{today.month:02d}/01','1','2','3','20,000','0']]})
                self.assertIn('/statistics/download?',url)
                return json.dumps({'stat':'OK','data':[['大盤、各產業類股及上市股票本益比、殖利率及股價淨值比','/count?url=/staticFiles/inspection/inspection/04/001/202601_C04001.zip']]})
            self.assertTrue(p.refresh(fetch)['available'])
