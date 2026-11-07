export type RateStep={date:string;low:number;high:number;change_bp:number}
export type FedRateData={as_of:string;low:number;high:number;history?:RateStep[];decision_date?:string;observed_through?:string;next_start?:string;next_end?:string;calendar_url?:string;history_url?:string;last_change_bp?:number}
const epoch=(date:string)=>Date.parse(date+'T00:00:00Z')
export function rateWindow(history:RateStep[],through:string,years:number){
 const end=epoch(through),begin=new Date(end);begin.setUTCFullYear(begin.getUTCFullYear()-years)
 const start=begin.toISOString().slice(0,10)
 const all=history.filter(r=>r.date<=through&&Number.isFinite(r.low)&&Number.isFinite(r.high)&&r.low<r.high).sort((a,b)=>a.date.localeCompare(b.date))
 const anchor=all.filter(r=>r.date<=start).at(-1)
 const visible=[...(anchor?[{...anchor,date:start}]:[]),...all.filter(r=>r.date>start)]
 return {start:visible[0]?.date||start,end:through,rows:visible,all}
}
export function stepPath(rows:RateStep[],x:(date:string)=>number,y:(n:number)=>number,end:string,field:'low'|'high'='high'){
 if(!rows.length)return ''
 return `M ${x(rows[0].date)} ${y(rows[0][field])}`+rows.slice(1).map(r=>` H ${x(r.date)} V ${y(r[field])}`).join('')+` H ${x(end)}`
}
export function rateAt(rows:RateStep[],date:string){return rows.filter(r=>r.date<=date).at(-1)}
