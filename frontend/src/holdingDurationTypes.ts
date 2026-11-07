export type HoldingDurationGroup={samples:number,avg_days:number|null}
export type HoldingDurationStats={
 as_of:string|null,win:HoldingDurationGroup,loss:HoldingDurationGroup,
 completed_count:number,flat_count:number,unavailable_count:number,method:string
}
