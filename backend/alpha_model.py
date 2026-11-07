"""Forward-only ridge calibration for research selections and TX forecasts.

The module never fetches data.  It trains only on outcomes whose dates are no
later than the current research date and returns a frozen, auditable snapshot.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

VERSION = 'alpha-ridge-v1'
STOCK_MIN_SAMPLES = 30
STOCK_MIN_DAYS = 8
FUTURES_MIN_SAMPLES = 12
RIDGE = 2.0


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def candidate_features(row):
    technical = row.get('technical') or {}
    sar = technical.get('sar') or {}
    reasons = row.get('reasons') or []
    return {
        'evidence_count': float(len(reasons)),
        'buy_count': float(row.get('buy_count') or 0),
        'net_lots_log': math.copysign(math.log1p(abs(float(row.get('net_lots') or 0))), float(row.get('net_lots') or 0)),
        'rs20': float(row.get('rs20') or 50),
        'rs_ytd': float(row.get('rs_ytd') or 50),
        'di_spread': float(technical.get('di_spread') or 0),
        'adx': float(technical.get('adx') or 0),
        'sar_bullish': 1.0 if sar.get('bullish') else 0.0,
        'early_turn': 1.0 if technical.get('status') in ('early_turn', 'early_confirmed') else 0.0,
    }


STOCK_FEATURES = tuple(candidate_features({}).keys())


def _vector(values, names):
    return [float(values.get(name) or 0) for name in names]


def _fit(samples, names, minimum):
    """Fit standardized ridge and report expanding-window out-of-sample error."""
    if len(samples) < minimum:
        return {'status': 'warming', 'samples': len(samples), 'minimum': minimum, 'feature_names': list(names),
                'message': f'累積 {len(samples)}/{minimum} 筆已揭曉樣本；尚未啟用模型權重'}
    x = np.asarray([_vector(s['features'], names) for s in samples], dtype=float)
    y = np.asarray([s['target'] for s in samples], dtype=float)
    mean = x.mean(axis=0); scale = x.std(axis=0); scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * RIDGE; penalty[0, 0] = 0
    coef = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    errors = []
    start = max(8, minimum // 2)
    for index in range(start, len(samples)):
        train_x=x[:index];train_y=y[:index];m=train_x.mean(axis=0);s=train_x.std(axis=0);s[s < 1e-9]=1.0
        train=np.column_stack([np.ones(index),(train_x-m)/s]);p=np.eye(train.shape[1])*RIDGE;p[0,0]=0
        local=np.linalg.solve(train.T@train+p,train.T@train_y)
        predicted=float(np.r_[1,(x[index]-m)/s]@local);errors.append(predicted-y[index])
    validation={'samples':len(errors),'mae':round(float(np.mean(np.abs(errors))),4) if errors else None,
                'bias':round(float(np.mean(errors)),4) if errors else None}
    return {'status':'trained','version':VERSION,'samples':len(samples),'minimum':minimum,'feature_names':list(names),
            'mean':[round(float(v),8) for v in mean],'scale':[round(float(v),8) for v in scale],
            'coefficients':[round(float(v),8) for v in coef],'target_mean':round(float(y.mean()),4),
            'validation':validation,'message':f'使用截止研究日以前 {len(samples)} 筆結果訓練；驗證採時間順序向前展開'}


def _predict(model, features):
    if model.get('status') != 'trained':
        return None
    x=np.asarray(_vector(features,model['feature_names']),dtype=float)
    z=(x-np.asarray(model['mean']))/np.asarray(model['scale'])
    return float(np.r_[1,z]@np.asarray(model['coefficients']))


def _stock_samples(rows, cutoff):
    samples=[]
    for row in sorted(rows,key=lambda r:(r.get('result',{}).get('exit_date') or '',r.get('id',''))):
        result=row.get('result') or {};rule=row.get('recommendation_rule') or {}
        if not row.get('eligible') or result.get('status')!='closed' or not result.get('exit_date') or result['exit_date']>cutoff:continue
        features=rule.get('features') or row.get('model_features');target=result.get('alpha_pct')
        if isinstance(features,dict) and finite(target):samples.append({'features':features,'target':float(target),'date':result['exit_date'],'basis_date':row.get('basis_date')})
    return samples


def _market_features(prediction):
    basis=prediction.get('basis') or {};base=float(basis.get('close') or 0)
    markets=(prediction.get('opening_markets') or {}).get('rows') or []
    gaps=[float(r['gap_pct']) for r in markets if finite(r.get('gap_pct'))]
    moves=[float(r['from_open_pct']) for r in markets if finite(r.get('from_open_pct'))]
    raw_open=float(prediction.get('raw_open') or prediction.get('open') or base or 0)
    raw_close=float(prediction.get('raw_close') or prediction.get('close') or raw_open or 0)
    return {
        'raw_open_gap_pct':(raw_open/base-1)*100 if base else 0,
        'raw_close_gap_pct':(raw_close/base-1)*100 if base else 0,
        'basis_body_pct':(float(basis.get('close') or 0)/float(basis.get('open') or 1)-1)*100 if basis.get('open') else 0,
        'basis_range_pct':(float(basis.get('high') or 0)-float(basis.get('low') or 0))/base*100 if base else 0,
        'opening_gap_pct':sum(gaps)/len(gaps) if gaps else 0,
        'opening_move_pct':sum(moves)/len(moves) if moves else 0,
    }


FUTURES_FEATURES=('raw_open_gap_pct','raw_close_gap_pct','basis_body_pct','basis_range_pct','opening_gap_pct','opening_move_pct')


def _future_samples(forecasts, cutoff, field):
    samples=[]
    for row in sorted(forecasts,key=lambda r:(r.get('target_date') or '',r.get('report_id',''))):
        result=row.get('result') or {};prediction=row.get('prediction') or {};actual=result.get('actual') or {}
        if not row.get('eligible') or result.get('status')!='scored' or not row.get('target_date') or row['target_date']>cutoff:continue
        raw=prediction.get('raw_'+field, prediction.get(field));base=(prediction.get('basis') or {}).get('close')
        if all(finite(v) for v in [raw,actual.get(field),base]) and base:
            samples.append({'features':_market_features(prediction),'target':(float(actual[field])-float(raw))/float(base)*100,'date':row['target_date']})
    return samples


def prepare(evidence, rows, forecasts):
    cutoff=evidence['date']
    stock_samples=_stock_samples(rows,cutoff);research_days=len({r.get('basis_date') for r in stock_samples if r.get('basis_date')})
    stock_model=_fit(stock_samples,STOCK_FEATURES,STOCK_MIN_SAMPLES);stock_model['research_days']=research_days;stock_model['minimum_days']=STOCK_MIN_DAYS
    if stock_model.get('status')=='trained' and research_days<STOCK_MIN_DAYS:
        stock_model={'status':'warming','samples':len(stock_samples),'minimum':STOCK_MIN_SAMPLES,'research_days':research_days,'minimum_days':STOCK_MIN_DAYS,
                     'feature_names':list(STOCK_FEATURES),'message':f'已有 {len(stock_samples)} 筆，但僅涵蓋 {research_days}/{STOCK_MIN_DAYS} 個研究日；尚未啟用模型權重'}
    scores=[]
    for index,row in enumerate(evidence.get('candidates',[])):
        features=candidate_features(row);predicted=_predict(stock_model,features)
        scores.append({'symbol':row['symbol'],'base_rank':index+1,'features':features,
                       'predicted_alpha_pct':round(predicted,4) if predicted is not None else None})
    if stock_model.get('status')=='trained':
        ordered=sorted(scores,key=lambda r:-(r['predicted_alpha_pct'] if r['predicted_alpha_pct'] is not None else -999))
        percentile={r['symbol']:100*(len(ordered)-i)/len(ordered) for i,r in enumerate(ordered)}
        for row in scores:row['alpha_percentile']=round(percentile[row['symbol']],1)
    else:
        for row in scores:row['alpha_percentile']=None
    open_model=_fit(_future_samples(forecasts,cutoff,'open'),FUTURES_FEATURES,FUTURES_MIN_SAMPLES)
    close_model=_fit(_future_samples(forecasts,cutoff,'close'),FUTURES_FEATURES,FUTURES_MIN_SAMPLES)
    return {'version':VERSION,'cutoff':cutoff,'leakage_control':'只使用結果日期不晚於研究日的盤前首版；驗證維持時間順序',
            'stock':{**stock_model,'candidates':scores},'futures':{'open':open_model,'close':close_model}}


def prepare_futures(evidence, forecasts):
    """Train only the two futures residual models used by the market report."""
    cutoff=evidence['date']
    open_model=_fit(_future_samples(forecasts,cutoff,'open'),FUTURES_FEATURES,FUTURES_MIN_SAMPLES)
    close_model=_fit(_future_samples(forecasts,cutoff,'close'),FUTURES_FEATURES,FUTURES_MIN_SAMPLES)
    return {'version':VERSION,'cutoff':cutoff,'leakage_control':'只使用結果日期不晚於研究日的盤前首版；驗證維持時間順序',
            'futures':{'open':open_model,'close':close_model}}


def calibrate_futures(raw, evidence):
    """Apply frozen residual models to a model forecast, while preserving raw values."""
    basis=(evidence.get('execution') or {}).get('basis') or {};base=basis.get('close')
    output=dict(raw);output['raw_open']=raw.get('open');output['raw_close']=raw.get('close')
    model=(evidence.get('alpha_model') or {}).get('futures') or {}
    probe={**raw,'raw_open':raw.get('open'),'raw_close':raw.get('close'),'basis':basis,
           'opening_markets':(evidence.get('execution') or {}).get('opening_markets') or {}}
    details={'version':VERSION,'applied':False,'open_adjustment':0.0,'close_adjustment':0.0}
    if finite(base) and all(finite(raw.get(k)) for k in ('open','close')):
        features=_market_features(probe)
        for field in ('open','close'):
            correction=_predict(model.get(field) or {},features)
            if correction is None:continue
            # Guard against an unstable early fit: at most 0.35% of the basis close.
            adjustment=max(-float(base)*.0035,min(float(base)*.0035,correction*float(base)/100))
            value=float(raw[field])+adjustment
            if field=='open' and finite(raw.get('support1')) and finite(raw.get('resistance1')):
                value=max(float(raw['support1'])+.01,min(float(raw['resistance1'])-.01,value))
            output[field]=round(value,2);details[field+'_adjustment']=round(output[field]-float(raw[field]),2);details['applied']=True
        details['features']={k:round(v,6) for k,v in features.items()}
    details['open_model_samples']=(model.get('open') or {}).get('samples',0)
    details['close_model_samples']=(model.get('close') or {}).get('samples',0)
    output['calibration']=details
    return output
