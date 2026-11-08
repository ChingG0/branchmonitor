"""Manual, bounded forward-PE source inventory and immutable data collection."""
from __future__ import annotations
import argparse,csv,json,re,sqlite3,time
from datetime import datetime
from pathlib import Path
import requests
from . import forward_pe_store as S,forward_pe_sources as P
WEIGHTS='https://www.taifex.com.tw/cht/2/weightedPropertion'
PROTOCOL=S.db.ROOT/'config/forward_pe/protocol.json'
def download(url):
 response=requests.get(url,timeout=(10,25),headers={'User-Agent':'Mozilla/5.0 (compatible; BranchMonitorSourceInventory/1.0)'})
 response.raise_for_status()
 if len(response.content)>4_000_000:raise ValueError('原檔過大，不繼續下載')
 return response.content

def current_report(*,cutoff=None):
 snap=S.read_snapshot()
 if snap is None:return {'status':'missing','message':'尚無包1、2保存紀錄'}
 if cutoff and S.instant(cutoff)<S.instant(snap['created_at']):
  with S.connect() as c:
   snaps=[dict(r) for r in c.execute('SELECT * FROM snapshots') if S.instant(r['created_at'])<=S.instant(cutoff)]
  if not snaps:return {'status':'missing','message':'該時點尚未保存此資料，未回填'}
  snap=max(snaps,key=lambda r:S.instant(r['created_at']));snap['payload']=json.loads(snap['payload'])
 with S.connect() as c:
  u=dict(c.execute('SELECT * FROM universes WHERE id=?',(snap['payload']['universe_id'],)).fetchone());full=json.loads(u['payload']);top=full[:50]
  quotes={}
  for id in snap['payload']['quote_ids']:
   r=c.execute('SELECT payload FROM quotes WHERE id=?',(id,)).fetchone();v=json.loads(r[0]);quotes[v['symbol']]=v
 period=str(S.instant(cutoff or snap['created_at']).year);rows=[];total=sum(r['weight_pct'] for r in top)
 for r in top:
  eps=next((v for provider in ('licensed_export','cnyes_factset') if (v:=S.select_estimate(r['symbol'],period,cutoff=cutoff or snap['created_at'],provider=provider)) is not None),None)
  eps_next=next((v for provider in ('licensed_export','cnyes_factset') if (v:=S.select_estimate(r['symbol'],str(int(period)+1),cutoff=cutoff or snap['created_at'],provider=provider)) is not None),None)
  q=quotes.get(r['symbol'],{})
  rows.append({**r,'weight_date':u['effective_date'],'price_date':q.get('market_date'),'close':q.get('close'),'shares':q.get('shares'),'shares_date':q.get('market_date'),'eps_year':period,'eps':eps,'next_year_eps':eps_next,'source_status':'published_forecast_unqualified_basis' if eps else 'missing_forecast','calculation_ready':bool(eps and eps['calculation_ready'])})
 coverage={'universe_names':len(full),'basket_names':len(top),'basket_market_weight_pct':round(total,4),'quote_names':sum(r['close'] is not None for r in rows),'shares_names':sum(r['shares'] is not None for r in rows),'eps_document_names':sum(r['eps'] is not None for r in rows),'eps_document_market_weight_pct':round(sum(r['weight_pct'] for r in rows if r['eps']),4),'calculation_ready_names':sum(r['calculation_ready'] for r in rows),'NTM_names':0}
 coverage['eps_document_basket_weight_pct']=round(coverage['eps_document_market_weight_pct']/total*100,4)
 return {'status':'source_gap','method_version':S.VERSION,'snapshot_id':snap['id'],'saved_at':snap['created_at'],'weight_date':u['effective_date'],'quote_dates':sorted({r['price_date'] for r in rows if r['price_date']}),'coverage':coverage,'rows':rows,'gates':{'valuation_ready':False,'historical_forward_band_ready':False,'reason':'預估EPS覆蓋不足且每股／原始預估時點尚未核對；無5年當時版本。'},'notes':snap['payload']['notes']}

def collect(*,weight_raw=None,article_files=None,source_findings=None):
 S.initialize();raw=weight_raw if weight_raw is not None else download(WEIGHTS);weight_date,rows=P.parse_weights(raw)
 sid=S.source('index_weights','taifex_index_month_end',WEIGHTS,raw,meta={'frequency':'month_end','data_provider':'Taiwan Index Plus','effective_date':weight_date})
 uid=S.universe(sid,weight_date,rows)
 day,quote_rows,refs=P.local_quotes([r['symbol'] for r in rows[:50]])
 payload={'kind':'read_only_official_normalized_extract','market_date':day,'quotes':quote_rows,'parent_snapshots':refs}
 qsid=S.source('prices_and_issued_shares','twse_saved_official', 'local://margin_statistics/sources.sqlite3',S.canonical(payload).encode(),meta={'market_date':day,'parent_raw_sha256':{k:r['sha256'] for k,r in refs.items()},'parent_retrieved_at':{k:r['retrieved_at'] for k,r in refs.items()}})
 qids=S.quote_batch(qsid,quote_rows);eids=[];failures=[];articles=[]
 for file in article_files or []:
  path=Path(file);url='https://news.cnyes.com/news/id/'+path.stem
  try:
   doc=path.read_bytes();pub,estimates=P.parse_cnyes_eps(doc);esid=S.source('analyst_eps_news','cnyes_factset',url,doc,published_at=pub,meta={'parser':'public_news_eps_table_only','collection_scope':'bounded_discovery_not_exhaustive_or_representative'})
   ids=S.estimate_batch(esid,estimates);eids.extend(ids);articles.append({'url':url,'symbol':estimates[0]['symbol'],'published_at':pub,'source_id':esid,'estimate_rows':len(ids)})
  except (ValueError,KeyError,IndexError,TypeError) as e:failures.append({'url':url,'reason':str(e)})
 notes=['包1、2只保存來源與數據，未計算本益比或修改頁面。','權重為期交所月底表，股價與發行股數為同日官方保存值；日期不混同。','公開新聞採有限樣本盤點，不代表已查遍全市場；索引偏向上修消息，不可拿來選股或推論分布。','新聞原始發布日不是本系統當時已知日；所有預估以實際入庫時間為可用時點。','今年與明年分開，未生成NTM；EPS為0或負數也保存，不用缺值充0。','共識基本／稀釋及股數調整口徑缺漏，先列待核對，不進估值。','資料在Cloud工作暫存；尚不代表Drive讀回驗證同步完成。']
 snapshot_id=S.snapshot(uid,qids,eids,notes=notes);report=current_report()
 source_findings=source_findings or []
 inventory={'report':report,'source_findings':source_findings,'articles':articles,'failures':failures,'protocol':json.loads(PROTOCOL.read_text())}
 audit_id=S.audit(inventory)
 out=S.home()/'exports'/snapshot_id;out.mkdir(parents=True,exist_ok=False)
 (out/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2,allow_nan=False))
 columns=['rank','symbol','name','weight_date','weight_pct','price_date','close','shares_date','shares','eps_year','eps_median','next_year_eps_median','forecast_published_at','source_status','calculation_ready']
 with (out/'coverage.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
  for row in report['rows']:
   r={k:row.get(k) for k in columns};r['eps_median']=(row.get('eps') or {}).get('value');r['next_year_eps_median']=(row.get('next_year_eps') or {}).get('value');r['forecast_published_at']=(row.get('eps') or {}).get('published_at');w.writerow(r)
 return {'snapshot_id':snapshot_id,'audit_id':audit_id,'export_directory':str(out),'coverage':report['coverage'],'failures':failures}

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);sub.add_parser('report');c=sub.add_parser('collect');c.add_argument('--weights-file');c.add_argument('--articles-directory');args=p.parse_args()
 if args.command=='report':r=current_report()
 else:r=collect(weight_raw=Path(args.weights_file).read_bytes() if args.weights_file else None,article_files=sorted(Path(args.articles_directory).glob('*.html')) if args.articles_directory else [])
 print(json.dumps(r,ensure_ascii=False))
if __name__=='__main__':main()
