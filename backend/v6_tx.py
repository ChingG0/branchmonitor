"""V6 short-only TX observation using the frozen 2026-10-17 score-65 study.

The research input and fitted coefficients are immutable. Only newer public
source rows are saved separately; missing input keeps the signal unavailable.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from . import db, v9_tx, v6_engine, v6_features

from . import v6_trade_policy
VERSION = 'v6-tx-short-score65-20261017'
SEED = Path(__file__).resolve().parents[1] / 'research' / 'v6_macro_20261017'
SEED_END = '2026-10-16'
LIVE = db.DATA / 'v6_sources'
TZ = ZoneInfo('Asia/Taipei')
THRESHOLD = .65
POINT_VALUE_TWD = 200
_LOCK = threading.Lock()
_FEATURE_LOCK = threading.RLock()
_NAMES = {'twd':'TWD=X','oil':'BZ=F','spx':'^GSPC','financial':'0055.TW',
          'electronics':'0052.TW','tsm':'TSM','rates-proxy':'^TNX'}
_REQUIRED = ['vix','feargreed','twd','oil','spx','financial','electronics','tsm','rates-proxy']


def _now():return datetime.now(TZ)


def _seed_input():
    with (SEED/'input.json').open() as handle:
        return json.load(handle)


def _complete_input():
    raw = _seed_input()
    newer_futures,newer_index = v9_tx._loaded()
    futures = {(r['date'],r['contract']):{key:float(r[key]) for key in ('open','high','low','close')}
               for r in raw['futures']}
    index = dict(raw['index'])
    for (day,contract),bar in newer_futures.items():
        if day > SEED_END:
            futures[(day,contract)] = dict(bar)
    for day,bar in newer_index.items():
        if day > SEED_END:
            index[day] = dict(bar)
    history = v9_tx.TXHistory(futures,index)
    latest = history.days[-1]
    raw['index'] = {day:bar for day,bar in index.items() if day <= latest}
    raw['futures'] = [{'date':day,'contract':contract,**bar}
                      for (day,contract),bar in futures.items() if day <= latest]
    return raw,[day for day in history.missing_dates if day>SEED_END]


def _score(frame):
    results=json.loads((SEED/'statistical-results.json').read_text())['results']['sentiment']
    model=results['model'];cols=results['features']
    x=frame.assign(rotation=frame.financial_r5-frame.electronics_r5,
                   below20=frame.close/frame.ma20-1,
                   ma5fall=frame.ma5/frame.ma5.shift()-1)[cols].astype(float).to_numpy()
    med=np.array(model['median']);sd=np.array(model['sd']);beta=np.array(model['beta'])
    z=np.clip((np.where(np.isfinite(x),x,med)-med)/sd,-5,5)
    linear=np.c_[np.ones(len(z)),z] @ beta
    score=1/(1+np.exp(-np.clip(linear,-50,50)))
    available=frame.assign(rotation=frame.financial_r5-frame.electronics_r5,
                           below20=frame.close/frame.ma20-1,
                           ma5fall=frame.ma5/frame.ma5.shift()-1)[cols].notna().all(axis=1)
    return pd.Series(score,index=frame.index),available


def _source_age(frame,day):
    missing=[]
    for key in _REQUIRED:
        value=frame.at[day,key+'_available']
        if pd.isna(value):
            missing.append(key);continue
        # Daily bars should not remain unchanged across a new Taiwan session.
        when=pd.Timestamp(value).date()
        if (date.fromisoformat(day)-when).days>4:
            missing.append(key)
    for key in ('foreign','retail'):
        value=frame.at[day,key+'_available']
        if pd.isna(value) or pd.Timestamp(value).date()!=date.fromisoformat(day):
            missing.append(key)
    return missing


def _downside_outlook(frame,score,available,readiness):
    """Historical downside frequencies for completed daily and weekly horizons.

    Scores were fitted through 2022; later observations are diagnostic
    frequencies, not calibrated probabilities or short-trade win rates.
    """
    days=list(frame.index)
    eligible=[(i,float(score.loc[day])) for i,day in enumerate(days[:-1])
              if day>='2023-01-01' and bool(available.loc[day]) and not _source_age(frame,day)]
    latest=float(score.iloc[-1]) if bool(available.iloc[-1]) and readiness=='ready' else None
    def summary(pair):
        n,wins=pair
        bounds=v6_engine.wilson(wins,n)
        return {'count':n,'declines':wins,'rate_pct':round(wins/n*100,1) if n else None,
                'range_95_pct':[round(x,1) for x in bounds] if n else None}
    def count_outcomes(outcomes):
        groups={name:[0,0] for name in ('all','below65','at_least65','at_least70')}
        seen={name:set() for name in groups}
        for key,level,down in outcomes:
            names=('all','at_least65' if level>=.65 else 'below65')
            if level>=.70:names+=('at_least70',)
            for name in names:
                if key in seen[name]:continue
                seen[name].add(key);groups[name][0]+=1;groups[name][1]+=int(down)
        base,low,high,strong=(summary(groups[name]) for name in groups)
        current=high if latest is not None and latest>=.65 else low
        elevated=bool(latest is not None and latest>=.65 and high['count']>=30 and
                      high['rate_pct'] is not None and base['rate_pct'] is not None and
                      high['rate_pct']>=50 and high['rate_pct']-base['rate_pct']>=5)
        return {'value':current['rate_pct'] if latest is not None and current['count']>=30 else None,
                'sample':current if latest is not None else None,'baseline':base,
                'below65':low,'at_least65':high,'at_least70':strong,'elevated':elevated}
    horizons=[]
    for sessions in (1,2,3,5):
        outcomes=[(days[i],level,float(frame.at[days[i+sessions],'close'])<float(frame.at[days[i],'close']))
                  for i,level in eligible if i+sessions<len(days)]
        horizons.append({'key':f'd{sessions}','label':f'{sessions} 個交易日','definition':'到期收盤低於訊號日收盤',
                         **count_outcomes(outcomes)})
    week_bars={}
    for day in days:
        current_day=date.fromisoformat(day)
        week_start=current_day-timedelta(days=current_day.weekday())
        week_bars.setdefault(week_start,[]).append(day)
    weekly=[];last_day=date.fromisoformat(days[-1])
    for i,level in eligible:
        decision_day=date.fromisoformat(days[i+1])
        this_week=decision_day-timedelta(days=decision_day.weekday())
        next_week=this_week+timedelta(days=7)
        # Require a later observed trading week, so the target week cannot
        # still be in progress on a Friday or over a long market holiday.
        target=week_bars.get(next_week)
        if not target or last_day<next_week+timedelta(days=7):continue
        down=float(frame.at[target[-1],'close'])<float(frame.at[target[0],'open'])
        weekly.append((next_week,level,down))
    horizons.append({'key':'next_week','label':'下週 K','definition':'下個完整日曆週的末日收盤低於首日開盤；同一訊號週每組只計一次',
                     **count_outcomes(weekly)})
    first=horizons[0]
    return {'as_of':days[-1],'decision_at':frame.at[days[-1],'decision_at'],
            'readiness':readiness,'score':round(latest*100,1) if latest is not None else None,
            **first,'horizons':horizons,
            'elevated_horizons':[h['key'] for h in horizons if h['elevated']],
            'score70_reached':bool(latest>=.70) if latest is not None else None,
            'rule':'每個區間分別判定：V6 分數≥65、相似日收跌率≥50%、高於該區間整體基準至少5個百分點，且相似樣本≥30筆，才標示偏高。70分獨立觀察。',
            'note':'2023年起完整來源的歷史訊號：1／2／3／5交易日比較到期收盤與訊號日收盤；下週K比較下個完整日曆週收盤與該週開盤，同一訊號週於各分數組只計一次。只計已結束的區間。95% Wilson為二項近似，未計入重疊日與市場日間相關。這些是歷史條件頻率，未經未來樣本校準，並非精確預測機率或V6空單勝率。'}


def report():
    raw,tx_gaps=_complete_input()
    with _FEATURE_LOCK:
        v6_features.RAW = LIVE if LIVE.exists() else SEED/'raw'
        frame=v6_features.build(raw)
    score,available=_score(frame)
    # Fitted parameters and the entry filter were fixed in the study.
    signal=(score>=THRESHOLD)&available&(frame.dd<.08)
    frame['prob_signal']=signal
    quotes={(r['date'],r['contract']):{key:float(r[key]) for key in ('open','high','low','close')}
            for r in raw['futures']}
    year=str(frame.index[-1][:4]);start='2025-01-01'
    cfg={'family':'probability','level':0,'take':1.5,'stop':1.5,'hold':3,'cooldown_days':v6_trade_policy.COOLDOWN_DAYS['legacy']}
    simulation=v6_engine.sim(frame,quotes,cfg,start,frame.index[-1])
    frame['prob_signal']=(score>=.70)&available&(frame.dd<.08)
    observed70=v6_engine.sim(frame,quotes,cfg,start,frame.index[-1])
    frame['prob_signal']=signal
    last=frame.index[-1];decision=pd.Timestamp(frame.at[last,'decision_at'])
    missing=_source_age(frame,last)
    events=json.loads((SEED/'event-dates.json').read_text())
    if date.fromisoformat(last)>(date.fromisoformat(events['cpi'][0][:10])+timedelta(days=40)):
        missing.append('cpi_calendar')
    if date.fromisoformat(last)>(date.fromisoformat(events['tsmc'][-1][:10])+timedelta(days=110)):
        missing.append('tsmc_calendar')
    current_time=_now()
    if current_time.date()>decision.date():
        missing.append('index_close')
    readiness='missing' if missing else 'waiting' if current_time<decision.to_pydatetime() else 'ready'
    latest_score=round(float(score.loc[last])*100,1) if readiness=='ready' and available.loc[last] and not missing else None
    latest_signal=bool(signal.loc[last]) if readiness=='ready' else None
    holding=simulation['open_position']
    days=list(frame.index)
    last_exit=simulation['trades'][-1]['exit_date'] if simulation['trades'] else None
    cooling=bool(last_exit and days.index(last)-days.index(last_exit)<cfg['cooldown_days'])
    if readiness=='ready':
        if latest_signal and holding is None and not cooling:next_action='下一交易日日盤開盤考慮建立一口空單'
        elif cooling and holding is None:next_action='出場後冷卻中，暫不建立新空單'
        elif holding:next_action='持有空單；依停利、停損或持有日數出場'
        else:next_action='空手觀察'
    elif readiness=='waiting':next_action='等待下一交易日08:30隔夜資料確認'
    else:next_action='資料未齊，不產生新訊號'
    outlook=_downside_outlook(frame,score,available,readiness)
    seventy_exit=observed70['trades'][-1]['exit_date'] if observed70['trades'] else None
    seventy_cooling=bool(seventy_exit and days.index(last)-days.index(seventy_exit)<cfg['cooldown_days'])
    observation70={'threshold':70,'signal':bool(score.loc[last]>=.70 and available.loc[last] and frame.at[last,'dd']<.08) if readiness=='ready' else None,
                   'entry_allowed':bool(score.loc[last]>=.70 and available.loc[last] and frame.at[last,'dd']<.08 and
                                        not observed70['open_position'] and not seventy_cooling) if readiness=='ready' else None,
                   'position':-1 if observed70['open_position'] else 0,
                   'wins':observed70['wins'],'completed':observed70['completed'],
                   'hit_rate':round(observed70['win_rate'],1) if observed70['win_rate'] is not None else None,
                   'pnl_points':round(observed70['points'],2),
                   'portfolio_return_pct':round(observed70['return_pct'],4),
                   'curve':[{'date':p['date'],'value':round(p['value'],4),'position':p['position'],
                             'pnl_points':round(p['points'],2)} for p in observed70['curve']]}
    trades=[{**t,'signal_score':round(float(score.loc[t['signal_date']])*100,1),
             'pnl_twd':round(t['points']*POINT_VALUE_TWD)} for t in simulation['trades']]
    feature_snapshot={}
    for key in ('vix','feargreed','twd','oil','spx','tsm','rates-proxy',
                'financial','electronics','foreign','retail'):
        value=frame.at[last,key+'_value']
        available_at=frame.at[last,key+'_available']
        if (pd.notna(value) and pd.notna(available_at) and math.isfinite(float(value))
                and pd.Timestamp(available_at).to_pydatetime() <= current_time):
            published=pd.Timestamp(available_at).date()
            # Overseas daily bars and VIX/CNN are available the next morning;
            # Taiwan ETF and TAIFEX positioning retain their same-day date.
            observed=published-timedelta(days=1) if key in ('vix','feargreed','twd','oil','spx','tsm','rates-proxy') else published
            feature_snapshot[key]={'value':round(float(value),4),'as_of':observed.isoformat(),
                                   'available_at':pd.Timestamp(available_at).isoformat()}
    if 'foreign' in feature_snapshot and pd.notna(frame.at[last,'foreign_net_short']):
        feature_snapshot['foreign_net_short']={'value':int(frame.at[last,'foreign_net_short']),
                                                'as_of':feature_snapshot.get('foreign',{}).get('as_of')}
    result={'key':'v6','trade_policy_version':v6_trade_policy.VERSION,'cooldown_days':cfg['cooldown_days'],'version':VERSION,'label':'V6 台指期純空｜研究觀察',
            'status':'partial' if tx_gaps or missing or readiness!='ready' else 'complete',
            'validation_status':'未通過穩定70%驗證','as_of':last,'decision_at':decision.isoformat(),
            'readiness':readiness,'missing_sources':missing,'missing_tx_dates':tx_gaps,
            'score':latest_score,'score_threshold':65,'signal':latest_signal,
            'feature_snapshot':feature_snapshot,
            'observation70':observation70,'outlook':outlook,
            'entry_allowed':bool(latest_signal and not holding and not cooling) if readiness=='ready' else None,
            'next_action':next_action,'position':-1 if holding else 0,
            'stop':round(holding['stop']) if holding else None,'take':round(holding['take']) if holding else None,
            'target_contract':v6_engine.target_contract(last,quotes),
            'index_close':float(frame.at[last,'close']),
            'period':f'2025 年初至 {last}',
            'portfolio_return_pct':round(simulation['return_pct'],4),
            'pnl_points':round(simulation['points'],2),
            'pnl_twd_per_contract':round(simulation['points']*POINT_VALUE_TWD),
            'max_drawdown_pct':round(simulation['max_drawdown_pct'],2),
            'wins':simulation['wins'],'completed':simulation['completed'],
            'hit_rate':round(simulation['win_rate'],1) if simulation['win_rate'] is not None else None,
            'curve':[{'date':p['date'],'value':round(p['value'],4),'position':p['position'],
                      'pnl_points':round(p['points'],2)} for p in simulation['curve']],
            'trades':trades[-60:],'events':simulation['events'][-60:],
            'basis':'固定一口台指期，空手或做空；每日決策於台灣08:30，下一交易日日盤開盤成交。停利／停損各1.5倍前日ATR，最多持有3日，出場後不額外冷卻、最快下一交易日重檢，出場當日不重複開倉；每邊2點及交易稅已扣。名目點數報酬，非保證金報酬。',
            'warning':'原5日冷卻研究：65分門檻在2023–2024僅3勝／4筆，2025–2026/10/16為5勝／8筆；本次改0日後的交易回放另列，不能沿用舊勝率。樣本不足以證明穩定達70%。分數不是勝率。夜盤停損不在日K回測內。',
            'sources':{'research':str(SEED/'README.md'),'asof':'外部日資料限08:30前已公布；法人與小台部位取前一交易日盤後'}}
    return result


def _request(url,params=None):
    response=requests.get(url,params=params,timeout=(10,35),headers={'User-Agent':'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)'})
    response.raise_for_status()
    return response.content


def _append_csv(old,new,cutoff,column=0,encoding='cp950'):
    before=list(csv.reader(io.StringIO(old.decode(encoding))))
    if new.lstrip().lower().startswith((b'<!doctype',b'<html')):
        raise ValueError('期交所回傳錯誤頁，未寫入資料')
    try: decoded=new.decode(encoding)
    except UnicodeDecodeError: decoded=new.decode('utf-8-sig')
    after=list(csv.reader(io.StringIO(decoded)))
    if not before or not after or before[0]!=after[0]:raise ValueError('期交所欄位格式改變')
    keep=[r for r in before[1:] if r and r[column].replace('/','-')<=cutoff]
    fresh=[r[:len(after[0])] for r in after[1:] if r and r[column].replace('/','-')>cutoff]
    allrows=keep+fresh
    if not allrows:
        raise ValueError('期交所下載內容沒有交易日')
    output=io.StringIO();writer=csv.writer(output,lineterminator='\n');writer.writerows([after[0],*allrows])
    return output.getvalue().encode(encoding),len(fresh)


def _completed_us_day(now=None):
    """Daily US/global bars remain provisional until 20:00 New York time."""
    new_york=(now or _now()).astimezone(ZoneInfo('America/New_York'))
    return (new_york.date() if new_york.hour >= 20 else
            new_york.date()-timedelta(days=1)).isoformat()


def _merge_yahoo(old,new,through_day=None,allowed_sessions=None):
    original=json.loads(old)['chart']['result'][0];latest=json.loads(new)['chart']['result'][0]
    if latest is None:raise ValueError('Yahoo 日行情空白')
    cutoff=int(datetime.fromisoformat(SEED_END+'T23:59:59+00:00').timestamp())
    old_ts=list(original.get('timestamp') or []);new_ts=list(latest.get('timestamp') or [])
    if not new_ts:raise ValueError('Yahoo 缺少時間序列')
    zone=ZoneInfo(original['meta'].get('exchangeTimezoneName','America/New_York'))
    def eligible(t):
        day=datetime.fromtimestamp(t,zone).date().isoformat()
        return (through_day is None or day<=through_day) and (allowed_sessions is None or day<=SEED_END or day in allowed_sessions)
    retain=[i for i,t in enumerate(old_ts) if eligible(t)]
    kept_ts=[old_ts[i] for i in retain]
    old_location={t:i for i,t in enumerate(kept_ts)}
    fresh=[i for i,t in enumerate(new_ts) if t>cutoff and eligible(t)]
    append=[i for i in fresh if new_ts[i] not in old_location]
    replace={old_location[new_ts[i]]:i for i in fresh if new_ts[i] in old_location}
    original['timestamp']=kept_ts+[new_ts[i] for i in append]
    for key in ('quote','adjclose'):
        if key not in original['indicators']:continue
        if key not in latest['indicators']:raise ValueError('Yahoo 欄位格式改變')
        for old_block,new_block in zip(original['indicators'][key],latest['indicators'][key]):
            for field,values in old_block.items():
                if field not in new_block:raise ValueError('Yahoo 欄位格式改變')
                updated=[values[i] for i in retain]
                for position,source in replace.items():updated[position]=new_block[field][source]
                old_block[field]=updated+[new_block[field][i] for i in append]
    return json.dumps({'chart':{'result':[original],'error':None}},ensure_ascii=False).encode(),len(append)


def refresh():
    if not _LOCK.acquire(blocking=False):return {'status':'partial','message':'V6 更新正在執行'}
    try:
        try:
            v9_result=v9_tx.refresh()
            today=datetime.now(TZ).date()
            raw,tx_gaps=_complete_input();market_day=date.fromisoformat(max(raw['index']))
            stage=LIVE.parent/('v6_sources.stage-'+uuid.uuid4().hex)
            shutil.copytree(LIVE if LIVE.exists() else SEED/'raw',stage)
        except Exception as error:
            return {'status':'partial','message':'V6 基礎行情更新未完成，沿用已保存資料：'+str(error)[:130]}
        try:
            added={};seed_cut=date.fromisoformat(SEED_END);complete_us_day=_completed_us_day()
            begin=int(datetime.combine(seed_cut-timedelta(days=8),datetime.min.time(),tzinfo=ZoneInfo('UTC')).timestamp())
            end=int(datetime.combine(today+timedelta(days=2),datetime.min.time(),tzinfo=ZoneInfo('UTC')).timestamp())
            with db.connect() as conn:
                taiwan_sessions={row[0] for row in conn.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date>? AND date<=?",(SEED_END,market_day.isoformat()))}
            for name,symbol in _NAMES.items():
                from urllib.parse import quote
                fresh=_request('https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol,safe=''),
                               {'period1':begin,'period2':end,'interval':'1d'})
                taiwan=name in ('financial','electronics')
                path=stage/(name+'.json');merged,count=_merge_yahoo(path.read_bytes(),fresh,market_day.isoformat() if taiwan else complete_us_day,
                                                                  taiwan_sessions if taiwan else None)
                path.write_bytes(merged);added[name]=count
            vix=_request('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv')
            before=list(csv.reader(io.StringIO((stage/'vix.csv').read_text())));after=list(csv.reader(io.StringIO(vix.decode('utf-8-sig'))))
            if before[0]!=after[0]:raise ValueError('VIX 欄位格式改變')
            extra=[r for r in after[1:] if seed_cut<datetime.strptime(r[0],'%m/%d/%Y').date()<=date.fromisoformat(complete_us_day)]
            out=io.StringIO();csv.writer(out,lineterminator='\n').writerows([before[0],*[r for r in before[1:] if datetime.strptime(r[0],'%m/%d/%Y').date()<=seed_cut],*extra]);(stage/'vix.csv').write_text(out.getvalue());added['vix']=len(extra)
            cnn=json.loads(_request('https://production.dataviz.cnn.io/index/fearandgreed/graphdata'))
            prior=json.loads((stage/'cnn-2021.json').read_text());old=prior['fear_and_greed_historical']['data'];new=cnn['fear_and_greed_historical']['data']
            complete=lambda row: datetime.fromtimestamp(row['x']/1000,ZoneInfo('UTC')).date().isoformat()<=complete_us_day
            old=[row for row in old if complete(row)]
            keys={row['x'] for row in old};rows=[r for r in new if r['x'] not in keys and complete(r) and datetime.fromtimestamp(r['x']/1000,ZoneInfo('UTC')).date()>seed_cut]
            prior['fear_and_greed_historical']['data']=old+rows;(stage/'cnn-2021.json').write_text(json.dumps(prior));added['feargreed']=len(rows)
            year=market_day.year
            for code in ('TXF','MXF'):
                filename=f'positions-{code}-{year}.csv';source=stage/filename
                params={'queryStartDate':f'{year}/01/01','queryEndDate':market_day.strftime('%Y/%m/%d'),'commodityId':code}
                fresh=_request('https://www.taifex.com.tw/cht/3/futContractsDateDown',params)
                base=source.read_bytes() if source.exists() else fresh[:fresh.find(b'\n')+1]
                data,count=_append_csv(base,fresh,SEED_END)
                source.write_bytes(data);added[code]=count
            filename=f'mtx-month-{year}-{market_day.month:02d}.csv';source=stage/filename
            params={'down_type':'1','commodity_id':'MTX','commodity_id2':'',
                    'queryStartDate':f'{year}/{market_day.month:02d}/01','queryEndDate':market_day.strftime('%Y/%m/%d')}
            fresh=_request('https://www.taifex.com.tw/cht/3/futDataDown',params)
            base=source.read_bytes() if source.exists() else fresh[:fresh.find(b'\n')+1]
            data,count=_append_csv(base,fresh,SEED_END)
            source.write_bytes(data);added['mtx_open_interest']=count
            # Validate all refreshed data with the same feature builder before activation.
            with _FEATURE_LOCK:
                previous=v6_features.RAW;v6_features.RAW=stage
                try:
                    raw,_=_complete_input();frame=v6_features.build(raw)
                    _,availability=_score(frame)
                    if not bool(availability.iloc[-1]):raise ValueError('最新日必要欄位尚未齊全')
                finally:v6_features.RAW=previous
            if LIVE.exists():
                old=LIVE.parent/('v6_sources.old-'+uuid.uuid4().hex);LIVE.rename(old)
                try:stage.rename(LIVE)
                except Exception:old.rename(LIVE);raise
                shutil.rmtree(old)
            else:stage.rename(LIVE)
            status='partial' if tx_gaps or v9_result.get('status')!='complete' else 'complete'
            note=(f'；台指期尚缺 {len(tx_gaps)} 日' if tx_gaps else
                  '；V9 台指期行情仍有待補資料' if status=='partial' else '')
            return {'status':status,'message':f'V6 外部來源已檢查至 {market_day.isoformat()}，新增或覆核 {sum(added.values())} 筆{note}；08:30 後才確認隔夜訊號',
                    'added':added,'as_of':frame.index[-1]}
        except Exception as error:
            shutil.rmtree(stage,ignore_errors=True)
            return {'status':'partial','message':'V6 外部資料更新未完成，沿用已保存行情：'+str(error)[:130]}
    finally:_LOCK.release()


def latest_date():
    return SEED_END if not LIVE.exists() else max(SEED_END, v9_tx.latest_date())
