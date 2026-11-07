export type WeatherRecord = {id:string; prepared_at:string; market_as_of:string; stage:string}

// Direction labels are not absolute severity levels; never invent a level for them.
export function stageStyle(stage:string) {
 const levels:Record<string,{radius:number;color:string;level:number|null}> = {
  '平穩':{radius:48,color:'#548674',level:1},
  '形成':{radius:72,color:'#b68a38',level:2},
  '高風險':{radius:96,color:'#bd6257',level:3},
 }
 return levels[stage] || {radius:62,color:stage==='資料不足'?'#929b96':'#8976a2',level:null}
}

export function priorReports(current:WeatherRecord, history:WeatherRecord[]) {
 return history.filter(row=>row.id!==current.id && Date.parse(row.prepared_at)<Date.parse(current.prepared_at))
  .sort((a,b)=>Date.parse(a.prepared_at)-Date.parse(b.prepared_at))
}

export function compareReports(current:WeatherRecord, previous?:WeatherRecord) {
 if(!previous) return {label:'首份預報',note:'尚無較早紀錄可比較。'}
 const sameSession=current.market_as_of===previous.market_as_of
 const scope=sameSession?'同一行情日的研究修訂，不能當成跨日風險走勢。':'比較兩份人工預報的警戒等級，並非風險百分比的變化。'
 const currentLevel=stageStyle(current.stage).level, previousLevel=stageStyle(previous.stage).level
 if(current.stage==='資料不足') return {label:'尚無法比較',note:scope+' 本次資料不足，不代表風險降低。'}
 if(previous.stage==='資料不足') return {label:'補充資料後重新評估',note:scope+' 前份缺資料不能視為零風險。'}
 if(current.stage===previous.stage) return {label:'警戒等級持平',note:scope+' 等級相同不代表所有指標都沒有變化。'}
 if(currentLevel!==null && previousLevel!==null) return {label:currentLevel>previousLevel?'警戒升高':'警戒降低',note:scope}
 return {label:'預報階段改變',note:scope+' 增強／減弱是方向描述，未換算成警戒級距。'}
}

// Wilson score interval, z=1.959963984540054 (two-sided 95%).
export function sampleStats(sample:{count:number;declines:number}) {
 const n=sample.count,k=sample.declines
 if(!Number.isInteger(n)||!Number.isInteger(k)||n<=0||k<0||k>n)return null
 const z=1.959963984540054,p=k/n,z2=z*z,denom=1+z2/n
 const center=(p+z2/(2*n))/denom
 const margin=z*Math.sqrt(p*(1-p)/n+z2/(4*n*n))/denom
 return {rate:p*100,low:Math.max(0,(center-margin)*100),high:Math.min(100,(center+margin)*100)}
}
