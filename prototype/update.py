"""一次更新到本機可支援的最新交易日。

行情唯讀取自正式系統，原型不自行抓行情，也不寫入正式庫——因此「最新日期」是
正式系統已驗證的最後一個交易日。國際大廠報價是即時來源，每次都更新。
"""
from __future__ import annotations

import traceback
from datetime import datetime

import sectors
import build_board
import fetch_reference


def latest_local():
    with sectors.quotes() as conn:
        return sectors.calendar(conn, '9999')[-1]


def _run(days=20, force=False, log=print):
    """回傳這次更新的摘要。任一步失敗都記錄下來，不中斷其餘步驟。"""
    started = datetime.now()
    steps, errors = [], []

    try:
        target = latest_local()
    except Exception as error:
        return {'ok': False, 'errors': [f'讀取本機交易日失敗：{error}'], 'steps': [],
                'started_at': started.isoformat(timespec='seconds')}

    with sectors.store() as conn:
        computed = {r['date'] for r in conn.execute('SELECT DISTINCT date FROM sector_days')}
        known = {r['date'] for r in conn.execute('SELECT date FROM institutional_days')}

    # 0) 行情：正式系統尚未同步的交易日，原型自己補收盤價（不寫入正式庫）。
    try:
        result = fetch_reference.sync_prices()
        if result['added']:
            steps.append(f"自有行情 新增 {'、'.join(result['added'])}")
            log(f"  行情：新增 {result['added']}")
            target = latest_local()
        else:
            steps.append('行情 已是最新')
            log('  行情：沒有新的交易日')
        errors.extend(result['errors'])
    except Exception as error:
        errors.append(f'自有行情：{error}')
        log(f'  行情失敗：{error}')

    # 1) 三大法人：只補本機有行情的交易日，缺行情就換不出金額。
    if target not in known or force:
        try:
            result = fetch_reference.sync_institutional(days, refresh=force)
            steps.append(f"三大法人 新增 {len(result['added'])} 日")
            errors.extend(result['errors'])
            log(f"  法人：新增 {len(result['added'])} 日 {result['added'] or ''}")
        except Exception as error:
            errors.append(f'三大法人：{error}')
            log(f'  法人失敗：{error}')
    else:
        steps.append('三大法人 已是最新')
        log('  法人：已涵蓋最新交易日')

    # 2) 國際大廠報價：即時來源，每次都更新。
    try:
        result = fetch_reference.sync_partner_quotes()
        steps.append(f"國際報價 {result['saved']}/{result['total']} 家")
        errors.extend(result['errors'])
        log(f"  報價：{result['saved']}/{result['total']} 家")
    except Exception as error:
        errors.append(f'國際報價：{error}')
        log(f'  報價失敗：{error}')

    # 3) 族群指標：目標日尚未計算過才算，force 時重算。
    if target not in computed or force:
        try:
            result = sectors.compute(target, verbose=False)
            steps.append(f"族群指標 {result['groups']} 個（{target}）")
            log(f"  指標：{result['groups']} 個族群 · 視窗 {result['window_start']}～{target}")
        except Exception as error:
            errors.append(f'族群指標：{error}')
            log(f'  指標失敗：{error}')
    else:
        steps.append(f'族群指標 已有 {target}')
        log(f'  指標：{target} 已計算過')

    # 4) 重建看板，讓網頁拿到新資料。
    try:
        path, groups, stocks = build_board.build(target)
        steps.append(f'看板已重建（{path.stat().st_size // 1024} KB）')
        log(f'  看板：{path.name} 重建完成')
    except Exception as error:
        errors.append(f'看板重建：{error}')
        log(f'  看板重建失敗：{error}\n{traceback.format_exc()[:400]}')

    finished = datetime.now()
    with sectors.store() as conn:
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                     ('last_update', finished.isoformat(timespec='seconds')))
    return {'ok': not errors, 'target': target, 'steps': steps, 'errors': errors,
            'started_at': started.isoformat(timespec='seconds'),
            'finished_at': finished.isoformat(timespec='seconds'),
            'seconds': round((finished - started).total_seconds(), 1)}


def run(days=20, force=False, log=print):
    """Both the integrated tab and standalone server share one process-level lock."""
    import fcntl
    with (sectors.STORE.parent / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有族群更新執行中，請稍後重新讀取看板')
        try:
            return _run(days, force, log)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='更新到本機可支援的最新交易日')
    parser.add_argument('--days', type=int, default=20, help='法人買賣超回補交易日數')
    parser.add_argument('--force', action='store_true', help='重算已完成的項目')
    args = parser.parse_args()
    print(f'更新目標日：{latest_local()}')
    summary = run(args.days, args.force)
    print(f"\n{'完成' if summary['ok'] else '完成（有缺項）'} · {summary['seconds']} 秒")
    for line in summary['steps']:
        print('  ·', line)
    for line in summary['errors']:
        print('  ⚠', line)
