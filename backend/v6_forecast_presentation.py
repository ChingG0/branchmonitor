"""Read-only V6 presentation diagnostics. No fetch, fit, grade or forecast append.

Sensitivity rows are hypothetical completed-night prices, not intranight forecasts.
Closed-night intervals are withheld for the intranight domain, not inflated by fiat.
"""
from __future__ import annotations
import csv,json,math,os,tempfile
from pathlib import Path
from . import db,v6_conditional as C

VERSION='v6-forecast-presentation-1.0.0'
AUDIT=db.ROOT/'research/v6_target_calibration_audit_20261106/results.json'
ROLLING=db.ROOT/'research/v6_calibration_review_20261106/rolling-events.csv'

def conditions(row,*,engine=None):
    if not row:return None
    engine=engine or C.Engine()
    if row.get('releaseSha256')!=C.digest((engine.config/'release.json').read_bytes()):raise ValueError('情境與封存模型版本不符')
    source=Path(row['contextFile'])
    if C.digest(source.read_bytes())!=row['contextSha256']:raise ValueError('情境來源雜湊不符')
    context=json.loads(source.read_text());night=row['night'];sigma=float(row['dailySigma'])
    lo,hi,observed,day=[float(night[k]) for k in ('low','high','price','day_close')]
    if not all(math.isfinite(x) and x>0 for x in (lo,hi,observed,day,sigma)) or not lo<=observed<=hi:raise ValueError('情境價格或波動無效')
    model=engine.params['H1:M1'];keys=engine.models.FEATURES[:4]
    if model['features']!=keys:raise ValueError('模型特徵不符')
    result=[]
    for level in sorted(set([lo,observed,hi])):
        features=dict(row['features']);features['night_return_scaled']=math.log(level/day)/sigma
        x=engine.models.np.array([[(features[k]-model['scaler']['mean'][i])/model['scaler']['std_ddof0'][i] for i,k in enumerate(keys)]])
        p=float(engine.models.initial_probability(model,x)['p_up'][0])
        if not .01<=p<=.99:raise ValueError('條件輸出無效')
        result.append({'nightCloseAssumed':level,'label':'當時報價' if level==observed else '已觀察低點' if level==lo else '已觀察高點','up':p,'down':1-p})
    return {'version':VERSION,'role':'conditional_sensitivity_not_forecast','isForecast':False,'validatedForThisTime':False,
        'computedAt':C.clock().isoformat(),'scenarioId':row['id'],'sourceSavedAt':row['savedAt'],'quoteAt':night['quote_at'],
        'anchorDate':row['anchorDate'],'anchorClose':row['anchorClose'],'targetDate':row['targets'][0]['targetDate'],
        'contract':night['contract'],'dayClose':day,'fixedHigh':hi,'fixedLow':lo,'dailySigma60Pct':sigma*100,
        'volatility':context.get('volatility',{}),'rows':result,'parameterSha256':model['parameter_sha256'],
        'driftCorrectionAdopted':False,'intervalStatus':'withheld_unvalidated_intranight',
        'note':'若完整夜盤分別收在下列價位，且高低區間不再擴大，固定08:30模型會輸出以下條件值。不是目前勝率，不包含尚未發布的消息，不計前瞻樣本。'}

def audit():
    d=json.loads(AUDIT.read_text())
    for value in d['source_hashes'].values():
        if C.digest(Path(value['path']).read_bytes())!=value['sha256']:raise ValueError('校準診斷來源與封存不符')
    records=[]
    with ROLLING.open() as f:
        for row in csv.DictReader(f):
            if C.stamp(row['latest_training_outcome'])>=C.stamp(row['cutoff']):raise ValueError('滾動校準混入尚未成熟答案')
            records.append({'horizon':int(row['horizon']),'date':row['decision_date'],'cutoff':row['cutoff'],
                'latestTrainingOutcome':row['latest_training_outcome'],'trainingN':int(row['training_n']),
                'fixed':float(row['fixed_p']),'rolling':float(row['rolling_p']),'up':int(float(row['y']))==1})
    return {'version':VERSION,'role':'descriptive_already_opened_history_not_new_holdout','sourceSha256':C.digest(AUDIT.read_bytes()),
        'rollingSourceSha256':C.digest(ROLLING.read_bytes()),'all':d['all'],'last60':d['last60'],
        'directions':d['direction_diagnostics_not_new_probability_models'],'calibration':d['calibration'],
        'rollingComparison':d['prior_calibration_review'],'rollingRows':records,
        'rounded50':d['rounded_display_50'],'exact50N':len(d['exact_raw_50']),
        'calibrationAdopted':False,'addsProspectiveSamples':False}

def export(history,*,roots=None):
    payload={'version':VERSION,'generatedAt':C.clock().isoformat(),'readOnly':True,'isForecast':False}
    try:payload['conditions']=conditions((history.get('upcoming') or {}).get('scenario'))
    except (OSError,ValueError,KeyError,TypeError) as e:payload['conditions']=None;payload['conditionsError']=str(e)
    try:payload['audit']=audit()
    except (OSError,ValueError,KeyError,TypeError) as e:payload['audit']=None;payload['auditError']=str(e)
    for root in roots if roots is not None else (db.ROOT/'frontend/public',db.ROOT/'dist'):
        path=Path(root)/'v6-research/forecast-presentation.json';path.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.forecast-presentation-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(C.encode(payload));f.flush();os.fsync(f.fileno())
            Path(temp).replace(path)
        finally:
            if Path(temp).exists():Path(temp).unlink()
    return payload
