export interface Flow extends StockWin { date:string; symbol:string; name:string; buy:number; sell:number; net:number; close:number|null; net_value:number|null; volume_share:number|null; source:string }
export interface Position extends StockWin { symbol:string; name:string; quantity:number; avg_cost:number|null; close:number|null; value:number|null; pnl:number|null; return_pct:number|null; holding_days:number|null; oldest_days:number|null; first_date:string }
export interface Closed { date:string; symbol:string; name:string; quantity:number; matched:number; unmatched:number; price:number|null; pnl:number|null; return_pct:number|null; holding_days:number|null; complete:boolean }
export interface Signal { date:string; symbol:string; name:string; net:number; maturity:string|null; status:string; return_pct:number|null; excess:number|null; hit:boolean|null }
export interface Watch { symbol:string; name:string; net:number; close:number|null; recent_net:number; streak:number; volume_share:number|null; hit_rate:number|null; samples:number; history_verified:boolean }
export interface Branch { broker:string; branch:string; name:string }
export interface SyncStage {label:string;done:number;total:number|null}
export interface SyncCurrent {kind?:string;branch?:string;date?:string;symbol?:string;name?:string;start?:string;end?:string;index?:number;branch_total?:number}
export interface SyncProgress {version:number;planned:boolean;phase:string;phase_label:string;rate_per_min:number|null;eta_seconds:number|null;current:SyncCurrent|null;stages:Record<string,SyncStage>}
export interface Job { id:string; started_at:string; finished_at:string|null; status:string; done:number; total:number; message:string; errors:string[];progress?:SyncProgress }
export interface Settings { schedule_enabled:boolean; schedule_time:string; lookback_days:number; history_start_date?:string|null }
export interface Quote { date:string; close:number; open:number|null; high:number|null; low:number|null }
export interface Detail { symbol:string; name:string; flows:Flow[]; prices:Quote[]; position:Position|null; signals:Signal[]; cycles:Cycle[]; stock_stats:StockStats|null; source:string }
export interface Dashboard {
 streak_buys:BuyPattern[];fresh_buys:BuyPattern[];
 buy_pattern_status:{calendar_ready:boolean;prior_sessions:number;unverified_candidates:number};
 major_balances?:Record<string,{buy:number;sell:number;net:number;branches:number;start:string;end:string}>;
 branch:Branch; branches:Branch[]; as_of:string|null; first_date:string|null; available_dates:string[];
 settings:Settings; job:Job|null; dates:string[];
 cycles:Cycle[]; stock_stats:StockStats[]; cycle_stats:CycleStats;
 daily:Flow[]; positions:Position[]; closed:Closed[]; signals:Signal[]; watchlist:Watch[];
 series:{date:string;net:number;cumulative_net:number;pnl:number|null;return_pct:number|null;realized_pnl:number;unrealized_pnl:number|null;unrealized_complete:boolean}[];
 stats:{ positions:number; invested:number|null; realized:number|null; unrealized:number|null; pnl:number|null; return_pct:number|null; known_pnl:number; market_value:number; unpriced_positions:number; unmatched_lots:number; win_rate:number|null; wins:number; closed_count:number; unscored_events:number };
 hit_stats:{rate:number|null;wins:number;scored:number;pending:number;missing:number;total:number;window_start:string;window_end:string;stocks:number;calendar_incomplete?:boolean};
 quality:{candidate_count:number;history_count:number;flow_count:number;failed_market_days:number;failed_snapshot_days:number;discovery_partial:boolean;snapshots:{date:string;status:string;count:number;message:string;fetched_at:string}[]};
}

export interface StockWin {win_rate:number|null;wins:number;closed_count:number}
export interface BuyPattern {
 date:string;symbol:string;name:string;buy:number;sell:number;net:number;close:number|null;buy_value:number|null;
 streak?:number;start_date?:string;streak_net?:number;streak_value?:number|null;streak_at_least?:boolean;
 quiet_start?:string;quiet_end?:string;quiet_days?:number;
}
export interface Cycle {
 symbol:string;name:string;start_date:string;end_date:string|null;as_of:string;status:'open'|'closed';
 bought_lots:number;sold_lots:number;quantity:number;cost:number|null;realized_pnl:number|null;
 unrealized_pnl:number|null;pnl:number|null;return_pct:number|null;holding_days:number|null;
 complete:boolean;history_verified:boolean;unmatched_lots:number;reason:string;
}
export interface StockStats extends StockWin {
 symbol:string;name:string;losses:number;breakeven:number;total_closed:number;unscored_count:number;
 closed_pnl:number|null;quantity:number;holding_days:number|null;unrealized_pnl:number|null;
 hit_rate:number|null;hit_samples:number;history_verified:boolean;
}
export interface CycleStats extends StockWin {unscored_count:number;open_count:number}

export interface MajorStock {
 major_balance?:{buy:number;sell:number;net:number;branches:number;start:string;end:string}|null;
 top_broker_names?:string[];
 symbol:string;name:string;buy:number;sell:number;net:number;close:number|null;market?:string|null;
 change?:number;change_pct?:number;net_value?:number|null;buy_branches?:number;sell_branches?:number;
 contributors?:{broker:string;branch:string;name:string;buy:number;sell:number;net:number;history_verified:boolean}[];
}
export interface MainWatchReport {
 as_of:string|null;available_dates:string[];
 market:{available:boolean;latest_date:string|null;buy_top:MajorStock[];sell_top:MajorStock[];buy_value_top:MajorStock[];sell_value_top:MajorStock[];fetched_at:string|null;definition_url:string;
 sources:{label:string;url:string;rows:number}[];job:{status:string;message:string;started_at:string;finished_at:string|null}|null};
 watched:{buy_top:MajorStock[];sell_top:MajorStock[];buy_value_top:MajorStock[];sell_value_top:MajorStock[];
 totals:{buy:number|null;sell:number|null;net:number|null;net_value:number|null;stocks:number;buy_stocks:number;sell_stocks:number;ready_branches:number;observed_branches:number;total_branches:number;unpriced_stocks:number};
 branches:(Branch&{observed:boolean;status:string;buy:number|null;sell:number|null;net:number|null;stocks:number;verified:number;candidates:number})[]};
}
