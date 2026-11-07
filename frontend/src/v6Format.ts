const numberText=(value:number)=>value.toLocaleString('zh-TW',{maximumFractionDigits:2})
export function formatObservation(value:number|string|null|undefined,unit='',label=''):string {
 if(value==null||(typeof value==='number'&&!Number.isFinite(value)))return '—'
 const raw=typeof value==='string'?value.replaceAll(',','').trim():value
 if(raw===''||!Number.isFinite(Number(raw)))return `${value}${unit?' '+unit:''}`
 let n=Number(raw),u=unit.trim()
 if(['百分比','百分率','％','percent','pct'].includes(u.toLowerCase()))u='%'
 if(label==='美債殖利率代理'&&!u)u='%'
 const money=u.match(/^(百萬|千萬|千|萬|億)?(美元|新台幣|台幣|元)$/)
 if(money){
  const scales:Record<string,number>={'百萬':1e6,'千萬':1e7,'千':1e3,'萬':1e4,'億':1e8}
  n*=scales[money[1]||'']||1;u=money[2]
  if(Math.abs(n)>=1e8)return `${numberText(n/1e8)} 億${u}`
  if(Math.abs(n)>=1e4)return `${numberText(n/1e4)} 萬${u}`
 }
 if(['口','張','股','交易單位'].includes(u)){
  if(Math.abs(n)>=1e8)return `${numberText(n/1e8)} 億${u}`
  if(Math.abs(n)>=1e4)return `${numberText(n/1e4)} 萬${u}`
 }
 // Percentage-point changes stay percentage points, not relative % changes.
 return `${numberText(n)}${u==='%'||u==='/100'?u:u?' '+u:''}`
}
export function formatMoney(value:number|null|undefined):string {
 return formatObservation(value,'元')
}
export function formatRiskText(text:string):string {
 return text.replace(/(-?\d[\d,]*(?:\.\d+)?)\s*(百萬|千萬|萬|億|千)?\s*(美元|新台幣|台幣|元)/g,
  (_,number,scale,currency)=>formatObservation(number,(scale||'')+currency))
  .replace(/(-?\d+(?:\.\d+)?)\s*(?:百分比|百分率|％)/g,'$1%')
}

export function formatObservationChange(delta:number,unit='',label=''):string {
 const normalized=unit.trim().toLowerCase()
 const suffix=['%','％','percent','pct','百分比','百分率'].includes(normalized)||(label==='美債殖利率代理'&&!unit)?'個百分點':unit==='/100'?'分':unit
 return `${delta>0?'+':''}${formatObservation(delta,suffix,label)}`
}
