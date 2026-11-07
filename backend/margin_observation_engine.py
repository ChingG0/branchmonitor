"""Observation-only cost continuation. Never substitutes for trading qualification.
The replay is a frozen observational fork of repair-3; official inputs stay intact.
"""
from .margin_principal_repair import EPS,flows,price_proxy,bucket,distribution_distance,sensitivity_tier,margin_rate,Book as StrictBook
VERSION='margin-observation-cost-1'
class Book(StrictBook):
 def __init__(self,method):
  super().__init__(method);self.reference=None;self.cost_filled=False
 def prepare(self,current_reference):
  priced_quantity=self.q-self.unpriced_q
  prior=(self.known+self.unknown_cost)/priced_quantity if priced_quantity>EPS else self.reference
  self.reference=prior if prior is not None and prior>0 else current_reference
  if self.unpriced_q>EPS and self.reference is not None and self.reference>0:
   # First usable cost reference is an explicit provisional initialization.
   q=self.unpriced_q;self.quarantine()
   if self.method=='fifo':
    for lot in self.lots:
     if lot.unit_debt is None:lot.unit_debt=self.reference
   self.unknown_cost+=q*self.reference;self.unpriced_q=0.;self.cost_filled=True
 def add(self,q,unit_debt,unknown=False,cap_unknown=False):
  if unit_debt is None and q>EPS and self.reference is not None and self.reference>0:
   unit_debt=self.reference;unknown=True;cap_unknown=True;self.cost_filled=True
  super().add(q,unit_debt,unknown,cap_unknown)
 def remove(self,q):
  super().remove(q)
  if self.q<=EPS:self.cost_filled=False
 def summary(self):return {**super().summary(),'cost_filled':self.cost_filled}

def reconstruct(rows,events=(),methods=('net_average','net_fifo','replacement_average','replacement_fifo'),*,market='TPEX',cap_mode='source'):
 """Iterate a single market's consecutive calendar days; yield provisional diagnostics."""
 state={k:{} for k in methods};last={};prior_notes={};prior_close={};prior_shares={};event_set=set(events)
 for index,day,day_rows,official in rows:
  if day_rows is None:
   yield {'date':day,'source_missing':True,'official':official,'methods':{},'securities':[]};continue
  sums={k:{'known_principal':0.,'unknown_principal_proxy':0.,'unpriced_units':0.,'warmup_names':0,'cap_unknown_names':0,'active_names':0,'unknown_principal_identifiable':False,'scenarios':{str(f):{'principal':0.,'bins':[0.]*6} for f in [.5,1.,2.]}} for k in methods};securities=[];day_resets={};active_symbols=set()
  for row in day_rows:
   symbol=row['symbol'];previous=row['previous'];balance=row['balance'];p,proxy_kind=price_proxy(row);seed_p,seed_kind=price_proxy(row,True);reasons=[];old_index=last.get(symbol)
   if old_index is None:reasons.append('initial_inventory')
   elif old_index!=index-1:reasons.append('disclosure_or_market_gap')
   elif any(abs(state[k][symbol].q-previous)>EPS for k in methods):reasons.append('previous_balance_difference')
   if (day,symbol) in event_set:reasons.append('unsafe_corporate_event')
   pc=prior_close.get(symbol);ps=prior_shares.get(symbol);close=row.get('close');shares=row.get('shares')
   if pc and close and abs(close/pc-1)>.25:reasons.append('large_price_discontinuity')
   if ps and shares and not .8<=shares/ps<=1.25:reasons.append('large_share_count_change')
   continuous=old_index==index-1
   if cap_mode=='fixed60':
    rate={'ratio':.6,'verified':continuous and not any(x.isdigit() or x in 'O!$' for x in prior_notes.get(symbol,'')),'prohibited':False,'reason':'原版固定六成對照'}
   elif cap_mode=='source':rate=margin_rate(market,prior_notes.get(symbol) if continuous else None)
   else:raise ValueError('unknown cap mode')
   cap_unknown=not rate['verified']
   resets=bool(reasons);daily={'symbol':symbol,'name':row['name'],'date':day,'close':close,'quantity':balance,'price_proxy':p,'proxy_kind':proxy_kind,'reset_reasons':reasons,'margin_rate':rate,'models':{}}
   if resets and previous>0:
    for reason in reasons:day_resets[reason]=day_resets.get(reason,0)+1
   for key in methods:
    mode,method=key.split('_');book=state[key].get(symbol)
    if book is None:
     book=Book(method);book.prepare(None if seed_p is None else seed_p*1000*.6);book.add(previous,None if seed_p is None else seed_p*1000*.6,unknown=True,cap_unknown=True);state[key][symbol]=book
    else:
     book.prepare(None if seed_p is None else seed_p*1000*.6)
    if resets and old_index is not None:
     # A quote, revised share count or corporate-event marker is not a cash flow.
     # Keep the old debt proxy, quarantine its identification, and never fill an
     # unexplained inventory increase with today's market price.
     book.quarantine()
     if book.q>previous:book.remove(book.q-previous)
     elif book.q<previous:book.add(previous-book.q,None,unknown=True,cap_unknown=True)
    removed,added=flows(previous,row['buy'],row['sell'],row['repay'],balance,mode);book.remove(removed);book.add(added,None if p is None or rate['prohibited'] else p*1000*rate['ratio'],cap_unknown=cap_unknown or rate['prohibited'])
    if abs(book.q-balance)>EPS:raise AssertionError('inventory conservation failed')
    info=book.summary();info.update(added_units=added,removed_units=removed,prohibited_added_units=added if rate['prohibited'] else 0);daily['models'][key]=info;total=sums[key];total['known_principal']+=info['known_proxy'];total['unknown_principal_proxy']+=info['unknown_principal_proxy'];total['unpriced_units']+=info['unpriced_units']
    if balance>0:
     total['active_names']+=1;total['warmup_names']+=info['unknown_fraction']>.1+1e-12;total['cap_unknown_names']+=info['cap_unknown_units']>EPS
    for factor in [.5,1.,2.]:
     debt=book.principal(factor);scenario=total['scenarios'][str(factor)]
     if debt is None:continue
     scenario['principal']+=debt
     if debt>0:scenario['bins'][bucket(balance*1000*close/debt) if close and close>0 else 5]+=debt
   if balance>0:active_symbols.add(symbol)
   last[symbol]=index;prior_notes[symbol]=row['notes'];prior_close[symbol]=close;prior_shares[symbol]=shares;securities.append(daily)
  for key,total in sums.items():
   total['unreported_inventory_names']=sum(book.q>EPS and symbol not in active_symbols and last.get(symbol)!=index for symbol,book in state[key].items())
   base=total['scenarios']['1.0']['principal'];total['principal']=base if total['unpriced_units']<=EPS and total['unreported_inventory_names']==0 else None;total['relative_error']=base/official-1 if official and total['principal'] is not None else None;proxy_subtotal=total['known_principal']+total['unknown_principal_proxy'];total['unknown_principal_proxy_share']=total['unknown_principal_proxy']/proxy_subtotal if proxy_subtotal>0 else None
   for scenario in total['scenarios'].values():
    value=scenario['principal'];scenario['shares']=[x/value for x in scenario.pop('bins')] if value>0 else None
    if total['unpriced_units']>EPS or total['unreported_inventory_names']>0:scenario['principal']=None;scenario['shares']=None
  a=sums['net_average']['scenarios']['1.0']['shares'];b=sums['net_fifo']['scenarios']['1.0']['shares'];a=a if a and a[5]<=EPS else None;b=b if b and b[5]<=EPS else None;distance=distribution_distance(a,b) if a and b else None;low=abs(a[0]-b[0]) if a and b else None
  yield {'date':day,'source_missing':False,'official':official,'methods':sums,'reset_counts':day_resets,'distribution_D':distance,'below130_difference':low,'raw_sensitivity_tier':sensitivity_tier(distance,low) if distance is not None else 'unverified','securities':securities}

