"""Remaining-horizon historical references, saved ONLY with chat-published snapshots."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json
import numpy as np
import pandas as pd
from . import v6_shared_distribution as D
METHOD='v6-remaining-baseline-1.0.0'
VERSION='v6-remaining-1'
CONFIG=D.CONFIG/'remaining-release.json'
KEYS=('up','flat','down')
LABELS=('上漲','平盤','下跌')
TZ=ZoneInfo('Asia/Taipei')

def release():
    r=json.loads(CONFIG.read_text())
    if r['method_version']!=METHOD or r.get('status')!='ready':raise ValueError('剩餘期間方法版本不符')
    if D.sha(D.CONFIG/'remaining-protocol.json')!=r['protocol_sha256']:raise ValueError('剩餘期間協議雜湊不符')
    return r

def rps(classes,actual):
    """Mean of the two ordered cumulative squared errors; down,flat,up."""
    p={c['key']:c['probability'] for c in classes}
    return float(((p['down']-int(actual=='down'))**2+(p['down']+p['flat']-int(actual!='up'))**2)/2)

def category(level,anchor):
    return 'up' if level>anchor*1.005 else 'down' if level<anchor*.995 else 'flat'

def _classes(endpoints,anchor):
    c=np.array([(endpoints>anchor*1.005).sum(),((endpoints>=anchor*.995)&(endpoints<=anchor*1.005)).sum(),(endpoints<anchor*.995).sum()],int)
    p=c/len(endpoints);tenths=np.floor(p*1000).astype(int)
    for j in np.argsort(-(p*1000-tenths),kind='stable')[:int(1000-tenths.sum())]:tenths[j]+=1
    return [{'key':k,'label':LABELS[i],'count':int(c[i]),'probability':float(p[i]),'probability_pct':float(tenths[i]/10)} for i,k in enumerate(KEYS)]

def calculate(official,root,asof):
    """No path matching: remaining unconditional empirical returns, shifted by observed close.

    The original anchor, +/-0.5% thresholds and third-session deadline stay fixed.
    This is a NEW descriptive baseline, not package6's accepted three-day T1 score.
    """
    f=official.loc[:asof].copy();D._check_prices(f)
    s=root['shared_distribution'];origin=s['market_as_of'];days=s['forecast_dates'];anchor=float(s['anchor'])
    if len(days)!=3 or len(set(days))!=3 or days!=sorted(days) or origin>=days[0]:raise ValueError('原預報三個到期交易日無效')
    if origin not in f.index or str(f.index[-1])!=asof:raise ValueError('官方起點或更新日缺漏')
    if abs(float(f.at[origin,'close'])-anchor)>.02:raise ValueError('原預報錨點與官方價格未對齊')
    actual_dates=list(f.loc[f.index>origin].index[:3])
    expected=[d for d in days if d<=asof]
    if actual_dates!=expected:raise ValueError('原到期日與實際交易日不一致，暫停更新')
    elapsed=len(expected)
    if elapsed not in (1,2,3):raise ValueError('尚未經過一個完整交易日')
    current=float(f.close.iloc[-1]);observed_low=float(f.loc[expected,'low'].min());seen=observed_low<=anchor*.98
    common={'status':'reference','remaining_sessions':3-elapsed,'elapsed_sessions':elapsed,'market_as_of':asof,'current_close':current,'original_market_as_of':origin,'anchor':anchor,'forecast_dates':days,'target_date':days[-1],'flat_bounds':[anchor*.995,anchor*1.005],'realized_return_pct':(current/anchor-1)*100,'observed_low':observed_low,'observed_drawdown_pct':(observed_low/anchor-1)*100}
    if elapsed==3:
        actual=category(current,anchor)
        return {**common,'status':'complete','classes':[],'actual_class':actual,'actual_return_pct':(current/anchor-1)*100,'event':{'status':'observed' if seen else 'not_observed','occurred':bool(seen),'label':'回落2%已發生' if seen else '到期未觸及回落2%','probability':None},'original_rps':rps(s['classes'],actual),'original_event_brier':(float(s['event_reference']['probability'])-int(seen))**2 if s.get('event_reference') else None,'note':'已到原第三日收盤；這是實際結果核對，不再發布剩餘期間機率。'}
    m=3-elapsed;idx=np.arange(252,len(f)-m)
    if len(idx)<1260:raise ValueError('剩餘期間成熟歷史樣本不足1260筆')
    close=f.close.to_numpy(float);low=f.low.to_numpy(float)
    ret=close[idx+m]/close[idx]-1;ends=current*(1+ret)
    minret=np.minimum.reduce([low[idx+k]/close[idx]-1 for k in range(1,m+1)])
    hits=current*(1+minret)<=anchor*.98
    q=np.quantile(ends,[.025,.1,.25,.5,.75,.9,.975],method='inverted_cdf')
    return {**common,'classes':_classes(ends,anchor),'sample_n':len(idx),'sample_first':str(f.index[idx[0]]),'sample_last':str(f.index[idx[-1]]),'last_outcome':asof,'median_level':float(q[3]),'intervals':[{'central_pct':pct,'low_level':float(a),'high_level':float(b)} for pct,a,b in [(50,q[2],q[4]),(80,q[1],q[5]),(95,q[0],q[6])]],'event':{'status':'observed' if seen else 'reference','occurred':bool(seen),'label':'回落2%已發生' if seen else '截至原到期日仍可能觸及回落2%','probability':None if seen else float((hits.sum()+.5)/(len(idx)+1)),'count':None if seen else int(hits.sum()),'sample_n':len(idx)},'note':'依已發生價位與剩餘天數更新歷史基準；沒有估計前段漲跌與後段的依存關係，未證明比原預報更準確。'}

def _eligible(row):
    s=row.get('shared_distribution') or {}
    return bool(row.get('id') and s.get('status')=='reference' and s.get('version')==D.VERSION and s.get('method_version')==D.METHOD)

def _read(store,version,cutoff):
    rows=[]
    for p in Path(store).glob('*.json'):
        try:
            b=p.read_bytes();r=json.loads(b)
            if r.get('version')==version and r.get('id')==p.stem and pd.Timestamp(r['prepared_at'])<=cutoff:
                rows.append((r,hashlib.sha256(b).hexdigest()))
        except (OSError,ValueError,KeyError,TypeError):continue
    return sorted(rows,key=lambda x:(x[0]['prepared_at'],x[0]['id']))

def prepare(snapshot,store,*,data_dir=None):
    """Return revisions embedded in the NEW atomic comparison snapshot. Never write old rows."""
    current=snapshot.get('shared_distribution')
    if not current:return []
    cfg=release();stamp=pd.Timestamp(snapshot['prepared_at'])
    if stamp<pd.Timestamp(cfg['activated_at']):return []
    asof=snapshot['market_as_of'];rows=_read(store,snapshot['version'],stamp)
    completed={u['root_id'] for r,_ in rows for u in r.get('remaining_updates',[]) if u.get('status')=='complete' and u.get('method_version')==METHOD}
    candidates=[(r,h) for r,h in rows if _eligible(r) and r['market_as_of']<asof and r['id'] not in completed]
    if not candidates:return []
    try:
        official=D.prices(data_dir)
        if asof not in official.index:raise ValueError('官方更新日尚未齊全')
        if asof>stamp.tz_convert(TZ).date().isoformat() or (asof==stamp.tz_convert(TZ).date().isoformat() and stamp.tz_convert(TZ).strftime('%H:%M')<'13:30'):raise ValueError('本次收盤尚未完成')
        cache=(data_dir or D.db.DATA)/'v6_distribution_prices';vintage=cache/'prices.csv' if (cache/'manifest.json').exists() else D.CONFIG/D.release()['base_file']
        provenance={'official_price_sha256':D.sha(vintage),'official_manifest_sha256':D.sha(cache/'manifest.json') if (cache/'manifest.json').exists() else None,'source_manifest_sha256':current.get('source_manifest_sha256'),'protocol_sha256':cfg['protocol_sha256']}
        error=None
    except (OSError,ValueError,KeyError,TypeError) as e:official=None;provenance={};error=str(e)
    updates=[]
    for root,h in candidates:
        s=root['shared_distribution'];days=s['forecast_dates'];elapsed=sum(d<=asof for d in days)
        base={'version':VERSION,'method_version':METHOD,'root_id':root['id'],'root_snapshot_sha256':h,'source_snapshot_id':snapshot['id'],'issued_at':snapshot['prepared_at'],'market_as_of':asof,'original_market_as_of':s['market_as_of'],'anchor':s['anchor'],'target_date':days[-1],'forecast_dates':days,'remaining_sessions':max(0,3-elapsed),'classes':[],'timing_qualified':bool(current.get('timing_qualified')),'information_cutoff':current.get('information_cutoff'),**provenance}
        try:
            if error:raise ValueError(error)
            # Once expired, verify at the original deadline, NOT at today's price.
            calc_asof=min(asof,days[-1])
            value=calculate(official,root,calc_asof)
            if value['status']!='complete' and current.get('status')!='reference':raise ValueError(current.get('note','本次來源不足，暫不發布剩餘機率'))
            if value['status']=='complete':
                prior={}
                for saved,_ in rows:
                    for u in saved.get('remaining_updates',[]):
                        if u.get('root_id')==root['id'] and u.get('status')=='reference' and u.get('method_version')==METHOD:prior[u['remaining_sessions']]=u
                value['revision_scores']=[{'remaining_sessions':m,'issued_at':u['issued_at'],'timing_qualified':bool(u.get('timing_qualified')),'rps':rps(u['classes'],value['actual_class'])} for m,u in sorted(prior.items(),reverse=True)]
            updates.append({**base,**value,'verified_on':asof if value['status']=='complete' else None})
        except (OSError,ValueError,KeyError,TypeError,IndexError) as e:
            updates.append({**base,'status':'unavailable','note':str(e)})
    return updates

def report(selected,rows):
    """Read saved updates only, cut to the selected publication. No price fetch or calculation."""
    empty={'method_version':METHOD,'groups':[],'summary':{'completed':0,'prospective_qualified':0,'original_mean_rps':None,'remaining_scores':[]},'note':'尚無新方法的剩餘期間紀錄。下次對話更新V6時，才會保存；舊相似日預報不回填。'}
    if not selected:return empty
    if selected.get('remaining_error'):empty['note']=selected['remaining_error']
    cutoff=pd.Timestamp(selected['prepared_at'])
    visible=sorted([r for r in rows if pd.Timestamp(r['prepared_at'])<=cutoff],key=lambda r:(r['prepared_at'],r['id']))
    roots={r['id']:r for r in visible if _eligible(r)}
    all_updates=[]
    for r in visible:
        for u in r.get('remaining_updates',[]):
            root=roots.get(u.get('root_id'))
            if root and u.get('source_snapshot_id')==r['id'] and u.get('method_version')==METHOD and pd.Timestamp(u['issued_at'])<=cutoff:
                all_updates.append(u)
    finalized={}
    for u in all_updates:
        if u['status']=='complete' and u['root_id'] not in finalized:finalized[u['root_id']]=u
    unique_completed={}
    unique_qualified={}
    for k,u in sorted(finalized.items(),key=lambda item:(roots[item[0]]['prepared_at'],item[0])):
        key=(roots[k]['market_as_of'],u['target_date'])
        unique_completed.setdefault(key,u)
        if roots[k]['shared_distribution'].get('timing_qualified'):unique_qualified.setdefault(key,u)
    qualified=list(unique_qualified.values())
    focus={u['root_id'] for u in selected.get('remaining_updates',[]) if u.get('root_id') in roots}
    focus.update(list(finalized)[-5:])
    rolling_scores=[]
    for m in (2,1):
        values=[z['rps'] for u in qualified for z in u.get('revision_scores',[]) if z['remaining_sessions']==m and z.get('timing_qualified')]
        rolling_scores.append({'remaining_sessions':m,'n':len(values),'mean_rps':float(np.mean(values)) if values else None})
    groups=[]
    for k in sorted(focus,key=lambda k:(roots[k]['market_as_of'],roots[k]['prepared_at']),reverse=True):
        root=roots[k];s=root['shared_distribution'];timeline=[u for u in all_updates if u['root_id']==k]
        groups.append({'root_id':k,'original_market_as_of':s['market_as_of'],'original_issued_at':root['prepared_at'],'anchor':s['anchor'],'target_date':s['forecast_dates'][-1],'original_classes':s['classes'],'original_event_probability':(s.get('event_reference') or {}).get('probability'),'updates':timeline,'latest':timeline[-1]})
    return {**empty,'groups':groups,'summary':{'completed':len(unique_completed),'completed_records':len(finalized),'prospective_qualified':len(qualified),'original_mean_rps':float(np.mean([u['original_rps'] for u in qualified])) if qualified else None,'remaining_scores':rolling_scores},'note':selected.get('remaining_error') or (empty['note'] if not groups else '固定原起點及到期日；剩餘期間只用新保存的歷史基準。同一起點日只計首筆合格預報；不同日起點仍有窗口重疊，不代表獨立試驗或策略勝率。')}
