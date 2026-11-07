"""Immutable 1/2/3-session empirical forecasts. Only chat publication creates rows.
GET/report groups saved rows; it never fetches, calculates or grades outcomes.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,sqlite3,math
import numpy as np
import pandas as pd
from . import db,v6_shared_distribution as D,v6_remaining as R,v6_forecast
METHOD='v6-daily-deadline-baseline-1.0.0'
VERSION='v6-daily-deadline-1'
CONFIG=D.CONFIG/'daily-release.json'
TZ=ZoneInfo('Asia/Taipei')
KEYS=('up','flat','down')

def release():
    cfg=json.loads(CONFIG.read_text())
    if cfg.get('status')!='ready' or cfg.get('method_version')!=METHOD:raise ValueError('逐日期限方法尚未啟用')
    if D.sha(D.CONFIG/'daily-protocol.json')!=cfg['protocol_sha256']:raise ValueError('逐日期限協議版本不符')
    return cfg

def calculate(official,asof,remaining,anchor):
    if isinstance(remaining,bool) or remaining not in (1,2,3):raise ValueError('剩餘期限須為1／2／3交易日')
    f=official.loc[:asof].copy();D._check_prices(f)
    if str(f.index[-1])!=asof or not math.isfinite(anchor) or anchor<=0:raise ValueError('起點或更新日無效')
    idx=np.arange(252,len(f)-remaining)
    if len(idx)<1260:raise ValueError('成熟歷史樣本不足1260筆')
    c=f.close.to_numpy(float);ends=c[-1]*(c[idx+remaining]/c[idx])
    q=np.quantile(ends,[.025,.1,.25,.5,.75,.9,.975],method='inverted_cdf')
    return {'status':'reference','market_as_of':asof,'current_close':float(c[-1]),'anchor':float(anchor),
      'remaining_sessions':remaining,'flat_bounds':[anchor*.995,anchor*1.005],
      'realized_return_pct':float((c[-1]/anchor-1)*100),'classes':R._classes(ends,anchor),
      'sample_n':len(idx),'sample_first':str(f.index[idx[0]]),'sample_last':str(f.index[idx[-1]]),
      'last_outcome':str(f.index[idx[-1]+remaining]),'median_level':float(q[3]),
      'intervals':[{'central_pct':pct,'low_level':float(a),'high_level':float(b)} for pct,a,b in [(50,q[2],q[4]),(80,q[1],q[5]),(95,q[0],q[6])]],
      'note':'長期等權歷史基準；只反映最新價位與剩餘天數，未加入方向訊號。'}

def calendar_snapshot(information_cutoff,*,data_dir=None,observed_at=None):
    """Save actual cached notice vintage. No calendar fetch, schema init, or writes."""
    data_dir=data_dir or db.DATA;cut=pd.Timestamp(information_cutoff)
    if cut.tzinfo is None:raise ValueError('日曆資訊截點缺少時區')
    path=data_dir/'research.sqlite3'
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as con:
        rows=con.execute('SELECT year,payload FROM bt_calendars').fetchall()
    years={}
    for year,text in rows:
        cal=json.loads(text);saved=pd.Timestamp(cal.get('saved_at'))
        if pd.isna(saved) or saved.tzinfo is None or saved>cut:continue
        years[str(year)]=cal
    if not years:raise ValueError('未有資訊截點以前保存的官方日曆')
    encoded=json.dumps(years,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return {'years':years,'sha256':hashlib.sha256(encoded.encode()).hexdigest(),
      'observed_at':str(observed_at or information_cutoff),'information_cutoff':str(information_cutoff),
      'note':'saved_at為本機取得日曆時間；未保存的原始公告時刻不推定為已知。'}

def sessions(origin,count,calendar):
    result=[];day=pd.Timestamp(origin)
    for _ in range(count*10+60):
        day+=pd.Timedelta(days=1);name=day.strftime('%Y-%m-%d');cal=calendar['years'].get(str(day.year))
        if not cal:raise ValueError('官方日曆尚未核對此年份')
        if name in cal.get('opened',[]) or (day.weekday()<5 and name not in cal.get('closed',[])):result.append(name)
        if len(result)==count:return result
    raise ValueError('官方交易日不足')

def _metadata(shared,cfg):
    names=['market_as_of','anchor','issued_at','information_cutoff','timing_qualified','timing_note',
      'official_price_sha256','official_manifest_sha256','source_manifest_sha256','source_checks']
    return {**{k:shared.get(k) for k in names},'version':VERSION,'method_version':METHOD,
      'protocol_sha256':cfg['protocol_sha256'],'targets':[],
      'note':'T+1／T+2／T+3為各到期日相對原起點的累積分類，不是逐日路徑或交易勝率。'}

def freeze(shared,*,data_dir=None):
    empty={'version':VERSION,'method_version':METHOD,'status':'unavailable','targets':[],
      'note':'等待對話更新保存逐日期限預報；舊紀錄不回填。'}
    try:
        cfg=release()
        if shared.get('status')!='reference':return {**empty,'note':shared.get('note') or '本次來源不足，不產生新百分比。'}
        stamp=pd.Timestamp(shared.get('issued_at'))
        if pd.isna(stamp) or stamp<pd.Timestamp(cfg['activated_at']):return empty
        info=_metadata(shared,cfg);asof=shared['market_as_of'];anchor=float(shared['anchor'])
        if asof>stamp.tz_convert(TZ).date().isoformat() or (asof==stamp.tz_convert(TZ).date().isoformat() and stamp.tz_convert(TZ).strftime('%H:%M')<'13:30'):raise ValueError('收盤尚未完成')
        official=D.prices(data_dir);cal=calendar_snapshot(shared['information_cutoff'],data_dir=data_dir,observed_at=shared['issued_at'])
        dates=sessions(asof,3,cal)
        if dates!=shared['forecast_dates']:raise ValueError('逐日期限與共用預報日曆不一致')
        if stamp>=pd.Timestamp(dates[0]+'T08:45:00+08:00'):raise ValueError('下一交易日已開盤，不回填預報')
        targets=[]
        for h in (1,2,3):
            value=calculate(official,asof,h,anchor)
            if h==3:
                old={c['key']:c for c in shared['classes']}
                if any(abs(c['probability']-old[c['key']]['probability'])>1e-13 for c in value['classes']):raise ValueError('T+3未能重現共用T1')
            targets.append({**value,'horizon':h,'elapsed_sessions':0,'original_target_date':dates[h-1],
              'target_date':dates[h-1],'issued_at':shared['issued_at'],'stage':'initial','change_previous_pp':None,
              'change_initial_pp':None,'schedule_changes':[]})
        return {**info,'status':'reference','calendar':cal,'targets':targets}
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        return {**empty,'note':'逐日期限暫不可用：'+str(error)}

def _eligible(row):
    value=row.get('daily_forecasts') or {}
    return bool(row.get('id') and value.get('status')=='reference' and value.get('version')==VERSION
      and value.get('method_version')==METHOD and {x.get('horizon') for x in value.get('targets',[])}=={1,2,3})

def _delta(current,previous):
    a={c['key']:c['probability_pct'] for c in current};b={c['key']:c['probability_pct'] for c in previous}
    return {k:round(a[k]-b[k],1) for k in KEYS}

def _schedule(root,current_dates,calendar,stamp,h):
    initial=root['daily_forecasts'];old=next(t for t in initial['targets'] if t['horizon']==h)['original_target_date']
    new=current_dates[h-1]
    if old==new:return []
    origin=initial['market_as_of'];prior=initial['calendar']['years'];changes=[]
    for year,cal in calendar['years'].items():
        base=prior.get(year,{});removed=set(cal.get('closed',[]))-set(base.get('closed',[]))
        removed.update(set(base.get('opened',[]))-set(cal.get('opened',[])))
        reopened=set(cal.get('opened',[]))-set(base.get('opened',[]))
        reopened.update(set(base.get('closed',[]))-set(cal.get('closed',[])))
        notices=sorted(d for d in removed|reopened if origin<d<=max(old,new))
        if notices:changes.append({'from_date':old,'to_date':new,'calendar_changed_dates':notices,
          'calendar_saved_at':cal.get('saved_at'),'observed_at':calendar['observed_at'],
          'source':cal.get('source'),'calendar_sha256':calendar['sha256'],'recorded_at':stamp,
          'note':'按本次已取得官方日曆順延／更正；原定日期保留，未推定原公告時刻。'})
    if not changes:raise ValueError('到期日異動缺少官方日曆更動依據')
    return changes

def prepare(snapshot,store,*,data_dir=None):
    """Append initial/revision/outcome rows to the NEW snapshot, never mutate a root."""
    current=snapshot.get('daily_forecasts')
    if not current:return []
    cfg=release();stamp=pd.Timestamp(snapshot['prepared_at'])
    if stamp<pd.Timestamp(cfg['activated_at']):return []
    rows=R._read(store,snapshot['version'],stamp);asof=snapshot['market_as_of']
    roots=[(r,h) for r,h in rows if _eligible(r) and r['market_as_of']<=asof]
    if not roots:return []
    done={(u['root_id'],u['horizon']) for r,_ in rows for u in r.get('daily_updates',[]) if u.get('method_version')==METHOD and u.get('status')=='complete'}
    error=None
    try:
        if asof>stamp.tz_convert(TZ).date().isoformat() or (asof==stamp.tz_convert(TZ).date().isoformat() and stamp.tz_convert(TZ).strftime('%H:%M')<'13:30'):raise ValueError('本次收盤尚未完成')
        official=D.prices(data_dir).loc[:asof];D._check_prices(official)
        cache=(data_dir or db.DATA)/'v6_distribution_prices'
        vintage=cache/'prices.csv' if (cache/'manifest.json').exists() else D.CONFIG/D.release()['base_file']
        price_hash=D.sha(vintage);manifest_hash=D.sha(cache/'manifest.json') if (cache/'manifest.json').exists() else None
        if str(official.index[-1])!=asof:raise ValueError('官方更新日尚未齊全')
        cutoff=min(stamp,pd.Timestamp(v6_forecast.session_dates(asof,1,data_dir=data_dir)[0]+'T08:30:00+08:00'))
        cal=calendar_snapshot(cutoff,data_dir=data_dir,observed_at=stamp.isoformat())
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,IndexError) as e:error=str(e);official=None;cal=None
    updates=[]
    for root,digest in roots:
        initial=root['daily_forecasts'];origin=initial['market_as_of'];anchor=float(initial['anchor'])
        prior=[u for row,_ in rows for u in row.get('daily_updates',[]) if u.get('root_id')==root['id'] and u.get('method_version')==METHOD]
        for initial_target in initial['targets']:
            h=initial_target['horizon']
            if (root['id'],h) in done:continue
            if origin==asof and (error or not cal or cal['sha256']==initial['calendar']['sha256']):continue
            meta={'version':VERSION,'method_version':METHOD,'root_id':root['id'],'root_snapshot_sha256':digest,
              'source_snapshot_id':snapshot['id'],'issued_at':snapshot['prepared_at'],'market_as_of':asof,
              'original_market_as_of':origin,'horizon':h,'anchor':anchor,'classes':[],
              'original_target_date':initial_target['original_target_date'],'target_date':initial_target['target_date'],
              'remaining_sessions':None,'elapsed_sessions':None,'schedule_changes':[],
              'timing_qualified':bool(current.get('timing_qualified')),'information_cutoff':current.get('information_cutoff'),
              'official_price_sha256':price_hash if not error else None,'official_manifest_sha256':manifest_hash if not error else None,
              'source_manifest_sha256':current.get('source_manifest_sha256'),'protocol_sha256':cfg['protocol_sha256']}
            try:
                if error:raise ValueError(error)
                if origin not in official.index or abs(float(official.at[origin,'close'])-anchor)>.02:raise ValueError('原起點與官方價格未對齊')
                dates=sessions(origin,3,cal);expected=[d for d in dates[:h] if d<=asof]
                actual=list(official.loc[official.index>origin].index[:h])
                if actual!=expected:raise ValueError('官方日曆與已保存行情不一致，不由缺價推定休市')
                elapsed=len(expected);remaining=h-elapsed
                shifts=_schedule(root,dates,cal,snapshot['prepared_at'],h)
                if elapsed==0 and not shifts:continue
                meta.update(target_date=dates[h-1],remaining_sessions=remaining,elapsed_sessions=elapsed,
                  schedule_changes=shifts,calendar_sha256=cal['sha256'],calendar=cal)
                old_refs=[u for u in prior if u['horizon']==h and u.get('status')=='reference']
                previous=old_refs[-1] if old_refs else initial_target
                meta.update(previous_issued_at=previous['issued_at'],previous_market_as_of=previous['market_as_of'])
                if remaining==0:
                    target=dates[h-1];cp=float(official.at[target,'close']);actual_class=R.category(cp,anchor)
                    initial_classes=initial_target['classes'];initial_p={x['key']:x['probability'] for x in initial_classes}
                    stages={}
                    for u in old_refs:
                        if u.get('timing_qualified'):stages.setdefault(u['elapsed_sessions'],u)
                    scores=[{'elapsed_sessions':e,'remaining_sessions':u['remaining_sessions'],'issued_at':u['issued_at'],
                      'rps':R.rps(u['classes'],actual_class)} for e,u in sorted(stages.items()) if e>0]
                    updates.append({**meta,'status':'complete','stage':'outcome','verified_on':asof,
                      'actual_class':actual_class,'actual_close':cp,'actual_return_pct':(cp/anchor-1)*100,
                      'original_rps':R.rps(initial_classes,actual_class),
                      'original_brier':sum((initial_p[k]-int(k==actual_class))**2 for k in KEYS),
                      'revision_scores':scores,'note':'按原交易日期限核對；日曆若有更正，保留原定日期及取得依據。不以較晚核對日價格替換。'})
                else:
                    if current.get('status')!='reference':raise ValueError(current.get('note') or '本次來源不足，不發布新比例')
                    value=calculate(official,asof,remaining,anchor)
                    updates.append({**meta,**value,'stage':'calendar_revision' if elapsed==0 else 'revision',
                      'change_previous_pp':_delta(value['classes'],previous['classes']),
                      'change_initial_pp':_delta(value['classes'],initial_target['classes'])})
            except (OSError,ValueError,KeyError,TypeError,IndexError) as e:
                updates.append({**meta,'status':'unavailable','stage':'missing','note':str(e)})
    return updates

def report(selected,rows):
    """Group only previously saved rows, cut to selected publication time."""
    empty={'method_version':METHOD,'groups':[],'summary':{'completed_origins':0,'completed_targets':0,
      'prospective_origins':0,'by_horizon':[]},'note':'尚無逐日期限紀錄；只在對話更新後保存，不回填舊預報。'}
    if not selected:return empty
    cutoff=pd.Timestamp(selected['prepared_at']);visible=sorted([r for r in rows if pd.Timestamp(r['prepared_at'])<=cutoff],key=lambda r:(r['prepared_at'],r['id']))
    roots={r['id']:r for r in visible if _eligible(r)};all_updates=[]
    for row in visible:
        for u in row.get('daily_updates',[]):
            root=roots.get(u.get('root_id'))
            if root and u.get('method_version')==METHOD and u.get('source_snapshot_id')==row['id'] and pd.Timestamp(u['issued_at'])<=cutoff:all_updates.append(u)
    # Canonical root per origin: first08:30-qualified; otherwise first saved reference.
    canonical={}
    for root in roots.values():
        origin=root['market_as_of'];old=canonical.get(origin)
        if old is None or (not old['daily_forecasts'].get('timing_qualified') and root['daily_forecasts'].get('timing_qualified')):canonical[origin]=root
    groups=[];outcomes=[];qualified_outcomes=[]
    for origin,root in sorted(canonical.items(),reverse=True):
        initial=root['daily_forecasts'];targets=[]
        for t in initial['targets']:
            timeline=[u for u in all_updates if u['root_id']==root['id'] and u['horizon']==t['horizon']]
            latest=timeline[-1] if timeline else t
            refs=[u for u in timeline if u['status']=='reference'];last_reference=refs[-1] if refs else t
            completed=next((u for u in timeline if u['status']=='complete'),None)
            if completed:
                outcomes.append((root,completed))
                if initial.get('timing_qualified'):qualified_outcomes.append((root,completed))
            targets.append({'horizon':t['horizon'],'initial':t,'updates':timeline,'latest':latest,'last_reference':last_reference})
        groups.append({'root_id':root['id'],'origin':origin,'anchor':initial['anchor'],'issued_at':root['prepared_at'],
          'timing_qualified':bool(initial.get('timing_qualified')),'timing_note':initial.get('timing_note'),
          'flat_bounds':[initial['anchor']*.995,initial['anchor']*1.005],'targets':targets,
          'alternative_saved_versions':sum(r['market_as_of']==origin for r in roots.values())-1})
    stats=[]
    for h in (1,2,3):
        complete=[u for _,u in outcomes if u['horizon']==h];q=[u for _,u in qualified_outcomes if u['horizon']==h]
        revisions=[]
        for remaining in range(1,h):
            a=[s['rps'] for u in q for s in u.get('revision_scores',[]) if s['remaining_sessions']==remaining]
            revisions.append({'remaining_sessions':remaining,'n':len(a),'mean_rps':float(np.mean(a)) if a else None})
        stats.append({'horizon':h,'completed':len(complete),'prospective_qualified':len(q),
          'original_mean_rps':float(np.mean([u['original_rps'] for u in q])) if q else None,'revision_scores':revisions})
    note=(selected.get('daily_error') or (selected.get('daily_forecasts') or {}).get('note') or empty['note'])
    return {**empty,'groups':groups[:10],'summary':{'completed_origins':len({r['market_as_of'] for r,_ in outcomes}),
      'completed_targets':len(outcomes),'prospective_origins':len({r['market_as_of'] for r,_ in qualified_outcomes}),
      'by_horizon':stats},'note':note}
