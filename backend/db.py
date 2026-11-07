from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('BRANCH_MONITOR_DATA', ROOT / 'data'))


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / 'monitor.sqlite3', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS branches (
          broker TEXT NOT NULL, branch TEXT NOT NULL, name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(broker, branch)
        );
        CREATE TABLE IF NOT EXISTS flows (
          broker TEXT NOT NULL, branch TEXT NOT NULL, date TEXT NOT NULL,
          symbol TEXT NOT NULL, name TEXT NOT NULL,
          buy REAL NOT NULL CHECK(buy >= 0), sell REAL NOT NULL CHECK(sell >= 0), net REAL NOT NULL,
          source TEXT NOT NULL, PRIMARY KEY(broker, branch, date, symbol)
        );
        CREATE TABLE IF NOT EXISTS snapshots (
          broker TEXT NOT NULL, branch TEXT NOT NULL, date TEXT NOT NULL,
          status TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL,
          PRIMARY KEY(broker, branch, date)
        );
        CREATE TABLE IF NOT EXISTS prices (
          date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          close REAL NOT NULL CHECK(close > 0), open REAL, high REAL, low REAL,
          volume REAL, market TEXT NOT NULL, PRIMARY KEY(date, symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date);
        CREATE TABLE IF NOT EXISTS market_days (
          date TEXT PRIMARY KEY, twse_status TEXT NOT NULL,
          tpex_status TEXT NOT NULL, message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS coverage (
          broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL,
          start TEXT NOT NULL, end TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(broker, branch, symbol)
        );
        CREATE TABLE IF NOT EXISTS candidates (
          broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          PRIMARY KEY(broker, branch, symbol)
        );
        CREATE TABLE IF NOT EXISTS ranking_entries (
          broker TEXT NOT NULL, branch TEXT NOT NULL, date TEXT NOT NULL,
          unit TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          buy REAL NOT NULL, sell REAL NOT NULL, net REAL NOT NULL, source TEXT NOT NULL,
          PRIMARY KEY(broker,branch,date,unit,symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_ranking_entries_recent ON ranking_entries(broker,branch,date,symbol);
        CREATE TABLE IF NOT EXISTS history_intervals (
          broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL,
          start TEXT NOT NULL, end TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(broker, branch, symbol, start, end)
        );
        CREATE TABLE IF NOT EXISTS major_days (date TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leader_reports (scope TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scope,date));
        CREATE TABLE IF NOT EXISTS focus_turnover_days (date TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS focus_tags (symbol TEXT PRIMARY KEY,sector TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS focus_state (id INTEGER PRIMARY KEY CHECK(id=1),status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS market_observations (key TEXT PRIMARY KEY, payload TEXT, saved_at TEXT, checked_at TEXT, error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS rs_quotes (date TEXT NOT NULL,market TEXT NOT NULL,payload TEXT NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(date,market));
        CREATE TABLE IF NOT EXISTS rs_days (date TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS etf_rs_days (date TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS etf_rs_state (id INTEGER PRIMARY KEY CHECK(id=1),status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rs_state (id INTEGER PRIMARY KEY CHECK(id=1),status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS active_etf_days (
          fund TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL,
          source TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(fund,date)
        );
        CREATE INDEX IF NOT EXISTS idx_active_etf_days_date ON active_etf_days(date,fund);
        CREATE TABLE IF NOT EXISTS active_etf_state (
          id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warrant_analysis_days (
          date TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warrant_lookup_days (
          query TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
          payload TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(query,date)
        );
        CREATE TABLE IF NOT EXISTS warrant_queries (
          query TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warrant_analysis_state (
          id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT, message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_follow_reports (
          date TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL,
          rs_date TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS major_turnover (symbol TEXT NOT NULL, date TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, payload TEXT, started_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY(symbol,date));
        CREATE TABLE IF NOT EXISTS major_state (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
          message TEXT NOT NULL DEFAULT '', errors TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS sync_audit_runs (
          id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, as_of TEXT, recent_cutoff TEXT,
          total INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS candidate_sync_state (
          broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          branch_active INTEGER NOT NULL, daily_tier TEXT NOT NULL, repair_tier TEXT NOT NULL,
          estimated_position REAL NOT NULL, unmatched_outflow REAL NOT NULL,
          first_flow_date TEXT, last_flow_date TEXT, last_ranking_date TEXT,
          last_success_date TEXT, target_date TEXT, next_query_start TEXT,
          recent_cutoff TEXT, coverage_complete INTEGER NOT NULL,
          ignored INTEGER NOT NULL, reason TEXT NOT NULL, audited_at TEXT NOT NULL,
          PRIMARY KEY(broker,branch,symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_sync_daily ON candidate_sync_state(branch_active,daily_tier);
        CREATE INDEX IF NOT EXISTS idx_candidate_sync_repair ON candidate_sync_state(repair_tier,next_query_start);
        CREATE TABLE IF NOT EXISTS history_repair_plans (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, audit_id TEXT,
          through_date TEXT, target_date TEXT, selected INTEGER NOT NULL,
          summary TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS history_repair_queue (
          broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          repair_tier TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '', plan_id TEXT NOT NULL, run_id TEXT,
          queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
          error_kind TEXT NOT NULL DEFAULT '', next_retry_at TEXT,
          retryable INTEGER NOT NULL DEFAULT 1, checkpoint_at TEXT,
          PRIMARY KEY(broker,branch,symbol,start,end)
        );
        CREATE INDEX IF NOT EXISTS idx_history_repair_queue ON history_repair_queue(status,repair_tier,start);
        CREATE TABLE IF NOT EXISTS history_repair_runs (
          id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
          succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '', errors TEXT NOT NULL DEFAULT '[]',
          checkpoint_at TEXT, checkpoint_item TEXT
        );
        ''')
        # Seed only a fresh database; never reactivate a branch removed by the user.
        if conn.execute('SELECT COUNT(*) FROM branches').fetchone()[0] == 0:
            path = ROOT / 'config' / 'watchlist.json'
            seed = json.loads(path.read_text())['branches'] if path.exists() else [
                {'broker':'9300','branch':'9339','name':'華南永昌-竹北'}]
            conn.executemany('INSERT INTO branches VALUES (?,?,?,1)',
                             [(b['broker'],b['branch'],b['name']) for b in seed])
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                         ('watchlist_order',json.dumps([b['broker']+':'+b['branch'] for b in seed])))
        if 'scope' not in [row['name'] for row in conn.execute('PRAGMA table_info(jobs)')]:
            conn.execute("ALTER TABLE jobs ADD COLUMN scope TEXT NOT NULL DEFAULT '[]'")
        if 'progress' not in [row['name'] for row in conn.execute('PRAGMA table_info(jobs)')]:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress TEXT NOT NULL DEFAULT '{}'")
        queue_columns={row['name'] for row in conn.execute('PRAGMA table_info(history_repair_queue)')}
        for name,definition in [('error_kind',"TEXT NOT NULL DEFAULT ''"),('next_retry_at','TEXT'),
                                ('retryable','INTEGER NOT NULL DEFAULT 1'),('checkpoint_at','TEXT')]:
            if name not in queue_columns:conn.execute(f'ALTER TABLE history_repair_queue ADD COLUMN {name} {definition}')
        run_columns={row['name'] for row in conn.execute('PRAGMA table_info(history_repair_runs)')}
        for name in ['checkpoint_at','checkpoint_item']:
            if name not in run_columns:conn.execute(f'ALTER TABLE history_repair_runs ADD COLUMN {name} TEXT')
        if not conn.execute("SELECT 1 FROM settings WHERE key='history_intervals_migrated'").fetchone():
            conn.execute('INSERT OR IGNORE INTO history_intervals SELECT * FROM coverage')
            conn.execute("INSERT INTO settings VALUES ('history_intervals_migrated','true')")
        if not conn.execute("SELECT 1 FROM settings WHERE key='ranking_entries_seeded'").fetchone():
            conn.execute("""INSERT OR IGNORE INTO ranking_entries
                SELECT broker,branch,date,'E',symbol,name,buy,sell,net,source
                FROM flows WHERE source LIKE '%/zgb0.djhtm%'""")
            conn.execute("INSERT INTO settings VALUES ('ranking_entries_seeded','true')")
        defaults = {'schedule_enabled': True, 'schedule_time': '18:00', 'lookback_days': 45}
        for key, value in defaults.items():
            conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        conn.execute("UPDATE jobs SET status='interrupted', message='上次更新中斷；可重新同步', finished_at=datetime('now') WHERE status='running'")
        conn.execute("UPDATE major_state SET status='interrupted', finished_at=datetime('now'), message='上次主力排行更新中斷，可重新讀取' WHERE status='running'")
        conn.execute("UPDATE major_turnover SET status='interrupted', finished_at=datetime('now'), message='上次個股查詢中斷；重新載入會沿用已快取的來源' WHERE status IN ('queued','running')")
        conn.execute("UPDATE focus_state SET status='interrupted',finished_at=datetime('now'),message='上次成交重心更新中斷；可繼續補齊' WHERE status='running'")
        conn.execute('PRAGMA optimize')
        conn.execute("UPDATE rs_state SET status='interrupted',finished_at=datetime('now'),message='上次 RS 計算中斷；可手動重新計算' WHERE status='running'")
        conn.execute("UPDATE etf_rs_state SET status='interrupted',finished_at=datetime('now'),message='上次 ETF RS 計算中斷；可手動重新計算' WHERE status='running'")
        conn.execute("UPDATE active_etf_state SET status='interrupted',finished_at=datetime('now'),message='上次主動 ETF 更新中斷；可手動重新讀取' WHERE status='running'")
        conn.execute("UPDATE warrant_analysis_state SET status='interrupted',finished_at=datetime('now'),message='上次權證資料更新中斷；可手動重新更新' WHERE status='running'")
        conn.execute("""UPDATE history_repair_queue SET status='interrupted',finished_at=datetime('now'),
            checkpoint_at=datetime('now'),last_error='程式重新啟動；下次執行批次會續排',error_kind='interrupted',retryable=1
            WHERE status='running'""")
        conn.execute("""UPDATE history_repair_runs SET status='interrupted',finished_at=datetime('now'),
            checkpoint_at=datetime('now'),message='修復批次因程式重新啟動而中斷；已完成項目保留'
            WHERE status='running'""")


def settings():
    with connect() as conn:
        return {r['key']: json.loads(r['value']) for r in conn.execute('SELECT * FROM settings')}


def ignored_symbols():
    values=settings().get('ignored_symbols',[])
    if not isinstance(values,list):return set()
    return {str(value).strip().upper() for value in values if str(value).strip()}


def update_settings(values):
    with connect() as conn:
        for key, value in values.items():
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
