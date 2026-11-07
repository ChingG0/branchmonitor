"""Offline Package3: as-of features and DEVELOPMENT labels. No test-label API."""
from pathlib import Path
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo,reset_tzpath
from collections import defaultdict,Counter
from bisect import bisect_left,bisect_right
import csv,json,math,hashlib,argparse
import numpy as np
from dateutil.easter import easter

METHOD='v6-conditional-0830-protocol-1.0.0'
DATASET='v6-conditional-dataset-1.0.0'
KEYS=['night_return_scaled','night_range_scaled','log_daily_sigma','night_age',
      'adr_new_return_scaled','sox_new_return_scaled','spx_new_return_scaled',
      'fx_new_return','log_vix','us_after_night_count']
DEV_END='2024-11-05'
TW=ZoneInfo('Asia/Taipei');NY=ZoneInfo('America/New_York')

class Ineligible(ValueError):pass
class HoldoutLocked(ValueError):pass
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read_csv(p):
    with Path(p).open(newline='') as f:return list(csv.DictReader(f))
def write_csv(p,rows,fields=None):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def save(p,value):Path(p).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def istrue(x):return x is True or x=='True'
def dt(d,h,m=0,zone=None):return datetime.combine(date.fromisoformat(d),time(h,m),zone or TW)
def positive(x):
    try:v=float(x)
    except (ValueError,TypeError):raise Ineligible('missing/non-numeric input')
    if not math.isfinite(v) or v<=0:raise Ineligible('nonpositive/nonfinite input')
    return v
def observed(d):return d-timedelta(days=1) if d.weekday()==5 else d+timedelta(days=1) if d.weekday()==6 else d
def nth(y,m,w,n):
    d=date(y,m,1);return d+timedelta(days=(w-d.weekday())%7+7*(n-1))
def lastmon(y,m):
    d=date(y,m+1,1)-timedelta(days=1);return d-timedelta(days=d.weekday())
def holidays(y):
    h={nth(y,1,0,3),nth(y,2,0,3),easter(y)-timedelta(days=2),lastmon(y,5),observed(date(y,7,4)),nth(y,9,0,1),nth(y,11,3,4),observed(date(y,12,25))}
    if date(y,1,1).weekday()!=5:h.add(observed(date(y,1,1)))
    if y>=2022:h.add(observed(date(y,6,19)))
    if y==2018:h.add(date(2018,12,5))
    if y==2025:h.add(date(2025,1,9))
    return h
def us_sessions(start,end):
    d=date.fromisoformat(start);last=date.fromisoformat(end);out=[]
    while d<=last:
        if d.weekday()<5 and d not in holidays(d.year):out.append(d.isoformat())
        d+=timedelta(days=1)
    return out
def us_close(d):return dt(d,16,zone=NY).astimezone(TW)
def us_available(d):return max(us_close(d),dt((date.fromisoformat(d)+timedelta(days=1)).isoformat(),8))
def sigma60(closes):
    if len(closes)!=61:raise Ineligible('sigma requires exactly61 known cash closes')
    c=np.array([positive(v) for v in closes],dtype=np.float64)
    returns=np.log(c[1:]/c[:-1]);w=.94**np.arange(59,-1,-1,dtype=np.float64)
    return max(.001,float(np.sqrt(np.sum(w*returns**2)/np.sum(w))))
def fit_scaler(rows):
    if not rows or any(r['split']!='train' for r in rows):raise ValueError('scaler accepts nonempty training rows only')
    x=np.array([[float(r[k]) for k in KEYS] for r in rows],dtype=np.float64)
    if not np.isfinite(x).all():raise ValueError('nonfinite training features')
    mean=x.mean(axis=0);std=x.std(axis=0,ddof=0)
    return {'n':len(rows),'mean':dict(zip(KEYS,mean.tolist())),'std_ddof0':dict(zip(KEYS,std.tolist())),
            'constant_features':[k for k,s in zip(KEYS,std) if s<=1e-12],
            'fit_split':'train','fit_first':min(r['decision_date'] for r in rows),'fit_last':max(r['decision_date'] for r in rows)}
def transform(row,scaler):
    return {k:0. if scaler['std_ddof0'][k]<=1e-12 else (float(row[k])-scaler['mean'][k])/scaler['std_ddof0'][k] for k in KEYS}

class Sources:
    """Unscaled price histories may include later dates; feature() time-filters every access.
    No function produces a sealed-test outcome. Future historical prices ARE known in
    this archive, so this is procedural label separation, not cryptographic blindness.
    """
    def __init__(self,parent1,parent2):
        global TW,NY
        self.p1=Path(parent1);self.p2=Path(parent2)
        reset_tzpath([str(self.p2/'environment/tz')]);ZoneInfo.clear_cache()
        TW=ZoneInfo('Asia/Taipei');NY=ZoneInfo('America/New_York')
        self.protocol=json.loads((self.p2/'protocol.json').read_text())
        assert self.protocol['method_version']==METHOD
        self.cash={r['date']:r for r in read_csv(self.p1/'local-snapshot/taiex-official.csv')}
        self.cash_dates=sorted(self.cash)
        self.metadata=read_csv(self.p1/'results/common-core-candidate-dates.csv')
        self.metadata_map={r['decision_date']:r for r in self.metadata}
        self.inventory=read_csv(self.p1/'results/decision-time-inventory.csv')
        self.futures={}
        for r in read_csv(self.p1/'results/tx-single-contract-ohlcv.csv'):
            key=(r['date'],r['contract'],r['session'])
            if key in self.futures:raise ValueError('duplicate futures key')
            self.futures[key]=r
        self.regular_days=sorted({d for d,c,s in self.futures if s=='一般'})
        self.nights=defaultdict(list)
        for (d,c,s),r in self.futures.items():
            if s=='盤後':self.nights[d].append(r)
        self.series={k:{r['date']:r for r in read_csv(self.p1/f'results/{k}-bars.csv')} for k in ['tsm','sox','spx']}
        self.vix={r['date']:r for r in read_csv(self.p1/'results/vix-official.csv')}
        self.fx=read_csv(self.p1/'results/fx-cbc.csv')
        self.fx.sort(key=lambda r:r['date'])
        self.fx_times=[datetime.fromisoformat(r['publication_bound']) for r in self.fx]
        self.stock_dates={r['date'] for r in read_csv(self.p1/'results/stock2330-research-combined.csv')}
        self.us_dates=us_sessions('2017-01-01',self.protocol['sources']['cutoff_cash'])
        self.us_times=[us_available(d) for d in self.us_dates]
        self.label_access=[]

    def feature(self,m):
        d=m['decision_date'];cut=dt(d,8,30);anchor=m['anchor_date'];at=dt(anchor,13,30)
        j=bisect_left(self.cash_dates,d)
        if j==0 or self.cash_dates[j-1]!=anchor:raise Ineligible('anchor is not previous actual cash day')
        history=self.cash_dates[max(0,j-61):j]
        if len(history)!=61:raise Ineligible('sigma warmup insufficient')
        if anchor not in self.stock_dates:raise Ineligible('stock2330 anchor missing')
        ledger=[]
        def add(key,source,observation,available,reference,values):
            if available>cut:raise Ineligible('input available after decision: '+key)
            ledger.append({'decision_date':d,'input_key':key,'source':source,'observation':observation,
                'available_bound':available.isoformat(),'decision_at':cut.isoformat(),
                'source_row_reference':reference,'values':json.dumps(values,separators=(',',':'),allow_nan=False),
                'time_evidence':'schedule_reconstruction_not_contemporaneous_vintage'})
        closes=[positive(self.cash[x]['close']) for x in history];sig=sigma60(closes)
        add('cash_sigma_history','TWSE','|'.join(history),at,'local-snapshot/taiex-official.csv:date in observation',
            {'n':61,'first':history[0],'last':history[-1],'history_sha256':hashlib.sha256(json.dumps(list(zip(history,closes)),separators=(',',':')).encode()).hexdigest()})
        reg_i=bisect_left(self.regular_days,d)-1
        prior=self.regular_days[reg_i] if reg_i>=0 else None
        if prior!=anchor:raise Ineligible('prior cash/futures sessions differ')
        eligible=[]
        for n in self.nights.get(d,[]):
            key=(prior,n['contract'],'一般')
            if key in self.futures and positive(n['volume'])>0:eligible.append(n)
        if not eligible:raise Ineligible('night same-contract pair missing')
        night=min(eligible,key=lambda r:r['contract']);contract=night['contract'];regular=self.futures[(prior,contract,'一般')]
        ns=dt(prior,15);ne=ns+timedelta(hours=14)
        if ne>cut:raise Ineligible('night not completed')
        nc,nh,nl,no=(positive(night[k]) for k in ['close','high','low','open'])
        if not nl<=min(no,nc)<=max(no,nc)<=nh:raise Ineligible('invalid night OHLC')
        pc=positive(regular['close'])
        add('night','TAIFEX',d,ne,f'results/tx-single-contract-ohlcv.csv:{d},{contract},盤後',{'open':no,'high':nh,'low':nl,'close':nc,'volume':float(night['volume']),'session_start_bound':ns.isoformat()})
        add('prior_same_contract_regular','TAIFEX',prior,dt(prior,13,45),f'results/tx-single-contract-ohlcv.csv:{prior},{contract},一般',{'close':pc})
        latest_i=bisect_right(self.us_times,cut)-1;base_i=bisect_right(self.us_times,at)-1
        if latest_i<0 or base_i<0:raise Ineligible('US window boundary unavailable')
        latest=self.us_dates[latest_i];base=self.us_dates[base_i];window=self.us_dates[base_i+1:latest_i+1]
        # Full intermediate windows are required, even though cumulative log returns telescope.
        for key,series in self.series.items():
            if any(u not in series for u in [base]+window):raise Ineligible('incomplete US window: '+key)
        extras={}
        for key,outkey in [('tsm','adr_new_return_scaled'),('sox','sox_new_return_scaled'),('spx','spx_new_return_scaled')]:
            series=self.series[key]
            a=positive(series[base]['close']);b=positive(series[latest]['close'])
            extras[outkey]=math.log(b/a)/sig
            for role,u,v in [('anchor',base,a),('latest',latest,b)]:
                add(key+'_'+role,'Yahoo sealed close',u,us_available(u),f'results/{key}-bars.csv:{u}',{'close':v,'regular_close_bound':us_close(u).isoformat()})
        # Do not allow a source value unknown at the older cash anchor to define that baseline.
        assert us_available(base)<=at
        if latest not in self.vix:raise Ineligible('Cboe VIX missing exact US reference date')
        vx=positive(self.vix[latest]['CLOSE'])
        add('vix','Cboe',latest,us_available(latest),f'results/vix-official.csv:{latest}',{'close':vx})
        fi=bisect_right(self.fx_times,cut)-1;fb=bisect_right(self.fx_times,at)-1
        if min(fi,fb)<0:raise Ineligible('CBC boundary missing')
        fxlatest,fxbase=self.fx[fi],self.fx[fb]
        for role,r in [('anchor',fxbase),('latest',fxlatest)]:
            add('fx_'+role,'CBC',r['date'],datetime.fromisoformat(r['publication_bound']),f'results/fx-cbc.csv:{r["date"]}',{'ntd_per_usd':positive(r['ntd_per_usd'])})
        # Independently reconstructed clocks must agree with the frozen parent metadata.
        checks={'decision_at':cut.isoformat(),'night_contract':contract,'night_start_assumed':ns.isoformat(),
                'night_end_bound':ne.isoformat(),'night_same_contract_prior_regular_date':prior,
                'latest_us_source_date':latest,'latest_us_close_bound':us_close(latest).isoformat(),
                'latest_us_available_assumed':us_available(latest).isoformat(),'us_anchor_reference_date':base,
                'overseas_window_dates':'|'.join(window),'fx_date':fxlatest['date']}
        for k,v in checks.items():
            if m[k]!=v:raise Ineligible('parent timing disagreement: '+k)
        n_after=sum(us_close(u)>ne for u in window)
        if int(float(m['us_sessions_after_night_end']))!=n_after or int(m['us_sessions_since_cash_anchor'])!=len(window):raise Ineligible('parent US session count disagreement')
        features={'night_return_scaled':math.log(nc/pc)/sig,'night_range_scaled':math.log(nh/nl)/sig,
                  'log_daily_sigma':math.log(sig),'night_age':math.log1p((cut-ne).total_seconds()/3600/24),
                  **extras,'fx_new_return':math.log(positive(fxlatest['ntd_per_usd'])/positive(fxbase['ntd_per_usd'])),
                  'log_vix':math.log(vx),'us_after_night_count':float(n_after)}
        if not all(math.isfinite(v) for v in features.values()):raise Ineligible('nonfinite feature')
        row={'decision_date':d,'decision_at':cut.isoformat(),'anchor_date':anchor,'anchor_close':closes[-1],
             'sigma_t':sig,**features,'night_contract':contract,'night_end_bound':ne.isoformat(),
             'night_age_hours':(cut-ne).total_seconds()/3600,'prior_regular_date':prior,'us_anchor_date':base,
             'us_latest_date':latest,'us_window_dates':'|'.join(window),'us_new_sessions':len(window),
             'reopening_multi_us_sessions':len(window)>1,'fx_anchor_date':fxbase['date'],'fx_latest_date':fxlatest['date'],
             'cash_sigma_first_date':history[0],'cash_sigma_last_date':anchor,
             'us_dst':bool(us_close(latest).astimezone(NY).dst()),'qualified':True,
             'method_version':METHOD,'dataset_version':DATASET,'point_in_time_vintage_verified':False}
        return row,ledger

    def development_outcome(self,row,revision=False):
        """Fail BEFORE requesting any test/late outcome price, including excluded rows."""
        split=row['split']
        target=row['original_target_date'] if revision else row['target_date']
        if split not in ['train','calibration'] or not target or target>DEV_END:raise HoldoutLocked('test outcomes remain locked until package5')
        split_end=next(p['label_cutoff'] for p in self.protocol['split']['periods'] if p['name']==split)
        if target>split_end or not istrue(row.get('eligible',False)):raise HoldoutLocked('purged/ineligible target prohibited')
        anchor=row['original_anchor_date'] if revision else row['anchor_date']
        end=positive(self.cash[target]['close']);start=positive(self.cash[anchor]['close'])
        self.label_access.append({'split':split,'target_date':target,'revision':revision})
        out={'y_up':int(end>=start),'log_return':math.log(end/start),'simple_return':end/start-1,
             'label_available_bound':dt(target,13,30).isoformat()}
        if revision:
            current=positive(self.cash[row['current_completed_cash_date']]['close'])
            r=int(row['remaining_sessions']);sigma=float(row['current_sigma_t'])
            out.update({'remaining_log_return':math.log(end/current),'remaining_standardized_return':math.log(end/current)/(math.sqrt(r)*sigma)})
        elif int(row['horizon'])==1:
            opening=positive(self.cash[target]['open'])
            out.update({'gap_log_return':math.log(opening/start),'intraday_log_return':math.log(end/opening)})
        else:out.update({'gap_log_return':'','intraday_log_return':''})
        return out

def verify_parents(p1,p2):
    p1,p2=Path(p1),Path(p2)
    manifests={}
    for name,p in [('package1',p1),('package2',p2)]:
        manifest=json.loads((p/'manifest-sha256.json').read_text())
        for f,h in manifest.items():
            if sha(p/f)!=h:raise ValueError('Parent checksum mismatch: '+name+'/'+f)
        manifests[name]={'manifest_sha256':sha(p/'manifest-sha256.json'),'files_verified':len(manifest)}
    refs=json.loads((p2/'input-references.json').read_text())
    assert refs['parent_manifest_sha256']==manifests['package1']['manifest_sha256']
    assert json.loads((p2/'protocol.json').read_text())['method_version']==METHOD
    return manifests

def build(p1,p2,out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('Output must be new or empty; no overwrite')
    out.mkdir(parents=True,exist_ok=True)
    parents=verify_parents(p1,p2);source=Sources(p1,p2)
    features=[];ledger=[];excluded=[]
    for m in source.metadata:
        try:
            row,trace=source.feature(m);features.append(row);ledger.extend(trace)
        except Ineligible as e:excluded.append({'decision_date':m['decision_date'],'reason':str(e)})
    fm={r['decision_date']:r for r in features}
    write_csv(out/'features-unscaled.csv',features)
    write_csv(out/'input-ledger.csv',ledger)
    write_csv(out/'feature-exclusions.csv',excluded,['decision_date','reason'])
    registered=[r['decision_date'] for r in source.metadata]
    membership=read_csv(Path(p2)/'date-membership.csv');joined=[];development=[];scaled=[];scalers={};summary=[]
    for r in membership:
        present=r['decision_date'] in fm;eligible=istrue(r['admitted']) and present
        joined.append({**r,'eligible':eligible,'feature_qualified':present,
            'eligibility_reason':'eligible' if eligible else ('feature_failure' if not present else r['reason']),
            'feature_key':r['decision_date'],'label_status':'development_available' if eligible and r['split']!='sealed_test' else 'sealed_test_locked' if r['split']=='sealed_test' else 'purged'})
    for h in (1,2,3):
        rows=[r for r in joined if int(r['horizon'])==h and r['eligible']]
        train=[{**fm[r['decision_date']],'split':'train'} for r in rows if r['split']=='train']
        scaler=fit_scaler(train);scalers['H'+str(h)]=scaler
        for r in rows:
            v=fm[r['decision_date']];scaled.append({'horizon':h,'split':r['split'],'decision_date':r['decision_date'],'anchor_date':r['anchor_date'],'target_date':r['target_date'],**transform(v,scaler)})
            if r['split']!='sealed_test':development.append({**{k:r[k] for k in ['horizon','split','decision_date','anchor_date','target_date']},**source.development_outcome(r)})
        for sp in ['train','calibration','sealed_test']:
            s=[r for r in rows if r['split']==sp]
            labels=[r for r in development if int(r['horizon'])==h and r['split']==sp]
            summary.append({'horizon':h,'split':sp,'feature_and_maturity_qualified_n':len(s),
                'first':s[0]['decision_date'] if s else None,'last':s[-1]['decision_date'] if s else None,
                'up_n':sum(r['y_up'] for r in labels) if sp!='sealed_test' else None,
                'down_n':sum(1-r['y_up'] for r in labels) if sp!='sealed_test' else None,
                'test_labels_opened':False})
    write_csv(out/'initial-membership.csv',joined);write_csv(out/'features-scaled-initial.csv',scaled)
    write_csv(out/'development-labels-initial.csv',development);save(out/'initial-scalers.json',scalers)
    save(out/'sample-summary.json',summary)
    # Full official decision grid retains the five known source gaps and any new failures.
    write_csv(out/'official-decision-grid.csv',[{'decision_date':r['decision_date'],'anchor_date':r['anchor_date'],
       'parent_common_qualified':r['decision_date'] in registered,'feature_qualified':r['decision_date'] in fm,
       'reason':r['exclusion_reasons'] if r['decision_date'] not in registered else '' if r['decision_date'] in fm else 'feature_failure'} for r in source.inventory])
    revisions=[];rd=[]
    for r in read_csv(Path(p2)/'revision-date-membership.csv'):
        eligible=istrue(r['admitted']) and r['root_decision_date'] in fm and r['revision_decision_date'] in fm
        current=fm.get(r['revision_decision_date'])
        row={**r,'eligible':eligible,'feature_key':r['revision_decision_date'],
             'current_sigma_t':current['sigma_t'] if eligible else '',
             'original_anchor_close':fm[r['root_decision_date']]['anchor_close'] if eligible else '',
             'current_completed_cash_close':current['anchor_close'] if eligible else '',
             'threshold_z':math.log(fm[r['root_decision_date']]['anchor_close']/current['anchor_close'])/(math.sqrt(int(r['remaining_sessions']))*current['sigma_t']) if eligible else '',
             'label_status':'development_available' if eligible and r['split']!='sealed_test' else 'sealed_test_locked' if r['split']=='sealed_test' else 'purged'}
        revisions.append(row)
        if eligible and r['split']!='sealed_test':rd.append({**{k:row[k] for k in ['case','split','root_decision_date','original_anchor_date','original_target_date','revision_decision_date','current_completed_cash_date','remaining_sessions','current_sigma_t']},**source.development_outcome(row,revision=True)})
    write_csv(out/'revision-membership.csv',revisions);write_csv(out/'development-labels-revision.csv',rd)
    # Deduplicated training examples for the frozen r=1/r=2 mean/distribution learners.
    unique={}
    for r in rd:
        if r['split']!='train':continue
        key=(r['revision_decision_date'],r['remaining_sessions'],r['original_target_date'])
        v={k:r[k] for k in ['revision_decision_date','remaining_sessions','original_target_date','remaining_log_return','remaining_standardized_return']}
        if key in unique and unique[key]!=v:raise ValueError('duplicate mean-fit target mismatch')
        unique[key]=v
    learning=[unique[k] for k in sorted(unique)]
    write_csv(out/'remaining-learning-train.csv',learning)
    rev_summary=[]
    for case in ['H2remaining1','H3remaining2','H3remaining1']:
        for sp in ['train','calibration','sealed_test']:
            a=[r for r in revisions if r['case']==case and r['split']==sp and r['eligible']]
            rev_summary.append({'case':case,'split':sp,'eligible_n':len(a),'test_labels_opened':False})
    save(out/'revision-summary.json',rev_summary)
    # Boundary/holiday/roll/DST cases are chosen by metadata, not by their outcomes.
    selected=set()
    for h in (1,2,3):
        for sp in ['train','calibration','sealed_test']:
            rs=[r for r in joined if int(r['horizon'])==h and r['split']==sp and r['eligible']]
            if rs:selected.update([rs[0]['decision_date'],rs[-1]['decision_date']])
    for predicate in [lambda r:r['reopening_multi_us_sessions'],lambda r:r['night_age_hours']>48,lambda r:r['us_dst'],lambda r:not r['us_dst']]:
        rs=[r for r in features if predicate(r)]
        if rs:selected.update([rs[0]['decision_date'],rs[-1]['decision_date']])
    previous=None
    for r in features:
        if previous and r['night_contract']!=previous:
            selected.add(r['decision_date'])
            if len(selected)>35:break
        previous=r['night_contract']
    write_csv(out/'timing-spot-cases.csv',[r for r in features if r['decision_date'] in selected])
    access=Counter((x['split'],'revision' if x['revision'] else 'initial') for x in source.label_access)
    audit={'method_version':METHOD,'dataset_version':DATASET,'parents':parents,'parent_feature_candidates_n':len(source.metadata),
      'feature_qualified_n':len(features),'feature_exclusions_n':len(excluded),'input_ledger_rows':len(ledger),
      'all_input_bounds_le_decision':all(datetime.fromisoformat(r['available_bound'])<=datetime.fromisoformat(r['decision_at']) for r in ledger),
      'initial_eligible_rows':len(scaled),'revision_eligible_rows':sum(r['eligible'] for r in revisions),
      'deduplicated_remaining_training_rows':{str(k):sum(int(r['remaining_sessions'])==k for r in learning) for k in [1,2]},
      'outcome_access_by_cohort':[{'split':s,'kind':kind,'n':n} for (s,kind),n in sorted(access.items())],
      'maximum_development_label_target':max(x['target_date'] for x in source.label_access),
      'sealed_test_target_labels_generated':0,'models_fit':0,'probabilities_generated':0,'performance_scored':False,
      'feature_build_reads_prior_test_prices_as_known_lags':True,
      'blindness_limit':'Raw historical quotes were already downloaded and known; separation protects target use, not human knowledge or cryptographic blindness.',
      'production_changed':False}
    save(out/'build-audit.json',audit)
    return audit

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--package1',required=True);ap.add_argument('--package2',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();print(json.dumps(build(a.package1,a.package2,a.out),ensure_ascii=False,indent=2))
