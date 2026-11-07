import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db, full_update, market_monitor, alpha_beta


class FullUpdateStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data = db.DATA
        db.DATA = Path(self.temp.name)
        db.initialize()

    def tearDown(self):
        db.DATA = self.original_data
        self.temp.cleanup()

    def test_local_latest_data_is_not_shown_as_waiting(self):
        day = '2026-09-29'
        with db.connect() as conn:
            branch = conn.execute('SELECT broker,branch FROM branches WHERE active=1 LIMIT 1').fetchone()
            conn.execute('INSERT INTO market_days VALUES (?,?,?,?)', (day, 'ok', 'ok', ''))
            conn.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',
                         (day, '2330', '台積電', 1000, 990, 1010, 980, 100, 'TWSE'))
            conn.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',
                         (branch['broker'], branch['branch'], day, 'observed', 1, '', day + 'T18:00:00'))
            for table in ('major_days', 'focus_turnover_days', 'rs_days', 'etf_rs_days'):
                conn.execute(f'INSERT INTO {table} VALUES (?,?,?)', (day, '{}', day + 'T18:00:00'))
            conn.execute('INSERT INTO active_etf_days VALUES (?,?,?,?,?)',
                         ('00981A', day, '{}', 'official', day + 'T18:00:00'))
            conn.execute('INSERT INTO daily_follow_reports VALUES (?,?,?,?,?)',
                         (day, day + 'T18:00:00', 'v1', day, '{}'))
            conn.execute('INSERT INTO leader_reports VALUES (?,?,?)', ('scope', day, '{}'))
            conn.execute('CREATE TABLE block_trade_days(date TEXT PRIMARY KEY,payload TEXT,fetched_at TEXT)')
            conn.execute('INSERT INTO block_trade_days VALUES (?,?,?)', (day, '{}', day + 'T18:00:00'))
            conn.execute('INSERT INTO warrant_lookup_days VALUES (?,?,?,?,?)',
                         ('081988', day, 'warrant', '{}', day + 'T18:00:00'))
            conn.execute('CREATE TABLE dividend_yield_days(date TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL)')
            conn.execute('INSERT INTO dividend_yield_days VALUES (?,?,?)',
                         (day, '{}', day + 'T18:00:00'))
            for key in market_monitor.CATALOG:
                conn.execute('INSERT INTO market_observations VALUES (?,?,?,?,?)',
                             (key, json.dumps({'as_of': day}), day + 'T18:00:00', day + 'T18:00:00', ''))

        research_path = db.DATA / 'research.sqlite3'
        conn = sqlite3.connect(research_path)
        try:
            conn.execute('CREATE TABLE bt_sessions(date TEXT PRIMARY KEY)')
            conn.execute('CREATE TABLE strategy_snapshots(id TEXT PRIMARY KEY,date TEXT,created_at TEXT,payload TEXT)')
            conn.execute('INSERT INTO bt_sessions VALUES (?)', (day,))
            conn.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)', ('one', day, day, '{}'))
            conn.commit()
        finally:
            conn.close()

        alpha_beta.atomic(db.DATA/'alpha_beta'/'latest.json', {'as_of':day,'rows':[]})
        insider = {'year': 2026, 'count': 1, 'coverage': [{'available': True}]}
        with patch.object(full_update.sector_flow, 'status', return_value={'computed': day}), \
                patch.object(full_update.insider_changes, 'report', return_value=insider), \
                patch.object(full_update.margin_refresh, 'latest_date', return_value=day), \
                patch.object(full_update.margin_refresh, 'monthly_date', return_value=day), \
                patch.object(full_update.business_cycle, 'home_status', return_value={'name':'景氣循環分析','status':'complete','message':'來源已核對'}):
            result = full_update._local_status()

        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['done'], len(full_update.NAMES))
        self.assertNotIn('台指預測核對', full_update.NAMES)
        self.assertTrue(all(row['status'] == 'complete' for row in result['modules']))
        self.assertFalse(any('等待' in row['message'] for row in result['modules']))
        self.assertIn('已是最新', result['message'])


if __name__ == '__main__':
    unittest.main()
