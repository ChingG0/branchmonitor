"""Read-only distance to dual SMA20 and explicit, non-predictive range scenarios."""
from __future__ import annotations
import math,sqlite3
from datetime import datetime,time,timedelta
from decimal import Decimal,ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db

VERSION='market-dual-ma20-range-v1'
TZ=ZoneInfo('Asia/Taipei')
SOURCE='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'
NOTE='使用已完成的本機日線；日振幅＝最高－最低，並非每日淨漲幅。天數是假設連續淨上漲的情境，非預測或機率；20MA隨假設收盤一起滾動。'

def number(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def good_close(row):return bool(row and number(row.get('close')) and row['close']>0)
def good_range(row):
    if not good_close(row):return False
    h,l=row.get('high'),row.get('low');op=row.get('open')
    return bool(number(h) and number(l) and h>=l>0 and l<=row['close']<=h and (op is None or number(op) and l<=op<=h))
def touch_days(closes,net_up):
    """First future close not below its contemporaneous rolling SMA20."""
    if len(closes)!=20 or not all(number(c) and c>0 for c in closes):return None
    if closes[-1]>=sum(closes)/20-1e-9:return 0
    if not number(net_up) or net_up<=0:return None
    history=list(closes);p0=closes[-1]
    for n in range(1,61):
        cp=p0+n*net_up;history.append(cp)
        if cp>=sum(history[-20:])/20-1e-9:return n
    return None
def _average_window(series,calendar,window,closes):
    dates=calendar[-window:];ranges=[];percentages=[]
    for d in dates:
        row=series.get(d)
        if not good_range(row):continue
        value=row['high']-row['low'];ranges.append(value)
        i=calendar.index(d)
        prior=series.get(calendar[i-1]) if i>0 else None
        if good_close(prior):percentages.append(value/prior['close']*100)
    ready=len(dates)==window and len(ranges)==window
    avg=sum(ranges)/window if ready else None
    gap=max(0,sum(closes)/20-closes[-1]) if len(closes)==20 else None
    return dict(window=window,samples=len(ranges),required=window,start=dates[0] if dates else None,end=dates[-1] if dates else None,
        ready=ready,average_points=avg,average_pct=sum(percentages)/window if ready and len(percentages)==window else None,
        amplitude_units=(gap/avg if avg and gap is not None else 0. if gap==0 else None),
        scenarios=[dict(fraction=f,net_up_points=avg*f if avg is not None else None,
            sessions=touch_days(closes,avg*f) if avg is not None and len(closes)==20 else None) for f in [.5,1.]],
        reason='' if ready and avg and avg>0 else '日振幅為0，無法換算推進天數' if ready else f'日K高低點不足 {len(ranges)}/{window}，不以收盤漲跌冒充振幅')

def build(rows,*,cutoff,fallback=()):
    by={s:{} for s in ['TAIEX','TPEX']}
    for row in rows:
        if row.get('symbol') in by and row['date']<=cutoff and good_close(row):by[row['symbol']][row['date']]=dict(row,ohlc_source='main_daily_bars')
    # Reuse official, cached TAIEX OHLC only when the same date's close agrees.
    # Never change the price table or any trading/account source.
    for row in fallback:
        prior=by['TAIEX'].get(row['date'])
        if prior and not good_range(prior) and good_range(row) and abs(row['close']-prior['close'])<=.011:
            prior.update(open=row.get('open'),high=row['high'],low=row['low'],ohlc_source='official_taiex_cache')
    latest={s:max(pp) if pp else None for s,pp in by.items()}
    shared=sorted(set(by['TAIEX'])&set(by['TPEX']))
    if not shared:return dict(version=VERSION,available=False,as_of=None,indices=[],both_below=None,note=NOTE,warnings=['尚無同日加權與櫃買日線，請先更新行情'])
    asof=shared[-1];calendar=sorted(d for d in by['TAIEX'] if d<=asof);indices=[];warnings=[]
    if any(d and d!=asof for d in latest.values()):warnings.append('雙指數資料日期不同，改用最近共同交易日比較；未混用不同日期')
    for symbol,title in [('TAIEX','加權指數'),('TPEX','櫃買指數')]:
        series=by[symbol];dates=calendar[-20:];closes=[series[d]['close'] for d in dates if d in series]
        cp=series[asof]['close'];ready=len(dates)==20 and len(closes)==20
        ma=sum(closes)/20 if ready else None
        raw_threshold=sum((Decimal(str(c)) for c in closes[-19:]),Decimal(0))/19 if ready else None
        threshold=float(raw_threshold.quantize(Decimal('0.01'),rounding=ROUND_CEILING)) if ready else None
        row=dict(symbol=symbol,title=title,as_of=asof,latest_available=latest[symbol],close=cp,ma20=ma,above=cp>=ma-1e-9 if ready else None,
            distance_points=cp-ma if ready else None,distance_pct=(cp/ma-1)*100 if ready else None,
            remaining_up_points=max(0,ma-cp) if ready else None,remaining_up_pct=max(0,ma/cp-1)*100 if ready else None,
            next_close_threshold=threshold,next_raw_threshold=float(raw_threshold) if ready else None,next_required_up_points=max(0,threshold-cp) if ready else None,
            ma_samples=len(closes),ma_ready=ready,range_windows={str(w):_average_window(series,calendar,w,closes if ready else []) for w in [5,20]},
            ohlc_sources={source:sum(series.get(d,{}).get('ohlc_source')==source for d in calendar[-20:]) for source in ['main_daily_bars','official_taiex_cache']})
        if not ready:row['warning']='20MA收盤資料不足，不壓縮缺漏交易日估算均線'
        indices.append(row)
    both=all(row['above'] is False for row in indices) if all(row['ma_ready'] for row in indices) else None
    return dict(version=VERSION,available=True,as_of=asof,indices=indices,both_below=both,note=NOTE,warnings=warnings,
        definition=dict(ma20='最近20個交易日收盤的簡單平均',distance_pct='(收盤/20MA−1)×100%',
            range='最高−最低；振幅%以各日前收盤為分母後取平均',
            next_threshold='最近19筆已知收盤的平均，再依指數0.01點報價向上取整；使下一日收盤不低於包含它自身的新20MA',
            scenario='每日淨上漲固定為所選平均振幅的50%或100%，逐日滾動20MA；不是預期報酬或機率'))

def _read(path,sql,args):
    if not path.exists():return []
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        try:return [dict(r) for r in c.execute(sql,args)]
        except sqlite3.OperationalError as e:
            if 'no such table' in str(e):return []
            raise
def report(path=None,*,research_path=None,now=None):
    stamp=now or datetime.now(TZ)
    if stamp.tzinfo is None:raise ValueError('市場觀測時間需含時區')
    stamp=stamp.astimezone(TZ);cutoff=stamp.date() if stamp.time()>=time(16) else stamp.date()-timedelta(days=1)
    rows=_read(Path(path) if path else db.DATA/'monitor.sqlite3',"SELECT date,symbol,open,high,low,close FROM prices WHERE symbol IN ('TAIEX','TPEX') AND date<=? ORDER BY date",(cutoff.isoformat(),))
    fallback=_read(Path(research_path) if research_path else db.DATA/'research.sqlite3','SELECT date,open,high,low,close FROM risk_index_bars WHERE date<=? ORDER BY date',(cutoff.isoformat(),))
    return build(rows,cutoff=cutoff.isoformat(),fallback=fallback)
