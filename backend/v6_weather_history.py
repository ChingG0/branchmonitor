"""Explicit, offline historical reconstruction; never backdate manual forecasts.

Uses the frozen V6 model and backward-aligned inputs. Historical source files may
contain revisions and do not prove original publication availability. This is a
research replay, not a point-in-time certified track record.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import db, liquidity_river, v6_features, v6_tx, v6_weather

FEATURES = ('vix', 'feargreed', 'twd', 'oil', 'spx', 'tsm', 'rates-proxy',
            'financial', 'electronics', 'foreign', 'retail')
OVERSEAS = {'vix', 'feargreed', 'twd', 'oil', 'spx', 'tsm', 'rates-proxy'}


def _features_at(row, cutoff):
    result = {}
    for key in FEATURES:
        value, available = row.get(key + '_value'), row.get(key + '_available')
        if pd.isna(value) or pd.isna(available) or not math.isfinite(float(value)):
            continue
        published = pd.Timestamp(available)
        if published > cutoff:
            continue
        observed = published.date() - timedelta(days=1) if key in OVERSEAS else published.date()
        result[key] = {'value': round(float(value), 4), 'as_of': observed.isoformat(),
                       'available_at': published.isoformat()}
    if 'foreign' in result and pd.notna(row.get('foreign_net_short')):
        result['foreign_net_short'] = {**result['foreign'], 'value': float(row['foreign_net_short'])}
    return result


def _dix_at(rows, cutoff):
    # DIX has no vintage/publication timestamp in the public CSV. Use the prior
    # US date for context only, never include it in the historical V6 score.
    eligible = [row for row in rows if row['date'] < cutoff.date().isoformat()]
    if not eligible:
        return []
    row = max(eligible, key=lambda item: item['date'])
    if (cutoff.date() - datetime.fromisoformat(row['date']).date()).days > 4:
        return []
    items = []
    for key, label, factor, unit in [('dix', '美國 DIX 暗池指標', 100, '%'),
                                     ('gex', '美國 GEX 估值', 1, '美元')]:
        try:
            value = float(row[key]) * factor
            if not math.isfinite(value):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        items.append({'category': 'darkpool', 'label': label, 'value': value, 'unit': unit,
                      'as_of': row['date'], 'source': 'SqueezeMetrics 公開歷史 CSV',
                      'url': 'https://squeezemetrics.com/monitor/dix',
                      'reading': '事後補充的前一美國交易日觀測；原始發布時刻未保存，不納入歷史 V6 分數。'})
    return items


def reconstruct(frame, score, available, *, start, end, river_points, dix_rows, generated_at):
    """Pure assembly with explicit historical cutoff; no latest-data fallback."""
    river = {row['date']: row for row in river_points}
    points = []
    for day, row in frame.iterrows():
        cutoff = pd.Timestamp(row['decision_at'])
        decision_day = cutoff.date().isoformat()
        if not start <= decision_day <= end or cutoff > pd.Timestamp(generated_at):
            continue
        features = _features_at(row, cutoff)
        missing = v6_tx._source_age(frame, day)
        if not bool(available.loc[day]):
            missing.append('model_features')
        # A malformed/aligned future row must never produce a historical score.
        for key in FEATURES:
            published = row.get(key + '_available')
            if pd.notna(published) and pd.Timestamp(published) > cutoff:
                missing.append(key)
        value = float(score.loc[day]) * 100
        value = round(value, 2) if not missing and math.isfinite(value) else None
        indicators = v6_weather._local_indicators({'feature_snapshot': features},
                                                 river_points=[river[day]] if day in river else [])
        indicators.insert(0, {'category': 'valuation', 'label': '前一交易日加權收盤',
            'value': float(row['close']), 'unit': '點', 'as_of': day,
            'source': 'V6 研究保存的加權指數', 'url': 'https://www.twse.com.tw/zh/trading/historical/stock-day.html',
            'reading': '盤前只取前一交易日收盤，不使用當日收盤。'})
        indicators += _dix_at(dix_rows, cutoff)
        for item in indicators:
            assert item['as_of'] <= decision_day
        points.append({'date': decision_day, 'decision_at': cutoff.isoformat(),
                       'market_as_of': day, 'score': value, 'missing_sources': sorted(set(missing)),
                       'indicators': indicators, 'news': [],
                       'missing_context': ['當時人工新聞研究', '集保大戶／散戶持股分級', '台指夜盤逐時變化']})
    return {'kind': 'retrospective', 'generated_at': generated_at,
            'requested_start': start, 'requested_end': end,
            'start': points[0]['date'] if points else None, 'end': points[-1]['date'] if points else None,
            'model_version': v6_tx.VERSION, 'points': points,
            'note': '以 2026/10/17 固定的 V6 模型事後重算；參數使用截至 2022 年資料擬合，策略選擇曾參考後續歷史。日期為台灣盤前 08:30，台股用前一交易日收盤、海外使用此前可對齊的日資料。這不是當日曾發布的人工預警，也不屬於前瞻績效驗證。歷史來源可能修訂，公布時刻依研究假設，無法保證原始版本完全一致。DIX／GEX 與融資資訊僅補充觀察，未加入原模型評分；新聞與集保歷史仍缺漏。分數不是下跌機率，低於門檻不表示安全。'}


def build(start, end, dix_path=None):
    v6_weather._date(start)
    v6_weather._date(end)
    if start > end:
        raise ValueError('回溯起日不可晚於迄日')
    raw, _ = v6_tx._complete_input()
    with v6_tx._FEATURE_LOCK:
        old = v6_features.RAW
        try:
            v6_features.RAW = v6_tx.LIVE if v6_tx.LIVE.exists() else v6_tx.SEED / 'raw'
            frame = v6_features.build(raw)
        finally:
            v6_features.RAW = old
    score, available = v6_tx._score(frame)
    dix_rows = []
    if dix_path:
        with Path(dix_path).open(encoding='utf-8-sig', newline='') as handle:
            dix_rows = list(csv.DictReader(handle))
    result = reconstruct(frame, score, available, start=start, end=end,
                         river_points=liquidity_river.report().get('points') or [],
                         dix_rows=dix_rows, generated_at=datetime.now(v6_weather.TZ).isoformat(timespec='seconds'))
    model_path = v6_tx.SEED / 'statistical-results.json'
    result['model_sha256'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if dix_path:
        result['dix_sha256'] = hashlib.sha256(Path(dix_path).read_bytes()).hexdigest()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Manually save a clearly labelled V6 historical reconstruction')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--dix', type=Path)
    parser.add_argument('--output', type=Path, default=db.DATA / 'v6_weather_retrospective.json')
    args = parser.parse_args()
    result = build(args.start, args.end, args.dix)
    if not result['points']:
        raise SystemExit('無可回溯資料；未寫入')
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(args.output)
    print(json.dumps({'start': result['start'], 'end': result['end'], 'days': len(result['points'])}, ensure_ascii=False))
