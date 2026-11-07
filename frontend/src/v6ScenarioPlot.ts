import type {V6ComparisonSnapshot,ThreeDayScenario,ScenarioPath} from './v6ComparisonTypes'
export function scenarioPlot(snapshot:V6ComparisonSnapshot,scenario:ThreeDayScenario){
 const history=snapshot.history.filter(r=>Number.isFinite(r.close)),branches=scenario.classes.flatMap(c=>c.path.map(p=>p.level)).filter(Number.isFinite)
 const shared=snapshot.strategies?.flatMap(s=>s.scenario.classes.flatMap(c=>c.path.map(p=>p.level))).filter(Number.isFinite)||[]
 const anchor=snapshot.anchor,values=[...history.map(r=>r.close),...branches,...shared,...snapshot.flat_bounds,anchor]
 const low=Math.min(...values),high=Math.max(...values),pad=Math.max((high-low)*.14,anchor*.003),bottom=low-pad,top=high+pad
 const x=(i:number)=>52+i/Math.max(1,history.length-1)*345
 const futureX=(step:number)=>397+step*40
 const y=(value:number)=>22+(top-value)/(top-bottom)*179
 const path=(points:{x:number;y:number}[])=>points.map((p,i)=>(i?'L':'M')+p.x.toFixed(2)+','+p.y.toFixed(2)).join(' ')
 return {history:path(history.map((p,i)=>({x:x(i),y:y(p.close)}))),branches:scenario.classes.map(c=>({...c,d:path(c.path.map((p:ScenarioPath)=>({x:futureX(p.step),y:y(p.level)})))})),
  anchorX:397,anchorY:y(anchor),anchor,low:bottom,high:top,y,grid:[bottom,(bottom+top)/2,top],lastX:517,
  bounds:snapshot.flat_bounds.map(y),date:history.at(-1)?.date||snapshot.market_as_of}
}
