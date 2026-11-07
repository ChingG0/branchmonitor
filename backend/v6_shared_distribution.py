"""Chat-published shared T1/E1 reference. Reads never fetch, fit, or migrate forecasts."""
from pathlib import Path
from datetime import datetime,date,timedelta
from zoneinfo import ZoneInfo
import argparse,hashlib,json,math,shutil,tempfile
import numpy as np
import pandas as pd
from . import db
ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config/v6_distribution'
METHOD='v6-distribution-protocol-1.0.0'
VERSION='v6-shared-distribution-1'
TZ=ZoneInfo('Asia/Taipei')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def release():
    x=json.loads((CONFIG/'release.json').read_text())
    if x['method_version']!=METHOD or x['selected']!={'terminal':'T1','event':'E1'}:
        raise ValueError('共用分布方法尚未完成資格核對')
    return x

def method_status():
    try:
        x=release()
        return {k:v for k,v in x.items() if k not in ('base_file','base_sha256')}
    except (OSError,ValueError,KeyError):
        return {'status':'unavailable','method_version':METHOD,'note':'共用分布方法檔案缺漏，暫不顯示新機率。'}

def prices(data_dir=None):
    x=release();base=CONFIG/x['base_file']
    if sha(base)!=x['base_sha256']:raise ValueError('官方基底資料雜湊不符')
    frame=pd.read_csv(base,index_col='date',float_precision='round_trip')[['open','high','low','close']]
    cache=(data_dir or db.DATA)/'v6_distribution_prices'
    if (cache/'manifest.json').exists():
        m=json.loads((cache/'manifest.json').read_text())
        if m['base_sha256']!=x['base_sha256'] or sha(cache/'prices.csv')!=m['sha256']:
            raise ValueError('官方新增行情版本未通過核對')
        updated=pd.read_csv(cache/'prices.csv',index_col='date',float_precision='round_trip')[frame.columns]
        old=updated.reindex(frame.index)
        if old.isna().any().any() or not np.allclose(old,frame,rtol=0,atol=1e-10):raise ValueError('新增行情改寫了凍結基底')
        frame=updated
    _check_prices(frame)
    return frame

def _check_prices(f):
    if f.empty or not f.index.is_unique or list(f.index)!=sorted(f.index):raise ValueError('官方價格日期缺漏或重複')
    a=f[['open','high','low','close']].to_numpy(float)
    if not np.isfinite(a).all() or (a<=0).any() or (f.high<f[['open','close','low']].max(axis=1)).any() or (f.low>f[['open','close','high']].min(axis=1)).any():
        raise ValueError('官方開高低收不完整')

def refresh_prices(as_of,data_dir=None):
    """Explicit CLI/source-refresh path only. Append official months, never revise old rows."""
    import requests
    target=(data_dir or db.DATA)/'v6_distribution_prices';old=prices(data_dir);last=str(old.index[-1]);cut=date.fromisoformat(as_of)
    if as_of<=last:return {'status':'ready','as_of':as_of,'cached_through':last,'added':0}
    if as_of>datetime.now(TZ).date().isoformat():raise ValueError('不能抓取未來收盤')
    target.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='v6-official-stage-',dir=target.parent))
    try:
        if target.exists():shutil.copytree(target,stage,dirs_exist_ok=True)
        month=date.fromisoformat(last).replace(day=1);new=old.copy();sources=[]
        while month<=cut:
            url='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'
            response=requests.get(url,params={'date':month.strftime('%Y%m01'),'response':'json'},timeout=(10,40));response.raise_for_status();r=response.json()
            if not str(r.get('stat','')).startswith('OK') or not r.get('data'):raise ValueError('官方當月OHLC尚未公布')
            rows={}
            for row in r['data']:
                y,m,d=map(int,row[0].split('/'));day=f'{y+1911:04d}-{m:02d}-{d:02d}'
                if day>as_of:continue
                rows[day]=dict(zip(['open','high','low','close'],[float(str(v).replace(',','')) for v in row[1:5]]))
            incoming=pd.DataFrame.from_dict(rows,orient='index').sort_index();_check_prices(incoming)
            shared=incoming.index.intersection(new.index)
            if len(shared) and not np.allclose(incoming.loc[shared,new.columns],new.loc[shared],rtol=0,atol=1e-10):
                raise ValueError('官方舊行情有修訂，需另存版本，不覆寫原基底')
            new=pd.concat([new,incoming.loc[incoming.index>str(new.index[-1])]]).sort_index();_check_prices(new)
            name=month.strftime('%Y%m')+'.json';(stage/name).write_bytes(response.content)
            sources.append({'url':response.url,'sha256':sha(stage/name),'observed_at':datetime.now(TZ).isoformat()})
            month=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
        if str(new.index[-1])!=as_of:raise ValueError('官方最新日尚未到本次收盤日')
        new.index.name='date';new.to_csv(stage/'prices.csv',float_format='%.17g')
        (stage/'manifest.json').write_text(json.dumps({'as_of':as_of,'base_sha256':release()['base_sha256'],'sha256':sha(stage/'prices.csv'),'observed_at':datetime.now(TZ).isoformat(),'sources':sources},ensure_ascii=False,indent=2))
        backup=target.with_name(target.name+'.previous')
        if backup.exists():raise ValueError('前次官方行情備份仍存在，請先核對')
        if target.exists():target.rename(backup)
        try:stage.rename(target)
        except Exception:
            if backup.exists():backup.rename(target)
            raise
        if backup.exists():shutil.rmtree(backup)
        return {'status':'ready','as_of':as_of,'added':len(new)-len(old)}
    finally:
        if stage.exists():shutil.rmtree(stage)

def _us_sessions(cutoff,count=7):
    cal=json.loads((CONFIG/'us-calendar.json').read_text());found=[];day=cutoff.date()
    for _ in range(60):
        year=str(day.year)
        if year not in cal['closed']:raise ValueError('美股官方交易日曆尚未核對此年份')
        available=pd.Timestamp((day+timedelta(days=1)).isoformat()+'T08:00:00+08:00')
        if day.weekday()<5 and day.isoformat() not in cal['closed'][year] and available<=cutoff:found.append(day.isoformat())
        if len(found)>=count:return list(reversed(found))
        day-=timedelta(days=1)
    raise ValueError('美股參考交易日不足')

def source_gate(frame,now,official,sources,verified=None):
    """Current source-specific calendars, not the old uniform4-calendar-day rule."""
    row=frame.iloc[-1];asof=str(frame.index[-1]);missing=[];checks=[]
    model=json.loads((ROOT/'research/v6_crossasset_20261031/models.json').read_text())['capital_regime']
    required=model['features']+['sox_r1','tsm_r1','spx_r1','retail_value','retail_change5']
    if any(k not in row or not np.isfinite(float(row[k])) for k in required) or not bool(row.get('capital_fresh',False)):
        missing.append('特徵或外資連續部位不足')
    us=_us_sessions(now);latest,prior=us[-1],us[-2]
    from .v6_comparison import yahoo
    for key in ('foreign','retail','spy','tlt','gold','sox','spx','tsm','twd','dollar'):
        av=pd.Timestamp(row.get(key+'_available'));obs=None;ok=False
        if key in ('foreign','retail'):
            proof=(verified or {}).get(key)
            if proof and proof.get('as_of')==asof:
                actual=pd.Timestamp(proof['available_at'])
                if actual.tzinfo and actual<=now:av=actual
            assumed=pd.Timestamp(row.get(key+'_available'));obs=assumed.date().isoformat() if pd.notna(assumed) else None
            ok=pd.notna(av) and av<=now and obs==asof
        else:
            obs=(av-pd.Timedelta(days=1)).date().isoformat() if pd.notna(av) else None
            dates=set(yahoo(sources/(key+'.json')).index)
            ok=pd.notna(av) and av<=now and obs in dates and obs>=(prior if key in ('twd','dollar') else latest)
            if key not in ('twd','dollar'):ok=ok and set(us[-6:])<=dates
            else:ok=ok and len([d for d in dates if d<=obs])>=6
        checks.append({'key':key,'as_of':obs,'available_at':av.isoformat() if pd.notna(av) else None,'ready':bool(ok)})
        if not ok:missing.append(key)
    need=set(official.loc[:asof].index[-5:])
    for key in ('stock2330','stock2317','stock2454','stock2308'):
        if not need<=set(yahoo(sources/(key+'.json')).index):missing.append(key)
    return checks,missing

def calculate(official,asof,anchor):
    """Exact package4 T1/E1 kernel. Later prices are excluded before label construction."""
    f=official.loc[:asof].copy();_check_prices(f)
    if str(f.index[-1])!=asof or abs(float(f.close.iloc[-1])-anchor)>.02:raise ValueError('官方行情與保存錨點未對齊')
    n=len(f);idx=np.arange(252,n-3)
    if len(idx)<1260:raise ValueError('成熟歷史樣本不足1260筆')
    close=f.close.to_numpy(float);low=f.low.to_numpy(float)
    ret=close[idx+3]/close[idx]-1;D=np.array([low[i+1:i+4].min()/close[i]-1 for i in idx])
    keys=['up','flat','down'];counts=[int((ret>.005).sum()),int(((ret>=-.005)&(ret<=.005)).sum()),int((ret<-.005).sum())]
    raw=np.array(counts)/len(idx);tenths=np.floor(raw*1000).astype(int)
    for j in np.argsort(-(raw*1000-tenths),kind='stable')[:int(1000-tenths.sum())]:tenths[j]+=1
    q=np.quantile(ret,[.025,.10,.25,.5,.75,.90,.975],method='inverted_cdf')
    return {'status':'reference','sample_n':len(idx),'sample_first':str(f.index[idx[0]]),'sample_last':str(f.index[idx[-1]]),'last_outcome':asof,
      'classes':[{'key':k,'label':['上漲','平盤','下跌'][j],'count':counts[j],'probability':float(raw[j]),'probability_pct':float(tenths[j]/10)} for j,k in enumerate(keys)],
      'intervals':[{'central_pct':pct,'low_return_pct':float(a*100),'high_return_pct':float(b*100),'low_level':float(anchor*(1+a)),'high_level':float(anchor*(1+b))} for pct,a,b in [(50,q[2],q[4]),(80,q[1],q[5]),(95,q[0],q[6])]],
      'median_level':float(anchor*(1+q[3])),
      'event_reference':{'model':'E1','event':'3日內最低價較起點回落至少2%','count':int((D<=-.02).sum()),'sample_n':len(idx),'probability':float(((D<=-.02).sum()+.5)/(len(idx)+1)),'method':'共同歷史基準；(事件數+0.5)/(樣本數+1)，不是V6-2分數換算。'}}

def freeze(frame,days,stamp,*,data_dir=None,sources=None,verified=None):
    empty={'version':VERSION,'method_version':METHOD,'status':'unavailable','classes':[],'note':'共用分布資料尚未齊全，不以缺值當安全。'}
    try:
        r=release();now=pd.Timestamp(stamp);asof=str(frame.index[-1])
        if now<pd.Timestamp(r['activated_at']):return {**empty,'note':'新方法當時尚未啟用；不回填歷史預報。'}
        if len(days)!=3 or now>=pd.Timestamp(days[0]+'T08:45:00+08:00'):return {**empty,'note':'下一交易日已開盤，不倒填新方法預報。'}
        official=prices(data_dir);cutoff=min(now,pd.Timestamp(days[0]+'T08:30:00+08:00'))
        if not sources:raise ValueError('缺少本次保存的外部來源')
        checks,missing=source_gate(frame,cutoff,official,sources,verified)
        if missing:return {**empty,'source_checks':checks,'missing':missing,'note':'必要來源不足或尚未可用：'+'、'.join(missing)+'；暫不顯示機率。'}
        if asof not in official.index or abs(float(official.at[asof,'close'])-float(frame.close.iloc[-1]))>.02:raise ValueError('官方行情與保存錨點未對齊')
        anchor=float(official.at[asof,'close']);result=calculate(official,asof,anchor)
        cache=(data_dir or db.DATA)/'v6_distribution_prices'
        vintage=cache/'prices.csv' if (cache/'manifest.json').exists() else CONFIG/r['base_file']
        from .v6_scenario_views import scenario_views
        views=scenario_views(frame.iloc[-1])
        for cls in result['classes']:cls['view']=views[cls['key']]
        return {**result,'version':VERSION,'method_version':METHOD,'terminal_model':'T1','event_model':'E1','market_as_of':asof,'anchor':anchor,'flat_bounds':[anchor*.995,anchor*1.005],'forecast_dates':days,
          'issued_at':now.isoformat(),'information_cutoff':cutoff.isoformat(),'scheduled_decision_at':days[0]+'T08:30:00+08:00','timing_qualified':now>=pd.Timestamp(days[0]+'T08:30:00+08:00'),
          'timing_note':'盤後／提前保存的歷史比例參考，未到研究的盤前08:30時點，不計為該時點前瞻驗證。' if now<pd.Timestamp(days[0]+'T08:30:00+08:00') else '按盤前08:30資訊截點保存；仍待真正前瞻資料驗證。',
          'source_checks':checks,'protocol_sha256':r['protocol_sha256'],'official_base_sha256':r['base_sha256'],'official_price_sha256':sha(vintage),'official_manifest_sha256':sha(cache/'manifest.json') if (cache/'manifest.json').exists() else None,'source_manifest_sha256':sha(sources/'comparison-manifest.json') if (sources/'comparison-manifest.json').exists() else None,'validation':r['validation'],'note':'共用歷史比例參考，僅描述第3日收盤；未通過增量的分數不調整機率，不推估逐日路徑。'}
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        return {**empty,'note':'共用分布暫不可用：'+str(error)}

if __name__=='__main__':
    p=argparse.ArgumentParser(description='Explicit chat-only official price refresh, never a web update');p.add_argument('--refresh-prices',required=True);a=p.parse_args();print(json.dumps(refresh_prices(a.refresh_prices),ensure_ascii=False))
