import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),cache=new Map()
const src=new URL('../frontend/src/',import.meta.url)
const url=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
async function component(file){
 if(cache.has(file))return cache.get(file)
 const text=await readFile(new URL(file,src),'utf8')
 let code=compileScript(parse(text).descriptor,{id:file,inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 for(const match of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]){
  let dep=match[1].endsWith('.vue')?await component(match[1]):url((await readFile(new URL(match[1]+'.ts',src),'utf8')).replace(/import ['"].*\.css['"]/g,''))
  code=code.replace(match[0],'from '+JSON.stringify(dep))
 }
 const result=url(code);cache.set(file,result);return result
}
const helpers=await import(url(await readFile(new URL('v6ConditionalResearch.ts',src),'utf8')))
const resource=await readFile(new URL('v6ConditionalResource.ts',src),'utf8')
const file=resource.match(/\/v6-research\/([^"']+\.json)/)[1]
const data=JSON.parse(await readFile(new URL('../frontend/public/v6-research/'+file,import.meta.url),'utf8'))
const root=data.roots['2026-10-06'],target=root.targets.find(t=>t.horizon===3)
const html=async(stage=0,origin='2026-10-06')=>renderToString(createSSRApp((await import(await component('V6ConditionalResearch.vue'))).default,{payload:data,initialOrigin:origin,initialStage:stage,initialRoot:'M2'}))

test('research release is explicitly nonproduction and source statuses cannot be flipped',()=>{
 assert.equal(helpers.validateResearch(data).mode,'research_only')
 for(const field of ['productionActive','shadow','summary']){
  const altered=structuredClone(data)
  if(field==='productionActive')altered.productionActive=true
  if(field==='shadow')altered.shadow.started=true
  if(field==='summary')altered.summary.revision[0].rootEligible=true
  assert.throws(()=>helpers.validateResearch(altered))
 }
})
test('invalid probability and overwritten original M2 are rejected',()=>{
 const x=structuredClone(data);x.roots['2026-10-06'].targets[2].updates[0].models.R0.up=.5
 assert.throws(()=>helpers.validateResearch(x),/機率資料|原M2/)
 const y=structuredClone(data);y.roots['2026-10-06'].targets[0].initial.M1.up=NaN
 assert.throws(()=>helpers.validateResearch(y),/機率資料/)
})
test('initial replay exposes no future revisions or outcome',()=>{
 const r=helpers.visibleReplay(root,target,0)
 assert.equal(r.points.length,1);assert.equal(r.current.kind,'initial');assert.equal(r.previous,null);assert.equal(r.outcomeKnown,false)
 assert.equal(r.points[0].probabilities.R2.up,target.initial.M2.up)
 assert.notEqual(target.initial.M1.up,target.initial.M2.up)
})
test('step changes retain root and target; outcome only known at actual closing bound',()=>{
 const first=helpers.visibleReplay(root,target,1),second=helpers.visibleReplay(root,target,2),last=helpers.visibleReplay(root,target,3)
 assert.equal(first.points.length,2);assert.equal(second.points.length,3);assert.equal(last.points.length,3)
 assert.equal(first.current.at,'2026-10-07T08:30:00+08:00');assert.equal(second.current.at,'2026-10-08T08:30:00+08:00')
 assert.equal(second.outcomeKnown,false);assert.equal(last.outcomeKnown,true);assert.equal(last.current.kind,'outcome')
 assert.equal(last.current.probabilities.R2.up,second.current.probabilities.R2.up)
 assert.equal(target.targetDate,'2026-10-08')
})
test('plot contains visible points only, neither zero fill nor outcome certainty',()=>{
 const initial=helpers.visibleReplay(root,target,0);const plot=helpers.probabilityPlot(initial.points,3,'up')
 assert.deepEqual(plot.lines.map(l=>l.points.length),[1,1,1]);assert.ok(plot.lines.every(l=>!l.path.includes('NaN')))
 const end=helpers.visibleReplay(root,target,3),final=helpers.probabilityPlot(end.points,3,'up')
 assert.equal(final.lines[2].points.length,3);assert.ok(final.lines[2].points.every(p=>p.p.up>=.01&&p.p.up<=.99))
 const down=helpers.probabilityPlot(end.points,3,'down');assert.equal(down.lines[2].points[2].p.up+down.lines[2].points[2].p.down,1)
})
test('as-of stages sanitize invalid storage and do not infer outcome from calendar day alone',()=>{
 for(const bad of [-100,NaN,Infinity])assert.equal(helpers.visibleReplay(root,target,bad).index,0)
 assert.equal(helpers.visibleReplay(root,target,100).index,3)
 assert.equal(helpers.outcomeKnownAt(target,'2026-10-08T08:30:00+08:00'),false)
 assert.equal(helpers.outcomeKnownAt(target,'2026-10-08T13:30:00+08:00'),true)
 assert.equal(helpers.outcomeKnownAt(target,'bad'),false)
})
test('SSR distinguishes M1 initial from original M2/R2 and explicitly preserves blocked rollout',async()=>{
 const text=await html(0)
 for(const t of ['歷史重建 · 尚未啟用','現貨加權指數','M1起點機率','原M2事件的修訂研究','與上方M1卡片分開','原M2資格未通過','實際前瞻進度見本頁上方','見上方前瞻觀察','沒有產生新的目標價或80%價格區間'])assert.ok(text.includes(t),t)
 assert.equal((text.match(/data-horizon=/g)||[]).length,3);assert.doesNotMatch(text,/NaN|Infinity|<button[^>]*>更新V6/)
})
test('SSR initial/first/last step table does not leak subsequent probability rows',async()=>{
 for(const [index,expected] of [[0,1],[1,2],[2,3],[3,3]]){
  const text=await html(index),table=text.match(/class="visible-history"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)[1]
  assert.equal((table.match(/<tr[\s>]/g)||[]).length,expected)
  if(index===0)assert.ok(!table.includes('30.7%'))
  if(index>=2)assert.ok(table.includes('30.7%'))
 }
})
test('unmatured horizons stay missing; no fabricated current T+2/T+3',async()=>{
 const text=await html(0,'2026-11-05')
 assert.ok(text.includes('封存時尚未到期，未納入本輪樣本；不補造機率'))
 assert.ok(text.includes('沒有可回放的修訂樣本'));assert.doesNotMatch(text,/NaN|Infinity/)
})
test('research component only GETs a static hash-bound file and has no publish/trading API',async()=>{
 const source=await readFile(new URL('V6ConditionalResearch.vue',src),'utf8')
 assert.ok(source.includes("crypto.subtle.digest('SHA-256'"));assert.ok(source.includes('localFetch(conditionalResearchUrl'))
 assert.doesNotMatch(source,/method\s*:\s*['"]POST|\/api\/|publish_current|setInterval\(/)
})


test('M1 bridge uses the matching night-model root while leaving frozen updates intact',()=>{
 const before=JSON.stringify(target);const r=helpers.visibleReplay(root,target,2,'M1')
 assert.equal(r.points[0].probabilities.R2.up,target.initial.M1.up)
 assert.ok(r.points.every(p=>p.probabilities.R0.up===target.initial.M1.up))
 assert.equal(r.points[1].probabilities.R1.up,target.updates[0].models.R1.up)
 assert.equal(r.points[2].probabilities.R2.up,target.updates[1].models.R2.up)
 assert.equal(JSON.stringify(target),before)
})
test('step plot has horizontal holds, separate binary outcome and unmodified probabilities',()=>{
 const r=helpers.visibleReplay(root,target,3,'M1'),p=helpers.probabilityPlot(r.points,3,'up',{up:target.outcome.up,at:target.outcome.availableAt})
 assert.ok(p.lines.every(l=>l.path.includes('H')&&l.path.includes('V')))
 assert.equal(p.actual.pct,target.outcome.up?100:0);assert.ok(p.actual.x>p.lines[2].points.at(-1).x)
 assert.ok(p.lines[2].points.every(v=>v.p.up>=.01&&v.p.up<=.99))
})
test('matured replay defaults to full path; explicit as-of mode hides later information',async()=>{
 const C=(await import(await component('V6ConditionalResearch.vue'))).default
 const complete=await renderToString(createSSRApp(C,{payload:data,initialOrigin:'2026-10-06'}))
 assert.ok(complete.includes('actual-endpoint'));assert.ok(complete.includes('13:30結果'));assert.ok(complete.includes('以夜盤模型為起點'))
 const asof=await html(0);assert.ok(!asof.includes('class="actual-endpoint"'))
})
test('calibration summary uses weighted raw values and both score types appear',async()=>{
 const text=await html(0);assert.ok(text.includes('59.14%'));assert.ok(text.includes('54.85%'));assert.ok(text.includes('+4.29'))
 assert.ok((text.match(/log loss/g)||[]).length>=12);assert.ok(text.includes('single-answer'))
})
