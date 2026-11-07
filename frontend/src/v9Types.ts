export type V9CurvePoint={date:string,value:number|null,pnl_points?:number,position?:number,contract?:string,action?:string}
export type V9Year={year:number,from:string,to:string,model_pct:number,hold_tx_pct:number,taiex_pct:number,edge_vs_index_pp:number,edge_vs_hold_tx_pp:number}
export type V9Activity={date:string,signal_date:string,action:string,contract:string,open:number}
export type V9Model={key:'v9',version:string,label:string,description:string,status:string,error?:string,
 as_of?:string,signal_date?:string,position?:number,next_action?:string,target_contract?:string,index_close?:number,
 portfolio_return_pct:number|null,period?:string,curve:V9CurvePoint[],index_curve?:V9CurvePoint[],hold_tx_curve?:V9CurvePoint[],
 taiex_pct?:number,hold_tx_pct?:number,pnl_points?:number,pnl_twd_per_contract?:number,max_drawdown_pct?:number,
 years:V9Year[],activity?:V9Activity[],missing_dates?:string[],basis?:string,validation?:string,
 cost_points_per_side?:number,point_value_twd?:number}
