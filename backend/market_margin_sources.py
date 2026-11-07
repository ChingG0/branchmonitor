"""Read the already validated TWSE snapshots shared with margin statistics.
No network, no source writes, and no mixing dates or listed/OTC markets.
"""
import math,sqlite3
from . import margin_statistics as src

def read_saved(dates,*,prices=False):
 dates=sorted(set(dates));out={}
 if not dates or not (src.folder()/'sources.sqlite3').exists():return out
 try:
  with src.connect() as c:
   c.execute('BEGIN')
   for day in dates:
    r=c.execute("SELECT l.snapshot_id,s.retrieved_at,t.loan_amount,t.units_balance,t.short_units FROM latest l JOIN snapshots s ON s.id=l.snapshot_id JOIN totals t ON t.snapshot_id=l.snapshot_id WHERE l.market='TWSE' AND l.kind='credit' AND l.day=?",(day,)).fetchone()
    if not r or r['loan_amount']<=0 or r['units_balance']<=0:continue
    rows=c.execute('SELECT symbol,balance FROM credit WHERE snapshot_id=?',(r['snapshot_id'],)).fetchall()
    balances={p['symbol']:p['balance'] for p in rows}
    if not balances or sum(balances.values())!=r['units_balance']:continue
    value={'margin':{'balances':balances,'total_units':r['units_balance'],'short_units':r['short_units'],'loan_thousand':r['loan_amount']/1000},'credit_snapshot_id':r['snapshot_id'],'credit_retrieved_at':r['retrieved_at']}
    if prices:
     q=c.execute("SELECT snapshot_id FROM latest WHERE market='TWSE' AND kind='quotes' AND day=?",(day,)).fetchone()
     if not q:continue
     quotes={p['symbol']:p['close'] for p in c.execute('SELECT symbol,close FROM quotes WHERE snapshot_id=?',(q[0],)) if p['close'] is not None and math.isfinite(p['close']) and p['close']>0}
     if not quotes:continue
     value.update(prices=quotes,price_snapshot_id=q[0])
    out[day]=value
 except sqlite3.Error:return {}
 return out
