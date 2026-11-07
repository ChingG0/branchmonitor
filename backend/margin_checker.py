"""Read-only visual checker derived from the frozen Package2 ledgers."""
from pathlib import Path
from functools import lru_cache
import gzip,json,hashlib,math,sqlite3
from contextlib import closing
from . import db,margin_statistics as m
VERSION='margin-checker-2a-1'
REASONS={'initial_inventory':'起始存量成本未知','disclosure_or_market_gap':'來源或揭露中斷','previous_balance_difference':'次日前餘與前日今餘不同','unsafe_corporate_event':'權利／公司行動待核對','large_price_discontinuity':'價格跳變超過25%，原因待核對','large_share_count_change':'發行股數大幅變動，原因待核對'}
MODES={'net':['net_average','net_fifo'],'replacement':['replacement_average','replacement_fifo']}
def derive(row):
 if row is None:return None
 q=row['quantity'];models={}
 for mode,keys in MODES.items():
  pair={}
  for name,key in zip(['average','fifo'],keys):
   model=row['models'][key]
   if abs(model['quantity']-q)>1e-6:raise ValueError('Ledger balance differs from source balance')
   principal=model['principal'];cost=principal/(q*1000*.6) if q>0 and principal is not None and model['unpriced_units']<=1e-7 else None
   if cost is not None and (not math.isfinite(cost) or cost<0):raise ValueError('Invalid equivalent cost')
   pair[name]={'cost':cost,'unknown_pct':model['unknown_fraction']*100 if q>0 else None,'unknown_units':model['unknown_units'],'unpriced_units':model['unpriced_units'],'cap_unknown_units':model['cap_unknown_units'],'quantity':model['quantity']}
  models[mode]=pair
 return {'date':row['date'],'close':row['close'],'balance':q,'proxy_price':row['price_proxy'],'reset_reasons':row['reset_reasons'],'reset_labels':[REASONS.get(x,x) for x in row['reset_reasons']],'models':models}
def assemble(calendar,selection,records):
 if calendar!=sorted(set(calendar)):raise ValueError('Calendar must be ordered and unique')
 days=set(calendar);out=[]
 for market,symbols in selection.items():
  for symbol in symbols:
   selected=[r for r in records.get(market,[]) if r['symbol']==symbol];byday={r['date']:r for r in selected}
   if len(byday)!=len(selected) or any(d not in days for d in byday):raise ValueError('Invalid checker dates')
   points=[derive(byday.get(d)) for d in calendar];present=[p for p in points if p is not None];latest=points[-1] if points else None
   out.append({'symbol':symbol,'name':selected[-1]['name'] if selected else symbol,'market':market,'days':len(present),'expected_days':len(calendar),'first':present[0]['date'] if present else None,'last':present[-1]['date'] if present else None,'latest':latest,'reset_days':sum(bool(p['reset_reasons']) for p in present),'price_days':sum(p['close'] is not None for p in present),'points':points})
 return out
def build(research=None,target=None):
 research=Path(research) if research else db.ROOT/'research/margin_statistics_p2_20261105';target=Path(target) if target else m.folder();manifest=json.loads((research/'manifest.json').read_text());protocol=json.loads((research/'implementation-protocol.json').read_text())
 if hashlib.sha256((research/'inputs.sqlite3').read_bytes()).hexdigest()!=manifest['inputs_sha256']:raise ValueError('Frozen input digest mismatch')
 if hashlib.sha256((research/'implementation-protocol.json').read_bytes()).hexdigest()!=manifest['protocol_sha256']:raise ValueError('Frozen selection/protocol digest mismatch')
 if hashlib.sha256((research/'frozen-engine.py').read_bytes()).hexdigest()!=manifest['engine_sha256']:raise ValueError('Frozen model digest mismatch')
 with closing(sqlite3.connect((research/'inputs.sqlite3').as_uri()+'?mode=ro&immutable=1',uri=True)) as c:calendar=[r[0] for r in c.execute('SELECT day FROM calendar ORDER BY day')]
 records={};hashes={}
 for market in ['TWSE','TPEX']:
  p=research/(market.lower()+'-checker-inputs.json.gz');raw=p.read_bytes();hashes[market]=hashlib.sha256(raw).hexdigest();records[market]=json.loads(gzip.decompress(raw))
 stocks=assemble(calendar,protocol['checker_2A_fixed_before_results'],records)
 result={'version':VERSION,'created_at':m.now(),'as_of':calendar[-1] if calendar else None,'start':calendar[0] if calendar else None,'calendar':calendar,'selection_frozen_at':protocol['frozen_at'],'method_version':protocol['version'],'input_sha256':manifest['inputs_sha256'],'checker_input_sha256':hashes,'model_ready':False,'status':'diagnostic_only','cost_definition':'六成假設等值成本＝診斷本金÷融資餘額張數÷1000÷0.60；不是實際買進均價。','unknown_definition':'未知存量包括起始持倉及中斷／差異／公司行動後重設的存量；比例降至10%以下也不代表整體模型通過。','stocks':stocks}
 target.mkdir(parents=True,exist_ok=True);path=target/'checker.json.gz';temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()));temp.replace(path);return result
def clip(data,day):
 calendar=data.get('calendar',[])
 if day not in calendar:raise ValueError('該日不在個股檢查封存日曆內')
 n=calendar.index(day)+1;stocks=[]
 for stock in data['stocks']:
  points=stock['points'][:n];present=[p for p in points if p is not None]
  stocks.append({**stock,'points':points,'days':len(present),'expected_days':n,'first':present[0]['date'] if present else None,'last':present[-1]['date'] if present else None,'latest':points[-1] if points else None,'reset_days':sum(bool(p['reset_reasons']) for p in present),'price_days':sum(p['close'] is not None for p in present)})
 return {**data,'calendar':calendar[:n],'as_of':day,'source_as_of':data.get('as_of'),'stocks':stocks}
@lru_cache(maxsize=2)
def _load(path,mtime,size):return json.loads(gzip.decompress(Path(path).read_bytes()))
def report(symbol=None,day=None,*,store=None):
 target=Path(store) if store else m.folder();path=target/'checker.json.gz'
 if not path.exists():return {'version':VERSION,'status':'missing','model_ready':False,'stocks':[],'message':'包2A檢查圖資料尚未保存。'}
 stamp=path.stat();data=_load(str(path),stamp.st_mtime_ns,stamp.st_size)
 if data.get('version')!=VERSION or data.get('model_ready') is not False:raise ValueError('Invalid checker artifact')
 if day is not None:data=clip(data,day)
 selected=None
 if symbol is not None:
  selected=next((s for s in data['stocks'] if s['symbol']==symbol),None)
  if selected is None:raise ValueError('僅開放包2前固定的10檔檢查標的')
 summary=[]
 for stock in data['stocks']:
  mini=stock['points'][-90:];summary.append({k:v for k,v in stock.items() if k!='points'}|{'mini':mini})
 return {k:v for k,v in data.items() if k!='stocks'}|{'stocks':summary,'detail':selected}
if __name__=='__main__':
 r=build();print(json.dumps({'version':r['version'],'stocks':[(s['symbol'],s['days'],s['reset_days']) for s in r['stocks']]},ensure_ascii=False))
