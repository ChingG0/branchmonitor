export type ValueReading={date:string;value:number}
export type FlowReading={date:string;rows:{name:string;buy:number;sell:number;net:number}[]}
export function readingComparison(history:ValueReading[],date:string,value:number|null|undefined){
 const unique=new Map<string,ValueReading>()
 for(const row of history)if(row.date<=date&&Number.isFinite(row.value))unique.set(row.date,{...row})
 if(value!=null&&Number.isFinite(value)&&date)unique.set(date,{date,value})
 const readings=[...unique.values()].sort((a,b)=>a.date.localeCompare(b.date)).slice(-20)
 const previous=readings.filter(r=>r.date<date).at(-1)
 const delta=value!=null&&Number.isFinite(value)&&previous?value-previous.value:null
 return {readings,previous,delta}
}
export function flowReadings(history:FlowReading[],name:string):ValueReading[]{
 return history.flatMap(row=>{const item=row.rows.find(item=>item.name===name);return item&&Number.isFinite(item.net)?[{date:row.date,value:item.net}]:[]})
}
export function flowChange(previous:number|undefined,value:number){
 if(previous===undefined||!Number.isFinite(previous)||!Number.isFinite(value))return '待累積前日資料'
 if(previous===value)return '淨額持平'
 if(previous<0&&value>0)return '由賣轉買'
 if(previous>0&&value<0)return '由買轉賣'
 if(value===0)return '買賣持平'
 if(previous===0)return value>0?'轉為買超':'轉為賣超'
 return value>0?(value>previous?'買超增加':'買超減少'):(value<previous?'賣超增加':'賣超減少')
}

export function quoteComparison(history:ValueReading[],date:string,value:number|null|undefined,sourceChange?:number|null){
 const view=readingComparison(history,date,value)
 if(view.previous&&view.delta!==null&&view.previous.value!==0)return {value:view.delta/Math.abs(view.previous.value)*100,label:'較前值'}
 return {value:sourceChange??null,label:'來源漲跌'}
}
