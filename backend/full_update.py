"""One-click incremental refresh for every local dashboard module."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import forward_pe_refresh, business_cycle, alpha_beta, active_etf, block_trades, broker_focus, daily_follow, db, dividend_yields, etf_rs, insider_changes, leaders, major, market_monitor, rs, sector_flow, service, v9_tx, warrants, monthly_statistics, margin_refresh

TZ = ZoneInfo('Asia/Taipei')
_lock = threading.Lock()
_state_lock = threading.Lock()
NAMES = ['追蹤分點', '全市場主力', '券商關注', '個股 RS', '每日跟單', 'ETF RS', 'Alpha／Beta 排行', '主動 ETF 動向', '內部人持股異動', '殖利率排行', '市場觀測', '族群流向', '每日鉅額交易', '權證分析', '券商風雲榜', 'V9 台指期', '月份漲跌統計', '融資維持率統計', '景氣循環分析', '預估本益比河流圖']
_job = {
    'status': 'idle',
    'message': '尚未執行全部更新',
    'started_at': None,
    'finished_at': None,
    'done': 0,
    'total': len(NAMES),
    'modules': [{'name': name, 'status': 'queued', 'message': '等待首次全部更新'} for name in NAMES],
}
_restored = False
STATE_KEY = 'full_update_status'


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _copy():
    with _state_lock:
        return {**_job, 'modules': [dict(row) for row in _job['modules']]}


def status():
    _restore()
    value=_copy()
    config=db.settings()
    value['schedule']={'enabled':bool(config.get('schedule_enabled')), 'time':config.get('schedule_time','18:00'), 'last_attempt':config.get('home_schedule_slot'), 'timezone':'Asia/Taipei','retry_policy':'manual'}
    return value


def _valid_saved(value):
    return (isinstance(value, dict) and value.get('status') in {'idle', 'complete', 'partial', 'failed'}
            and isinstance(value.get('modules'), list)
            and [row.get('name') for row in value['modules']] == NAMES)


def _saved_status():
    try:
        with db.connect() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (STATE_KEY,)).fetchone()
        value = json.loads(row[0]) if row else None
        return value if _valid_saved(value) else None
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return None


def _persist(value=None):
    try:
        value = value or _copy()
        # A running state is useful while the process is alive, but restoring it
        # after a restart would make the page poll forever.
        if value.get('status') == 'running':
            return
        with db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                         (STATE_KEY, json.dumps(value, ensure_ascii=False, allow_nan=False)))
    except (sqlite3.Error, TypeError, ValueError):
        pass


def _main_value(sql, params=()):
    try:
        with db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.Error:
        return None


def _research_value(sql, params=()):
    path = db.DATA / 'research.sqlite3'
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _module_row(name, latest, target, message=None, ready=None):
    ready = bool(latest) if ready is None else ready
    fresh = bool(ready and (not target or not latest or str(latest)[:10] >= target))
    if fresh:
        suffix = f' · {str(latest)[:10]}' if latest else ''
        return {'name': name, 'status': 'complete', 'message': message or f'本機資料已是最新{suffix}'}
    if ready:
        suffix = f'（目前 {str(latest)[:10]}）' if latest else ''
        return {'name': name, 'status': 'partial', 'message': message or f'待更新至 {target}{suffix}'}
    return {'name': name, 'status': 'queued', 'message': message or '尚無本機資料'}


def _local_status():
    """Rebuild the home status from saved data without making network requests."""
    target = _main_value("SELECT MAX(date) FROM market_days WHERE twse_status='ok' OR tpex_status='ok'")
    target = target or _main_value('SELECT MAX(date) FROM prices')
    dates = {
        '追蹤分點': _main_value("SELECT MAX(s.date) FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed'"),
        '全市場主力': _main_value('SELECT MAX(date) FROM major_days'),
        '券商關注': _main_value('SELECT MAX(date) FROM focus_turnover_days'),
        '個股 RS': _main_value('SELECT MAX(date) FROM rs_days'),
        '每日跟單': _main_value('SELECT MAX(date) FROM daily_follow_reports'),
        'ETF RS': _main_value('SELECT MAX(date) FROM etf_rs_days'),
        'Alpha／Beta 排行': alpha_beta.latest_date(),
        '主動 ETF 動向': _main_value('SELECT MAX(date) FROM active_etf_days'),
        '殖利率排行': _main_value('SELECT MAX(date) FROM dividend_yield_days'),
        '族群流向': None,
        '月份漲跌統計': margin_refresh.monthly_date(),
        '融資維持率統計': margin_refresh.latest_date(),
        '每日鉅額交易': _main_value('SELECT MAX(date) FROM block_trade_days'),
        '權證分析': _main_value('SELECT MAX(date) FROM warrant_lookup_days'),
        '券商風雲榜': _main_value('SELECT MAX(date) FROM leader_reports'),
        'V9 台指期': v9_tx.latest_date(),
    }
    try:
        sector = sector_flow.status()
        dates['族群流向'] = sector.get('computed')
    except Exception:
        pass

    rows = [_module_row(name, dates.get(name), target) for name in NAMES if name not in {'市場觀測', '內部人持股異動', '景氣循環分析', '預估本益比河流圖'}]
    rows.append(business_cycle.home_status())
    rows.append(forward_pe_refresh.home_status(target=target))
    insider = insider_changes.report()
    saved_months = sum(item['available'] for item in insider['coverage'])
    expected_months = len(insider['coverage'])
    insider_status = 'complete' if saved_months == expected_months else 'partial' if saved_months else 'queued'
    rows.append({'name': '內部人持股異動', 'status': insider_status,
                 'message': f"{insider['year']} 年官方月報 {saved_months}/{expected_months} 組 · {insider['count']} 筆非零異動"})
    saved_cards = _main_value('SELECT COUNT(*) FROM market_observations WHERE payload IS NOT NULL') or 0
    latest_card = _main_value("SELECT MAX(substr(saved_at,1,10)) FROM market_observations WHERE payload IS NOT NULL")
    total_cards = len(market_monitor.CATALOG)
    market_ready = saved_cards >= total_cards
    market_message = (f'本機資料已是最新 · {latest_card} · {saved_cards}/{total_cards} 張'
                      if market_ready and (not target or latest_card and latest_card >= target)
                      else f'已保存 {saved_cards}/{total_cards} 張；可手動補更新')
    rows.append(_module_row('市場觀測', latest_card, target, market_message, market_ready))
    rs_latest = dates.get('個股 RS')
    branch_latest = dates.get('追蹤分點')
    if rs_latest and branch_latest and branch_latest < rs_latest:
        for row in rows:
            if row['name'] == '追蹤分點':
                row.update(status='partial', message=f'個股 RS 已至 {rs_latest}，本機分點榜單仍至 {branch_latest}；等待新榜單發布後，請手動更新')
            elif row['name'] == '每日跟單':
                row.update(status='partial', message=f'每日跟單仍至 {dates.get("每日跟單") or "尚無資料"}；缺少 {rs_latest} 同日分點榜單，尚不能產生名單')
    order = {name: index for index, name in enumerate(NAMES)}
    rows.sort(key=lambda row: order[row['name']])
    complete = sum(row['status'] == 'complete' for row in rows)
    partial = sum(row['status'] == 'partial' for row in rows)
    done = complete + partial
    if complete == len(rows):
        state = 'complete'
        message = f'本機資料已是最新{(" · " + target) if target else ""}'
    elif done:
        state = 'partial'
        message = f'已載入本機資料 · {complete}/{len(rows)} 項為最新'
    else:
        state = 'idle'
        message = '尚無本機資料，請執行全部更新'
    return {'status': state, 'message': message, 'started_at': None, 'finished_at': None,
            'done': done, 'total': len(rows), 'modules': rows}


def _restore():
    global _restored
    with _state_lock:
        if _restored or _job.get('status') == 'running':
            return
        _restored = True
    saved = _saved_status()
    value = _local_status()
    if saved:
        value['started_at'] = saved.get('started_at')
        value['finished_at'] = saved.get('finished_at')
    with _state_lock:
        if _job.get('status') != 'running':
            _job.clear()
            _job.update(value)
    _persist(value)


def _set(**values):
    with _state_lock:
        _job.update(values)
    if values.get('status') != 'running':
        _persist()


def _module(name, status_value, message, detail=None):
    with _state_lock:
        row = next((row for row in _job['modules'] if row['name'] == name), None)
        if row is None:
            row = {'name': name, 'status': status_value, 'message': message, 'detail': detail}
            _job['modules'].append(row)
        else:
            row.update(status=status_value, message=message)
            if detail is not None:row['detail']=detail
        _job['done'] = sum(row['status'] in {'complete', 'partial', 'failed'} for row in _job['modules'])
        _job['message'] = f"正在更新：{name}" if status_value == 'running' else message
    if status_value != 'running':
        _persist()


def _wait(name, trigger, getter, running, timeout=1800):
    _module(name, 'running', f'{name}更新中')
    try:
        trigger()
        deadline = time.monotonic() + timeout
        while True:
            current = getter() or {}
            state = current.get('status')
            if not running(current):
                message = current.get('message') or f'{name}更新完成'
                source_partial = state is None and any(word in message for word in ['暫不可用', '未完成', '缺漏', '失敗'])
                result = 'partial' if source_partial or state == 'partial' else 'complete' if state in {'complete', 'idle', None} else 'failed'
                _module(name, result, message, current.get('progress'))
                return result
            _module(name, 'running', current.get('message') or f'{name}更新中', current.get('progress'))
            if time.monotonic() >= deadline:
                _module(name, 'partial', f'{name}仍在背景處理，可稍後查看分頁')
                return 'partial'
            time.sleep(1)
    except Exception as error:
        _module(name, 'failed', str(error)[:180] or f'{name}更新失敗')
        return 'failed'


def _run_one(name):
    if name == '追蹤分點':
        result = _wait(name, service.sync, service.job_status, lambda x: x.get('status') == 'running')
        rs_latest = _main_value('SELECT MAX(date) FROM rs_days')
        branch_latest = _main_value("SELECT MAX(s.date) FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed'")
        if result == 'complete' and rs_latest and branch_latest and branch_latest < rs_latest:
            _module(name, 'partial', f'個股 RS 已至 {rs_latest}，本機分點榜單仍至 {branch_latest}；等待新榜單發布後，請手動更新')
            return 'partial'
        return result
    if name == '預估本益比河流圖':
        _module(name,'running','核對最新保存行情與EPS；不下載共識、不發布策略')
        value=forward_pe_refresh.update_home()
        _module(name,value['status'],value['message'],value.get('detail'))
        return value['status']
    if name == '景氣循環分析':return _wait(name, business_cycle.refresh, business_cycle.job_status, lambda x: x.get('status') == 'running', timeout=240)
    if name == '月份漲跌統計':return _wait(name, monthly_statistics.refresh, lambda: monthly_statistics.report().get('job', {}), lambda x: x.get('status') == 'running', timeout=900)
    if name == '融資維持率統計':return _wait(name, margin_refresh.refresh, margin_refresh.status, lambda x: x.get('status') == 'running', timeout=1800)
    if name == '全市場主力':return _wait(name, major.start_refresh, major.refresh_status, lambda x: x.get('status') == 'running')
    if name == '券商關注':return _wait(name, broker_focus.refresh, broker_focus.job_status, lambda x: x.get('status') == 'running')
    if name == '個股 RS':return _wait(name, rs.refresh, rs.job_status, lambda x: x.get('status') == 'running')
    if name == '每日跟單':
        _module(name,'running','先補最新候選的歷史驗證，再建立每日名單')
        try:
            repair=daily_follow.refresh_latest_history();value=daily_follow.generate_all();created=len(value['created'])
            rs_latest=_main_value('SELECT MAX(date) FROM rs_days')
            branch_latest=_main_value("SELECT MAX(s.date) FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed'")
            waiting=bool(rs_latest and branch_latest and branch_latest<rs_latest)
            result='partial' if repair['failed'] or waiting else 'complete'
            message=(f"每日跟單已更新至 {value.get('latest') or '尚無同日資料'} · "
                     f"近期驗證 {repair['succeeded']}/{repair['planned']} 組 · "
                     f"可評分買方 {repair['buy_ready']} 檔／賣方 {repair['sell_ready']} 檔 · 新增 {created} 日")
            if repair['failed']:message+=f"；{repair['failed']} 組來源待手動更新"
            if waiting:
                message+=f'；個股 RS 已至 {rs_latest}，分點榜單仍至 {branch_latest}，待新榜單發布後，請手動更新'
                _module('追蹤分點','partial',f'個股 RS 已至 {rs_latest}，本機分點榜單仍至 {branch_latest}；等待新榜單發布後，請手動更新')
            _module(name,result,message,{'repair':repair});return result
        except Exception as error:_module(name,'failed',str(error)[:180] or '每日跟單更新失敗');return 'failed'
    if name == 'Alpha／Beta 排行':return _wait(name, alpha_beta.refresh, alpha_beta.job_status, lambda x: x.get('status') == 'running', timeout=1200)
    if name == 'ETF RS':return _wait(name, etf_rs.refresh, etf_rs.job_status, lambda x: x.get('status') == 'running')
    if name == '主動 ETF 動向':return _wait(name, active_etf.refresh, active_etf.job_status, lambda x: x.get('status') == 'running', timeout=600)
    if name == '內部人持股異動':return _wait(name, insider_changes.refresh, lambda: insider_changes.report()['job'], lambda x: x.get('status') == 'running', timeout=180)
    if name == '殖利率排行':return _wait(name, dividend_yields.refresh, lambda: dividend_yields.report()['job'], lambda x: x.get('status') == 'running', timeout=180)
    if name == '市場觀測':return _wait(name, lambda: market_monitor.refresh(), lambda: market_monitor.report().get('job', {}), lambda x: bool(x.get('running')))
    if name == '族群流向':return _wait(name, lambda: sector_flow.refresh(False), sector_flow.status, lambda x: x.get('status') == 'running', timeout=1200)
    if name == '每日鉅額交易':return _wait(name, block_trades.refresh, lambda: block_trades.report().get('job',{}), lambda x: x.get('status') == 'running')
    if name == '權證分析':return _wait(name, warrants.refresh, warrants.job_status, lambda x: x.get('status') == 'running', timeout=180)
    if name == '券商風雲榜':
        _module(name, 'running', '依最新分點資料重算並保存')
        try:
            value = leaders.report(refresh=True);_module(name, 'complete', f"已保存至 {value.get('as_of') or '最新交易日'}");return 'complete'
        except Exception as error:_module(name, 'failed', str(error)[:180] or '券商風雲榜更新失敗');return 'failed'
    if name == 'V9 台指期':
        _module(name, 'running', '只補新交易日的台指期契約行情')
        try:
            value = v9_tx.refresh()
            result = value['status']
            _module(name, result, value['message'])
            return result
        except Exception as error:
            _module(name, 'failed', str(error)[:180] or 'V9 台指期更新失敗')
            return 'failed'
    raise ValueError('找不到可更新的資料項目')


def start():
    _restore()
    if not _lock.acquire(blocking=False):
        return status()
    _set(status='running', message='準備更新全部分頁', started_at=now(), finished_at=None, done=0, total=len(NAMES),
         modules=[{'name': name, 'status': 'queued', 'message': '等待更新'} for name in NAMES])

    def worker():
        results = []
        try:
            for name in NAMES:results.append(_run_one(name))

            failed = results.count('failed'); partial = results.count('partial')
            final = 'complete' if not failed and not partial else 'partial'
            message = '全部分頁資料已更新'
            if failed or partial:
                message = f'全部更新結束；{failed} 項失敗、{partial} 項仍有缺漏，請手動更新；成功資料已保留'
            _set(status=final, message=message, finished_at=now())
            if final=='complete':db.update_settings({'home_full_success_date':datetime.now(TZ).date().isoformat()})
        except Exception:
            _set(status='failed', message='全部更新未完成；成功的分頁資料已保留', finished_at=now())
        finally:
            _lock.release()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        _set(status='failed', message='無法啟動全部更新', finished_at=now())
        _lock.release()
        raise
    return status()


def retry(name):
    _restore()
    if name not in NAMES:raise ValueError('找不到可更新的資料項目')
    if not _lock.acquire(blocking=False):return status()
    _set(status='running',message=f'準備重新更新：{name}',started_at=now(),finished_at=None)
    _module(name,'queued','等待重新更新')

    def worker():
        try:
            result=_run_one(name)
            rows=status()['modules'];failed=sum(r['status']=='failed' for r in rows);partial=sum(r['status']=='partial' for r in rows)
            overall='complete' if not failed and not partial else 'partial'
            message=f'{name}已重新更新完成' if result=='complete' else f'{name}重新更新後仍有缺漏' if result=='partial' else f'{name}重新更新失敗；其他成功資料已保留'
            _set(status=overall,message=message,finished_at=now())
        except Exception as error:
            _module(name,'failed',str(error)[:180] or f'{name}更新失敗');_set(status='partial',message=f'{name}重新更新失敗；其他成功資料已保留',finished_at=now())
        finally:_lock.release()
    try:threading.Thread(target=worker,daemon=True).start()
    except Exception:
        _set(status='failed',message=f'無法啟動{name}更新',finished_at=now());_lock.release();raise
    return status()
