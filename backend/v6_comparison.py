"""Two frozen V6 strategies and causal three-session analog scenarios.

Only the chat-led CLI builds/publishes. HTTP reads never fetch, fit or trade.
Scores are pressure indices; scenario frequencies use a separate close target.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,sqlite3,uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from . import db,v6_engine as E,v6_features as F,v6_tx,v6_forecast
from .v6_scenario_views import scenario_views,market_view
from .v6_source_availability import verified_positions
from . import v6_shared_distribution as shared_distribution
from . import v6_remaining,v6_trade_policy,v6_daily_forecast,v6_daily_direction,v6_short_simulation

TZ=ZoneInfo('Asia/Taipei')
ROOT=Path(__file__).resolve().parents[1]
ARTIFACT=ROOT/'research/v6_crossasset_20261031/models.json'
SOURCES=db.DATA/'v6_compare_sources'
STORE=db.DATA/'v6_comparison'
VERSION='v6-dual-crossasset-scenarios-20261031'
FLAT=.005
K=80
MIN_SAMPLES=40
LABELS={'up':'上漲','flat':'平盤','down':'下跌'}

def clean(x):return F.clean(x)

def pressure_score(p,cut):
    """Fixed log-odds shift; cut maps to65, preserves all entry decisions."""
    p=np.clip(np.asarray(p,float),1e-12,1-1e-12)
    v=np.log(p/(1-p))-math.log(cut/(1-cut))+math.log(.65/.35)
    return 100/(1+np.exp(-v))

def model_predict(frame,model):
    x=frame[model['features']].to_numpy(float);m=model['model']
    z=np.clip((x-np.array(m['median']))/np.array(m['sd']),-5,5)
    return pd.Series(1/(1+np.exp(-np.clip(np.c_[np.ones(len(z)),z]@np.array(m['beta']),-50,50))),index=frame.index)

def raw_input(data_dir=None):
    """Read-only snapshot of existing cash/futures bars; no schema migrations."""
    data_dir=data_dir or db.DATA;raw=v6_tx._seed_input();last=max(raw['index'])
    quotes={(r['date'],r['contract']):{k:float(r[k]) for k in ('open','high','low','close')} for r in raw['futures']}
    with sqlite3.connect(f'file:{data_dir/"monitor.sqlite3"}?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        for r in c.execute('SELECT date,contract,open,high,low,close FROM v9_tx_quotes WHERE date>? ORDER BY date,contract',(last,)):
            if all(v is not None and math.isfinite(v) and v>0 for v in r[2:]):quotes[(r[0],r[1])]=dict(zip(('open','high','low','close'),map(float,r[2:])))
        for r in c.execute("SELECT date,open,high,low,close FROM prices WHERE symbol='TAIEX' AND date>? ORDER BY date",(last,)):
            if all(v is not None and math.isfinite(v) and v>0 for v in r[1:]):raw['index'][r[0]]=dict(zip(('open','high','low','close'),map(float,r[1:])))
    raw['futures']=[{'date':d,'contract':k,**v} for (d,k),v in quotes.items()]
    return raw,quotes

def yahoo(path):
    x=json.loads(path.read_text())['chart']['result'][0]
    dates=pd.to_datetime(x['timestamp'],unit='s',utc=True).tz_convert(x['meta'].get('exchangeTimezoneName','America/New_York')).strftime('%Y-%m-%d')
    values=x['indicators'].get('adjclose',[{}])[0].get('adjclose',x['indicators']['quote'][0]['close'])
    return pd.DataFrame({'date':dates,'value':values}).dropna().drop_duplicates('date').set_index('date').sort_index()

def build_frame(raw,sources=None):
    sources=sources or SOURCES
    with v6_tx._FEATURE_LOCK:
        previous=F.RAW
        try:F.RAW=sources;f=F.build(raw)
        finally:F.RAW=previous
    f['legacy_raw'],f['legacy_valid']=v6_tx._score(f)
    f['idx_r1']=f.close.pct_change();f['idx_r3']=f.close.pct_change(3);f['idx_gap5']=f.close/f.ma5-1
    f['idx_clv']=(f.close-f.low)/(f.high-f.low).replace(0,np.nan)
    peers=[]
    for symbol in ('2330','2317','2454','2308'):
        z=yahoo(sources/('stock'+symbol+'.json')).value.reindex(f.index)
        f[symbol+'_gap5']=z/z.rolling(5).mean()-1;f[symbol+'_r3']=z.pct_change(3,fill_method=None)
        if symbol!='2330':peers.append(f[symbol+'_gap5'])
    f['tsmc_gap5']=f['2330_gap5'];f['tsmc_r3']=f['2330_r3'];a=pd.concat(peers,axis=1)
    f['peer_weak']=(a<0).mean(axis=1).where(a.notna().all(axis=1))
    arr=[]
    for path in sorted(sources.glob('positions-TXF-*.csv')):
        z=pd.read_csv(path,encoding='cp950',index_col=False);z=z[z.iloc[:,2].str.contains('外資')]
        arr.append(pd.DataFrame({'date':pd.to_datetime(z.iloc[:,0]).dt.strftime('%Y-%m-%d'),'long':z.iloc[:,9].astype(float),'short':z.iloc[:,11].astype(float)}))
    pos=pd.concat(arr).drop_duplicates('date',keep='last').set_index('date').sort_index().reindex(f.index)
    f['foreign_delta5_share']=(pos.short-pos.long).diff(5)/(pos.short+pos.long)
    f['capital_fresh']=pos.notna().all(axis=1)&pos.long.rolling(6).count().eq(6)
    de=pd.DatetimeIndex(pd.to_datetime(f.decision_at,utc=True)).tz_convert('Asia/Taipei')
    for key in ('spy','tlt','gold','dollar','sox'):
        z=yahoo(sources/(key+'.json'));z['r1']=z.value.pct_change();z['r5']=z.value.pct_change(5)
        z['available']=pd.to_datetime(z.index).tz_localize('Asia/Taipei')+pd.Timedelta(days=1,hours=8)
        for k,v in F.align(z[['available','value','r1','r5']],de,key).items():f[k]=v
    f['global_fresh']=True
    for key in ('sox','tsm','spx','twd','spy','tlt','gold','dollar'):
        av=pd.to_datetime(f[key+'_available'],utc=True);decision=pd.to_datetime(f.decision_at,utc=True)
        f['global_fresh']&=av.notna()&(av<=decision)&((decision-av)<=pd.Timedelta(days=4))
    f['retail_fresh']=pd.to_datetime(f.retail_available,utc=True).dt.tz_convert('Asia/Taipei').dt.strftime('%Y-%m-%d').eq(f.index)
    f['co_weak']=f.spy_r5.clip(upper=0)*f.tlt_r5.clip(upper=0)
    f['triple_weak']=((f.spy_r5<0)&(f.tlt_r5<0)&(f.gold_r5<0)).astype(float)
    model=json.loads(ARTIFACT.read_text())['capital_regime']
    # Preserve the research's common availability universe, including overseas/
    # retail controls; a missing source never silently becomes low risk.
    needed=model['features']+['sox_r1','tsm_r1','spx_r1','retail_value','retail_change5']
    f['cross_valid']=f[needed].notna().all(axis=1)&f.capital_fresh&f.global_fresh&f.retail_fresh
    f['cross_raw']=model_predict(f,model).where(f.cross_valid)
    f['legacy_score']=f.legacy_raw*100
    f['cross_score']=pressure_score(f.cross_raw,model['fixed_threshold'])
    return f

def scenario_class(value):return 'up' if value>FLAT+1e-12 else 'down' if value<-FLAT-1e-12 else 'flat'

def scenarios(frame,score_col,valid_col,at=None):
    """Nearest80 historical *matured* score analogs from post-fit2025 onward.

    Every analog's whole3day path ends by the forecast's cash date. Future
    prices and incomplete outcomes cannot affect probabilities or paths.
    """
    f=frame.loc[:at] if at else frame;day=str(f.index[-1]);score=f[score_col].iloc[-1]
    empty={'status':'missing','sample_n':0,'definition':'第3個交易日收盤相對起點；上漲>0.5%、平盤±0.5%、下跌>0.5%','flat_pct':.5,'horizon':3,'as_of':day,'classes':[],'note':'資料不足，不將風險分數換成三種機率。'}
    if not f[valid_col].iloc[-1] or not np.isfinite(score):return empty
    candidates=[]
    for i in range(len(f)-3):
        d=str(f.index[i]);end=str(f.index[i+3])
        if d<'2025-01-01' or not bool(f[valid_col].iloc[i]) or not np.isfinite(f[score_col].iloc[i]):continue
        path=(f.close.iloc[i:i+4].to_numpy(float)/float(f.close.iloc[i])-1)
        if not np.isfinite(path).all():continue
        candidates.append({'date':d,'end':end,'distance':abs(float(f[score_col].iloc[i])-float(score)),'score':float(f[score_col].iloc[i]),'path':path,'class':scenario_class(float(path[-1]))})
    chosen=sorted(candidates,key=lambda r:(r['distance'],r['date']))[:K];n=len(chosen)
    if n<MIN_SAMPLES:return {**empty,'sample_n':n,'note':f'完整歷史相似樣本只有{n}筆，至少需40筆才顯示三種參考機率。'}
    counts=[sum(r['class']==k for r in chosen) for k in LABELS]
    # Dirichlet(1,1,1) smoothing. Largest remainder displays sum exactly100.0.
    p=(np.array(counts)+1)/(n+3);tenths=np.floor(p*1000).astype(int)
    for i in np.argsort(-(p*1000-tenths),kind='stable')[:int(1000-tenths.sum())]:tenths[i]+=1
    classes=[];anchor=float(f.close.iloc[-1])
    for j,k in enumerate(LABELS):
        matched=[r for r in chosen if r['class']==k]
        paths=np.array([r['path'] for r in matched]) if matched else None
        med=np.median(paths,axis=0) if matched else None
        classes.append({'key':k,'label':LABELS[k],'count':counts[j],'probability_pct':float(tenths[j]/10),'range95_pct':E.wilson(counts[j],n),
                        'path':([{'step':i,'return_pct':round(float(v)*100,3),'level':round(anchor*(1+float(v)),2)} for i,v in enumerate(med)] if matched else []),
                        'endpoint_range':([round(anchor*(1+float(v)),2) for v in np.quantile(paths[:,-1],[.1,.9])] if matched else None)})
    baseline_counts=[sum(r['class']==k for r in candidates) for k in LABELS]
    baseline=[{'key':k,'label':LABELS[k],'count':baseline_counts[j],'probability_pct':round((baseline_counts[j]+1)/(len(candidates)+3)*100,1)} for j,k in enumerate(LABELS)]
    return {'status':'reference','as_of':day,'horizon':3,'flat_pct':.5,'definition':empty['definition'],'sample_n':n,'eligible_n':len(candidates),'classes':classes,'baseline':baseline,
            'sample_first':min(r['date'] for r in chosen),'sample_last':max(r['date'] for r in chosen),'last_outcome':max(r['end'] for r in chosen),
            'score_range':[round(min(r['score'] for r in chosen),1),round(max(r['score'] for r in chosen),1)],
            'analog_dates':[r['date'] for r in chosen],
            'note':'已完成3日結果中，取分數最接近的最多80筆（至少40筆）；以各類次數加1平滑。歷史窗口重疊，未驗證為校準機率；路徑為各類歷史中位走勢，非保證價位。'}

def scenario_validation(f,score_col,valid_col):
    """Causal replay, fixed analog rule, compare to same-history class prior."""
    rows=[]
    for i in range(len(f)-3):
        day=str(f.index[i])
        if day<'2025-01-01':continue
        r=scenarios(f,score_col,valid_col,at=day)
        if r['status']!='reference':continue
        truth=scenario_class(float(f.close.iloc[i+3]/f.close.iloc[i]-1));ys=np.array([int(k==truth) for k in LABELS]);ps=np.array([a['probability_pct']/100 for a in r['classes']])
        historic=[]
        for j in range(i-2):
            if str(f.index[j])>='2025-01-01' and bool(f[valid_col].iloc[j]):historic.append(scenario_class(float(f.close.iloc[j+3]/f.close.iloc[j]-1)))
        prior=np.array([(historic.count(k)+1)/(len(historic)+3) for k in LABELS])
        rows.append({'date':day,'target_end':str(f.index[i+3]),'brier':float(np.sum((ps-ys)**2)),'baseline_brier':float(np.sum((prior-ys)**2)),'class':truth})
    def stats(part):return {'n':len(part),'brier':float(np.mean([r['brier'] for r in part])) if part else None,'baseline_brier':float(np.mean([r['baseline_brier'] for r in part])) if part else None}
    return {'status':'retrospective_reference','all':stats(rows),'2025':stats([r for r in rows if r['date']<'2026-01-01']),'2026':stats([r for r in rows if r['date']>='2026-01-01']),
            'note':'逐日只用當時已完成的樣本重播；多分類Brier越低越好，基準為當時已知的三類比例。方法仍待真正未來紀錄驗證。','rows':rows}

def latest_sources(f,now,kind,verified_sources=None):
    keys=['vix','feargreed','twd','oil','spx','financial','electronics','tsm','rates-proxy','foreign','retail'] if kind=='legacy' else ['foreign','retail','twd','spx','tsm','sox','spy','tlt','gold','dollar']
    now=pd.Timestamp(now);rows=[];missing=[]
    for key in keys:
        assumed=pd.Timestamp(f[key+'_available'].iloc[-1]);av=assumed;proof=None
        if key in ('foreign','retail') and pd.notna(assumed):
            candidate=(verified_sources or {}).get(key)
            if candidate and candidate['as_of']==str(f.index[-1])==assumed.date().isoformat():
                actual=pd.Timestamp(candidate['available_at'])
                if pd.notna(actual) and actual.tzinfo is not None and actual<=now and now-actual<=pd.Timedelta(days=4):
                    av=actual;proof=candidate
        state='missing' if pd.isna(av) else 'pending' if av>now else 'stale' if now-av>pd.Timedelta(days=4) else 'ready'
        valid=state=='ready'
        if not valid:missing.append(key)
        observed=assumed if key in ('foreign','retail','financial','electronics') else assumed-pd.Timedelta(days=1)
        row={'key':key,'as_of':observed.date().isoformat() if pd.notna(observed) else None,'available_at':av.isoformat() if pd.notna(av) else None,'fresh':valid,'state':state,'availability_basis':'verified_fetch' if proof else 'assumed_schedule'}
        if proof:row.update(assumed_available_at=assumed.isoformat(),verified_files=proof['files'])
        rows.append(row)
    return rows,missing

def cooldown_details(last_exit,as_of,remaining,data_dir=None,*,total_sessions=5):
    dates=v6_forecast.session_dates(as_of,remaining+1,data_dir=data_dir or db.DATA) if remaining else []
    return {'last_exit':last_exit,'total_sessions':total_sessions,'remaining':remaining,'blocked_dates':dates[:remaining] if dates else [],
            'earliest_review':dates[-1] if dates else None,
            'note':(('出場後不額外冷卻，最快下一交易日重新檢查；出場當日不重複開倉。' if total_sessions==0 else f'每次模擬空單出場後，接下來{total_sessions}個台股交易日暫停建立新空單；不含出場當日，也不計休市。')+'風險評分和預警照常更新，冷卻不是風險降低。仍須符合分數、回檔與開盤跳空條件，不代表一定進場。')}

def freeze(f,quotes,*,now=None,data_dir=None,validation=None,verified_sources=None):
    stamp=pd.Timestamp(now or datetime.now(TZ));last=str(f.index[-1]);days=v6_forecast.session_dates(last,3,data_dir=data_dir or db.DATA)
    if not days:raise ValueError('缺少交易所日曆，不能指定後3日')
    next_open=pd.Timestamp(days[0]+'T08:45:00+08:00')
    if stamp>=next_open:raise ValueError('下一交易日已開盤，請補齊最新完整資料後重新研究，不倒填盤前預報')
    if validation is None:
        try:
            saved=json.loads((ARTIFACT.parent/'scenario-validation.json').read_text())
            if saved['data_through']<=last:validation=saved['strategies']
        except (OSError,ValueError,KeyError):pass
    cutoff=json.loads(ARTIFACT.read_text())['capital_regime']['fixed_threshold'];strategies=[]
    for key,label,scorecol,rawcol,validcol in [('legacy','原版65分策略','legacy_score','legacy_raw','legacy_valid'),('cross','V6-2｜價格＋外資＋跨資產','cross_score','cross_raw','cross_valid')]:
        rows,missing=latest_sources(f,stamp,key,verified_sources);pending=bool(missing) and all(r['state']=='pending' for r in rows if not r['fresh']);available=bool(f[validcol].iloc[-1]) and not missing
        work=f.copy();work['prob_signal']=(work[rawcol]>=(.65 if key=='legacy' else cutoff))&work[validcol]&(work.dd<.08)
        cooldown_days=v6_trade_policy.COOLDOWN_DAYS[key]
        E.CONTRACT_CACHE.clear();s=E.sim(work,quotes,{'family':'probability','level':0,'take':1.5,'stop':1.5,'hold':3,'cooldown_days':cooldown_days},'2025-01-01',last)
        if key=='cross':primary_simulation=v6_short_simulation.freeze(f,quotes,now=stamp,simulation=s)
        last_exit=s['trades'][-1]['exit_date'] if s['trades'] else None
        cool=max(0,cooldown_days-(list(f.index).index(last)-list(f.index).index(last_exit))) if last_exit else 0
        alert=bool(f[rawcol].iloc[-1]>=(.65 if key=='legacy' else cutoff)) if available else None
        scenario={'status':'retired','classes':[],'sample_n':0,'note':'新快照改用共用T1收盤分布，策略分數不再另算相似80日機率。'} if available else {'status':'pending' if pending else 'missing','classes':[],'sample_n':0,'note':'等待來源達可用時間，暫不顯示機率。' if pending else '最新來源不足，暫不顯示機率。'}
        views=scenario_views(f.iloc[-1])
        for cls in scenario['classes']:cls['view']=views[cls['key']]
        cooldown=cooldown_details(last_exit,last,cool,data_dir,total_sessions=cooldown_days)
        score_basis=('原版用固定3日空單扣成本後是否獲利作為訓練標籤；模型輸出不是已驗證的獲利機率。' if key=='legacy' else
                     'V6-2用3日內最低價是否曾比起點收盤回落至少2%作為訓練標籤；不是第3日收盤必跌2%。')
        assessment=(('等待來源達可用時間，暫不判讀。' if pending else '必要資料不足，暫不判讀。') if not available else
                    '這套策略已達65分觀察線；但風險評分與第三日收盤劇本目標不同，期間先跌、最後反彈收紅可能同時發生。' if alert else
                    '這套策略未達65分觀察線，只表示原進場條件未觸發，不能據此判定沒有下跌風險。')
        action=('等待來源可用' if pending else '資料不足') if not available else '模擬持有空單' if s['open_position'] else f'冷卻中，尚餘{cool}個交易日' if cool else '回落達8%，暫不追空' if f.dd.iloc[-1]>=.08 else '達觀察線，下一開盤檢查跳空條件' if alert else '空手觀察'
        strategies.append({'key':key,'label':label,'score':round(float(f[scorecol].iloc[-1]),2) if available else None,'threshold':65,
          'score_change':round(float(f[scorecol].iloc[-1]-f[scorecol].iloc[-2]),2) if available and np.isfinite(f[scorecol].iloc[-2]) else None,
          'raw_output':float(f[rawcol].iloc[-1]) if available else None,'raw_threshold':.65 if key=='legacy' else cutoff,'alert':alert,'action':action,'cooldown_sessions':cool,'cooldown':cooldown,'score_basis':score_basis,'assessment':assessment,'missing':missing,'sources':rows,'scenario':scenario,
          'backtest':{'cooldown_days':cooldown_days,'start':'2025-01-01','end':last,'completed':s['completed'],'wins':s['wins'],'win_rate_pct':s['win_rate'],'net_points':s['points'],'max_drawdown_pct':s['max_drawdown_pct'],'trades':s['trades'],'curve':s['curve'],'open_position':s['open_position']},
          'scenario_validation':({k:v for k,v in validation[key].items() if k!='rows'} if validation and key in validation else None),
          'factors':(['情緒、海外、匯率與技術條件，沿用原版係數'] if key=='legacy' else [f'股債金近5日同跌：{"是" if f.triple_weak.iloc[-1] else "否"}',f'美元指數近5日：{f.dollar_r5.iloc[-1]*100:+.2f}%',f'外資期貨淨空占比：{f.foreign_value.iloc[-1]*100:.1f}%'])})
    return clean({'short_simulation':primary_simulation,'unconditional_forecast_retired':True,'trade_policy':{'version':v6_trade_policy.VERSION,'cooldown_days':dict(v6_trade_policy.COOLDOWN_DAYS),'note':v6_trade_policy.NOTE},'version':VERSION,'prepared_at':stamp.isoformat(),'market_as_of':last,'status':'complete' if all(s['score'] is not None for s in strategies) else 'partial',
      'forecast_dates':days,'anchor':float(f.close.iloc[-1]),'market_view':market_view(f.iloc[-1]),'history':[{'date':str(d),'close':float(r.close)} for d,r in f.tail(20).iterrows()],
      'flat_bounds':[float(f.close.iloc[-1])*(1-FLAT),float(f.close.iloc[-1])*(1+FLAT)],'strategies':strategies,
      'method':'無條件歷史比例已停止計算與顯示；條件機率另按盤前資格保存。風險分數及事件目標獨立呈現，不由65分換算機率。',
      'execution_note':'兩策略獨立回測，非實際成交。固定1口台指期、1.5ATR停利停損、最多3日；原V6冷卻0日、V6-2冷卻2日，出場當日不重複開倉；盤前警報與能否開倉分開。新策略前瞻紀錄由本次保存開始，未改寫舊預報。'})

def publish(snapshot,store=None):
    target=store or STORE;target.mkdir(parents=True,exist_ok=True)
    stamp=datetime.fromisoformat(snapshot['prepared_at']);name=stamp.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    payload={**snapshot,'id':name}
    # Retire unconditional odds and revisions, including old origins. Preserve
    # their immutable files, but never generate replacement baseline forecasts.
    payload['remaining_updates']=[]
    payload['daily_updates']=[]
    path=target/(name+'.json');temp=target/('.'+name+'.tmp')
    with temp.open('x') as h:json.dump(payload,h,ensure_ascii=False,allow_nan=False);h.flush();os.fsync(h.fileno())
    temp.replace(path);return payload

def publish_current(*,live_assessment=None):
    raw,q=raw_input();frame=build_frame(raw)
    if live_assessment is None:
        from . import v6_live_assessment
        live_assessment=v6_live_assessment.collect(str(frame.index[-1]))
    stamp=datetime.now(TZ)
    proof=verified_positions(SOURCES,stamp,str(frame.index[-1]))
    snapshot=freeze(frame,q,now=stamp,verified_sources=proof)
    if isinstance(live_assessment,dict):snapshot['live_assessment']=live_assessment
    from . import v6_cross_history
    snapshot['retrospective']=v6_cross_history.save(frame,generated_at=snapshot['prepared_at'])
    return publish(snapshot)

def audit(snapshot,frame):
    if not snapshot:return None
    end=snapshot['forecast_dates'][-1]
    if end not in frame.index:return {'status':'pending','target_date':end}
    anchor=(snapshot.get('shared_distribution') or {}).get('anchor',snapshot['anchor'])
    value=float(frame.at[end,'close']);r=value/float(anchor)-1
    return {'status':'complete','target_date':end,'actual_class':scenario_class(r),'return_pct':round(r*100,3),'close':value}

def report(report_id=None,*,store=None,before=None):
    target=store or STORE;rows=[]
    for path in sorted(target.glob('*.json'),reverse=True):
        try:
            r=json.loads(path.read_text())
            if r.get('version')!=VERSION or r.get('id')!=path.stem:continue
            if before and pd.Timestamp(r['prepared_at'])>pd.Timestamp(before):continue
            rows.append(r)
        except (OSError,ValueError,TypeError):continue
    selected=next((r for r in rows if r['id']==report_id),None) if report_id else (rows[0] if rows else None)
    try:outcome=audit(selected,v6_forecast.index_frame()) if selected else None
    except (OSError,ValueError,sqlite3.Error):outcome=None
    retrospective=(selected or {}).get('retrospective')
    if retrospective and pd.Timestamp(retrospective['generated_at'])>pd.Timestamp(selected['prepared_at']):retrospective=None
    history_cutoff=before or (selected['prepared_at'] if report_id and selected else None)
    try:
        r=json.loads((target.parent/'v6_comparison_history.json').read_text())
        if retrospective is None and r.get('kind')=='retrospective' and r.get('strategy_key')=='v6-2' and isinstance(r.get('points'),list) and (not history_cutoff or pd.Timestamp(r['generated_at'])<=pd.Timestamp(history_cutoff)):
            retrospective=r
    except (OSError,ValueError,TypeError,KeyError):pass
    return {'unconditional_forecast_retired':True,'snapshot':selected,'audit':outcome,'retrospective':retrospective,'history':[{'id':r['id'],'prepared_at':r['prepared_at'],'market_as_of':r['market_as_of']} for r in rows[:100]]}

def main():
    parser=argparse.ArgumentParser(description='Chat-led V6 dual-strategy publication; no HTTP update entry')
    parser.add_argument('--publish',action='store_true');parser.add_argument('--validate',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args();raw,q=raw_input();f=build_frame(raw)
    validation={key:scenario_validation(f,key+'_score',key+'_valid') for key in ('legacy','cross')} if args.validate else None
    live=None
    if args.publish:
        from . import v6_live_assessment
        live=v6_live_assessment.collect(str(f.index[-1]))
    stamp=datetime.now(TZ)
    proof=verified_positions(SOURCES,stamp,str(f.index[-1])) if args.publish else None
    snap=freeze(f,q,now=stamp,validation=validation,verified_sources=proof)
    if live is not None:snap['live_assessment']=live
    if args.output:args.output.write_text(json.dumps(snap,ensure_ascii=False,allow_nan=False,indent=2))
    if args.publish:
        from . import v6_cross_history
        snap['retrospective']=v6_cross_history.save(f,generated_at=snap['prepared_at'])
        snap=publish(snap)
    print(json.dumps({k:v for k,v in snap.items() if k not in ('history','strategies','retrospective')},ensure_ascii=False))

if __name__=='__main__':main()
