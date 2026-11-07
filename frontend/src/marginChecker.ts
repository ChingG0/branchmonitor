export type Mode='net'|'replacement'
export type Model={cost:number|null;unknown_pct:number|null;unknown_units:number;unpriced_units:number;cap_unknown_units:number;quantity:number}
export type Point={date:string;close:number|null;balance:number;proxy_price:number|null;reset_reasons:string[];reset_labels:string[];models:Record<Mode,{average:Model;fifo:Model}>}
export type Stock={symbol:string;name:string;market:string;days:number;expected_days:number;first:string|null;last:string|null;latest:Point|null;reset_days:number;price_days:number;mini:(Point|null)[]}
export type Detail=Omit<Stock,'mini'>&{points:(Point|null)[]}
export type Checker={status:string;version:string;as_of:string;start:string;calendar:string[];model_ready:false;stocks:Stock[];detail:Detail|null;cost_definition:string;unknown_definition:string;selection_frozen_at:string;created_at:string}
export function segments(values:(number|null)[],x:(i:number)=>number,y:(v:number)=>number,breaks:boolean[]=[]):string[]{
 const result:string[]=[];let path=''
 values.forEach((v,i)=>{if(v===null||!Number.isFinite(v)){if(path)result.push(path.includes(' L')?path:path+' L'+path.slice(1));path='';return}if(breaks[i]&&path){result.push(path);path=''}path+=(path?' L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)})
 if(path)result.push(path.includes(' L')?path:path+' L'+path.slice(1));return result
}
export function bounds(values:(number|null)[]):[number,number]{const good=values.filter((v):v is number=>v!==null&&Number.isFinite(v));if(!good.length)return [0,1];const lo=Math.min(...good),hi=Math.max(...good),pad=Math.max((hi-lo)*.12,Math.abs(hi)*.015,.01);return [Math.max(0,lo-pad),hi+pad]}
export function gaps(points:(Point|null)[]):{start:number;end:number;count:number}[]{const result:{start:number;end:number;count:number}[]=[];let start=-1;points.forEach((p,i)=>{if(p===null&&start<0)start=i;if(p!==null&&start>=0){result.push({start,end:i-1,count:i-start});start=-1}});if(start>=0)result.push({start,end:points.length-1,count:points.length-start});return result}
export function values(points:(Point|null)[],mode:Mode,field:'close'|'average'|'fifo'|'unknownAverage'|'unknownFifo'):(number|null)[]{return points.map(p=>p===null?null:field==='close'?p.close:field==='average'?p.models[mode].average.cost:field==='fifo'?p.models[mode].fifo.cost:field==='unknownAverage'?p.models[mode].average.unknown_pct:p.models[mode].fifo.unknown_pct)}
export function unknownLabel(value:number|null|undefined):string{return value==null?'無可評估部位':value>10+1e-8?'未知存量 >10%':'未知存量 ≤10%'}
