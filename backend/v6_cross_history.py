"""Explicit V6-2 historical reconstruction; never rewrite original V6 history."""
from __future__ import annotations
import argparse,hashlib,json,math,os
from datetime import datetime
from pathlib import Path
import pandas as pd
from . import db,v6_comparison as C

PATH=db.DATA/'v6_comparison_history.json'
SPECS=[
 ('idx_gap5','加權指數 5 日乖離',100.,'%',None,'valuation','價格相對5MA的位置；低於均線不等於必然下跌。'),
 ('tsmc_gap5','台積電 5 日乖離',100.,'%',None,'valuation','台積電以除權息還原價格計算相對5MA位置。'),
 ('peer_weak','研究權值股低於5MA比例',100.,'%',None,'valuation','固定研究籃子為鴻海、聯發科、台達電，不是歷史實際前三大權重。'),
 ('foreign_value','外資期貨淨空占多空總部位',100.,'%','foreign','futures','（空單−多單）÷（空單＋多單）；不是看跌機率。'),
 ('foreign_delta5_share','外資期貨5日增空占比',100.,'%','foreign','futures','淨空單5日變化相對多空總部位，不代表單一外資機構。'),
 ('spy_r5','美股 SPY 近5日',100.,'%','spy','global','美元還原報酬，使用盤前已完成的美國日線。'),
 ('tlt_r5','長債 TLT 近5日',100.,'%','tlt','global','20年以上美國公債ETF美元還原報酬，不代表所有債券。'),
 ('gold_r5','黃金 GLD 近5日',100.,'%','gold','global','黃金ETF美元還原報酬，不代表台幣黃金報酬。'),
 ('dollar_r5','美元指數近5日',100.,'%','dollar','global','美元指數變化由固定模型估計影響，不以美元走升就固定加分。'),
]

def reconstruct(frame,*,start,end,generated_at):
    stamp=pd.Timestamp(generated_at);points=[]
    for cash,row in frame.iterrows():
        cutoff=pd.Timestamp(row.decision_at);day=cutoff.date().isoformat()
        if not start<=day<=end or cutoff>stamp:continue
        missing=[]
        if not bool(row.cross_valid) or not math.isfinite(float(row.cross_score)):missing.append('V6-2必要欄位')
        for key in ('foreign','retail','twd','spx','tsm','sox','spy','tlt','gold','dollar'):
            av=pd.Timestamp(row.get(key+'_available'))
            if pd.isna(av) or av>cutoff or cutoff-av>pd.Timedelta(days=4):missing.append(key)
        indicators=[{'category':'valuation','label':'前一交易日加權收盤','value':float(row.close),'unit':'點','as_of':cash,
          'source':'研究保存的加權指數','url':'https://www.twse.com.tw/zh/trading/indices/taiex.html','reading':'只使用盤前已完成的台股收盤，不使用當日收盤。'}]
        for col,label,scale,unit,key,category,reading in SPECS:
            value=row.get(col)
            if value is None or pd.isna(value) or not math.isfinite(float(value)):continue
            av=pd.Timestamp(row.get(key+'_available')) if key else pd.Timestamp(cash+'T16:00:00+08:00')
            if pd.isna(av) or av>cutoff:continue
            observed=av.date().isoformat() if key=='foreign' else (av-pd.Timedelta(days=1)).date().isoformat() if key else cash
            indicators.append({'category':category,'label':label,'value':round(float(value)*scale,3),'unit':unit,'as_of':observed,
              'available_at':av.isoformat(),'source':'臺灣期貨交易所' if key=='foreign' else 'Yahoo Finance／本機研究',
              'url':'https://www.taifex.com.tw/cht/3/futContractsDate' if key=='foreign' else 'https://finance.yahoo.com/','reading':reading})
        points.append({'date':day,'decision_at':cutoff.isoformat(),'market_as_of':cash,'score':None if missing else round(float(row.cross_score),2),
          'missing_sources':sorted(set(missing)),'indicators':indicators,'news':[],
          'missing_context':['當時人工新聞研究','集保大戶／散戶分級','台指夜盤逐時資料']})
    return {'kind':'retrospective','strategy_key':'v6-2','label':'V6-2｜價格＋外資＋跨資產','generated_at':stamp.isoformat(),
      'requested_start':start,'requested_end':end,'start':points[0]['date'] if points else None,'end':points[-1]['date'] if points else None,
      'model_version':C.VERSION+':v6-2','points':points,'model_sha256':hashlib.sha256(C.ARTIFACT.read_bytes()).hexdigest(),
      'context_note':'此區已納入價格、外資期貨、跨資產與美元條件；DIX／GEX、融資及人工新聞未加入這套固定模型。',
      'note':'V6-2歷史重算，非當時預報。模型使用2023年11月至2024年底資料訓練；研究曾查看後續歷史，不屬完全未接觸的樣本外驗證。日期為台灣盤前08:30，台股只用前一交易日收盤，海外只取當時已公布的日線。原始分數門檻0.2342675752固定換算為65分，保持研究進場條件；分數不是下跌機率。歷史來源可能事後修訂，無法保證與當時發布版本完全一致。原版V6回溯及已保存人工預報均不改寫。'}

def save(frame,*,start='2026-10-01',end=None,generated_at=None,path=None):
    stamp=generated_at or datetime.now(C.TZ).isoformat();end=end or pd.Timestamp(stamp).date().isoformat()
    result=reconstruct(frame,start=start,end=end,generated_at=stamp)
    if not result['points']:raise ValueError('無V6-2可回溯日期，不寫入')
    target=path or PATH;target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name('.'+target.name+'.tmp')
    with temp.open('w') as h:json.dump(C.clean(result),h,ensure_ascii=False,allow_nan=False);h.flush();os.fsync(h.fileno())
    temp.replace(target);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--start',default='2026-10-01');p.add_argument('--end');args=p.parse_args()
    raw,_=C.raw_input();f=C.build_frame(raw);r=save(f,start=args.start,end=args.end)
    print(json.dumps({'start':r['start'],'end':r['end'],'points':len(r['points'])},ensure_ascii=False))
