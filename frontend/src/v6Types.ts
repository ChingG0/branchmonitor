export type V6Trade={signal_date:string,entry_date:string,exit_date:string,entry:number,exit:number,contract:string,points:number,return_pct:number,reason:string,held:number,signal_score:number,signal_drawdown_pct?:number,pnl_twd:number}
export type V6Observation={threshold:number,signal:boolean|null,entry_allowed:boolean|null,position:number,wins:number,completed:number,hit_rate:number|null,pnl_points:number,portfolio_return_pct:number,curve:{date:string,value:number|null,position?:number,pnl_points?:number}[]}
export type V6RiskSample={count:number,declines:number,rate_pct:number|null}
export type V6RiskHorizon={key:string,label:string,definition:string,value:number|null,baseline:V6RiskSample,at_least65:V6RiskSample,at_least70:V6RiskSample,elevated:boolean}
export type V6Model={key:'v6',strategy_key?:'v6-2',simulation_kind?:'historical_replay',generated_at?:string,cooldown_days?:number,cooldown_remaining?:number,drawdown_cap_pct?:number,max_drawdown_points?:number,profit_factor?:number|null,version:string,label:string,status:string,error?:string,validation_status:string,
 as_of?:string,decision_at?:string,readiness?:'ready'|'waiting'|'missing',missing_sources?:string[],missing_tx_dates?:string[],
 score?:number|null,score_threshold?:number,signal?:boolean|null,entry_allowed?:boolean|null,next_action?:string,position?:number,
 stop?:number|null,take?:number|null,target_contract?:string,index_close?:number,period?:string,
 portfolio_return_pct:number|null,pnl_points?:number,pnl_twd_per_contract?:number,max_drawdown_pct?:number,
 wins?:number,completed?:number,hit_rate?:number|null,curve:{date:string,value:number|null,position?:number,pnl_points?:number}[],
 trades?:V6Trade[],observation70?:V6Observation,basis?:string,warning?:string,
 outlook?:{horizons:V6RiskHorizon[],readiness:string,note:string},
 feature_snapshot?:Record<string,{value:number,as_of:string,available_at?:string}>}
