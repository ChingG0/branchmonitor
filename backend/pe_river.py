"""Retrospective TAIEX valuation river; isolated from strategy/backtest inputs."""
from __future__ import annotations
import calendar
import io
import json
import math
import re
import threading
import zipfile
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
from . import db, liquidity_river

TZ = ZoneInfo('Asia/Taipei')
SOURCE = 'https://www.twse.com.tw/zh/trading/statistics/index04.html?tm=04'
INDEX_SOURCE = 'https://www.twse.com.tw/zh/trading/historical/fmtqik.html'
MULTIPLES = [4, 8, 12, 16, 20, 24]
_lock = threading.Lock()


def shift_month(day, offset):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1]))


def number(value):
    value = float(str(value).replace(',', '').strip())
    if not math.isfinite(value) or value <= 0:
        raise ValueError('估值或指數數值無效')
    return value


def parse_month(raw, month):
    import xlrd
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [f for f in archive.infolist() if f.filename.lower().endswith('.xls')]
        if len(files) != 1 or files[0].file_size > 5_000_000:
            raise ValueError('本益比月報檔案格式改變')
        book = xlrd.open_workbook(file_contents=archive.read(files[0]))
    sheet = book.sheet_by_name('new')
    if not any('本益比' in re.sub(r'\s', '', str(v)) for row in range(min(5, sheet.nrows)) for v in sheet.row_values(row)):
        raise ValueError('月報缺少本益比欄位')
    stamp = xlrd.xldate_as_datetime(sheet.cell_value(1, 0), book.datemode).date().isoformat()
    if stamp[:7] != month:
        raise ValueError('月報日期不符')
    rows = [sheet.row_values(i) for i in range(sheet.nrows) if re.sub(r'\s', '', str(sheet.cell_value(i, 0))) == '大盤']
    if len(rows) != 1:
        raise ValueError('月報缺少唯一的大盤估值')
    pe = number(rows[0][5])
    if pe > 300:
        raise ValueError('大盤本益比超出合理範圍')
    return dict(month=month, date=stamp, pe=pe)


def parse_closes(raw, month):
    data = json.loads(raw)
    fields = ['日期', '成交股數', '成交金額', '成交筆數', '發行量加權股價指數', '漲跌點數']
    if data.get('stat', '').lower() != 'ok' or data.get('fields') != fields:
        raise ValueError('大盤每日收盤格式改變')
    rows = {}
    for row in data.get('data', []):
        y, m, d = map(int, row[0].split('/'))
        stamp = date(y + 1911, m, d).isoformat()
        if stamp[:7] != month:
            raise ValueError('收盤資料月份不符')
        rows[stamp] = number(row[4])
    if not rows:
        raise ValueError('尚無當月收盤資料')
    return rows


def cached():
    try:
        state = json.loads((db.DATA / 'market_pe_cache.json').read_text())
        if not all(isinstance(state.get(k), dict) for k in ('months', 'closes', 'price_months')):
            raise ValueError('快取格式異常')
        return state
    except (OSError, ValueError):
        return dict(months={}, closes={}, price_months={})


def build(state, today=None):
    today = today or datetime.now(TZ).date()
    closes = {d: v for d, v in state['closes'].items() if d <= today.isoformat()}
    anchors = []
    for row in sorted(state['months'].values(), key=lambda r: r['date']):
        days = [d for d in closes if d[:7] == row['month'] and d <= row['date']]
        if not days:
            continue
        stamp = max(days)
        # A monthly report without its month-end close must not use an earlier partial quote.
        if (date.fromisoformat(row['date']) - date.fromisoformat(stamp)).days > 7:
            continue
        close = closes[stamp]
        anchors.append(dict(**row, close_date=stamp, index_close=close, earnings_proxy=close / row['pe']))
    points = []
    for stamp, close in sorted(closes.items()):
        eligible = [a for a in anchors if a['date'] <= stamp]
        if not eligible:
            continue
        anchor = eligible[-1]
        if (date.fromisoformat(stamp) - date.fromisoformat(anchor['date'])).days > 62:
            continue
        eps = anchor['earnings_proxy']
        points.append(dict(date=stamp, close=close, estimated_pe=close / eps, anchor_date=anchor['date'], bands=[eps * m for m in MULTIPLES]))
    if points:
        start = shift_month(date.fromisoformat(points[-1]['date']), -12).isoformat()
        points = [p for p in points if p['date'] >= start]
    return dict(available=bool(points), as_of=points[-1]['date'] if points else None, points=points,
                multiples=MULTIPLES, official=anchors[-1] if anchors else None,
                checked_at=state.get('checked_at'), warning=state.get('warning', ''),
                source_url=SOURCE, index_source_url=INDEX_SOURCE,
                note='官方月末本益比＋每日指數收盤。河流＝月末指數÷同月官方本益比×倍數，基準沿用至下一月末；每日本益比為推估。歷史月報在次月公布，圖表為事後估值回顧，不代表當日已知資訊，不納入策略回測或買賣判斷。')


def report():
    result = build(cached())
    result['liquidity'] = liquidity_river.report()
    return result


def refresh(fetch):
    with _lock:
        state = cached()
        today = datetime.now(TZ).date()
        target = today
        # Recheck the current month on demand, and finalize it once after month rollover.
        # Completed historical months stay cached; missing months remain retryable.
        completed = state.setdefault('complete_price_months', [])
        months = [shift_month(target.replace(day=1), i).strftime('%Y-%m') for i in range(-13, 1)]
        errors, published = [], {}
        for year in sorted({m[:4] for m in months}):
            try:
                data = json.loads(fetch(f'https://www.twse.com.tw/rwd/zh/statistics/download?type=04&date={year}0101&response=json'))
                if data.get('stat', '').lower() != 'ok':
                    raise ValueError('月報目錄尚未提供')
                row = next(r for r in data['data'] if r[0] == '大盤、各產業類股及上市股票本益比、殖利率及股價淨值比')
                for i, value in enumerate(row[1:13], 1):
                    path = parse_qs(urlparse(str(value)).query).get('url', [''])[0]
                    if re.fullmatch(r'/staticFiles/inspection/inspection/04/001/\d{6}_C04001\.zip', path):
                        published[f'{year}-{i:02d}'] = 'https://www.twse.com.tw' + path
            except Exception:
                errors.append(f'{year} 月報目錄暫不可用')
        for month in months:
            if month in published and month not in state['months']:
                try:
                    row = parse_month(fetch(published[month]), month)
                    state['months'][month] = dict(**row, source_url=published[month])
                except Exception:
                    errors.append(f'{month} 本益比月報待補')
            if month not in completed:
                try:
                    rows = parse_closes(fetch('https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json&date=' + month.replace('-', '') + '01'), month)
                    rows = {d: v for d, v in rows.items() if d <= today.isoformat()}
                    state['closes'].update(rows)
                    if rows:
                        state['price_months'][month] = max(rows)
                        if month < today.strftime('%Y-%m'):
                            completed.append(month)
                except Exception:
                    errors.append(f'{month} 指數收盤待補')
        if not published:
            errors.append('尚未取得可驗證的官方月報目錄')
        state['checked_at'] = datetime.now(TZ).isoformat(timespec='seconds')
        state['warning'] = '；'.join(errors)
        result = build(state)
        if not result['available']:
            raise ValueError(state['warning'] or '尚無可對齊的官方估值與收盤資料')
        db.DATA.mkdir(parents=True, exist_ok=True)
        path = db.DATA / 'market_pe_cache.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
        tmp.replace(path)
        try:
            result['liquidity'] = liquidity_river.refresh(fetch)
        except ValueError as exc:
            result['liquidity'] = liquidity_river.report()
            result['liquidity']['warning'] = str(exc)
        if result['liquidity']['warning']:
            result['warning'] = '；'.join(x for x in (result['warning'], result['liquidity']['warning']) if x)
        return result
