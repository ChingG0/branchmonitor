"""Package 6 read-only integration. Independent source failures stay visible."""
from __future__ import annotations
import sqlite3
from datetime import date,timedelta
from contextlib import closing
from . import db, forward_pe_store as S, forward_pe_river as R, forward_pe_sectors as C


def latest_market_day():
    with closing(sqlite3.connect((db.DATA/'monitor.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as c:
        return c.execute("SELECT MAX(date) FROM market_days WHERE twse_status='ok' AND tpex_status='ok' AND date<=?",(S.instant(S.now()).date().isoformat(),)).fetchone()[0]


def weights_current(weight_day,market_day):
    try:
        day=date.fromisoformat(market_day);weight=date.fromisoformat(weight_day)
        previous=(day.replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
        return previous<=weight.strftime('%Y-%m')<=day.strftime('%Y-%m') and weight<=day
    except (TypeError,ValueError):
        return None


def report():
    errors=[]
    try:
        river=R.report()
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        river={'status':'read_error','valuation':None,'series':[],'warnings':[]}
        errors.append('估值保存檔無法核對，請手動檢查來源；類股資料仍獨立顯示。')
    try:
        sectors=C.report()
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        sectors=None
        errors.append('類股來源無法讀取，請手動補齊市場資料中心；不自動重試。')
    latest=None
    try:
        latest=latest_market_day()
    except (OSError,sqlite3.Error):
        errors.append('最新交易日曆無法核對，未將舊行情標為最新。')
    price=(river.get('valuation') or {}).get('price_date')
    freshness='unknown' if not latest or not price else 'current' if price==latest else 'stale' if price<latest else 'unverified'
    if freshness=='stale':
        errors.append(f'估值股價仍為{price}，最新已核對收盤為{latest}；請手動保存新估值，暫不顯示目前位階。')
    weight_ok=weights_current((river.get('valuation') or {}).get('weight_date'),price)
    if weight_ok is False:
        errors.append('官方月底權重已過期，保留歷史參考；請在對話補齊，暫不顯示目前位階。')
    return {'version':'forward-pe-dashboard.v1','read_at':S.now(),'river':river,'sectors':sectors,
            'freshness':{'status':freshness,'latest_verified_market_date':latest,'price_date':price,'weight_status':'unknown' if weight_ok is None else 'current' if weight_ok else 'stale'},'errors':errors,
            'read_only':True,'source_update':'saved_inputs_only','eps_import':'conversation_manual','valuation_update':'home_or_conversation'}
