"""Project chat-saved V6 forecasts into a daily chart; no fit/fetch/grade/publish.

The enrolled runtime stays byte-identical. Reading a report never writes;
the explicit export command is also called after a chat-led V6 publication.
"""
from __future__ import annotations
import argparse,json,math,os,tempfile
from datetime import datetime
from pathlib import Path
from . import db,v6_conditional as C

VERSION='v6-daily-probability-history-1.0.0'

def _market_update(store,start,now):
    updates=[]
    for p in sorted(Path(store).glob('*.json')) if Path(store).exists() else []:
        try:
            row=json.loads(p.read_text());saved=C.stamp(row['prepared_at'])
            if row.get('id')!=p.stem or saved>now:continue
            live=row.get('live_assessment') or {}
            if not isinstance(live,dict):live={}
            updates.append({'id':row['id'],'savedAt':row['prepared_at'],'marketAsOf':row.get('market_as_of'),
                'liveCapturedAt':live.get('captured_at'),'liveStatus':live.get('status'),'beforeObservationStarted':saved<start,
                'note':'市場研究保存時間，不等於條件機率已更新。'})
        except (OSError,ValueError,TypeError,KeyError):continue
    return max(updates,key=lambda r:C.stamp(r['savedAt'])) if updates else None

def _finite_probability(p,display):
    if not isinstance(p,dict):raise ValueError('缺少機率物件')
    a,b=float(p['up']),float(p['down'])
    if not all(math.isfinite(x) and .01<=x<=.99 for x in (a,b)) or abs(a+b-1)>1e-12:
        raise ValueError('機率無效或不互補')
    shown=display(a)
    return {'up':a,'down':b,'upPct':shown['up_percent'],'downPct':shown['down_percent']}

def _upcoming(market,calendar,series,*,scenario_store=None,now=None):
    """Next three sessions from the saved cash anchor, never projected research odds."""
    if not market or not market.get('marketAsOf'):return None
    anchor=market['marketAsOf']
    if datetime.fromisoformat(anchor).date().isoformat()!=anchor:raise ValueError('最新收盤日期無效')
    valid=calendar==sorted(set(calendar)) and anchor in calendar
    future=[d for d in calendar if d>anchor][:3] if valid else []
    targets=[]
    for h in (1,2,3):
        target=future[h-1] if len(future)>=h else None
        matches=[r for s in series if s['horizon']==h for r in s['rows']
            if r['status']=='saved' and r.get('anchorDate')==anchor and r.get('targetDate')==target]
        point=matches[-1] if matches else None
        targets.append({'horizon':h,'targetDate':target,
            'status':'saved' if point else 'pending' if target else 'calendar_missing',
            'upPct':point['upPct'] if point else None,'downPct':point['downPct'] if point else None,
            'probabilitySavedAt':point['savedAt'] if point else None})
    from . import v6_night_scenario as S
    scenario=S.latest(anchor,[t['targetDate'] for t in targets],store=scenario_store,now=now)
    return {'anchorDate':anchor,'marketSavedAt':market['savedAt'],'targets':targets,'scenario':scenario,
        'note':'T為最新已保存現貨收盤日；T+1～T+3是之後三個交易日。日期先列出，未保存合格機率時留空，不沿用歷史研究數字。'}

def build(*,store=None,weather_store=None,config=None,scenario_store=None,now=None):
    """Read-only projection. The optional clock is for isolated tests only."""
    now=now or C.clock()
    base={'version':VERSION,'kind':'actual_saved_forecasts','model':'M1','modelLabel':'夜盤模型',
        'generatedAt':now.isoformat(),'forecastScope':'08:30固定截點；同期限每日新預報，到期日隨起點改變。',
        'readOnly':True,'addsProspectiveSamples':False,'series':[],'lastMarketUpdate':None}
    try:
        engine=C.Engine(store=store,config=config);events=engine.read()
        if not events:return {**base,'status':'not_started','withheld':True,'note':'尚未開始前瞻觀察。'}
        if C.stamp(events[-1]['saved_at'])>now:raise ValueError('觀察紀錄的實際保存時間在未來')
        start=C.stamp(events[0]['saved_at']);forecasts={};outcomes={};changes={};attempts={};calendar=[]
        for e in events:
            p=e['payload'];d=C.stamp(e['saved_at']).date().isoformat()
            if e['kind']=='capture':calendar=p.get('calendar',calendar)
            elif e['kind']=='forecast':
                day=p['decision_date'];issued=C.stamp(e['saved_at']);cut=C.at(day,8,30)
                if day in forecasts:raise ValueError('同日出現多份合格初始預報，不重複或默默挑選')
                if p.get('qualified') is not True or p.get('role')!='prospective_parallel':raise ValueError('混入非前瞻預報')
                if p.get('information_cutoff')!=cut.isoformat() or not cut<=issued<C.at(day,8,45):raise ValueError('預報截點／實際保存時段不合格')
                forecasts[day]=e
            elif e['kind']=='source_status':attempts[d]={'savedAt':e['saved_at'],'withheld':p['withheld'],'note':p['note']}
            elif e['kind']=='calendar_change':changes[p['root'],int(p['horizon'])]=p['to']
            elif e['kind']=='outcome':
                key=(p['root'],int(p['horizon']))
                if key in outcomes:raise ValueError('重複的到期結果')
                if C.stamp(p['available_at'])>C.stamp(e['saved_at']):raise ValueError('答案早於可取得時間保存')
                outcomes[key]={**p,'gradedAt':e['saved_at']}
        # Include known trading sessions, never fabricate a calendar from weekdays.
        last_saved=C.stamp(events[-1]['saved_at']).date().isoformat()
        known_days={d for d in calendar if start.date().isoformat()<=d<=last_saved}
        known_days.update(forecasts);known_days.update(attempts)
        days=sorted(known_days)
        for h in (1,2,3):
            rows=[];previous=None
            for day in days:
                e=forecasts.get(day);attempt=attempts.get(day)
                common={'date':day,'horizon':h,'marketAttempt':attempt}
                if not e:
                    rows.append({**common,'status':'missing','savedAt':attempt['savedAt'] if attempt else None,
                        'up':None,'down':None,'upPct':None,'downPct':None,'deltaUpPp':None,'deltaDownPp':None,
                        'note':attempt['note'] if attempt else '此交易日未保存合格預報；不補值。'})
                    continue
                p=e['payload'];f=p['feature'];prob=_finite_probability(p['models'][str(h)]['M1'],engine.models.display_percentages)
                target=p['targets'][h-1];effective=changes.get((day,h),target)
                if target<day or effective<day:raise ValueError('到期日在預報之前')
                outcome=outcomes.get((day,h))
                if outcome and outcome['target_date']!=effective:raise ValueError('到期結果與已知日曆異動不一致')
                delta=None if previous is None else 100*(prob['up']-previous['up'])
                point={**common,**prob,'status':'saved','savedAt':e['saved_at'],'informationCutoff':p['information_cutoff'],
                    'targetDate':effective,'originalTargetDate':target,'anchorDate':f['anchor_date'],'anchorClose':f['anchor_close'],
                    'deltaUpPp':delta,'deltaDownPp':None if delta is None else -delta,
                    'previousValidDate':previous['date'] if previous else None,'outcome':None if not outcome else {
                        'up':bool(outcome['up']),'close':outcome['close'],'availableAt':outcome['available_at'],'gradedAt':outcome['gradedAt']},
                    'modelVersion':C.VERSION,'note':'每日新起點預報；較前筆有效預報的差值，不是相同到期日修訂。'}
                rows.append(point);previous=point
            base['series'].append({'horizon':h,'rows':rows,'savedCount':sum(r['status']=='saved' for r in rows)})
        if any(day not in forecasts or h not in (1,2,3) for day,h in outcomes):raise ValueError('答案沒有對應的合格預報')
        statuses=[e['payload'] for e in events if e['kind']=='source_status'];latest=statuses[-1] if statuses else {'withheld':True,'note':'等待第一份合格盤前資料'}
        last_probability=max((e['saved_at'] for e in forecasts.values()),key=C.stamp) if forecasts else None
        market=_market_update(weather_store or (db.DATA/'v6_weather'),start,now)
        from . import v6_night_scenario as S
        return {**base,'scenarioHistory':S.history_report(store=scenario_store,now=now),'upcoming':_upcoming(market,calendar,base['series'],scenario_store=scenario_store,now=now),'status':'ready','withheld':latest['withheld'],'note':latest['note'],'startedAt':events[0]['saved_at'],
            'updatedAt':events[-1]['saved_at'],'sourceVersion':C.VERSION,'ledgerHeadSha256':C.digest(C.encode(events[-1])),
            'lastMarketUpdate':market,
            'lastProbabilitySavedAt':last_probability,'liveMaturedByHorizon':[sum(k[1]==h for k in outcomes) for h in (1,2,3)]}
    except (OSError,ValueError,TypeError,KeyError,IndexError) as error:
        return {**base,'series':[],'status':'unavailable','withheld':True,'note':'走勢來源待核對：'+str(error)}

def export(*,store=None,weather_store=None,config=None,roots=None):
    """Explicit manual projection only. Does not append any model event."""
    result=build(store=store,weather_store=weather_store,config=config)
    for root in roots if roots is not None else (db.ROOT/'frontend/public',db.ROOT/'dist'):
        p=Path(root)/'v6-research/daily-probability-history.json';p.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.probability-history-',dir=p.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(C.encode(result));f.flush();os.fsync(f.fileno())
            Path(temp).replace(p)
        finally:
            if Path(temp).exists():Path(temp).unlink()
    from . import v6_forecast_presentation
    v6_forecast_presentation.export(result,roots=roots)
    return result

def main():
    parser=argparse.ArgumentParser(description='V6已存機率的每日走勢；不抓行情、不產生預報')
    parser.add_argument('action',choices=['report','export']);args=parser.parse_args()
    print(json.dumps(export() if args.action=='export' else build(),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
