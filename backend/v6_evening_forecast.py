"""Chat-only 18/22 forecasts. A separate ledger preserves the sealed08:30 era.

Current fallback is causal cash-only M0. Missing same-clock historical minute
bars never cause the full05:00 night-close model to be used intranight.
"""
from __future__ import annotations
import argparse,copy,fcntl,hashlib,json,math,os,tempfile,uuid,sqlite3
from datetime import timedelta
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db,v6_evening_model as M

TZ=ZoneInfo('Asia/Taipei');NY=ZoneInfo('America/New_York')
VERSION='v6-evening-forecast-1.0.0';STORE=db.DATA/'v6_evening';CONFIG=db.ROOT/'config/v6_evening'
ROLE_LABELS={'18_formal':'18:00版 · 正式','22_revision':'22:00版 · 修訂','22_supplement':'22:00版 · 補位',
 'same_window_revision':'同窗口追加修訂','unqualified':'非合格時段'}

def clock():return datetime.now(TZ)
def stamp(value):
    d=datetime.fromisoformat(value) if isinstance(value,str) else value
    if d.tzinfo is None:raise ValueError('時間必須含時區')
    return d.astimezone(TZ)
def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(value).hexdigest()
def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.evening-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(encode(value));f.flush();os.fsync(f.fileno())
        Path(tmp).replace(path)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
def slot(at):
    t=stamp(at).time().replace(tzinfo=None)
    return '18' if time(18)<=t<=time(18,30) else '22' if time(22)<=t<=time(22,30) else None

def classify(previous,anchor,cutoff,saved):
    cut,saved=stamp(cutoff),stamp(saved)
    if cut>saved:raise ValueError('資訊截點晚於實際保存')
    family=slot(saved)
    qualified=bool(family and slot(cut)==family and cut.date()==saved.date() and str(saved.date())==anchor)
    before=[p for p in previous if p['anchorDate']==anchor and p.get('qualified')]
    if not qualified:return {'slot':family,'role':'unqualified','qualified':False,'countsFormal':False}
    same=[p for p in before if p['slot']==family]
    role=('same_window_revision' if same else '18_formal' if family=='18' else
          '22_revision' if any(p['role']=='18_formal' for p in before) else '22_supplement')
    return {'slot':family,'role':role,'qualified':True,'countsFormal':role in ('18_formal','22_supplement')}

def read(store=None):
    store=Path(store or STORE);events=[];previous='0'*64
    for index,path in enumerate(sorted(store.glob('event-*.json'))):
        raw=path.read_bytes();row=json.loads(raw)
        if raw!=encode(row):raise ValueError('帳本原始位元組不符')
        if row.get('version')!=VERSION or row.get('index')!=index or row.get('previousSha256')!=previous or path.name!=f"event-{index:07d}-{digest(encode(row))[:16]}.json":raise ValueError('18/22預報帳本完整性不符')
        if events and stamp(row['savedAt'])<stamp(events[-1]['savedAt']):raise ValueError('帳本保存時間倒退')
        events.append(row);previous=digest(encode(row))
    return events

def append(store,events,kind,payload,at):
    row={'version':VERSION,'index':len(events),'previousSha256':digest(encode(events[-1])) if events else '0'*64,
        'savedAt':stamp(at).isoformat(),'kind':kind,'payload':payload}
    if events and stamp(row['savedAt'])<stamp(events[-1]['savedAt']):raise ValueError('不倒填保存時間')
    path=store/f"event-{len(events):07d}-{digest(encode(row))[:16]}.json"
    with path.open('xb') as f:f.write(encode(row));f.flush();os.fsync(f.fileno())
    events.append(row);return row

def calendar():
    # Prefer the current archived official calendar, including special closures.
    # A year without an actual cached declaration is never guessed from weekdays.
    path=db.DATA/'research.sqlite3';now=clock();years={}
    if path.exists():
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
            for year,raw in connection.execute('SELECT year,payload FROM bt_calendars'):
                payload=json.loads(raw)
                if not payload.get('source') or not payload.get('saved_at') or stamp(payload['saved_at'])>now:continue
                years[int(year)]=payload
    if years:
        sessions=[]
        for year,cal in sorted(years.items()):
            day=datetime(year,1,1,tzinfo=TZ)
            while day.year==year:
                date=day.date().isoformat()
                if date in cal.get('opened',[]) or (day.weekday()<5 and date not in cal.get('closed',[])):sessions.append(date)
                day+=timedelta(days=1)
        if str(now.year) in [str(y) for y in years]:
            return sessions,{'source':'current_archived_official_calendar','years':{str(y):v for y,v in years.items()},'sha256':digest(encode(years))}
    # Initial installations may only have the older captured official vintage.
    from . import v6_conditional as Old
    events=Old.Engine().read();captures=[e for e in events if e['kind']=='capture']
    if not captures:raise ValueError('缺少已保存官方交易日曆')
    cap=captures[-1]
    return cap['payload']['calendar'],{'source':'archived_official_calendar','captureSavedAt':cap['saved_at'],'captureSha256':digest(Old.encode(cap))}

def backtest(prices,*,root=None):
    raw=prices.to_csv(float_format='%.17g').encode();sha=digest(raw);root=Path(root or db.DATA/'v6_evening_backtests')
    protocol=CONFIG/'protocol.json';protocol_sha=digest(protocol.read_bytes())
    code_sha=digest(Path(M.__file__).read_bytes());key=digest(encode([sha,protocol_sha,code_sha]));path=root/(key+'.json')
    if path.exists():
        result=json.loads(path.read_bytes())
        if result.get('pricesSha256')!=sha or result.get('protocolSha256')!=protocol_sha or result.get('modelCodeSha256')!=code_sha:raise ValueError('M0回溯版本不符')
        return result
    model=M.Baseline(prices);rows=model.replay();result={'version':VERSION,'kind':'walk_forward_historical_reconstruction',
        'computedAt':clock().isoformat(),'pricesSha256':sha,'protocolSha256':protocol_sha,'modelCodeSha256':code_sha,
        'dataFirst':str(prices.index[0]),'dataLast':str(prices.index[-1]),'summary':M.summary(rows),'rows':rows,
        'slotSpecificNightModelsTrained':False,'slotComparisonNote':'目前18與22均使用相同收盤資料M0；不是已完成兩個夜盤時段的訓練。',
        'featuresUsed':['已完成現貨收盤','近期波動縮放'],'candidateFeaturesAdopted':[],
        'unseenCaveat':'逐日只讀當時已成熟資料；既有歷史曾用於其他研究，這不是全新未接觸的盲測。',
        'inputFile':str(root/(sha+'.csv'))}
    root.mkdir(parents=True,exist_ok=True)
    if not (root/(sha+'.csv')).exists():
        with (root/(sha+'.csv')).open('xb') as f:f.write(raw)
    with path.open('xb') as f:f.write(encode(result))
    return result

def settle(events,store,prices,at,*,sessions=None,calendar_proof=None):
    known={(e['payload']['forecastId'],e['payload']['horizon']) for e in events if e['kind']=='outcome'}
    forecasts=[e['payload'] for e in events if e['kind']=='forecast']
    changes={(e['payload']['forecastId'],e['payload']['horizon']):e['payload']['to'] for e in events if e['kind']=='calendar_change'}
    for f in forecasts:
        for p in f['targets']:
            key=(f['id'],p['horizon']);d=changes.get(key,p['targetDate'])
            if sessions and key not in known:
                dates=[day for day in sessions if day>f['anchorDate']][:3]
                target=dates[p['horizon']-1] if len(dates)==3 else d
                if target!=d:
                    append(store,events,'calendar_change',{'forecastId':f['id'],'horizon':p['horizon'],'from':d,'to':target,'calendarProof':calendar_proof},at)
                    d=target;changes[key]=target
            if key in known or d not in prices.index or datetime.fromisoformat(d).replace(hour=13,minute=45,tzinfo=TZ)>at:continue
            value=float(prices.at[d,'close']);up=None if value==f['anchorClose'] else value>f['anchorClose']
            append(store,events,'outcome',{'forecastId':f['id'],'horizon':p['horizon'],'targetDate':d,
                'actualClose':value,'actualUp':up,'availableAt':at.isoformat(),'source':'official_completed_cash',
                'priceEvidenceSha256':digest(prices.to_csv(float_format='%.17g').encode())},at);known.add(key)

def publish(prices,sessions,*,live=None,calendar_proof=None,store=None,at=None,backtest_root=None,weather_id=None,minute_bars=None,feature_history=None):
    """Only called by chat CLI. Injected clock/prices/store are isolated test hooks."""
    store=Path(store or STORE);start=stamp(at or clock());f=prices.loc[[datetime.fromisoformat(str(d)).replace(hour=13,minute=45,tzinfo=TZ)<=start for d in prices.index]]
    if f.empty:raise ValueError('無完整現貨錨點；不得編造機率與點數')
    anchor=str(f.index[-1]);future=[d for d in sessions if d>anchor][:3]
    if sessions!=sorted(set(sessions)) or anchor not in sessions or len(future)!=3:raise ValueError('缺已公告後三個交易日，不用工作日猜測')
    if datetime.fromisoformat(future[0]).replace(hour=13,minute=45,tzinfo=TZ)<=start:raise ValueError('現貨來源過期，首個到期日已收盤；保留上次有效紀錄，不重算成新預報')
    study=backtest(f,root=backtest_root);model=M.Baseline(f);targets=[]
    for h,d in enumerate(future,1):
        p=model.predict(anchor,h);p.update({'targetDate':d,'candidateUp':None,'M0Up':p['up'],
            'modelSelectionReason':'缺歷史同時刻夜盤訓練；目前用基準模型，不挪用05:00模型',
            'historical':study['summary'][h-1]});targets.append(p)
    saved=stamp(at or clock());store.mkdir(parents=True,exist_ok=True)
    with (store/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);events=read(store);settle(events,store,f,saved,sessions=sessions,calendar_proof=calendar_proof)
        forecasts=[e['payload'] for e in events if e['kind']=='forecast'];policy=classify(forecasts,anchor,start,saved)
        ny=start.astimezone(NY);identity=saved.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
        primary=next((p for p in forecasts if p['anchorDate']==anchor and p.get('countsFormal')),None)
        # A model is used only with an exact-clock artifact and completed bars.
        # Raw instantaneous quotes are not substituted for completed minute bars.
        from . import v6_evening_candidates as Candidates,v6_evening_inputs as Inputs
        candidate_ready=False;candidate_inputs=[];candidate_error=None
        try:
            bars=Inputs.read_bars(minute_bars) if minute_bars else None
            if bars and bars['issues']:raise ValueError('分鐘資料缺可取得時間')
            macro=[]
            if feature_history:
                import csv
                with Path(feature_history).open(encoding='utf-8-sig') as handle:macro=list(csv.DictReader(handle))
            answers={(e['payload']['forecastId'],e['payload']['horizon']):e['payload'] for e in events if e['kind']=='outcome'}
            for index,pred in enumerate(targets):
                artifact=Candidates.load(start.strftime('%H:%M'),pred['horizon'],start.isoformat()) if policy['qualified'] else None
                if not artifact or not bars:continue
                x=Candidates.features_at(f,bars['rows'],start.isoformat(),artifact['model']['features'],sigma_override=model.sigma[-1],macro_history=macro)
                if x is None:continue
                raw=Candidates.infer(artifact['model'],x['features'],pred['anchorClose'],model.sigma[-1],pred);candidate_ready=True
                shadow=[]
                for old in forecasts:
                    if old['slot']!=policy['slot'] or old['role']!=policy['role']:continue
                    target=old['targets'][index];answer=answers.get((old['id'],pred['horizon']))
                    if answer:shadow.append({'qualified':old['qualified'],'candidateUp':target.get('candidateUp'),'M0Up':target['M0Up'],'actualUp':answer['actualUp']})
                selected=M.select_model(historical_eligible=artifact['historicalEligible'],shadow_rows=shadow,source_qualified=True)
                pred['candidateUp']=raw['up'];pred['candidateHistorical']=artifact['validation'];pred['candidateSourceSha256']=bars['sha256'];pred['candidateArtifactSha256']=artifact['artifactSha256']
                if selected=='candidate' and not raw.get('guardFallback'):
                    targets[index]={**raw,'M0Up':pred['M0Up'],'candidateUp':raw['up'],'candidateHistorical':artifact['validation'],
                        'historical':artifact['historical'],'candidateArtifactSha256':artifact['artifactSha256'],'modelSelectionReason':'同時刻walk-forward及特徵檢查通過，最近60筆未輸M0'}
                else:pred['modelSelectionReason']=raw.get('modelSelectionReason') if raw.get('guardFallback') else '最近60筆候選Brier輸給M0，自動退回基準；影子預報持續保存'
                candidate_inputs.append({'horizon':pred['horizon'],'features':x,'barsSha256':bars['sha256'],'modelCodeSha256':artifact['codeSha256']})
        except (OSError,ValueError,KeyError,TypeError) as error:
            candidate_error=str(error) # Baseline remains complete, never zero-fill.
        # User-authorized unfitted observation is a separate, honestly labelled
        # method. It overrides the primary display, never the sealed old models.
        observation=None
        observation_config=CONFIG/'observation.json'
        if observation_config.exists() and json.loads(observation_config.read_text()).get('primary'):
            from . import v6_observation as O
            monthly,monthly_proof=O.load_monthly()
            observer=O.Observation(f,live,start,monthly)
            observation=observer.public_context();observation['monthlySource']=monthly_proof
            observation['codeSha256']=digest(Path(O.__file__).read_bytes())
            for k,previous_target in enumerate(targets):
                raw=observer.predict(k+1)
                targets[k]={**previous_target,**raw,'M0Up':previous_target['M0Up'],
                            'candidateUp':previous_target.get('candidateUp')}
            # Timing qualification remains visible; no untrained result consumes
            # the validated-model formal sample count.
            policy['countsObservation']=policy['countsFormal'];policy['countsFormal']=False
        slot_studies=[]
        for family in ('18','22'):
            studies=[]
            clock_label=start.strftime('%H:%M') if policy['slot']==family else family+':00'
            for h in (1,2,3):
                try:artifact=Candidates.inspect(clock_label,h,start.isoformat())
                except (OSError,ValueError,KeyError,TypeError):artifact=None
                studies.append({'horizon':h,'clock':clock_label,'modelReady':bool(artifact and artifact['historicalEligible']),
                    'M0':artifact['validation']['M0'] if artifact else study['summary'][h-1]['M0'],
                    'candidate':artifact['validation']['candidate'] if artifact else None,
                    'improvement':artifact['validation']['relativeImprovement'] if artifact else None,
                    'featureTests':artifact['validation']['featureTests'] if artifact else [],
                    'features':artifact['model']['features'] if artifact and artifact.get('model') else [],
                    'coverage80':artifact['validation']['candidate']['coverage80'] if artifact else None})
            slot_studies.append({'slot':family,'horizons':studies})
        # Recheck the real save clock AFTER observation/candidate computation.
        # A computation crossing18:30/22:30 is not retrospectively qualified.
        saved=stamp(at or clock());policy=classify(forecasts,anchor,start,saved)
        if observation:
            policy['countsObservation']=policy['countsFormal'];policy['countsFormal']=False
        identity=saved.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
        payload={'id':identity,'version':VERSION,'anchorDate':anchor,'anchorClose':float(f.close.iloc[-1]),
            'informationCutoff':start.isoformat(),'savedAt':saved.isoformat(),**policy,
            'roleLabel':ROLE_LABELS[policy['role']],'formalParentId':primary['id'] if primary else None,
            'usTimeRegime':ny.tzname(),'usTime':ny.isoformat(),'sameClockNightTrainingAvailable':candidate_ready,
            'observation':observation,'forecastMode':'observation' if observation else 'validated_or_baseline','targets':targets,'backtestBySlot':slot_studies,'calendarProof':calendar_proof,'sourcePricesSha256':study['pricesSha256'],
            'protocolSha256':study['protocolSha256'],'sourcePriceFile':study['inputFile'],
            'liveContext':live,'liveInputsUsedByM0':False,'weatherId':weather_id,'candidateInputs':candidate_inputs,'candidateError':candidate_error,
            'dataNote':observation['dataNote'] if observation else '基準M0只用已完成現貨與波動；時段候選僅在同時刻來源及回測資格通過後採用。各卡片列實際模型，不把資料收集當成已用進模型。'}
        append(store,events,'forecast',payload,saved)
        fcntl.flock(lock,fcntl.LOCK_UN)
    return payload

def report(*,store=None):
    """Project only already saved facts; no fetch, prediction, backtest or grading."""
    events=read(store);forecasts=copy.deepcopy([e['payload'] for e in events if e['kind']=='forecast'])
    changes={(e['payload']['forecastId'],e['payload']['horizon']):e['payload']['to'] for e in events if e['kind']=='calendar_change'}
    for forecast in forecasts:
        for target in forecast['targets']:
            target['originalTargetDate']=target['targetDate'];target['targetDate']=changes.get((forecast['id'],target['horizon']),target['targetDate'])
    if not forecasts:return {'version':VERSION,'status':'not_started','readOnly':True,'latest':None,'progress':[],'history':[]}
    outcomes={(e['payload']['forecastId'],e['payload']['horizon']):e['payload'] for e in events if e['kind']=='outcome'}
    mode=forecasts[-1].get('forecastMode','validated_or_baseline')
    method=(forecasts[-1].get('observation') or {}).get('methodVersion')
    scoring_forecasts=[f for f in forecasts if f.get('forecastMode','validated_or_baseline')==mode and (f.get('observation') or {}).get('methodVersion')==method]
    progress=[];scored=[]
    for f in scoring_forecasts:
        for t in f['targets']:
            o=outcomes.get((f['id'],t['horizon']))
            if o:scored.append({**t,**o,'anchorDate':f['anchorDate'],'role':f['role'],'slot':f['slot'],
                'qualified':f['qualified'],'countsFormal':f['countsFormal'],'savedAt':f['savedAt']})
    for role in ('18_formal','22_supplement','22_revision','same_window_revision','unqualified'):
        progress.append({'role':role,'label':ROLE_LABELS[role],
            'saved':sum(f['role']==role for f in scoring_forecasts),'horizons':[{'horizon':h,**M.metrics([r for r in scored if r['role']==role and r['horizon']==h])} for h in (1,2,3)]})
    history=[]
    for f in forecasts[-180:]:
        history.append({k:copy.deepcopy(f[k]) for k in ('id','anchorDate','anchorClose','informationCutoff','savedAt','slot','role','roleLabel','qualified','countsFormal','usTimeRegime','targets')})
        for t in history[-1]['targets']:t['outcome']=outcomes.get((f['id'],t['horizon']))
    return {'version':VERSION,'status':'ready','readOnly':True,'latest':forecasts[-1],
        'progress':progress,'history':history,'ledgerHeadSha256':digest(encode(events[-1])),
        'scoreNote':'65%才判方向；無方向不計方向命中，但非收平答案全部列Brier。未到期不算錯或對。',
        'sourceRequirement':'未校準觀察版使用當下可核對夜盤與歷史現貨代理；不冒充已完成18/22分時訓練。' if mode=='observation' else '需匯入TX分合約分鐘資料才可訓練18/22模型；目前使用M0。'}

def export(*,store=None,roots=None):
    result=report(store=store)
    for root in roots if roots is not None else (db.ROOT/'frontend/public',db.ROOT/'dist'):
        atomic(Path(root)/'v6-research/evening-forecast.json',result)
    return result

def manual_update(*,live_assessment=None,weather_id=None,minute_bars=None,feature_history=None):
    from . import v6_shared_distribution as D,v6_live_assessment as L
    prices=D.prices();now=clock();sessions,proof=calendar()
    completed=[d for d in sessions if datetime.fromisoformat(d).replace(hour=13,minute=45,tzinfo=TZ)<=now]
    expected=completed[-1] if completed else None
    if expected and str(prices.index[-1])<expected:
        # Explicit chat update only: refresh complete official cash, never GET.
        D.refresh_prices(expected);prices=D.prices()
    prices=prices.loc[[datetime.fromisoformat(str(d)).replace(hour=13,minute=45,tzinfo=TZ)<=now for d in prices.index]]
    live=live_assessment if live_assessment is not None else L.collect(str(prices.index[-1]))
    result=publish(prices,sessions,live=live,calendar_proof=proof,weather_id=weather_id,minute_bars=minute_bars,feature_history=feature_history);export();return result

def main():
    p=argparse.ArgumentParser(description='對話專用18/22機率與點數；盤外仍計算M0並另標非合格')
    p.add_argument('action',choices=['publish','report','export']);p.add_argument('--minute-bars',type=Path);p.add_argument('--feature-history',type=Path);args=p.parse_args()
    result=manual_update(minute_bars=args.minute_bars,feature_history=args.feature_history) if args.action=='publish' else export() if args.action=='export' else report()
    print(json.dumps({k:v for k,v in result.items() if k not in ('history','liveContext')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
