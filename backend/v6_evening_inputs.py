"""Strict minute-bar/point-in-time feature input contract for evening research."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import csv,hashlib,json,math
import pandas as pd
from pathlib import Path
TZ=ZoneInfo('Asia/Taipei')

def stamp(x):
    d=datetime.fromisoformat(x)
    if d.tzinfo is None:raise ValueError('實際時間必須含時區，不能猜測夜盤日期')
    return d.astimezone(TZ)

def read_bars(path):
    out=[];seen={};issues=[]
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        for line,row in enumerate(csv.DictReader(f),2):
            start,end=stamp(row['bar_start_at']),stamp(row['bar_end_at'])
            if not start<end or end-start>timedelta(minutes=1):raise ValueError(f'第{line}列不是有效1分K')
            contract=row['contract'];symbol=row['symbol']
            if symbol!='TX' or len(contract)!=6 or not contract.isdigit() or not 1<=int(contract[4:])<=12:raise ValueError('需要TX各實際到期月合約，不接受連續／價差合約')
            datetime.fromisoformat(row['trading_date'])
            values={k:float(row[k]) for k in ('open','high','low','close','volume')}
            if any(not math.isfinite(x) for x in values.values()) or min(values[k] for k in ('open','high','low','close'))<=0 or values['volume']<0:raise ValueError('價格／量無效')
            if not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:raise ValueError('OHLC範圍不符')
            available=stamp(row['available_at']) if row.get('available_at') else None
            if available and available<end:raise ValueError('K棒結束前不可取得其完整高低收')
            key=(contract,end.isoformat())
            normalized={**values,'symbol':symbol,'contract':contract,'trading_date':row['trading_date'],
                'bar_start_at':start.isoformat(),'bar_end_at':end.isoformat(),
                'available_at':available.isoformat() if available else None,'source':row.get('source','')}
            if key in seen:
                if seen[key]!=normalized:raise ValueError('重複分K內容衝突')
                continue
            seen[key]=normalized;out.append(normalized)
            if not available:issues.append({'line':line,'issue':'缺歷史可取得時間，只盤點、不直接宣稱同時刻可用'})
    if not out:raise ValueError('只有欄名，尚無歷史分時資料')
    return {'rows':sorted(out,key=lambda r:(r['bar_end_at'],r['contract'])),'issues':issues,
        'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'historicalMinuteData':True}

def at_cutoff(bars,cutoff,contract):
    """Only complete, already available bars; never end-of-night high/low."""
    cut=stamp(cutoff);begin=cut.replace(hour=15,minute=0,second=0,microsecond=0)
    if cut<begin:begin-=timedelta(days=1)
    rows=[r for r in bars if r['contract']==contract and r.get('available_at') and begin<=stamp(r['bar_start_at']) and stamp(r['bar_end_at'])<=cut and stamp(r['available_at'])<=cut]
    if not rows:return None
    rows.sort(key=lambda r:r['bar_end_at']);last=rows[-1]
    if stamp(rows[0]['bar_start_at'])!=begin or any(stamp(b['bar_start_at'])!=stamp(a['bar_end_at']) for a,b in zip(rows,rows[1:])):return None
    if cut-stamp(last['bar_end_at'])>timedelta(minutes=5):return None
    return {'price':last['close'],'quote_at':last['bar_end_at'],'available_at':last['available_at'],
        'high_so_far':max(r['high'] for r in rows),'low_so_far':min(r['low'] for r in rows),
        'volume_so_far':sum(r['volume'] for r in rows),'contract':contract,'bars':len(rows)}

def feature_asof(rows,cutoff):
    cut=stamp(cutoff);out={}
    for r in rows:
        if r.get('publication_verified') not in (True,'true','True','1') or len(r.get('source_sha256',''))!=64 or not r.get('available_at'):continue
        available=stamp(r['available_at'])
        if available>cut or stamp(r['event_at'])>cut:continue
        key=r['key']
        if key not in out or available>stamp(out[key]['available_at']):out[key]=r
    return out

def seasonal(prices,origin):
    """Recompute complete-month seasonality before origin; never read UI totals."""
    f=prices.loc[prices.index<origin].copy();month=origin[5:7]
    f.index=pd.to_datetime(f.index);ends=f.close.resample(pd.offsets.MonthEnd()).last()
    # The current partial month cannot supply a known final-month return.
    ends=ends[ends.index.strftime('%Y-%m')<origin[:7]];returns=ends.pct_change().dropna();values=returns[returns.index.strftime('%m')==month]
    day=int(origin[-2:])
    return {'pastFullMonths':len(values),'monthUpRate':float((values>0).mean()) if len(values) else None,
        'monthMeanReturn':float(values.mean()) if len(values) else None,'phase':'early' if day<=10 else 'middle' if day<=20 else 'late',
        'dataThrough':str(f.index[-1].date()) if len(f) else None,'usedInLiveModel':False}
