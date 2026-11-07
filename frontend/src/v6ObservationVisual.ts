import type {HistoricalIndicator,HistoricalPoint} from './v6HistoryTypes'

type Reading={date:string;value:number}
export function numericReading(value:number|string):number|null{
 if(typeof value!=='number'&&typeof value!=='string')return null
 if(typeof value==='string'&&!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim()))return null
 const number=Number(value)
 return Number.isFinite(number)?number:null
}
function sameMetric(a:HistoricalIndicator,b:HistoricalIndicator){
 return a.category===b.category&&a.label===b.label&&a.unit===b.unit&&a.source===b.source
}
export function observationVisual(item:HistoricalIndicator,points:HistoricalPoint[],current:HistoricalPoint){
 const value=numericReading(item.value)
 const series=new Map<string,Reading>()
 // Compare only the selected date's available history; repeated source dates are not new observations.
 const eligible=points.filter(p=>p.date<=current.date).sort((a,b)=>a.date.localeCompare(b.date))
 for(const point of eligible){
  const row=point.indicators.find(row=>sameMetric(item,row))
  if(!row||row.as_of>item.as_of||(row.available_at&&row.available_at>current.decision_at))continue
  const n=numericReading(row.value)
  if(n!==null)series.set(row.as_of,{date:row.as_of,value:n})
 }
 const readings=[...series.values()].sort((a,b)=>a.date.localeCompare(b.date)).slice(-20)
 const prior=readings.filter(row=>row.date<item.as_of).at(-1)
 const previous=eligible.filter(p=>p.date<current.date).at(-1)?.indicators.find(row=>sameMetric(item,row))
 const reused=!!previous&&previous.as_of===item.as_of&&numericReading(previous.value)===value
 const min=readings.length?Math.min(...readings.map(row=>row.value)):null
 const max=readings.length?Math.max(...readings.map(row=>row.value)):null
 const position=value!==null&&min!==null&&max!==null&&max>min?Math.max(0,Math.min(100,(value-min)/(max-min)*100)):null
 const delta=value!==null&&prior?value-prior.value:null
 return {readings,prior,value,min,max,position,delta,reused}
}
export function scoreDirection(score:number|null,prior:number|null){
 if(score===null||prior===null||!Number.isFinite(score)||!Number.isFinite(prior))return {delta:null,tone:'neutral',label:'尚無前日分數可比較'}
 const delta=score-prior
 return {delta,tone:delta<0?'easing':delta>0?'rising':'neutral',label:delta<0?'← 往較低風險端移動':delta>0?'往較高風險端移動 →':'模型警戒位置持平'}
}
