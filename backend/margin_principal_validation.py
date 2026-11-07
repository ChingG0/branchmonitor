"""Evaluate the fixed Package 2 gates. Missing evidence cannot pass a gate."""
from . import margin_principal_repair as engine

def grade(day,history,*,source_checks):
 gates={}
 for key,x in day['methods'].items():
  history[key].append(x['relative_error']);d=engine.drift(history[key]);x['drift_60d']=d
  shares=x['scenarios']['1.0']['shares']
  gates[key]={'total':None if x['relative_error'] is None else abs(x['relative_error'])<=.03+1e-12,
   'warmup':x['warmup_names']==0,'drift':None if d is None else abs(d)<=.01+1e-12,
   'price_identified':x['unpriced_units']<=engine.EPS and shares is not None and shares[5]<=engine.EPS,
   'cap_rule_verified':x['cap_unknown_names']==0,'disclosure_continuity':x['unreported_inventory_names']==0}
 numeric=all(gates[k][g] is True for k in ('net_average','net_fifo') for g in ('total','warmup','drift')) and day['raw_sensitivity_tier'] in ('point','interval')
 sources=bool(source_checks) and all(v is True for v in source_checks.values())
 eligible=sources and all(all(v is True for v in gates[k].values()) for k in ('net_average','net_fifo'))
 day.update(quantitative_gates=gates,two_method_numeric_pass=numeric,accepted=numeric and eligible,source_eligible=sources,source_checks=dict(source_checks))
 return day
