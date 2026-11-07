"""Chat-only frozen-M1 what-if calculation, not a validated intranight forecast.

Assumes the currently observed night price/high/low remain unchanged until the
scheduled night close. Never appends prospective events or supplies M2 inputs.
"""
from __future__ import annotations
import argparse, hashlib, json, math, uuid
from datetime import datetime, timedelta
from pathlib import Path
from . import db, v6_conditional as C

VERSION='v6-night-close-scenario-1.0.0'
STORE=db.DATA/'v6_night_scenarios'
ASSUMPTION='假設夜盤以目前價格收盤，且高低區間不再擴大；套用原08:30夜盤模型的情境值，不是已驗證的盤後預報。'

def calculate(context,prices,calendar,*,engine=None):
    engine=engine or C.Engine();stamp=C.stamp(context['captured_at']);anchor=context['market_as_of']
    if calendar!=sorted(set(calendar)) or anchor not in calendar:raise ValueError('缺少已保存交易日曆')
    targets=[d for d in calendar if d>anchor][:3]
    if len(targets)!=3:raise ValueError('後三個交易日不足')
    if C.at(anchor,13,30)>stamp:raise ValueError('現貨錨點尚未收盤')
    if str(prices.index[-1])!=anchor:raise ValueError('完整日線與錨點日期不符')
    history=prices.close.iloc[-61:].tolist()
    sigma=engine.dataset.sigma60(history)
    anchor_close=float(history[-1])
    vol=context.get('volatility') or {}
    if vol.get('as_of')!=anchor or abs(float(vol.get('close',0))-anchor_close)>.02:raise ValueError('即時來源與官方現貨錨點不符')
    start=C.at(anchor,15);end=start+timedelta(hours=14)
    if not start<=stamp<=end:raise ValueError('此情境僅適用目前進行中的同一夜盤，不能沿用舊盤')
    candidates=[]
    for n in context.get('night',[]):
        if n.get('included') is not True or n.get('state')!='ready' or n.get('day_close_date')!=anchor:continue
        at=C.stamp(n['quote_at']);age=(stamp-at).total_seconds()
        if not start<=at<=stamp or not 0<=age<=15*60:continue
        price,high,low,prior=[float(n[k]) for k in ('price','high','low','day_close')]
        if not all(math.isfinite(x) and x>0 for x in (price,high,low,prior)) or not low<=price<=high:continue
        if not str(n['contract']).isdigit() or len(str(n['contract']))!=6:continue
        candidates.append(n)
    if not candidates:raise ValueError('缺少新鮮且同合約日盤可核對的夜盤報價')
    night=min(candidates,key=lambda n:n['contract'])
    cut=C.at(targets[0],8,30)
    age_hours=(cut-end).total_seconds()/3600
    if age_hours<0:raise ValueError('夜盤結束與下一盤前截點衝突')
    keys=engine.models.FEATURES[:4]
    f={'night_return_scaled':math.log(night['price']/night['day_close'])/sigma,
       'night_range_scaled':math.log(night['high']/night['low'])/sigma,
       'log_daily_sigma':math.log(sigma),'night_age':math.log1p(age_hours/24)}
    points=[]
    for h,target in enumerate(targets,1):
        model=engine.params[f'H{h}:M1']
        if model['features']!=keys:raise ValueError('固定M1特徵與情境方法不符')
        mu=model['scaler']['mean'][:4];sd=model['scaler']['std_ddof0'][:4]
        # Only the four actual M1 inputs are used; missing foreign factors are
        # never filled with zero and M2 is not evaluated.
        x=engine.models.np.array([[(f[k]-mu[i])/sd[i] for i,k in enumerate(keys)]])
        raw=engine.models.initial_probability(model,x);p=float(raw['p_up'][0]);shown=engine.models.display_percentages(p)
        points.append({'horizon':h,'targetDate':target,'up':p,'down':1-p,
            'upPct':shown['up_percent'],'downPct':shown['down_percent'],
            'parameterSha256':model['parameter_sha256']})
    return {'version':VERSION,'kind':'night_close_what_if','isForecast':False,'addsProspectiveSamples':False,
        'anchorDate':anchor,'anchorClose':anchor_close,'savedAt':stamp.isoformat(),
        'assumption':ASSUMPTION,'validatedForThisTime':False,'forecastInformationCutoffAssumed':cut.isoformat(),
        'nightEndAssumed':end.isoformat(),'night':night,'dailySigma':sigma,'features':f,'targets':points,
        'foreignInputsUsed':False,'foreignNote':'原M1不含海外因子；美股、ADR與新聞不主觀加減此情境值。',
        'releaseSha256':C.digest((engine.config/'release.json').read_bytes())}

def history(*,store=None,now=None):
    """Read every saved vintage; refreshes never discard original values."""
    now=now or C.clock();rows=[]
    for p in Path(store or STORE).glob('*.json'):
        try:
            row=json.loads(p.read_text())
            if row.get('version')!=VERSION or row.get('id')!=p.stem:continue
            if row.get('isForecast') is not False or row.get('addsProspectiveSamples') is not False or C.stamp(row['savedAt'])>now:continue
            source=Path(row['contextFile'])
            if C.digest(source.read_bytes())!=row['contextSha256']:continue
            if row.get('releaseSha256')!=C.digest((C.CONFIG/'release.json').read_bytes()):continue
            if len(row['targets'])!=3:continue
            if any(t['horizon']!=i+1 or not .01<=t['up']<=.99 or abs(t['up']+t['down']-1)>1e-10 or abs(t['upPct']+t['downPct']-100)>1e-8 for i,t in enumerate(row['targets'])):continue
            if [t['targetDate'] for t in row['targets']]!=sorted(set(t['targetDate'] for t in row['targets'])) or row['targets'][0]['targetDate']<=row['anchorDate']:continue
            rows.append(row)
        except (OSError,ValueError,TypeError,KeyError):continue
    return sorted(rows,key=lambda row:(C.stamp(row['savedAt']),row['id']))

def latest(anchor,targets,*,store=None,now=None):
    rows=[r for r in history(store=store,now=now) if r['anchorDate']==anchor and [t['targetDate'] for t in r['targets']]==targets]
    return rows[-1] if rows else None

def history_report(*,store=None,outcome_store=None,now=None):
    now=now or C.clock();groups={};outcome_store=Path(outcome_store or db.DATA/'v6_night_scenario_outcomes')
    for row in history(store=store,now=now):
        key=(row['anchorDate'],row['anchorClose'],tuple((t['targetDate'],t['parameterSha256']) for t in row['targets']))
        if key not in groups:
            groups[key]={'rootId':row['id'],'anchorDate':row['anchorDate'],'anchorClose':row['anchorClose'],
                'assumption':row['assumption'],'snapshots':[],'outcomes':{}}
        from . import v6_night_points as P
        groups[key]['snapshots'].append({'pointEstimate':P.saved(row,now=now),'id':row['id'],'savedAt':row['savedAt'],'targets':row['targets'],
            'quoteAt':row['night']['quote_at'],'nightPrice':row['night']['price']})
    for group in groups.values():
        for t in group['snapshots'][0]['targets']:
            p=outcome_store/(group['rootId']+'-h'+str(t['horizon'])+'.json')
            try:
                o=json.loads(p.read_text());evidence=Path(o['priceEvidenceFile'])
                if o['rootId']!=group['rootId'] or o['targetDate']!=t['targetDate'] or o['anchorClose']!=group['anchorClose']:continue
                if C.stamp(o['gradedAt'])>now or C.stamp(o['availableAt'])>C.stamp(o['gradedAt']) or C.at(o['targetDate'],13,30)>C.stamp(o['gradedAt']):continue
                if o['up']!=(o['close']>=o['anchorClose']) or C.digest(evidence.read_bytes())!=o['priceEvidenceSha256']:continue
                group['outcomes'][str(t['horizon'])]=o
            except (OSError,ValueError,TypeError,KeyError):continue
    return {'version':'v6-night-scenario-history-1.0.0','groups':list(groups.values()),
        'note':'盤後情境的原值與各次實際保存；同一錨點與到期日才比較修訂，不混入08:30前瞻成績。'}

def mature(prices,*,store=None,outcome_store=None,now=None):
    """Explicit chat publication only. Save answers separately after maturity."""
    now=now or C.clock();outcome_store=Path(outcome_store or db.DATA/'v6_night_scenario_outcomes');created=[]
    report=history_report(store=store,outcome_store=outcome_store,now=now)
    evidence=None
    for g in report['groups']:
        for t in g['snapshots'][0]['targets']:
            path=outcome_store/(g['rootId']+'-h'+str(t['horizon'])+'.json')
            if path.exists() or t['targetDate'] not in prices.index or C.at(t['targetDate'],13,30)>now:continue
            if evidence is None:
                raw=prices.to_csv().encode();sha=C.digest(raw);evidence=outcome_store/'price-evidence'/(sha+'.csv');evidence.parent.mkdir(parents=True,exist_ok=True)
                if not evidence.exists():
                    with evidence.open('xb') as f:f.write(raw)
            close=float(prices.at[t['targetDate'],'close'])
            if not math.isfinite(close) or close<=0:raise ValueError('到期收盤無效')
            o={'rootId':g['rootId'],'horizon':t['horizon'],'targetDate':t['targetDate'],'anchorClose':g['anchorClose'],
                'close':close,'up':close>=g['anchorClose'],'availableAt':now.isoformat(),'gradedAt':now.isoformat(),
                'priceEvidenceFile':str(evidence),'priceEvidenceSha256':C.digest(evidence.read_bytes()),'source':'TWSE official completed daily prices'}
            with path.open('xb') as f:f.write(C.encode(o))
            created.append(o)
    return created

def publish():
    from . import v6_live_assessment as L,v6_shared_distribution as D,v6_probability_history as H
    engine=C.Engine();events=engine.read()
    captures=[e for e in events if e['kind']=='capture']
    if not captures:raise ValueError('沒有封存來源包交易日曆')
    now=C.clock();market=H._market_update(db.DATA/'v6_weather',now,now)
    if not market:raise ValueError('沒有已保存市場錨點')
    anchor=market['marketAsOf'];prices=D.prices().loc[:anchor]
    mature(prices)
    context=L.collect(anchor)
    context_file=Path(context['raw_directory'])/'manifest.json'
    row=calculate(context,prices,captures[-1]['payload']['calendar'],engine=engine)
    row['contextFile']=str(context_file);row['contextSha256']=C.digest(context_file.read_bytes())
    row['id']=C.clock().strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    # Preserve real calculation publication time, separately from quote capture.
    row['capturedAt']=row['savedAt'];row['savedAt']=C.clock().isoformat()
    STORE.mkdir(parents=True,exist_ok=True)
    with (STORE/(row['id']+'.json')).open('xb') as f:f.write(C.encode(row))
    # This time of night has no validated interval coverage. Preserve the raw
    # conditional-model sensitivity source; do not auto-publish a point/band.
    H.export()
    return row

def settle():
    """Manual close reconciliation, including updates outside night hours."""
    from . import v6_shared_distribution as D,v6_probability_history as H
    now=C.clock();prices=D.prices();prices=prices.loc[[d for d in prices.index if C.at(str(d),13,30)<=now]]
    result=mature(prices,now=now);H.export();return {'graded':result,'new_forecasts':0}

def main():
    p=argparse.ArgumentParser(description='對話專用：夜盤維持目前價位之情境，不是盤後合格預報')
    p.add_argument('action',choices=['publish','settle']);args=p.parse_args()
    print(json.dumps(publish() if args.action=='publish' else settle(),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
