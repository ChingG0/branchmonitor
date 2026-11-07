"""Frozen V6 weather reports, published only after a chat-led review.

The dashboard is read-only. Publishing is a local CLI operation so a routine
homepage refresh cannot silently replace the analyst's dated assessment.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import db, liquidity_river, v6_tx, v6_forecast, v6_comparison, v6_short_simulation

TZ = ZoneInfo('Asia/Taipei')
STORE = db.DATA / 'v6_weather'
STAGES = {'形成', '增強', '高風險', '減弱', '平穩', '資料不足'}
CATEGORIES = [
    ('ownership', '散戶／大戶持股', '集保每週資料；觀察持股級距變化，不當成每日數據'),
    ('margin', '融資與維持率', '融資餘額、變化速度及維持率；晚間公布後才納入'),
    ('valuation', '大盤乖離與量價', '20／60 日均線乖離、成交量及市場廣度'),
    ('futures', '台指期與籌碼', '夜盤、期現價差、外資期貨部位與小台非三大法人部位'),
    ('global', '海外與匯率', '美股、VIX、台幣及利率；VIX 不提供方向'),
    ('darkpool', '美國暗池 DIX／GEX', 'SqueezeMetrics 標普 500 指標；DIX 看暗池情緒，GEX 看選擇權部位的波動背景，均不直接預測台股漲跌'),
    ('rotation', '族群輪動', '電子、金融、傳產強弱及權值股集中度'),
    ('events', '事件與新聞', 'CPI、央行、台積電法說、油價、地緣事件及具來源的重大新聞'),
]


def _url(value):
    parsed = urlparse(value)
    return parsed.scheme in ('https', 'http') and bool(parsed.netloc)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('資料日期需為 YYYY-MM-DD')
    return datetime.fromisoformat(value).date().isoformat()


def _text(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('V6 文字欄位空白或過長')
    return value.strip()


def _checked_items(items, *, news=False):
    if not isinstance(items, list) or len(items) > 60:
        raise ValueError('V6 資料／新聞清單格式不符')
    checked = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError('V6 資料／新聞列格式不符')
        source = _text(row.get('source', ''), 120)
        url = _text(row.get('url', ''), 1000)
        if not _url(url):
            raise ValueError('V6 資料來源需為 http(s) 網址')
        if news:
            checked.append({'title': _text(row.get('title', ''), 220),
                            'published_at': _text(row.get('published_at', ''), 40),
                            'source': source, 'url': url,
                            'impact': _text(row.get('impact', ''), 360),
                            'direction': row.get('direction', '觀察')})
        else:
            category = row.get('category')
            if category not in {item[0] for item in CATEGORIES}:
                raise ValueError('V6 監控分類無效')
            value = row.get('value')
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError('V6 指標數值無效')
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError('V6 指標數值需為有限值')
            checked.append({'category': category, 'label': _text(row.get('label', ''), 100),
                            'value': value, 'unit': str(row.get('unit', ''))[:30],
                            'as_of': _date(row.get('as_of')), 'source': source,
                            'url': url, 'reading': _text(row.get('reading', ''), 350)})
    return checked


def _local_indicators(model=None, *, river_points=None):
    """Capture available local observations at publish time, never at page read."""
    result = []
    try:
        points = (liquidity_river.report().get('points') or []) if river_points is None else river_points
        if points:
            row = points[-1]
            day = row['date']
            source = '臺灣證券交易所／本機價量河流'
            url = liquidity_river.SOURCE
            for length in (20, 60):
                ma = row.get(f'ma{length}')
                if ma and row.get('close'):
                    result.append({'category': 'valuation', 'label': f'加權指數 {length} 日乖離',
                                   'value': round((row['close'] / ma - 1) * 100, 2), 'unit': '%',
                                   'as_of': day, 'source': source, 'url': url,
                                   'reading': '價格與均線的距離；過高或過低都不單獨推論跌勢'})
            for key, label, unit, source_url in (
                    ('financing_units', '融資餘額', '交易單位', 'https://www.twse.com.tw/zh/trading/margin/mi-margn.html'),
                    ('maintenance_pct', '融資維持率估值', '%', 'https://www.twse.com.tw/zh/trading/margin/mi-margn.html'),
                    ('day_trade_pct', '當沖金額占比', '%', url)):
                value = row.get(key)
                if value is not None:
                    result.append({'category': 'margin' if key != 'day_trade_pct' else 'valuation',
                                   'label': label, 'value': round(float(value), 2), 'unit': unit,
                                   'as_of': day, 'source': source, 'url': source_url,
                                   'reading': '本機已保存的觀測值；需與前期變化合看'})
    except (OSError, ValueError, KeyError, TypeError):
        pass
    sources = {
        'vix': ('global', 'VIX', '', 'Cboe', 'https://www.cboe.com/tradable_products/vix/vix_historical_data', '預期波動，不代表下跌方向'),
        'feargreed': ('global', '恐懼貪婪', '/100', 'CNN', 'https://www.cnn.com/markets/fear-and-greed', '情緒指標；與價格確認合看'),
        'twd': ('global', '美元／台幣', '', 'Yahoo Finance', 'https://finance.yahoo.com/quote/TWD=X/', '數值上升表示台幣相對美元走弱'),
        'oil': ('events', '布蘭特原油', '美元', 'Yahoo Finance', 'https://finance.yahoo.com/quote/BZ=F/', '油價衝擊代理，並非戰爭預測'),
        'spx': ('global', '標普 500', '點', 'Yahoo Finance', 'https://finance.yahoo.com/quote/%5EGSPC/', '美股收盤；需核對台指夜盤反應'),
        'tsm': ('global', '台積電 ADR', '美元', 'Yahoo Finance', 'https://finance.yahoo.com/quote/TSM/', '海外權值股反應'),
        'rates-proxy': ('global', '美債殖利率代理', '', 'Yahoo Finance', 'https://finance.yahoo.com/quote/%5ETNX/', 'Yahoo ^TNX 報價；不是 Fed 政策利率'),
        'financial': ('rotation', '金融 ETF 0055', '元', 'Yahoo Finance', 'https://finance.yahoo.com/quote/0055.TW/', '金融族群代理，不能代表全部傳產'),
        'electronics': ('rotation', '電子 ETF 0052', '元', 'Yahoo Finance', 'https://finance.yahoo.com/quote/0052.TW/', '電子族群代理'),
        'foreign_net_short': ('futures', '外資台指期淨空單', '口', '臺灣期貨交易所', 'https://www.taifex.com.tw/cht/3/futContractsDate', '三大法人分類合計；不代表單一機構'),
        'retail': ('futures', '小台非三大法人部位比', '%', '臺灣期貨交易所／本機估算', 'https://www.taifex.com.tw/cht/3/futContractsDate', '非三大法人推估，不能逐戶辨認散戶'),
    }
    features = (model or {}).get('feature_snapshot') or {}
    for key, (category, label, unit, source, url, reading) in sources.items():
        row = features.get(key)
        if row and row.get('as_of'):
            result.append({'category': category, 'label': label, 'value': row['value'],
                           'unit': unit, 'as_of': row['as_of'], 'source': source,
                           'url': url, 'reading': reading})
    return result


def publish(draft, *, now=None, store=None, model=None, local_indicators=None):
    """Validate, freeze and atomically save one chat-prepared V6 report."""
    if not isinstance(draft, dict):
        raise ValueError('V6 報告必須是 JSON 物件')
    stamp = now or datetime.now(TZ)
    target = store or STORE
    stage = draft.get('stage')
    if stage not in STAGES:
        raise ValueError('V6 階段必須為形成、增強、高風險、減弱、平穩或資料不足')
    indicators = _checked_items(draft.get('indicators', []))
    news = _checked_items(draft.get('news', []), news=True)
    for key in ('drivers', 'watch_next', 'weakening'):
        if not isinstance(draft.get(key, []), list):
            raise ValueError(f'V6 {key} 必須為清單')
    if not news and not indicators:
        raise ValueError('至少需有一項已核對的資料或新聞')
    live_assessment = None
    if store is None and now is None:
        from . import v6_live_assessment
        try:
            live_assessment = v6_live_assessment.collect(_date(draft.get('market_as_of')))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            live_assessment = {'version': v6_live_assessment.VERSION, 'status': 'unavailable',
                'market_as_of': draft.get('market_as_of'), 'captured_at': datetime.now(TZ).isoformat(),
                'night': [], 'overseas': [], 'volatility': None,
                'assessment': {'state': '即時資料待核對', 'reading': str(error)[:180],
                    'drivers': [], 'watch_next': [], 'updates_probabilities': False,
                    'note': '無即時來源不等於安全；現有百分比仍為歷史基準。'}}
        # The true publication must follow quote acquisition, not precede it.
        stamp = datetime.now(TZ)
    frozen_model = model
    if frozen_model is None:
        try:
            frozen_model = v6_tx.report()
        except (OSError, ValueError, KeyError) as error:
            frozen_model = {'key': 'v6', 'status': 'failed', 'error': str(error)[:180]}
    checked = {
        'id': stamp.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8],
        'prepared_at': stamp.isoformat(timespec='seconds'),
        'market_as_of': _date(draft.get('market_as_of')),
        'stage': stage, 'summary': _text(draft.get('summary', ''), 1200),
        'drivers': [_text(item, 350) for item in draft.get('drivers', [])[:12]],
        'watch_next': [_text(item, 350) for item in draft.get('watch_next', [])[:12]],
        'weakening': [_text(item, 350) for item in draft.get('weakening', [])[:12]],
        'indicators': indicators,
        'live_assessment': live_assessment,
        'local_indicators': local_indicators if local_indicators is not None else _local_indicators(frozen_model),
        'news': news,
        'probability_note': '風險階段不是下跌機率；V6 模型分數與相似日收跌率未經未來樣本校準。',
        'model': frozen_model,
    }
    # Only a chat-led publication may create a new forecast. Old reports stay immutable.
    try:
        checked['forecast'] = v6_forecast.freeze(checked['market_as_of'], checked['prepared_at'])
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        checked['forecast'] = {'version': v6_forecast.VERSION, 'status': 'unavailable',
                               'reason': '預報資料不足：' + str(error)[:160]}
    if store is None and v6_comparison.SOURCES.exists():
        try:
            checked['comparison_id'] = v6_comparison.publish_current(live_assessment=live_assessment if live_assessment is not None else False)['id']
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            checked['comparison_error'] = '雙策略資料待補：' + str(error)[:160]
    target.mkdir(parents=True, exist_ok=True)
    path = target / (checked['id'] + '.json')
    temp = target / ('.' + checked['id'] + '.tmp')
    with temp.open('x', encoding='utf-8') as handle:
        json.dump(checked, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    # CLI publication only. HTTP/report/schedulers never call this observer.
    conditional_observation = None
    probability_history = None
    evening_observation = None
    if store is None and now is None:
        # New user-authorized18/22 flow; the sealed08:30 observer is archived.
        # It must not receive new captures or have its old clock restrictions edited.
        from . import v6_evening_forecast
        try:
            current=v6_evening_forecast.manual_update(live_assessment=live_assessment,weather_id=checked['id'],minute_bars=draft.get('evening_bars'),feature_history=draft.get('evening_feature_history'))
            evening_observation={k:current.get(k) for k in ('id','savedAt','informationCutoff','role','roleLabel','qualified')}
        except (OSError,ValueError,KeyError,TypeError) as error:
            evening_observation={'status':'source_or_integrity_failed','note':str(error),'keepsPreviousValidDisplay':True}
    return {'id': checked['id'], 'prepared_at': checked['prepared_at'], 'market_as_of': checked['market_as_of'],
            'conditional_observation': conditional_observation, 'probability_history': probability_history, 'evening_observation':evening_observation}


def report(report_id=None, store=None, historical_path=None):
    target = store or STORE
    history = []
    if target.exists():
        for path in sorted(target.glob('*.json'), reverse=True):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(row, dict) and row.get('id') == path.stem:
                    history.append(row)
            except (OSError, ValueError):
                continue
    selected = next((row for row in history if row['id'] == report_id), None) if report_id else (history[0] if history else None)
    history_file = historical_path or (target.parent / 'v6_weather_retrospective.json')
    try:
        historical = json.loads(history_file.read_text(encoding='utf-8'))
        if historical.get('kind') != 'retrospective' or not isinstance(historical.get('points'), list):
            historical = None
    except (OSError, ValueError, AttributeError):
        historical = None
    try:
        crash_scale = json.loads((target.parent / 'v6_crash_scale.json').read_text(encoding='utf-8'))
        if (not isinstance(crash_scale, dict) or crash_scale.get('kind') != 'observed_crash_scale'
                or not isinstance(crash_scale.get('as_of'), str)
                or (selected and crash_scale['as_of'] > selected['market_as_of'])):
            crash_scale = None
    except (OSError, ValueError, TypeError, KeyError):
        crash_scale = None
    audit = None; scorecard = {'saved': 0, 'rows': [], 'note': '新版預報尚待對話更新後累積。'}
    if any((row.get('forecast') or {}).get('targets') for row in history):
        try:
            index = v6_forecast.index_frame()
            audit = v6_forecast.audit(selected, index) if selected else None
            scorecard = v6_forecast.scorecard(history, index)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            audit = {'status': 'missing', 'targets': []}
    # A report owns the comparison created by the same publication. Its model
    # build may finish after the research timestamp; selecting it must not fall
    # back to an older partial snapshot or borrow a later unrelated revision.
    if selected and selected.get('comparison_id'):
        comparison = v6_comparison.report(selected['comparison_id'], store=target.parent/'v6_comparison')
    elif selected:
        comparison = v6_comparison.report(before=selected['prepared_at'], store=target.parent/'v6_comparison')
    else:
        comparison = {'snapshot': None, 'history': []}
    # The main execution panel now reads the saved V6-2 replay. Historical
    # reports must never borrow a replay created after their publication.
    primary = None
    if not report_id or selected:
        primary = v6_short_simulation.report(store=target.parent/'v6_short_simulation',
            before=selected['prepared_at'] if report_id and selected else None)
        owned = (comparison.get('snapshot') or {}).get('short_simulation')
        if owned and (not primary or datetime.fromisoformat(owned['generated_at']) > datetime.fromisoformat(primary['generated_at'])):
            primary = owned
    return {'short_simulation': primary, 'comparison': comparison, 'forecast_research': v6_forecast.research_summary(), 'forecast_audit': audit, 'forecast_scorecard': scorecard,
            'snapshot': selected, 'historical': historical, 'crash_scale': crash_scale,
            'history': [{'id': row['id'], 'prepared_at': row['prepared_at'],
                         'market_as_of': row['market_as_of'], 'stage': row['stage']}
                        for row in history[:100]],
            'categories': [{'key': key, 'label': label, 'description': description}
                           for key, label, description in CATEGORIES]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Save a chat-reviewed V6 weather report')
    parser.add_argument('draft', type=Path, help='UTF-8 JSON research draft with cited sources')
    args = parser.parse_args()
    print(json.dumps(publish(json.loads(args.draft.read_text(encoding='utf-8'))), ensure_ascii=False))
