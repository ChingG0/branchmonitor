"""Deterministic daily DMI and Parabolic SAR signals for frozen research evidence."""
from __future__ import annotations
import math

VERSION='dmi-sar-v1'
PERIOD=14
SAR_STEP=.02
SAR_MAX=.20


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _value(value,digits=4):
    return round(value,digits) if finite(value) else None


def classify(plus,minus,adx,prev_plus,prev_minus,prev_adx,bullish_days=0,sessions_since_cross=None):
    if not all(finite(v) for v in [plus,minus,adx]):
        return {'dmi_pass':False,'status':'insufficient','label':'DMI資料不足','cross_up_today':False,
                'adx_rising':None,'strong_trend':False}
    spread=plus-minus;previous=(prev_plus-prev_minus) if finite(prev_plus) and finite(prev_minus) else None
    passed=spread>0;cross=passed and previous is not None and previous<=0
    rising=adx>=prev_adx if finite(prev_adx) else None
    if not passed:status,label='bearish','DMI空方／轉弱'
    elif cross and adx<=15:status,label='early_turn','早期轉強'
    elif bullish_days>=2 and sessions_since_cross is not None and sessions_since_cross<=2 and rising:
        status,label='early_confirmed','早期轉強確認'
    elif adx>=20:status,label='bullish_continuation','多方延續'
    else:status,label='bullish_unconfirmed','多方未確認'
    return {'dmi_pass':passed,'status':status,'label':label,'cross_up_today':cross,'adx_rising':rising,
            'strong_trend':bool(passed and plus>=25 and adx>=20 and rising)}


def _dmi(rows,period):
    size=len(rows);plus=[None]*size;minus=[None]*size;adx=[None]*size;dx=[]
    true_range=[0.]*size;plus_dm=[0.]*size;minus_dm=[0.]*size
    for index in range(1,size):
        row,previous=rows[index],rows[index-1]
        up=row['high']-previous['high'];down=previous['low']-row['low']
        plus_dm[index]=up if up>down and up>0 else 0
        minus_dm[index]=down if down>up and down>0 else 0
        true_range[index]=max(row['high']-row['low'],abs(row['high']-previous['close']),abs(row['low']-previous['close']))
    if size<=period:return plus,minus,adx
    smooth_tr=sum(true_range[1:period+1]);smooth_plus=sum(plus_dm[1:period+1]);smooth_minus=sum(minus_dm[1:period+1])
    previous_adx=None
    for index in range(period,size):
        if index>period:
            smooth_tr=smooth_tr-smooth_tr/period+true_range[index]
            smooth_plus=smooth_plus-smooth_plus/period+plus_dm[index]
            smooth_minus=smooth_minus-smooth_minus/period+minus_dm[index]
        if smooth_tr<=0:continue
        plus[index]=100*smooth_plus/smooth_tr;minus[index]=100*smooth_minus/smooth_tr
        total=plus[index]+minus[index]
        current_dx=100*abs(plus[index]-minus[index])/total if total else 0
        dx.append(current_dx)
        if len(dx)==period:previous_adx=sum(dx)/period
        elif len(dx)>period:previous_adx=(previous_adx*(period-1)+current_dx)/period
        if previous_adx is not None:adx[index]=previous_adx
    return plus,minus,adx


def _sar(rows,step,max_step):
    size=len(rows);values=[None]*size
    if size<2:return values
    up=rows[1]['close']>=rows[0]['close']
    sar=rows[0]['low'] if up else rows[0]['high']
    extreme=max(rows[0]['high'],rows[1]['high']) if up else min(rows[0]['low'],rows[1]['low'])
    acceleration=step;values[1]=sar
    for index in range(2,size):
        candidate=sar+acceleration*(extreme-sar)
        if up:
            candidate=min(candidate,rows[index-1]['low'],rows[index-2]['low'])
            if rows[index]['low']<candidate:
                up=False;candidate=extreme;extreme=rows[index]['low'];acceleration=step
            elif rows[index]['high']>extreme:
                extreme=rows[index]['high'];acceleration=min(max_step,acceleration+step)
        else:
            candidate=max(candidate,rows[index-1]['high'],rows[index-2]['high'])
            if rows[index]['high']>candidate:
                up=True;candidate=extreme;extreme=rows[index]['high'];acceleration=step
            elif rows[index]['low']<extreme:
                extreme=rows[index]['low'];acceleration=min(max_step,acceleration+step)
        sar=candidate;values[index]=sar
    return values


def calculate(raw_rows,period=PERIOD,sar_step=SAR_STEP,sar_max=SAR_MAX):
    """Return a frozen latest-day signal; invalid or short input is explicit, never neutral."""
    unique={}
    for raw in raw_rows:
        try:
            row={'date':str(raw['date']),'high':float(raw['high']),'low':float(raw['low']),'close':float(raw['close'])}
        except (KeyError,TypeError,ValueError):continue
        if all(finite(row[key]) and row[key]>0 for key in ['high','low','close']) and row['high']>=row['low'] and row['low']<=row['close']<=row['high']:
            unique[row['date']]=row
    rows=sorted(unique.values(),key=lambda row:row['date'])
    minimum=period*2
    base={'version':VERSION,'date':rows[-1]['date'] if rows else None,'period':period,'available':False,
          'dmi_pass':False,'status':'insufficient','label':'DMI資料不足','sar':{'available':False,'step':sar_step,'max':sar_max}}
    if len(rows)<minimum:return {**base,'reason':f'有效日線不足 {minimum} 筆（目前 {len(rows)} 筆）','samples':len(rows)}
    recent=rows[-(period*2+1):]
    for previous,current in zip(recent,recent[1:]):
        if abs(current['close']/previous['close']-1)>.35:
            return {**base,'reason':'近期價格出現超過35%的不連續變動，DMI等待調整後行情','samples':len(rows)}
    plus,minus,adx=_dmi(rows,period);sar_values=_sar(rows,sar_step,sar_max);last=len(rows)-1;previous=last-1
    if not all(finite(v) for v in [plus[last],minus[last],adx[last]]):
        return {**base,'reason':'DMI平滑期尚未完成','samples':len(rows)}
    spreads=[(plus[i]-minus[i]) if finite(plus[i]) and finite(minus[i]) else None for i in range(len(rows))]
    bullish_days=0
    for value in reversed(spreads):
        if value is not None and value>0:bullish_days+=1
        else:break
    cross_index=None
    for index in range(last,period,-1):
        if spreads[index] is not None and spreads[index]>0 and spreads[index-1] is not None and spreads[index-1]<=0:
            cross_index=index;break
    sessions_since=(last-cross_index) if cross_index is not None else None
    state=classify(plus[last],minus[last],adx[last],plus[previous],minus[previous],adx[previous],bullish_days,sessions_since)
    sar=sar_values[last];previous_sar=sar_values[previous];sar_bullish=finite(sar) and sar<rows[last]['close']
    sar_flip=bool(sar_bullish and finite(previous_sar) and previous_sar>=rows[previous]['close'])
    sar_data={'available':finite(sar),'value':_value(sar),'bullish':bool(sar_bullish),'flipped_bullish':sar_flip,
              'label':'SAR當日翻多' if sar_flip else 'SAR多方確認' if sar_bullish else 'SAR尚未確認',
              'distance_pct':_value((rows[last]['close']-sar)/rows[last]['close']*100,2) if finite(sar) else None,
              'step':sar_step,'max':sar_max}
    return {**base,**state,'available':True,'reason':'','samples':len(rows),'close':_value(rows[last]['close']),
            'plus_di':_value(plus[last]),'minus_di':_value(minus[last]),'di_spread':_value(spreads[last]),'adx':_value(adx[last]),
            'previous_plus_di':_value(plus[previous]),'previous_minus_di':_value(minus[previous]),
            'previous_di_spread':_value(spreads[previous]),'previous_adx':_value(adx[previous]),
            'cross_date':rows[cross_index]['date'] if cross_index is not None else None,'bullish_days':bullish_days,
            'sessions_since_cross':sessions_since,'sar':sar_data}
