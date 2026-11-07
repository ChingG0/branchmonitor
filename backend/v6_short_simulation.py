"""V6-2 primary short replay. Only chat publication writes; GET reads snapshots.

This is a retrospective execution replay, not a new forecast or paper fill.
Original V6 scoring and archived weather/comparison records remain untouched.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from . import db,v6_engine as E,v6_trade_policy

TZ=ZoneInfo('Asia/Taipei')
VERSION='v6-primary-short-cross-20261105'
STORE=db.DATA/'v6_short_simulation'
MODEL=db.ROOT/'research/v6_crossasset_20261031/models.json'
START='2025-01-01'
DRAWDOWN_CAP=.08


def freeze(frame,quotes,*,now=None,simulation=None):
    """Build a frozen V6Model-compatible replay; never announces a live order."""
    stamp=pd.Timestamp(now or datetime.now(TZ))
    if stamp.tzinfo is None:raise ValueError('模擬保存時間需含時區')
    if frame.empty or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('模擬行情需按交易日排序且不可重複')
    last=str(frame.index[-1])
    if last>stamp.tz_convert(TZ).date().isoformat():raise ValueError('不能使用未來行情')
    if last==stamp.tz_convert(TZ).date().isoformat() and stamp.tz_convert(TZ).strftime('%H:%M')<'13:30':
        raise ValueError('當日尚未收盤，不能回放完整日線')
    model_bytes=MODEL.read_bytes();model=json.loads(model_bytes)['capital_regime'];threshold=model['fixed_threshold']
    cfg=dict(family='probability',level=0,take=1.5,stop=1.5,hold=3,cooldown_days=v6_trade_policy.COOLDOWN_DAYS['cross'])
    if simulation is None:
        work=frame.copy();work['prob_signal']=(work.cross_raw>=threshold)&work.cross_valid&(work.dd<DRAWDOWN_CAP)
        E.CONTRACT_CACHE.clear();simulation=E.sim(work,quotes,cfg,START,last)
    sim=simulation;holding=sim['open_position'];valid=bool(frame.cross_valid.iloc[-1]);score=float(frame.cross_score.iloc[-1]) if valid else None
    if score is not None and not math.isfinite(score):score=None
    last_exit=sim['trades'][-1]['exit_date'] if sim['trades'] else None
    days=list(frame.index);remaining=max(0,cfg['cooldown_days']-(days.index(last)-days.index(last_exit))) if last_exit else 0
    p=np.r_[0.,[r['points'] for r in sim['curve']]];maxdd=float(np.max(np.maximum.accumulate(p)-p))
    trades=[{**t,'signal_score':round(float(frame.loc[t['signal_date'],'cross_score']),2),
             'signal_drawdown_pct':round(float(frame.loc[t['signal_date'],'dd'])*100,4),
             'pnl_twd':round(t['points']*200)} for t in sim['trades']]
    periods=[]
    for year in sorted({r['date'][:4] for r in sim['curve']}):
        curve=sim['curve'];first=next(i for i,r in enumerate(curve) if r['date'].startswith(year));last_i=max(i for i,r in enumerate(curve) if r['date'].startswith(year))
        beginning=curve[first-1]['points'] if first else 0.
        closed=[t for t in trades if t['exit_date'].startswith(year)]
        periods.append({'year':year,'net_points':curve[last_i]['points']-beginning,'completed':len(closed),'wins':sum(t['points']>0 for t in closed)})
    name='持有一口模擬空單' if holding else '空手'
    curve=[{'date':p['date'],'value':round(p['value'],4),'position':p['position'],'pnl_points':round(p['points'],2)} for p in sim['curve']]
    historical_signal=bool(valid and frame.cross_raw.iloc[-1]>=threshold and frame.dd.iloc[-1]<DRAWDOWN_CAP)
    return {'key':'v6','strategy_key':'v6-2','version':VERSION,'label':'V6-2 台指期純空模擬',
      'simulation_kind':'historical_replay','generated_at':stamp.isoformat(),'as_of':last,
      'status':'complete' if valid else 'partial','readiness':'ready' if valid else 'missing',
      'score':round(score,2) if score is not None else None,'score_threshold':65,'raw_threshold':threshold,
      'signal':None,'entry_allowed':None,'historical_signal':historical_signal,
      'next_action':f'截至 {last} 歷史模擬：{name}；本次不補發當時交易訊號',
      'position':-1 if holding else 0,'stop':holding['stop'] if holding else None,'take':holding['take'] if holding else None,
      'target_contract':holding['contract'] if holding else E.target_contract(last,quotes),
      'index_close':float(frame.close.iloc[-1]),'period':f'2025 年初至 {last}',
      'portfolio_return_pct':round(sim['return_pct'],4),'pnl_points':round(sim['points'],2),
      'pnl_twd_per_contract':round(sim['points']*200),'max_drawdown_pct':round(sim['max_drawdown_pct'],2),
      'max_drawdown_points':round(maxdd,2),'profit_factor':sim['pf'],
      'wins':sim['wins'],'completed':sim['completed'],'hit_rate':round(sim['win_rate'],1) if sim['win_rate'] is not None else None,
      'curve':curve,'trades':trades,'events':sim['events'],'yearly':periods,
      'cooldown_days':cfg['cooldown_days'],'cooldown_remaining':remaining,'drawdown_cap_pct':8,
      'trade_policy_version':v6_trade_policy.VERSION,
      'basis':f'以V6-2固定價格、外資與跨資產模型為主，65分門檻；距前20交易日盤中高點回落達8%不新開空單。固定一口台指期，空手或做空；訊號後下一實際交易日日盤開盤，向下跳空超過前日1ATR不追空。停利／停損各1.5ATR、最多持有3日，出場後冷卻{cfg["cooldown_days"]}交易日，出場當日不重進。實際月合約轉倉，每邊2點加交易稅，轉倉亦計費。名目報酬使用起始指數點位作分母，非保證金或複利報酬。',
      'warning':'以目前固定模型與規則重新回放歷史，並非當時已發報或已成交；2025／2026已被研究檢視，不是未碰過的樣本外證據。勝率非未來保證，分數不是下跌機率，日K不含完整夜盤停損。末日分數僅為該筆歷史資料的計算結果，新決策請看上方同日V6-2已保存的研究卡。',
      'validation_status':'歷史回放，不視為當時已成交或前瞻驗證',
      'provenance':{'model_sha256':hashlib.sha256(model_bytes).hexdigest(),
        'engine_sha256':hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        'frame_sha256':hashlib.sha256(frame.to_csv(float_format='%.17g').encode()).hexdigest(),
        'quote_sha256':hashlib.sha256(json.dumps([(d,k,v) for (d,k),v in sorted(quotes.items())],sort_keys=True).encode()).hexdigest(),
        'valid_sessions':int(frame.loc[START:,'cross_valid'].sum()),'source_end':last}}


def publish(model,*,store=None):
    if model.get('version')!=VERSION or model.get('strategy_key')!='v6-2' or model.get('simulation_kind')!='historical_replay':
        raise ValueError('只能保存V6-2主模擬回放')
    stamp=datetime.fromisoformat(model['generated_at'])
    target=store or STORE;target.mkdir(parents=True,exist_ok=True)
    name=stamp.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    result={**model,'id':name};path=target/(name+'.json');temp=target/('.'+name+'.tmp')
    with temp.open('x') as h:
        json.dump(result,h,ensure_ascii=False,allow_nan=False);h.flush();os.fsync(h.fileno())
    temp.replace(path);return result


def report(*,store=None,before=None):
    """Read saved replay only; never fetch, simulate, update or mint a signal."""
    target=store or STORE;rows=[]
    for path in sorted(target.glob('*.json'),reverse=True):
        try:
            row=json.loads(path.read_text())
            if row.get('version')!=VERSION or row.get('id')!=path.stem or row.get('strategy_key')!='v6-2':continue
            if before and pd.Timestamp(row['generated_at'])>pd.Timestamp(before):continue
            rows.append(row)
        except (OSError,ValueError,TypeError,KeyError):continue
    return rows[0] if rows else None


def current(*,now=None):
    # Local import avoids a cycle: comparison freezes also carry this replay.
    from . import v6_comparison as C
    raw,quotes=C.raw_input();return freeze(C.build_frame(raw),quotes,now=now)


def main():
    parser=argparse.ArgumentParser(description='Chat-only V6-2 primary replay, no forecast/account update')
    parser.add_argument('--publish',action='store_true');parser.add_argument('--output',type=Path);args=parser.parse_args()
    model=current()
    if args.publish:model=publish(model)
    if args.output:args.output.write_text(json.dumps(model,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({k:model[k] for k in ['strategy_key','as_of','generated_at','completed','wins','pnl_points','hit_rate','position','max_drawdown_points']},ensure_ascii=False))
if __name__=='__main__':main()
