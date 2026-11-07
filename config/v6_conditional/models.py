"""Frozen numerical implementations. No source-price access or target-label loader."""
import os
for _name in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ[_name]='1'
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
import json,hashlib,math
from decimal import Decimal,ROUND_FLOOR

METHOD='v6-conditional-0830-protocol-1.0.0'
IMPLEMENTATION='v6-conditional-models-1.0.0'
FEATURES=['night_return_scaled','night_range_scaled','log_daily_sigma','night_age',
          'adr_new_return_scaled','sox_new_return_scaled','spx_new_return_scaled',
          'fx_new_return','log_vix','us_after_night_count']
L2=.01
OPTIONS={'maxiter':10000,'ftol':1e-12,'gtol':1e-8}
class InvalidModel(ValueError):pass
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def vector(a):
    a=np.asarray(a,dtype=np.float64)
    if a.ndim!=1 or len(a)==0 or not np.isfinite(a).all():raise InvalidModel('nonempty finite vector required')
    return a
def matrix(x):
    x=np.asarray(x,dtype=np.float64)
    if x.ndim!=2 or x.shape[0]==0 or not np.isfinite(x).all():raise InvalidModel('nonempty finite matrix required')
    return x
def binary(y):
    y=vector(y)
    if not np.isin(y,[0,1]).all():raise InvalidModel('binary labels required')
    return y
def fit_gate(y,n_min,class_min):
    y=binary(y);up=int(y.sum());down=len(y)-up
    return {'n':len(y),'up':up,'down':down,'required_n':n_min,'required_each_class':class_min,
            'eligible':len(y)>=n_min and min(up,down)>=class_min}
def require_gate(y,n_min,class_min):
    gate=fit_gate(y,n_min,class_min)
    if not gate['eligible']:raise InvalidModel('sample gate failed: '+json.dumps(gate))
    return gate
def clipped(p):
    p=np.asarray(p,dtype=np.float64)
    if not np.isfinite(p).all() or ((p<0)|(p>1)).any():raise InvalidModel('invalid probability')
    return np.clip(p,.01,.99)
def logit_for_cdf(p):
    p=np.asarray(p,dtype=np.float64)
    if not np.isfinite(p).all() or ((p<0)|(p>1)).any():raise InvalidModel('invalid CDF probability')
    q=np.clip(p,1e-9,1-1e-9)
    return np.log(q)-np.log1p(-q)
def display_percentages(p_up):
    p=float(p_up)
    if not math.isfinite(p) or not .01<=p<=.99:raise InvalidModel('final published probability required')
    up=Decimal(str(p));a=[up*1000,(1-up)*1000]
    units=[int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in a]
    order=sorted(range(2),key=lambda j:(-(a[j]-units[j]),j))
    for j in order[:1000-sum(units)]:units[j]+=1
    return {'up_percent':units[0]/10,'down_percent':units[1]/10}
def logistic_objective(theta,x,y):
    z=theta[0]+x@theta[1:]
    loss=float(np.mean(np.logaddexp(0,z)-y*z)+L2/2*np.dot(theta[1:],theta[1:]))
    e=expit(z)-y
    grad=np.r_[e.mean(),x.T@e/len(y)+L2*theta[1:]]
    return loss,grad
def optimizer_receipt(result):
    if not result.success or not np.isfinite(result.x).all() or not math.isfinite(float(result.fun)):
        raise InvalidModel('fixed optimizer failed; no retries: '+str(result.message))
    return {'success':bool(result.success),'iterations':int(result.nit),'evaluations':int(result.nfev),
            'status':int(result.status),'message':str(result.message),'penalized_fit_objective':float(result.fun)}
def fit_logistic(x,y):
    x,y=matrix(x),binary(y)
    if len(x)!=len(y):raise InvalidModel('X/y length mismatch')
    result=minimize(logistic_objective,np.zeros(x.shape[1]+1,dtype=np.float64),args=(x,y),
                    jac=True,method='L-BFGS-B',options=dict(OPTIONS))
    receipt=optimizer_receipt(result)
    return {'intercept':float(result.x[0]),'beta':result.x[1:].tolist(),'fit':receipt}
def logits(model,x):
    x=matrix(x);b=np.asarray(model['beta'],dtype=np.float64)
    if x.shape[1]!=len(b):raise InvalidModel('feature dimension mismatch')
    z=float(model['intercept'])+x@b
    if not np.isfinite(z).all():raise InvalidModel('nonfinite model logits')
    return z
def calibration_objective(theta,z,y,constant=False):
    a,b=(1.,theta[0]) if constant else theta
    v=a*z+b
    loss=float(np.mean(np.logaddexp(0,v)-y*v)+.01*((a-1)**2+b*b))
    e=expit(v)-y
    g=np.array([e.mean()+.02*b]) if constant else np.array([np.mean(e*z)+.02*(a-1),e.mean()+.02*b])
    return loss,g
def fit_calibrator(z,y):
    z,y=vector(z),binary(y)
    if len(z)!=len(y):raise InvalidModel('logit/y length mismatch')
    constant=float(z.std(ddof=0))<=1e-12
    result=minimize(calibration_objective,np.array([0.] if constant else [1.,0.]),args=(z,y,constant),
        jac=True,method='L-BFGS-B',bounds=[(None,None)] if constant else [(0,None),(None,None)],options=dict(OPTIONS))
    receipt=optimizer_receipt(result)
    a,b=(1.,float(result.x[0])) if constant else (float(result.x[0]),float(result.x[1]))
    if a<0:raise InvalidModel('negative calibration slope')
    return {'a':a,'b':b,'constant_logits_a_fixed_to_1':constant,'fit':receipt}
def probability_record(z,calibrator,uncalibrated=None):
    z=np.asarray(z,dtype=np.float64)
    if not np.isfinite(z).all():raise InvalidModel('nonfinite raw logit')
    uncal=expit(z) if uncalibrated is None else np.asarray(uncalibrated,dtype=np.float64)
    if uncal.shape!=z.shape:raise InvalidModel('probability/logit shape mismatch')
    if not np.isfinite(uncal).all() or ((uncal<0)|(uncal>1)).any():raise InvalidModel('invalid uncalibrated probability')
    calibrated=expit(float(calibrator['a'])*z+float(calibrator['b']))
    final=clipped(calibrated)
    return {'raw_model_logit':z,'uncalibrated_p_up':uncal,'calibrated_unclipped_p_up':calibrated,
            'p_up':final,'p_down':1-final}
def frequency(y):
    y=binary(y);p=(float(y.sum())+.5)/(len(y)+1)
    return {'up':int(y.sum()),'n':len(y),'p_unclipped':p,'p_up':float(clipped(p)),
            'raw_model_logit':float(math.log(p)-math.log1p(-p))}
def fit_scaler(x,dates):
    x=matrix(x)
    if len(x)!=len(dates) or any(d>'2023-11-05' for d in dates):raise InvalidModel('training-only scaler dates required')
    return {'n':len(x),'mean':x.mean(axis=0).tolist(),'std_ddof0':x.std(axis=0,ddof=0).tolist(),
            'constant_indices':np.flatnonzero(x.std(axis=0,ddof=0)<=1e-12).tolist(),
            'first_date':min(dates),'last_date':max(dates),'features':FEATURES}
def scale(x,scaler):
    x=matrix(x);mu=np.asarray(scaler['mean']);sd=np.asarray(scaler['std_ddof0'])
    if x.shape[1]!=len(mu) or len(mu)!=len(sd):raise InvalidModel('scaler dimension mismatch')
    out=(x-mu)/np.where(sd<=1e-12,1.,sd)
    out[:,sd<=1e-12]=0.
    if not np.isfinite(out).all():raise InvalidModel('nonfinite standardized features')
    return out
def empirical_distribution(z):
    z=vector(z);mean=float(z.mean());centered=np.sort(z-mean)
    return {'n':len(z),'training_mean_removed':mean,'centered_samples_sorted':centered.tolist()}
def tail_probability(distribution,threshold):
    a=np.asarray(distribution['centered_samples_sorted'],dtype=np.float64)
    t=np.asarray(threshold,dtype=np.float64)
    if len(a)==0 or not np.isfinite(a).all() or not np.isfinite(t).all() or (np.diff(a)<0).any():raise InvalidModel('invalid empirical distribution or threshold')
    # searchsorted(left) counts strictly smaller; exact terminal ties belong to UP.
    return 1.-np.searchsorted(a,t,side='left')/len(a)
def fit_ridge(x,y):
    x,y=matrix(x),vector(y)
    if len(x)!=len(y):raise InvalidModel('ridge X/y length mismatch')
    design=np.column_stack([np.ones(len(x)),x]);penalty=np.diag([0.]+[L2]*x.shape[1])
    try:theta=np.linalg.solve(design.T@design/len(x)+penalty,design.T@y/len(x))
    except np.linalg.LinAlgError as e:raise InvalidModel('fixed ridge solve failed; no fallback') from e
    if not np.isfinite(theta).all():raise InvalidModel('nonfinite ridge coefficients')
    residual=design@theta-y
    gradient=2*design.T@residual/len(x)+2*penalty@theta
    return {'intercept':float(theta[0]),'beta':theta[1:].tolist(),
            'fit':{'success':True,'solver':'numpy.linalg.solve','penalized_fit_objective':float(np.mean(residual**2)+L2*np.dot(theta[1:],theta[1:])),
                   'normal_equation_max_abs_gradient':float(np.abs(gradient).max())}}
def initial_probability(model,x=None):
    if model['model']=='M0':
        n=1 if x is None else len(x);f=model['frequency'];z=np.full(n,f['raw_model_logit'])
        raw=np.full(n,f['p_unclipped']);final=clipped(raw)
        return {'raw_model_logit':z,'uncalibrated_p_up':raw,'calibrated_unclipped_p_up':raw,'p_up':final,'p_down':1-final}
    return probability_record(logits(model['regression'],x),model['calibrator'])
def revision_probability(model,threshold,x=None):
    threshold=np.asarray(threshold,dtype=np.float64)
    if model['model']=='R1':
        raw=tail_probability(model['distribution'],threshold);p=clipped(raw)
        return {'raw_cdf_p_up':raw,'raw_model_logit':np.full(raw.shape,np.nan),
                'uncalibrated_p_up':raw,'calibrated_unclipped_p_up':raw,'p_up':p,'p_down':1-p,
                'conditional_mean_z':np.zeros(raw.shape)}
    if model['model']!='R2':raise InvalidModel('R0 must copy exact saved original M2, not recompute')
    mu=logits(model['mean_model'],scale(x,model['mean_scaler']))
    raw=tail_probability(model['distribution'],threshold-mu)
    return {**probability_record(logit_for_cdf(raw),model['calibrator'],raw),
            'raw_cdf_p_up':raw,'conditional_mean_z':mu}
