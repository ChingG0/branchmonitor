"""Chat/CLI-only prospective conditional forecasts; reads never mutate or grade.

Package4 parameters are used unchanged. No fit, network request, order or timer.
Production CLI deliberately has no --now, --date or supplied-probability option.
"""
from __future__ import annotations
import argparse, base64, csv, fcntl, hashlib, importlib.util, io, json, math, os
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db

TZ = ZoneInfo('Asia/Taipei')
CONFIG = db.ROOT / 'config/v6_conditional'
STORE = db.DATA / 'v6_conditional'
VERSION = 'v6-conditional-prospective-1.0.0'
SOURCES = ('cash','tx','tsm','sox','spx','vix','fx','stock2330','calendar')

def encode(x): return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',',':'), allow_nan=False).encode()
def digest(x): return hashlib.sha256(x).hexdigest()
def clock(): return datetime.now(TZ)
def stamp(s):
    d = datetime.fromisoformat(s)
    if d.tzinfo is None: raise ValueError('時間必須包含時區')
    return d.astimezone(TZ)
def at(d,h,m=0): return datetime.combine(datetime.fromisoformat(d).date(),time(h,m),TZ)
def read_csv(raw): return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class Engine:
    def __init__(self, store=None, config=None):
        self.store=Path(store or STORE);self.config=Path(config or CONFIG)
        self.release=json.loads((self.config/'release.json').read_text())
        if self.release['version']!=VERSION: raise ValueError('觀察版本不符')
        if self.release['historical_initial_choice']!={'1':'M1','2':'M1','3':'M1'} or self.release['revision_eligible'] is not False:
            raise ValueError('歷史資格與本次固定版本不符')
        if digest(Path(__file__).read_bytes())!=self.release['runtime_sha256']: raise ValueError('執行程式與封存不符')
        for name,h in self.release['assets'].items():
            if digest((self.config/name).read_bytes())!=h: raise ValueError('封存資產被修改：'+name)
        self.models=load_module(self.config/'models.py','_v6_conditional_frozen_models')
        self.dataset=load_module(self.config/'dataset.py','_v6_conditional_frozen_dataset')
        self.params=json.loads((self.config/'initial-models.json').read_text())
        self.protocol=json.loads((self.config/'protocol.json').read_text())
        self.events=[]

    def read(self):
        events=[];prev='0'*64
        if self.store.exists():
            for i,p in enumerate(sorted(self.store.glob('event-*.json'))):
                raw=p.read_bytes();e=json.loads(raw)
                if e['sequence']!=i or e['previous_sha256']!=prev or p.name!=f'event-{i:07d}-{digest(raw)[:16]}.json': raise ValueError('觀察紀錄鏈不完整或被修改')
                if e['version']!=VERSION: raise ValueError('混入其他方法版本')
                if events and stamp(e['saved_at'])<stamp(events[-1]['saved_at']): raise ValueError('紀錄時間倒退')
                events.append(e);prev=digest(raw)
        self.events=events
        if events and (events[0]['kind']!='enroll' or events[0]['payload']['release_sha256']!=digest((self.config/'release.json').read_bytes())):
            raise ValueError('觀察版本與起跑封存不符')
        return events

    @contextmanager
    def transaction(self):
        self.store.mkdir(parents=True,exist_ok=True)
        with (self.store/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);self.read()
            try: yield
            finally: fcntl.flock(lock,fcntl.LOCK_UN)

    def append(self,kind,payload,now):
        if now.tzinfo is None: raise ValueError('保存時間缺少時區')
        if self.events and now<stamp(self.events[-1]['saved_at']): raise ValueError('不可倒填保存時間')
        prev=digest(encode(self.events[-1])) if self.events else '0'*64
        e={'sequence':len(self.events),'previous_sha256':prev,'version':VERSION,'saved_at':now.isoformat(), 'kind':kind,'payload':payload}
        raw=encode(e);path=self.store/f'event-{len(self.events):07d}-{digest(raw)[:16]}.json'
        with path.open('xb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
        self.events.append(e);return e

    def enroll(self,now=None):
        now=now or clock()
        with self.transaction():
            if self.events: raise ValueError('已開始觀察，不可重設起跑日')
            self.append('enroll',{'release_sha256':digest((self.config/'release.json').read_bytes()),'models':['M0','M1','M2'], 'revisions':'blocked_original_M2_not_eligible','production_active':False},now)
        return self.report()

    def capture(self,path,now):
        bundle=json.loads(Path(path).read_text());captured={};evidence={}
        if set(bundle.get('sources',{}))!=set(SOURCES): raise ValueError('九項來源須完整，不以缺值代替安全')
        for key in SOURCES:
            item=bundle['sources'][key];p=Path(path).parent/item['file'];raw=p.read_bytes()
            original=(Path(path).parent/item['raw_file']).read_bytes()
            if digest(raw)!=item['sha256'] or digest(original)!=item['raw_sha256']: raise ValueError('原始來源或正規化檔案雜湊不符：'+key)
            available=stamp(item['available_at'])
            if available>now or not item['source_url'].startswith('https://'): raise ValueError('來源公布時間或連結不合格：'+key)
            captured[key]=json.loads(raw) if key=='calendar' else read_csv(raw)
            # Preserve both exact normalized data and the retrieved raw vintage.
            evidence[key]={'sha256':digest(raw),'raw_sha256':digest(original),'available_at':available.isoformat(),'source_url':item['source_url'],
                'normalized_base64':base64.b64encode(raw).decode(),'raw_base64':base64.b64encode(original).decode()}
        cal=captured['calendar'];sessions=cal.get('sessions',[])
        if len(sessions)!=len(set(sessions)) or sessions!=sorted(sessions) or not sessions: raise ValueError('日曆須為完整排序且不重複的實際／已公告交易日')
        if any(datetime.fromisoformat(d).date().isoformat()!=d for d in sessions): raise ValueError('日曆日期無效')
        return bundle,captured,evidence

    def feature(self,decision,data,evidence):
        """Construct original Package3 Sources from archived normalized tables."""
        D=self.dataset;cut=at(decision,8,30)
        if any(stamp(e['available_at'])>cut for e in evidence.values()): raise ValueError('來源在08:30之後才可取得')
        sessions=data['calendar']['sessions'];j=sessions.index(decision)
        if j<61 or len(sessions[j:j+3])!=3: raise ValueError('日曆暖機或三個到期日不足')
        anchor=sessions[j-1]
        s=object.__new__(D.Sources)
        s.cash={r['date']:r for r in data['cash'] if at(r['date'],13,30)<=cut};s.cash_dates=sorted(s.cash)
        # A missing cash day must not silently shift the sixty-return window.
        if s.cash_dates[-61:]!=sessions[j-61:j]: raise ValueError('最近61筆官方現貨收盤不完整')
        s.stock_dates={r['date'] for r in data['stock2330']}
        s.futures={};s.nights={}
        from collections import defaultdict
        s.nights=defaultdict(list)
        for r in data['tx']:
            if r['session']=='一般': known=at(r['date'],13,45)
            elif r['session']=='盤後':
                k=sessions.index(r['date']);known=at(sessions[k-1],15)+timedelta(hours=14)
            else: raise ValueError('期貨盤別只能為一般／盤後')
            if known>cut: continue
            key=(r['date'],r['contract'],r['session'])
            if key in s.futures: raise ValueError('期貨合約盤別重複')
            s.futures[key]=r
            if r['session']=='盤後':s.nights[r['date']].append(r)
        s.regular_days=sorted({d for d,c,k in s.futures if k=='一般'})
        s.series={k:{r['date']:r for r in data[k] if D.us_available(r['date'])<=cut} for k in ['tsm','sox','spx']}
        s.vix={r['date']:{**r,'CLOSE':r.get('CLOSE',r.get('close'))} for r in data['vix'] if D.us_available(r['date'])<=cut}
        s.fx=sorted([r for r in data['fx'] if stamp(r['publication_bound'])<=cut],key=lambda r:r['date'])
        s.fx_times=[stamp(r['publication_bound']) for r in s.fx]
        s.us_dates=D.us_sessions('2017-01-01',decision);s.us_times=[D.us_available(d) for d in s.us_dates]
        from bisect import bisect_right
        window=s.us_dates[bisect_right(s.us_times,at(anchor,13,30)):bisect_right(s.us_times,cut)]
        base=s.us_dates[bisect_right(s.us_times,at(anchor,13,30))-1];latest=s.us_dates[bisect_right(s.us_times,cut)-1]
        pairs=[n for n in s.nights.get(decision,[]) if (anchor,n['contract'],'一般') in s.futures and float(n['volume'])>0]
        if not pairs: raise ValueError('缺少已完成夜盤與同合約日盤')
        n=min(pairs,key=lambda r:r['contract']);ns=at(anchor,15);ne=ns+timedelta(hours=14)
        m={'decision_date':decision,'anchor_date':anchor,'decision_at':cut.isoformat(),'night_contract':n['contract'],'night_start_assumed':ns.isoformat(),
           'night_end_bound':ne.isoformat(),'night_same_contract_prior_regular_date':anchor,'latest_us_source_date':latest,'latest_us_close_bound':D.us_close(latest).isoformat(),
           'latest_us_available_assumed':D.us_available(latest).isoformat(),'us_anchor_reference_date':base,'overseas_window_dates':'|'.join(window),
           'fx_date':s.fx[-1]['date'],'us_sessions_after_night_end':sum(D.us_close(u)>ne for u in window),'us_sessions_since_cash_anchor':len(window)}
        f,ledger=s.feature(m)
        return f,ledger,sessions[j:j+3]

    def predictions(self,f):
        M=self.models;out={}
        x=M.np.array([[float(f[k]) for k in M.FEATURES]])
        for h in (1,2,3):
            out[str(h)]={}
            for name,n in [('M0',0),('M1',4),('M2',10)]:
                model=self.params[f'H{h}:{name}']
                values=M.initial_probability(model,None if n==0 else M.scale(x,model['scaler'])[:,:n])
                p=float(values['p_up'][0]);out[str(h)][name]={'up':p,'down':1-p,**M.display_percentages(p),'parameter_sha256':model['parameter_sha256']}
        return out

    def mature(self,data,evidence,now):
        cash={r['date']:r for r in data['cash'] if at(r['date'],13,30)<=now}
        calendar=data['calendar']['sessions'];done={(e['payload']['root'],e['payload']['horizon']) for e in self.events if e['kind']=='outcome'}
        for e in list(self.events):
            if e['kind']!='forecast':continue
            root=e['payload'];anchor=root['feature']['anchor_date']
            # Reconcile only from the actual newly archived calendar vintage.
            updated=[d for d in calendar if d>anchor][:3]
            if len(updated)!=3: raise ValueError('新日曆未涵蓋原事件三個到期日')
            for h in (1,2,3):
                key=(root['decision_date'],h)
                if key in done:continue
                old=self.target(root,h)
                if updated[h-1]!=old:
                    self.append('calendar_change',{'root':root['decision_date'],'horizon':h,'original_target':root['targets'][h-1], 'from':old,'to':updated[h-1], 'calendar_sha256':evidence['calendar']['sha256']},now)
                target=updated[h-1]
                if target not in cash:continue
                close=float(cash[target]['close']);anchor_price=root['feature']['anchor_close']
                if not math.isfinite(close) or close<=0:raise ValueError('到期收盤無效')
                self.append('outcome',{'root':root['decision_date'],'horizon':h,'target_date':target,'close':close,'up':int(close>=anchor_price),
                    'available_at':at(target,13,30).isoformat(),'cash_sha256':evidence['cash']['sha256']},now)

    def target(self,root,h):
        changes=[e['payload']['to'] for e in self.events if e['kind']=='calendar_change' and e['payload']['root']==root['decision_date'] and e['payload']['horizon']==h]
        return changes[-1] if changes else root['targets'][h-1]

    def update(self,bundle_path=None,now=None):
        now=now or clock()
        with self.transaction():
            if not self.events:raise ValueError('請先開始觀察')
            try:
                if bundle_path is None:raise ValueError('缺少本次已查核來源包；未產生條件機率')
                bundle,data,evidence=self.capture(bundle_path,now)
                capture=self.append('capture',{'decision_date':bundle['decision_date'],'sources':evidence,'calendar':data['calendar']['sessions']},now)
                self.mature(data,evidence,now)
                decision=bundle['decision_date'];cut=at(decision,8,30)
                if now.date().isoformat()!=decision or not cut<=now<at(decision,8,45):
                    raise ValueError('不在當日08:30～08:44:59保存時段；只保留來源／已成熟結果，不倒填盤前預報')
                if any(e['kind']=='forecast' and e['payload']['decision_date']==decision for e in self.events):
                    self.append('duplicate',{'decision_date':decision,'note':'保留第一份合格機率，不重複計樣本'},now)
                else:
                    f,ledger,targets=self.feature(decision,data,evidence)
                    self.append('forecast',{'decision_date':decision,'information_cutoff':cut.isoformat(),'feature':f,'feature_ledger':ledger,
                        'targets':targets,'models':self.predictions(f),'source_capture_sequence':capture['sequence'],'qualified':True,'role':'prospective_parallel'},now)
                self.append('source_status',{'withheld':False,'note':'本次盤前資料完整'},now)
            except (ValueError,KeyError,TypeError,IndexError,OSError) as ex:
                self.append('source_status',{'withheld':True,'note':str(ex)[:350]},now)
            self.monitor(now)
        return self.report()

    def pairs(self,h,after=None):
        roots={e['payload']['decision_date']:e for e in self.events if e['kind']=='forecast'};result=[]
        for e in self.events:
            if e['kind']!='outcome' or e['payload']['horizon']!=h:continue
            p=e['payload'];root=roots[p['root']]
            if after and stamp(root['saved_at'])<=after:continue
            result.append({'root':p['root'],'y':p['up'],'models':root['payload']['models'][str(h)]})
        return sorted(result,key=lambda r:r['root'])

    @staticmethod
    def metrics(pairs):
        out={}
        for name in ('M0','M1','M2'):
            p=[r['models'][name]['up'] for r in pairs];y=[r['y'] for r in pairs];n=len(p)
            out[name]={'n':n,'brier':sum((a-b)**2 for a,b in zip(p,y))/n if n else None,
                'log_loss':-sum(b*math.log(a)+(1-b)*math.log1p(-a) for a,b in zip(p,y))/n if n else None}
        return out

    def eligibility(self,h):
        pairs=self.pairs(h);m=self.metrics(pairs);n=len(pairs);up=sum(r['y'] for r in pairs)
        calendars=[e for e in self.events if e['kind']=='capture'];now=stamp(self.events[-1]['saved_at']);start=stamp(self.events[0]['saved_at'])
        sessions=calendars[-1]['payload']['calendar'] if calendars else []
        elapsed=sum(start<at(d,13,30)<=now for d in sessions)
        susp=[e for e in self.events if e['kind']=='suspend' and e['payload']['horizon']==h]
        new=self.pairs(h,stamp(susp[-1]['saved_at'])) if susp else pairs
        evaluation=new if susp else pairs;em=self.metrics(evaluation);en=len(evaluation);eu=sum(r['y'] for r in evaluation)
        score_ok=en>0 and em['M1']['brier']<=.99*em['M0']['brier'] and em['M1']['log_loss']<=em['M0']['log_loss']
        gates={'elapsed60':elapsed>=60,'matured60':en>=60,'eachClass20':min(eu,en-eu)>=20,'brierAndLogLoss':score_ok,'historicalEligible':True}
        return {'horizon':h,'matured':n,'up':up,'down':n-up,'elapsedSessions':elapsed,'newSinceSuspension':len(new) if susp else None,
            'evaluationN':en,'metrics':m,'rolling60':self.metrics(pairs[-60:]),'gates':gates,'eligible':all(gates.values())}

    def active(self,h):
        events=[e for e in self.events if e['kind'] in ('activate','suspend') and e['payload']['horizon']==h]
        return bool(events and events[-1]['kind']=='activate')

    def monitor(self,now):
        for h in (1,2,3):
            if not self.active(h):continue
            pairs=self.pairs(h)[-60:];m=self.metrics(pairs)
            if len(pairs)==60 and m['M1']['brier']>m['M0']['brier']:
                self.append('suspend',{'horizon':h,'reason':'rolling60 Brier worse than same-date M0','fallback':'M0','rolling60':m},now)

    def activate(self,h,now=None):
        now=now or clock()
        with self.transaction():
            if not self.events:raise ValueError('尚未開始觀察')
            if self.active(h):raise ValueError('此期限已啟用')
            status=self.report()
            if status['withheld']:raise ValueError('當前來源不足，不可啟用')
            gate=self.eligibility(h)
            if not gate['eligible']:raise ValueError('未達固定接入條件：'+json.dumps(gate['gates']))
            self.append('activate',{'horizon':h,'model':'M1','gate':gate,'note':'僅啟用條件預報，不改動交易策略'},now)
        return self.report()

    def report(self):
        self.read()
        if not self.events:return {'version':VERSION,'status':'not_started','withheld':True,'horizons':[],'active':False}
        statuses=[e for e in self.events if e['kind']=='source_status'];last=statuses[-1]['payload'] if statuses else {'withheld':True,'note':'等待第一份合格盤前資料'}
        forecasts=[e for e in self.events if e['kind']=='forecast']
        return {'version':VERSION,'status':'parallel','startedAt':self.events[0]['saved_at'],'updatedAt':self.events[-1]['saved_at'],
            'withheld':last['withheld'],'note':last['note'],'active':any(self.active(h) for h in (1,2,3)),
            'horizons':[{**self.eligibility(h),'active':self.active(h)} for h in (1,2,3)],
            'lastForecast':{**forecasts[-1]['payload'],'currentTargets':[self.target(forecasts[-1]['payload'],h) for h in (1,2,3)]} if forecasts else None,'lastForecastSavedAt':forecasts[-1]['saved_at'] if forecasts else None,
            'revisionStatus':'blocked_original_M2_not_eligible','releaseSha256':digest((self.config/'release.json').read_bytes()),
            'eventCount':len(self.events),'ledgerHeadSha256':digest(encode(self.events[-1])),
            'formalTradingChanged':False,'readOnly':True}

def report():
    try:return Engine().report()
    except (OSError,ValueError,KeyError,TypeError) as e:return {'version':VERSION,'status':'integrity_failed','withheld':True,'active':False,'horizons':[],'note':str(e)}

def export_status(value):
    """Only called after explicit CLI/chat mutation; never from HTTP/report."""
    for root in (db.ROOT/'frontend/public',db.ROOT/'dist'):
        path=root/'v6-research/prospective-status.json';path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_bytes(encode(value));temp.replace(path)

def manual_update(bundle_path=None):
    if not STORE.exists() or not CONFIG.exists():return None
    engine=Engine();result=engine.update(bundle_path);export_status(result);return result

def main():
    parser=argparse.ArgumentParser(description='對話授權的V6條件預報觀察；無自動排程')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('enroll');sub.add_parser('status')
    p=sub.add_parser('update');p.add_argument('--bundle',type=Path)
    p=sub.add_parser('activate');p.add_argument('--horizon',type=int,choices=[1,2,3],required=True)
    args=parser.parse_args()
    try:e=Engine()
    except (OSError,ValueError,KeyError,TypeError):
        value=report()
        if args.action!='status':export_status(value)
        print(json.dumps(value,ensure_ascii=False,indent=2));raise SystemExit(1)
    if args.action=='enroll':value=e.enroll()
    elif args.action=='update':value=e.update(args.bundle)
    elif args.action=='activate':value=e.activate(args.horizon)
    else:value=e.report()
    if args.action!='status':export_status(value)
    print(json.dumps(value,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
