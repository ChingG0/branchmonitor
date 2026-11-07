export type Probability = {up:number;down:number;upPct:number;downPct:number}
export type Metric = {n:number;brier:number;logLoss:number;accuracy:number;auc:number;up:number;down:number}
export type InitialName = 'M0'|'M1'|'M2'
export type RevisionName = 'R0'|'R1'|'R2'
export type Revision = {case:string;date:string;availableAt:string;remaining:number;models:Record<RevisionName,Probability>}
export type Target = {horizon:number;targetDate:string;initial:Record<InitialName,Probability>;updates:Revision[];
 outcome:{availableAt:string;close:number;returnPct:number;up:boolean}}
export type Root = {origin:string;anchorDate:string;anchorClose:number;inputs:{nightReturnPct:number;dailySigmaPct:number;nightContract:string;nightEnd:string};targets:Target[]}
export type Calibration = {family:'initial'|'revision';group:string;model:string;low:number;high:number;n:number;mean:number|null;actual:number|null;enough:boolean}
export type InitialSummary = {horizon:number;selected:InitialName;models:Record<InitialName,Metric>;month:Record<InitialName,Metric>;improvement:number;holmP:number}
export type RevisionSummary = {case:string;models:Record<RevisionName,Metric>;month:Record<RevisionName,Metric>;selected:RevisionName;rootEligible:boolean;improvement:number;holmP:number}
export type Interval = {lower:number;upper:number;valid_draws:number}
export type ResearchData = {version:string;mode:'research_only';productionActive:false;generatedAt:string;calculatedOn:string;dataCutoff:string;
 testPeriod:[string,string];monthPeriod:[string,string];indexLabel:string;methodVersion:string;sourceSeals:Record<string,string>;
 defaultOrigin:string;origins:string[];roots:Record<string,Root>;
 summary:{initial:InitialSummary[];revision:RevisionSummary[];calibration:Calibration[];formalComparisons:number;passedComparisons:number;
 deltas:{paired_H3_roots:number;mean_delta1:number;mean_delta2:number;successive_delta_pearson:number;intervals:{mean_delta1:Interval;mean_delta2:Interval;successive_delta_pearson:Interval}}};
 shadow:{started:false;maturedByHorizon:number[];minimumSessions:number;minimumMatured:number;minimumEachClass:number}}

export type ReplayStep = {at:string;label:string;kind:'initial'|'revision'|'outcome';probabilities:Record<RevisionName,Probability>}
export function replaySteps(root:Root,target:Target,initialModel:InitialName='M2'):ReplayStep[]{
 const first=target.initial[initialModel]
 const steps:ReplayStep[]=[{at:root.origin+'T08:30:00+08:00',label:initialModel==='M1'?'夜盤模型起點':'原M2預報',kind:'initial',probabilities:{R0:first,R1:first,R2:first}}]
 target.updates.forEach((u,i)=>steps.push({at:u.availableAt,label:`第${i+1}次修訂`,kind:'revision',probabilities:initialModel==='M1'?{...u.models,R0:first}:u.models}))
 const last=steps.at(-1)!
 steps.push({at:target.outcome.availableAt,label:'到期核對',kind:'outcome',probabilities:last.probabilities})
 return steps
}
export function visibleReplay(root:Root,target:Target,stage:number,initialModel:InitialName='M2'){
 const steps=replaySteps(root,target,initialModel),index=Math.max(0,Math.min(steps.length-1,Math.floor(Number.isFinite(stage)?stage:0)))
 const current=steps[index],points=steps.slice(0,index+1).filter(s=>s.kind!=='outcome')
 const previous=index>0?steps[index-1]:null
 return {steps,index,current,points,previous,outcomeKnown:Date.parse(target.outcome.availableAt)<=Date.parse(current.at)}
}
export function outcomeKnownAt(target:Target,at:string){return Number.isFinite(Date.parse(at))&&Date.parse(target.outcome.availableAt)<=Date.parse(at)}
export function probabilityPlot(points:ReplayStep[],totalProbabilitySteps:number,direction:'up'|'down',outcome?:{up:boolean;at:string}){
 const width=720,height=330,left=52,right=682,top=24,bottom=244
 const x=(i:number)=>left+(right-left)*i/Math.max(1,totalProbabilitySteps-1+(outcome?1:0))
 const y=(p:number)=>bottom-(bottom-top)*p
 const lines=(['R0','R1','R2'] as const).map(key=>({key,
  points:points.map((s,i)=>({x:x(i),y:y(s.probabilities[key][direction]),p:s.probabilities[key],at:s.at,label:s.label})),
  path:points.map((s,i)=>i?'H'+x(i).toFixed(3)+' V'+y(s.probabilities[key][direction]).toFixed(3):'M'+x(i).toFixed(3)+','+y(s.probabilities[key][direction]).toFixed(3)).join(' ')}))
 const actual=outcome?{x:x(totalProbabilitySteps),y:y(direction==='up'?Number(outcome.up):1-Number(outcome.up)),at:outcome.at,
  pct:100*(direction==='up'?Number(outcome.up):1-Number(outcome.up))}:null
 return {width,height,left,right,top,bottom,x,y,lines,actual}
}
export function validateResearch(value:unknown):ResearchData{
 const d=value as ResearchData
 if(!d||d.version!=='v6-conditional-research-view-1.0.0'||d.mode!=='research_only'||d.productionActive!==false||d.shadow?.started!==false)
  throw new Error('研究版本或狀態不符，暫不顯示。')
 if(!Array.isArray(d.origins)||!d.origins.length||!d.roots?.[d.defaultOrigin]||!d.summary?.initial?.length||!d.summary?.revision?.length)
  throw new Error('研究資料不完整，暫不顯示。')
 const check=(p:Probability)=>{
  if(!p||![p.up,p.down,p.upPct,p.downPct].every(Number.isFinite)||p.up<.01||p.up>.99||Math.abs(p.up+p.down-1)>1e-12||Math.abs(p.upPct+p.downPct-100)>1e-8)
   throw new Error('研究機率資料不完整，暫不顯示。')
 }
 for(const origin of d.origins){
  const r=d.roots[origin]
  if(!r||r.origin!==origin||!(r.anchorDate<origin)||!Number.isFinite(r.anchorClose)||!Array.isArray(r.targets))throw new Error('研究日期資料不符。')
  for(const t of r.targets){
   if(![1,2,3].includes(t.horizon)||t.targetDate<origin||t.updates.length!==t.horizon-1)throw new Error('研究到期日不符。')
   for(const key of ['M0','M1','M2'] as const)check(t.initial[key])
   let before=origin+'T08:30:00+08:00'
   for(const u of t.updates){
    if(!(Date.parse(u.availableAt)>Date.parse(before))||Date.parse(u.availableAt)>=Date.parse(t.outcome.availableAt))throw new Error('研究修訂時間不符。')
    for(const key of ['R0','R1','R2'] as const)check(u.models[key])
    if(u.models.R0.up!==t.initial.M2.up||u.models.R0.down!==t.initial.M2.down)throw new Error('原M2預報被改寫，暫不顯示。')
    before=u.availableAt
   }
  }
 }
 if(d.summary.initial.some(r=>r.selected!=='M1')||d.summary.revision.some(r=>r.rootEligible!==false))throw new Error('研究採用狀態與此版本不符。')
 return d
}

const preferenceKey='branch-monitor:v6:conditional-research:v1'
export type Preferences={origin?:string;horizon?:number;stage?:number;direction?:'up'|'down';asOfOnly?:boolean;initialHorizon?:number;bridgeMode?:boolean}
export function readPreferences():Preferences{
 try{const x=JSON.parse(localStorage.getItem(preferenceKey)||'{}');return x&&typeof x==='object'&&!Array.isArray(x)?x:{}}catch{return {}}
}
export function savePreferences(value:Preferences){try{localStorage.setItem(preferenceKey,JSON.stringify(value))}catch{/* Browsing stays usable without storage. */}}
