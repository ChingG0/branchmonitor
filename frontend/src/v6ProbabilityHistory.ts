import type {ResearchData} from './v6ConditionalResearch'

export type ProbabilityDay={date:string;horizon:number;status:'saved'|'missing';savedAt:string|null;informationCutoff?:string;
 up:number|null;down:number|null;upPct:number|null;downPct:number|null;deltaUpPp:number|null;deltaDownPp:number|null;previousValidDate?:string|null;
 targetDate?:string;originalTargetDate?:string;anchorDate?:string;anchorClose?:number;note:string;
 outcome?:{up:boolean;close:number;availableAt:string;gradedAt?:string}|null;
 marketAttempt?:{savedAt:string;withheld:boolean;note:string}|null}
export type NightScenario={version:string;kind:string;isForecast:false;addsProspectiveSamples:false;validatedForThisTime:false;anchorDate:string;anchorClose:number;savedAt:string;assumption:string;foreignNote:string;contextSha256:string;night:{contract:string;price:number;high:number;low:number;day_close:number;quote_at:string};targets:{horizon:number;targetDate:string;up:number;down:number;upPct:number;downPct:number}[]}
export type PointEstimate={version:string;role:'research_only';generatedAt:string;scenarioSavedAt:string;anchorClose:number;isOriginalIssueTimeEstimate:false;note:string;targets:{horizon:number;targetDate:string;medianClose:number;medianChangePoints:number;medianReturnPct:number;low80Close:number;high80Close:number;low80ChangePoints:number;high80ChangePoints:number;historicalMaePct:number;historicalCoverage80Pct:number}[]}
export type ScenarioSnapshot={id:string;savedAt:string;quoteAt:string;nightPrice:number;targets:NightScenario['targets'];pointEstimate?:PointEstimate|null}
export type ScenarioGroup={rootId:string;anchorDate:string;anchorClose:number;assumption:string;snapshots:ScenarioSnapshot[];outcomes:Record<string,NonNullable<ProbabilityDay['outcome']>&{targetDate:string;gradedAt:string}>}
export type ScenarioHistory={version:string;groups:ScenarioGroup[];note:string}
export type History={version:string;kind:'actual_saved_forecasts';status:string;withheld:boolean;note:string;generatedAt:string;
 scenarioHistory?:ScenarioHistory;startedAt?:string;updatedAt?:string;lastProbabilitySavedAt?:string|null;lastMarketUpdate?:{savedAt:string;marketAsOf:string;liveCapturedAt?:string|null}|null;
 series:{horizon:number;rows:ProbabilityDay[];savedCount:number}[];liveMaturedByHorizon?:number[];
 upcoming?:{anchorDate:string;marketSavedAt:string;note:string;scenario?:NightScenario|null;targets:{horizon:number;targetDate:string|null;status:'saved'|'pending'|'calendar_missing';upPct:number|null;downPct:number|null;probabilitySavedAt:string|null}[]}|null}

export function validateHistory(value:unknown):History{
 const data=value as History
 if(!data||data.version!=='v6-daily-probability-history-1.0.0'||data.kind!=='actual_saved_forecasts'||!Array.isArray(data.series))throw Error('每日走勢版本不符。')
 if(data.upcoming){
  const u=data.upcoming
  if(u.anchorDate!==data.lastMarketUpdate?.marketAsOf||u.targets.length!==3)throw Error('未來三日錨點或期限不符。')
  if(u.scenario){
   const s=u.scenario
   if(s.version!=='v6-night-close-scenario-1.0.0'||s.kind!=='night_close_what_if'||s.anchorDate!==u.anchorDate||s.isForecast!==false||s.addsProspectiveSamples!==false||s.validatedForThisTime!==false||!s.assumption||!Number.isFinite(Date.parse(s.savedAt))||s.targets.length!==3)throw Error('夜盤情境版本或口徑不符。')
   for(const [i,t] of s.targets.entries())if(t.horizon!==i+1||t.targetDate!==u.targets[i]?.targetDate||![t.up,t.down,t.upPct,t.downPct].every(Number.isFinite)||t.up<.01||t.up>.99||t.down<.01||t.down>.99||Math.abs(t.up+t.down-1)>1e-10||Math.abs(t.upPct+t.downPct-100)>1e-8)throw Error('夜盤情境機率或日期不符。')
  }
  let previous=u.anchorDate
  for(const [i,t] of u.targets.entries()){
   if(t.horizon!==i+1)throw Error('未來三日期限未排序。')
   if(t.targetDate){if(t.targetDate<=previous)throw Error('未來三日必須晚於錨點且依序排列。');previous=t.targetDate}
   if(t.status==='saved'){
    if(!t.targetDate||!t.probabilitySavedAt||t.upPct==null||t.downPct==null||!Number.isFinite(t.upPct)||!Number.isFinite(t.downPct)||t.upPct<1||t.upPct>99||Math.abs(t.upPct+t.downPct-100)>1e-8)throw Error('未來三日機率無效。')
    const matching=data.series.some(s=>s.horizon===t.horizon&&s.rows.some(r=>r.status==='saved'&&r.anchorDate===u.anchorDate&&r.targetDate===t.targetDate&&r.savedAt===t.probabilitySavedAt&&r.upPct===t.upPct&&r.downPct===t.downPct))
    if(!matching)throw Error('未來三日機率沒有對應實際保存紀錄。')
   }else if(!['pending','calendar_missing'].includes(t.status)||t.upPct!==null||t.downPct!==null||t.probabilitySavedAt!==null||(t.status==='pending'&&!t.targetDate)||(t.status==='calendar_missing'&&t.targetDate!==null))throw Error('未來三日待更新不得填入機率。')
  }
 }
 if(data.scenarioHistory){
  const s=data.scenarioHistory
  if(s.version!=='v6-night-scenario-history-1.0.0'||!Array.isArray(s.groups))throw Error('情境歷史版本不符。')
  const roots=new Set<string>()
  for(const g of s.groups){
   if(roots.has(g.rootId)||!g.snapshots.length||g.snapshots[0]!.id!==g.rootId||!Number.isFinite(g.anchorClose)||g.anchorClose<=0)throw Error('情境起點無效或重複。')
   roots.add(g.rootId);let previous=0
   for(const v of g.snapshots){
    const stamp=Date.parse(v.savedAt)
    if(v.pointEstimate){
     const e=v.pointEstimate
     if(e.version!=='v6-night-points-research-1.0.0'||e.role!=='research_only'||e.isOriginalIssueTimeEstimate!==false||e.anchorClose!==g.anchorClose||e.scenarioSavedAt!==v.savedAt||!Number.isFinite(Date.parse(e.generatedAt))||Date.parse(e.generatedAt)<stamp||e.targets.length!==3)throw Error('幅度估計版本或補算時間不符。')
     for(const [i,t] of e.targets.entries())if(t.horizon!==i+1||t.targetDate!==v.targets[i]?.targetDate||![t.medianClose,t.medianChangePoints,t.low80Close,t.high80Close,t.low80ChangePoints,t.high80ChangePoints].every(Number.isFinite)||t.low80Close<=0||t.low80Close>t.medianClose||t.medianClose>t.high80Close||Math.abs(t.medianClose-g.anchorClose-t.medianChangePoints)>1e-6)throw Error('幅度估計點位、區間或基準不符。')
    }
    if(!Number.isFinite(stamp)||stamp<previous||v.targets.length!==3)throw Error('情境時間或期限不符。');previous=stamp
    for(const [i,t] of v.targets.entries())if(t.horizon!==i+1||t.targetDate!==g.snapshots[0]!.targets[i]!.targetDate||t.targetDate<=g.anchorDate||![t.up,t.down,t.upPct,t.downPct].every(Number.isFinite)||t.up<.01||t.up>.99||t.down<.01||t.down>.99||Math.abs(t.up+t.down-1)>1e-10||Math.abs(t.upPct+t.downPct-100)>1e-8)throw Error('情境修訂不可更換基準或到期日。')
   }
  }
 }
 const horizons=new Set<number>()
 for(const s of data.series){
  if(![1,2,3].includes(s.horizon)||horizons.has(s.horizon)||!Array.isArray(s.rows))throw Error('每日走勢期限不符。')
  horizons.add(s.horizon);let previous='';let n=0
  for(const r of s.rows){
   if(r.date<=previous||r.horizon!==s.horizon)throw Error('每日走勢日期重複或未排序。');previous=r.date
   if(r.status==='missing'){
    if(r.up!==null||r.down!==null||r.upPct!==null||r.downPct!==null)throw Error('缺項日不可補造機率。')
   }else if(r.status==='saved'){
    n++;const p=[r.up,r.down,r.upPct,r.downPct]
    if(p.some(v=>v==null||!Number.isFinite(v))||r.up!<.01||r.up!>.99||r.down!<.01||r.down!>.99||Math.abs(r.up!+r.down!-1)>1e-10||Math.abs(r.upPct!+r.downPct!-100)>1e-8)
     throw Error('每日走勢機率無效。')
    if(!r.savedAt||!Number.isFinite(Date.parse(r.savedAt))||!r.targetDate||r.targetDate<r.date)throw Error('預報保存時間或到期日不足。')
   }else throw Error('每日走勢狀態不符。')
  }
  if(n!==s.savedCount)throw Error('每日走勢筆數不符。')
 }
 return data
}

export function researchSeries(data:ResearchData,horizon:number):ProbabilityDay[]{
 let previous:ProbabilityDay|null=null
 return [...data.origins].sort().map<ProbabilityDay>(date=>{
  const root=data.roots[date]!,t=root.targets.find(t=>t.horizon===horizon)
  if(!t)return {date,horizon,status:'missing',savedAt:null,up:null,down:null,upPct:null,downPct:null,deltaUpPp:null,deltaDownPp:null,note:'封存時尚未成熟，未列入本輪研究；不補造機率。'}
  const p=t.initial.M1,delta=previous?100*(p.up-previous.up!):null
  const row:ProbabilityDay={date,horizon,status:'saved',savedAt:null,informationCutoff:date+'T08:30:00+08:00',
   up:p.up,down:p.down,upPct:p.upPct,downPct:p.downPct,deltaUpPp:delta,deltaDownPp:delta==null?null:-delta,
   previousValidDate:previous?.date,targetDate:t.targetDate,originalTargetDate:t.targetDate,anchorDate:root.anchorDate,anchorClose:root.anchorClose,
   outcome:{up:t.outcome.up,close:t.outcome.close,availableAt:t.outcome.availableAt},note:'歷史重建；資訊截點是假設，並非當年實際發布紀錄，不計入0／60。'}
  previous=row;return row
 })
}

export type ProbabilityCheck={status:'missing'|'pending'|'invalid'|'verified';forecastDirection:'up'|'down'|'none'|null;
 confidence:number|null;actualClose:number|null;actualReturnPct:number|null;actualUp:boolean|null;hit:boolean|null}

export function probabilityOutcome(row:ProbabilityDay):ProbabilityCheck{
 const blank:ProbabilityCheck={status:'missing',forecastDirection:null,confidence:null,actualClose:null,actualReturnPct:null,actualUp:null,hit:null}
 if(row.status!=='saved')return blank
 const p=row.up,q=row.down
 if(p==null||q==null||!Number.isFinite(p)||!Number.isFinite(q)||p<0||p>1||Math.abs(p+q-1)>1e-10)return {...blank,status:'invalid'}
 const forecastDirection:ProbabilityCheck['forecastDirection']=p>.5?'up':p<.5?'down':'none'
 const prediction:ProbabilityCheck={...blank,status:'pending',forecastDirection,confidence:Math.max(p,q)*100}
 if(!row.outcome)return prediction
 const o=row.outcome,anchor=row.anchorClose
 if(anchor==null||!Number.isFinite(anchor)||anchor<=0||!Number.isFinite(o.close)||o.close<=0||typeof o.up!=='boolean'||
  !Number.isFinite(Date.parse(o.availableAt))||o.up!==(o.close>=anchor))return {...prediction,status:'invalid'}
 return {...prediction,status:'verified',actualClose:o.close,actualReturnPct:(o.close/anchor-1)*100,actualUp:o.up,
  hit:forecastDirection==='none'?null:(forecastDirection==='up')===o.up}
}

export function probabilityAccuracy(rows:ProbabilityDay[]){
 const checked=rows.map(probabilityOutcome),verified=checked.filter(c=>c.status==='verified')
 const assessed=verified.filter(c=>c.hit!==null),correct=assessed.filter(c=>c.hit===true).length
 const loss=rows.reduce((total,row,i)=>checked[i]!.status==='verified'?total+(row.up!-Number(checked[i]!.actualUp))**2:total,0)
 return {verified:verified.length,assessed:assessed.length,correct,neutral:verified.length-assessed.length,
  pending:checked.filter(c=>c.status==='pending').length,invalid:checked.filter(c=>c.status==='invalid').length,
  missing:checked.filter(c=>c.status==='missing').length,accuracy:assessed.length?correct/assessed.length:null,
  brier:verified.length?loss/verified.length:null}
}

export function probabilityHistoryPlot(rows:ProbabilityDay[],height=320,width=800){
 const left=20,right=width-24,top=28,bottom=height-82
 const x=(i:number)=>rows.length===1?(left+right)/2:left+(right-left)*i/Math.max(1,rows.length-1)
 const y=(v:number)=>bottom-(bottom-top)*v
 const lines=(['up','down'] as const).map(key=>{
  const segments:string[]=[];const points:{x:number;y:number;row:ProbabilityDay;index:number}[]=[];let open=false
  rows.forEach((row,i)=>{
   const p=row[key]
   if(row.status!=='saved'||p==null){open=false;return}
   const point={x:x(i),y:y(p),row,index:i};points.push(point)
   if(!open){segments.push(`M${point.x.toFixed(2)},${point.y.toFixed(2)}`);open=true}
   else segments[segments.length-1]+=` H${point.x.toFixed(2)} V${point.y.toFixed(2)}`
  })
  return {key,segments,points}
 })
 const tickStep=Math.max(1,Math.ceil(100/((right-left)/Math.max(1,rows.length-1))))
 const ticks=rows.map((_,i)=>i).filter(i=>i===0||i===rows.length-1||(i%tickStep===0&&i<rows.length-1-tickStep/2))
 return {width,height,left,right,top,bottom,tickY:height-46,missingY:height-66,x,y,lines,ticks}
}

const preferenceKey='branch-monitor:v6:daily-probability-chart:v3'
export type HistoryPreferences={mode?:'actual'|'research'|'scenario';horizon?:number;range?:number}
export function historyPreferences():HistoryPreferences{
 try{const p=JSON.parse(localStorage.getItem(preferenceKey)||'{}');return p&&typeof p==='object'&&!Array.isArray(p)?p:{}}catch{return {}}
}
export function saveHistoryPreferences(value:HistoryPreferences){try{localStorage.setItem(preferenceKey,JSON.stringify(value))}catch{/* Display remains usable without storage. */}}
