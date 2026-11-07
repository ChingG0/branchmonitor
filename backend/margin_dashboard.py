"""Package 8: read-only integration of validated official financing totals."""
from pathlib import Path
from datetime import date
from math import isfinite
from . import margin_statistics as sources
VERSION='margin-statistics-dashboard-8-1'
LINKS={'TWSE':'https://www.twse.com.tw/zh/trading/margin/mi-margn.html','TPEX':'https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html'}
def change(current,previous):
 if current is None or previous is None:return {'amount':None,'pct':None}
 return {'amount':current-previous,'pct':current/previous-1 if previous>0 else None}
def summarize(calendar,observations,market):
 by={r['date']:r for r in observations};series=[{'date':d,**by.get(d,{'official_loan':None,'source_id':None,'retrieved_at':None})} for d in calendar]
 latest=series[-1];present=[r for r in series if r['official_loan'] is not None]
 def baseline(n):return series[-1-n] if len(series)>n else None
 b1,b20=baseline(1),baseline(20)
 return {'market':market,'label':'上市' if market=='TWSE' else '上櫃','date':latest['date'],'official_loan':latest['official_loan'],
  'change1':change(latest['official_loan'],b1['official_loan'] if b1 else None),'change20':change(latest['official_loan'],b20['official_loan'] if b20 else None),
  'baseline1':b1['date'] if b1 else None,'baseline20':b20['date'] if b20 else None,
  'available_days':len(present),'expected_days':len(calendar),'missing_days':len(calendar)-len(present),
  'first':present[0]['date'] if present else None,'last_observation':present[-1] if present else None,'series':series,'source_url':LINKS[market]}
def report(day=None,*,store=None):
 root=Path(store) if store else sources.folder()
 empty={'version':VERSION,'status':'missing','model_ready':False,'dates':[],'as_of':None,'source_as_of':None,'markets':[],
  'risk_metrics':{'maintenance':None,'below130_amount':None,'cleaning_signal':None,'divergence_signal':None},
  'vintage':'回溯取得的官方版本；不代表當時原始發布版本','notice':'官方總融資包含ETF等標的；總量增減不是買賣訊號。'}
 if not (root/'sources.sqlite3').exists():return empty
 scope=sources.job(root);start=scope.get('start',sources.START);end=scope.get('end','9999-12-31')
 with sources.connect(root) as c:
  dates=[r[0] for r in c.execute('SELECT day FROM calendar WHERE day BETWEEN ? AND ? ORDER BY day',(start,end))]
  if not dates:return empty
  if day is not None:
   try:date.fromisoformat(day)
   except (ValueError,TypeError):raise ValueError('請選擇有效的交易日')
   if day not in dates:raise ValueError('該日不在已保存交易日曆內')
  chosen=day or dates[-1];prefix=[d for d in dates if d<=chosen];markets=[]
  for market in ('TWSE','TPEX'):
   rows=[dict(r) for r in c.execute("SELECT l.day AS date,t.loan_amount AS official_loan,l.snapshot_id AS source_id,s.retrieved_at FROM latest l JOIN totals t ON t.snapshot_id=l.snapshot_id JOIN snapshots s ON s.id=l.snapshot_id WHERE l.market=? AND l.kind='credit' AND l.day BETWEEN ? AND ? ORDER BY l.day",(market,start,chosen))]
   if any(r['official_loan'] is None or not isfinite(r['official_loan']) or r['official_loan']<0 for r in rows):raise ValueError('官方融資金額無效，未當作零')
   markets.append(summarize(prefix,rows,market))
 validation=sources.principal_validation(root)
 return {**empty,'status':'observed_only','dates':dates,'as_of':chosen,'source_as_of':dates[-1],'markets':markets,
  'validation':{'as_of':validation.get('as_of'),'version':validation.get('version'),'status':validation.get('status'),'scope':'latest_method_validation_not_historical_decision'} if validation else None,
  'archived_sections':'五籃、事件及10檔圖像沿用各自封存版本，與本頁最新來源觀測分開標示；不自動升級模型。'}
