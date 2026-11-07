"""Package 2 diagnostic reconstruction. No strategy writes, no debt rescaling."""
from dataclasses import dataclass
from collections import deque
from math import isfinite
EPS=1e-7
VERSION='margin-principal-diagnostic-1'
@dataclass
class Lot:
 quantity:float
 unit_debt:float|None
 unknown:bool
 cap_unknown:bool
class Book:
 def __init__(self,method):
  if method not in ['average','fifo']:raise ValueError('unknown method')
  self.method=method;self.lots=deque();self.q=0.;self.known=0.;self.unknown_cost=0.;self.unknown_q=0.;self.unpriced_q=0.;self.cap_q=0.
 def add(self,q,unit_debt,unknown=False,cap_unknown=False):
  if q<0 or unit_debt is not None and (not isfinite(unit_debt) or unit_debt<0):raise ValueError('invalid lot')
  if q<=EPS:return
  if self.method=='fifo':self.lots.append(Lot(q,unit_debt,unknown,cap_unknown))
  self.q+=q
  if unit_debt is None:self.unpriced_q+=q
  elif unknown:self.unknown_cost+=q*unit_debt
  else:self.known+=q*unit_debt
  if unknown:self.unknown_q+=q
  if cap_unknown:self.cap_q+=q
 def remove(self,q):
  if q<-EPS or q>self.q+EPS:raise ValueError('repayment exceeds inventory')
  q=min(q,self.q)
  if q<=EPS:return
  if self.method=='average':
   remain=(self.q-q)/self.q
   for k in ['known','unknown_cost','unknown_q','unpriced_q','cap_q']:setattr(self,k,getattr(self,k)*remain)
   self.q-=q
  else:
   left=q
   while left>EPS:
    lot=self.lots[0];take=min(left,lot.quantity);left-=take;lot.quantity-=take;self.q-=take
    if lot.unit_debt is None:self.unpriced_q-=take
    elif lot.unknown:self.unknown_cost-=take*lot.unit_debt
    else:self.known-=take*lot.unit_debt
    if lot.unknown:self.unknown_q-=take
    if lot.cap_unknown:self.cap_q-=take
    if lot.quantity<=EPS:self.lots.popleft()
  if self.q<=EPS:
   self.q=self.known=self.unknown_cost=self.unknown_q=self.unpriced_q=self.cap_q=0.;self.lots.clear()
 def principal(self,seed_factor=1.):
  return None if self.unpriced_q>EPS else max(0.,self.known+self.unknown_cost*seed_factor)
 def summary(self):
  return {'quantity':self.q,'principal':self.principal(),'known_proxy':max(0.,self.known),'unknown_principal_proxy':max(0.,self.unknown_cost),'unknown_units':max(0.,self.unknown_q),'unknown_fraction':max(0.,min(1.,self.unknown_q/self.q)) if self.q>EPS else 0.,'unpriced_units':max(0.,self.unpriced_q),'cap_unknown_units':max(0.,self.cap_q)}
def flows(previous,buy,sell,repay,balance,mode='net'):
 if any(x<0 for x in [previous,buy,sell,repay,balance]) or previous+buy-sell-repay!=balance:raise ValueError('unreconciled official flows')
 if mode=='net':return max(previous-balance,0),max(balance-previous,0)
 if mode=='replacement':
  removed=min(previous,sell+repay);return removed,balance-(previous-removed)
 raise ValueError('invalid turnover mode')
def price_proxy(row,seed=False):
 volume,amount=row.get('volume'),row.get('turnover');p=amount/volume if volume and amount is not None and amount>0 else None
 if p is not None and isfinite(p) and p>0:
  lo,hi=row.get('low'),row.get('high')
  if (not lo or p>=lo*.99) and (not hi or p<=hi*1.01):return p,'market_vwap'
 if seed and row.get('close') and row['close']>0:return row['close'],'seed_close'
 return None,'unpriced'
def bucket(maintenance):
 if maintenance<1.3:return 0
 distance=1-1.3/maintenance
 for i,limit in enumerate([.07,.13,.22],1):
  if distance<=limit+1e-12:return i
 return 4
def distribution_distance(a,b):return .5*sum(abs(x-y) for x,y in zip(a,b))
def sensitivity_tier(distance,low_difference):
 if distance<=.05+1e-12 and low_difference<=.01+1e-12:return 'point'
 if distance<=.10+1e-12 and low_difference<=.03+1e-12:return 'interval'
 return 'suppress'
def drift(errors):
 if len(errors)<60 or any(x is None for x in errors[-60:]):return None
 w=errors[-60:];return sum(w[-20:])/20-sum(w[:20])/20

def reconstruct(rows,events=(),methods=('net_average','net_fifo','replacement_average','replacement_fifo')):
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
   cap_unknown=old_index!=index-1 or any(x.isdigit() or x in 'O!$' for x in prior_notes.get(symbol,''))
   resets=bool(reasons);daily={'symbol':symbol,'name':row['name'],'date':day,'close':close,'quantity':balance,'price_proxy':p,'proxy_kind':proxy_kind,'reset_reasons':reasons,'models':{}}
   if resets and previous>0:
    for reason in reasons:day_resets[reason]=day_resets.get(reason,0)+1
   for key in methods:
    mode,method=key.split('_');book=state[key].get(symbol)
    if book is None or resets:
     book=Book(method);book.add(previous,None if seed_p is None else seed_p*1000*.6,unknown=True,cap_unknown=True);state[key][symbol]=book
    removed,added=flows(previous,row['buy'],row['sell'],row['repay'],balance,mode);book.remove(removed);book.add(added,None if p is None else p*1000*.6,cap_unknown=cap_unknown)
    if abs(book.q-balance)>EPS:raise AssertionError('inventory conservation failed')
    info=book.summary();daily['models'][key]=info;total=sums[key];total['known_principal']+=info['known_proxy'];total['unknown_principal_proxy']+=info['unknown_principal_proxy'];total['unpriced_units']+=info['unpriced_units']
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
   base=total['scenarios']['1.0']['principal'];total['principal']=base if total['unpriced_units']<=EPS else None;total['relative_error']=base/official-1 if official and total['principal'] is not None else None;total['unknown_principal_proxy_share']=total['unknown_principal_proxy']/base if base>0 else None
   for scenario in total['scenarios'].values():
    value=scenario['principal'];scenario['shares']=[x/value for x in scenario.pop('bins')] if value>0 else None
    if total['unpriced_units']>EPS:scenario['principal']=None;scenario['shares']=None
  a=sums['net_average']['scenarios']['1.0']['shares'];b=sums['net_fifo']['scenarios']['1.0']['shares'];distance=distribution_distance(a,b) if a and b else None;low=abs(a[0]-b[0]) if a and b else None
  yield {'date':day,'source_missing':False,'official':official,'methods':sums,'reset_counts':day_resets,'distribution_D':distance,'below130_difference':low,'raw_sensitivity_tier':sensitivity_tier(distance,low) if distance is not None else 'unverified','securities':securities}
