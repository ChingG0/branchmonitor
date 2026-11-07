import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),cache=new Map(),src=new URL('../frontend/src/',import.meta.url)
const url=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
async function component(file){
 if(cache.has(file))return cache.get(file)
 const text=await readFile(new URL(file,src),'utf8');let code=compileScript(parse(text).descriptor,{id:file,inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 for(const match of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]){
  const dep=match[1].endsWith('.vue')?await component(match[1]):url((await readFile(new URL(match[1]+'.ts',src),'utf8')).replace(/import ['"].*\.css['"]/g,''))
  code=code.replace(match[0],'from '+JSON.stringify(dep))
 }
 const result=url(code);cache.set(file,result);return result
}
const helpers=await import(url(await readFile(new URL('v6ProbabilityHistory.ts',src),'utf8')))
const file=(await readFile(new URL('v6ConditionalResource.ts',src),'utf8')).match(/\/v6-research\/([^"']+\.json)/)[1]
const research=JSON.parse(await readFile(new URL('../frontend/public/v6-research/'+file,import.meta.url),'utf8'))
const row=(date,up)=>({date,horizon:1,status:'saved',savedAt:date+'T08:35:00+08:00',informationCutoff:date+'T08:30:00+08:00',targetDate:date,originalTargetDate:date,anchorDate:'2026-11-02',anchorClose:100,up,down:1-up,upPct:100*up,downPct:100*(1-up),deltaUpPp:null,deltaDownPp:null,note:'test'})
const gap={date:'2026-11-04',horizon:1,status:'missing',savedAt:null,up:null,down:null,upPct:null,downPct:null,deltaUpPp:null,deltaDownPp:null,note:'未保存合格機率'}
const payload=()=>({version:'v6-daily-probability-history-1.0.0',kind:'actual_saved_forecasts',status:'ready',withheld:false,note:'test',generatedAt:'2026-11-06T16:00:00+08:00',series:[{horizon:1,rows:[row('2026-11-03',.6),{...gap},{...row('2026-11-05',.8),deltaUpPp:20,deltaDownPp:-20,previousValidDate:'2026-11-03'}],savedCount:2}]})
const html=async(props)=>renderToString(createSSRApp((await import(await component('V6ProbabilityHistory.vue'))).default,props))
test('valid saved probabilities accepted and fabricated missing probabilities rejected',()=>{const d=payload();assert.equal(helpers.validateHistory(d),d);d.series[0].rows[1].up=.5;assert.throws(()=>helpers.validateHistory(d),/缺項/)} )
test('duplicate dates and mismatched counts rejected',()=>{const d=payload();d.series[0].rows.push(d.series[0].rows[2]);assert.throws(()=>helpers.validateHistory(d));const e=payload();e.series[0].savedCount++;assert.throws(()=>helpers.validateHistory(e))})
test('step paths break at missing dates and never use outcome certainty',()=>{const r=payload().series[0].rows;r[0].outcome={up:false,close:98,availableAt:'2026-11-03T13:30:00+08:00'};const p=helpers.probabilityHistoryPlot(r);assert.deepEqual(p.lines.map(l=>l.segments.length),[2,2]);assert.ok(p.lines.every(l=>l.points.length===2&&!l.segments.join().includes('NaN')));assert.equal(p.lines[0].points[0].y,p.y(.6));const q=helpers.probabilityHistoryPlot([r[0],r[2]]);assert.ok(q.lines[0].segments[0].includes(' H')&&q.lines[0].segments[0].includes(' V'))})
test('research history keeps exact frozen M1 probabilities and no actual publication timestamps',()=>{for(const h of [1,2,3]){const r=helpers.researchSeries(research,h);assert.ok(r.filter(r=>r.status==='saved').length>400);for(const v of r.filter(r=>r.status==='saved')){assert.equal(v.up,research.roots[v.date].targets.find(t=>t.horizon===h).initial.M1.up);assert.equal(v.savedAt,null);assert.ok(v.note.includes('不計入0／60'))}assert.equal(r.at(-1).status,h===1?'saved':'missing')}})
test('empty live SSR does not silently substitute research or draw fake lines',async()=>{const d=payload();d.series=[];d.withheld=true;d.note='等待第一份合格盤前資料';const text=await html({payload:d});assert.ok(text.includes('尚未累積實際保存的機率'));assert.ok(text.includes('本次沒有新機率'));assert.ok(!text.includes('class="daily-chart"'));assert.ok(text.includes('查看歷史研究參考'))})
test('saved SSR exposes pp changes and distinct source/forecast times',async()=>{const d=payload();d.lastMarketUpdate={savedAt:'2026-11-06T15:30:00+08:00',marketAsOf:'2026-11-06'};d.lastProbabilitySavedAt='2026-11-05T08:35:00+08:00';const text=await html({payload:d});for(const s of ['2026-11-06 15:30:00','2026-11-05 08:35:00','80.0%','20.0%','+20.0 個百分點','到期 2026-11-05','不是同一到期日'])assert.ok(text.includes(s),s);assert.ok(!text.includes('NaN'))})
test('explicit research mode is clearly separate from live observations',async()=>{const text=await html({payload:payload(),researchPayload:research,initialMode:'research'});assert.ok(text.includes('歷史重建 · 非本次預報・不計入0/60'));assert.ok(text.includes('並非當年實際發佈'));assert.ok(text.includes('歷史假設資訊截點'));assert.ok(text.includes('daily-chart'))})
test('source only reads static files and is wired beside live status',async()=>{const source=await readFile(new URL('V6ProbabilityHistory.vue',src),'utf8');assert.doesNotMatch(source,/method\s*:\s*['"]POST|\/api\/|setInterval\(/);assert.ok(source.includes("crypto.subtle.digest('SHA-256'"));const v6=await readFile(new URL('V6Weather.vue',src),'utf8');assert.ok(v6.includes('<V6ProbabilityHistory/>'))})

test('outcome accounting uses only verified records; losses are probability squared errors',()=>{
 const a={...row('2026-11-03',.8),outcome:{up:true,close:101,availableAt:'2026-11-03T13:30:00+08:00'}}
 const b={...row('2026-11-04',.8),outcome:{up:false,close:99,availableAt:'2026-11-04T13:30:00+08:00'}}
 const pending=row('2026-11-05',.9),before=JSON.stringify([a,b,pending,gap])
 const s=helpers.probabilityAccuracy([a,b,pending,gap]);assert.equal(s.correct,1);assert.equal(s.assessed,2);assert.equal(s.verified,2);assert.equal(s.pending,1);assert.equal(s.missing,1);assert.equal(s.accuracy,.5);assert.ok(Math.abs(s.brier-.34)<1e-12);assert.equal(JSON.stringify([a,b,pending,gap]),before)
 assert.ok(Math.abs(helpers.probabilityOutcome(b).actualReturnPct+1)<1e-12)
})
test('50/50 abstains on direction while counted in Brier; unchanged close follows existing up definition',()=>{
 const a={...row('2026-11-03',.5),outcome:{up:true,close:100,availableAt:'2026-11-03T13:30:00+08:00'}}
 const s=helpers.probabilityAccuracy([a]);assert.equal(s.neutral,1);assert.equal(s.assessed,0);assert.equal(s.accuracy,null);assert.equal(s.brier,.25);assert.equal(helpers.probabilityOutcome(a).actualReturnPct,0)
})
test('inconsistent close labels and invalid price evidence are excluded rather than counted as misses',()=>{
 const bad={...row('2026-11-03',.8),outcome:{up:true,close:99,availableAt:'2026-11-03T13:30:00+08:00'}}
 assert.equal(helpers.probabilityOutcome(bad).status,'invalid');assert.equal(helpers.probabilityAccuracy([bad]).accuracy,null)
 const noAnchor={...bad,anchorClose:undefined};assert.equal(helpers.probabilityOutcome(noAnchor).status,'invalid')
})
test('all historical initial M1 scores agree with sealed independent study summaries at each horizon',()=>{
 for(const h of [1,2,3]){const s=helpers.probabilityAccuracy(helpers.researchSeries(research,h)),expected=research.summary.initial.find(s=>s.horizon===h).models.M1
  assert.equal(s.verified,expected.n);assert.equal(s.invalid,0);assert.ok(Math.abs(s.accuracy-expected.accuracy)<1e-12);assert.ok(Math.abs(s.brier-expected.brier)<1e-12)
 }
})
test('visible actual-close panel renders scores and scope; live empty never fabricates accuracy',async()=>{
 const d=payload();d.series[0].rows[0].outcome={up:true,close:101,availableAt:'2026-11-03T13:30:00+08:00'};d.series[0].rows[2].outcome={up:false,close:99,availableAt:'2026-11-05T13:30:00+08:00'}
 const text=await html({payload:d});for(const s of ['預測與實際收盤對照','50.0%','101.00','99.00','+1.00%','-1.00%','未命中','方向命中率','比較基準收盤','機率誤差 Brier'])assert.ok(text.includes(s),s)
 const empty=payload();empty.series=[];const e=await html({payload:empty});assert.ok(e.includes('尚無已保存預報，方向命中率與機率誤差不補0'));assert.match(e,/class="direction-accuracy">—/)
 const history=await html({payload:empty,researchPayload:research,initialMode:'research'});assert.ok(history.includes('這是歷史研究成績，不能當作實際前瞻準確率'))
})

const future=()=>({anchorDate:'2026-11-06',marketSavedAt:'2026-11-06T19:57:23+08:00',note:'日期先列出，未保存合格機率時留空，不沿用歷史研究數字。',targets:['2026-11-09','2026-11-10','2026-11-11'].map((targetDate,i)=>({horizon:i+1,targetDate,status:'pending',upPct:null,downPct:null,probabilitySavedAt:null}))})
test('latest three target dates remain visible in both actual and historical modes without fabricated odds',async()=>{
 const d=payload();d.lastMarketUpdate={marketAsOf:'2026-11-06',savedAt:'2026-11-06T19:57:23+08:00'};d.upcoming=future();helpers.validateHistory(d)
 for(const initialMode of ['actual','research']){
  const text=await html({payload:d,researchPayload:research,initialMode});const section=text.match(/<section class="upcoming-forecast"[\s\S]*?<\/section>/)[0]
  for(const s of ['T＝2026-11-06','T+1','2026-11-09','T+2','2026-11-10','T+3','2026-11-11','機率待更新'])assert.ok(section.includes(s),s)
  assert.doesNotMatch(section,/80\.0%|20\.0%|60\.0%/)
  assert.ok(text.includes('不是到期日'))
 }
})
test('future projections reject invented or stale saved odds and unordered deadlines',()=>{
 const d=payload();d.lastMarketUpdate={marketAsOf:'2026-11-06',savedAt:'2026-11-06T19:57:23+08:00'};d.upcoming=future()
 d.upcoming.targets[0].upPct=53.1;assert.throws(()=>helpers.validateHistory(d),/待更新/)
 d.upcoming=future();Object.assign(d.upcoming.targets[0],{status:'saved',upPct:80,downPct:20,probabilitySavedAt:'2026-11-05T08:35:00+08:00'});assert.throws(()=>helpers.validateHistory(d),/沒有對應/)
 d.upcoming=future();d.upcoming.targets[0].targetDate='2026-11-06';assert.throws(()=>helpers.validateHistory(d),/晚於錨點/)
})
test('night-close scenario is clearly hypothetical and never counted as qualified history',async()=>{
 const d=payload();d.series=[];d.lastMarketUpdate={marketAsOf:'2026-11-06',savedAt:'2026-11-06T19:57:23+08:00'};d.upcoming=future();d.upcoming.scenario={version:'v6-night-close-scenario-1.0.0',kind:'night_close_what_if',isForecast:false,addsProspectiveSamples:false,validatedForThisTime:false,anchorDate:'2026-11-06',anchorClose:100,savedAt:'2026-11-06T20:20:00+08:00',assumption:'假設夜盤以目前價格收盤，且高低區間不再擴大；不是已驗證的盤後預報。',foreignNote:'原M1不含海外因子',contextSha256:'0'.repeat(64),night:{contract:'202611',price:101,high:102,low:99,day_close:100,quote_at:'2026-11-06T20:19:59+08:00'},targets:d.upcoming.targets.map(t=>({horizon:t.horizon,targetDate:t.targetDate,up:.6,down:.4,upPct:60,downPct:40}))}
 helpers.validateHistory(d);const text=await html({payload:d});for(const s of ['夜盤條件情境研究','尚未累積實際保存的機率','機率待更新'])assert.ok(text.includes(s),s);for(const s of ['上漲 60.0%','下跌 40.0%','scenario-chart-scroll'])assert.ok(!text.includes(s),s)
 assert.equal(helpers.probabilityAccuracy(d.series.flatMap(s=>s.rows)).verified,0)
 d.upcoming.scenario.isForecast=true;assert.throws(()=>helpers.validateHistory(d),/情境版本/)
})

function scenarioHistory(){const targets=[9,10,11].map((day,i)=>({horizon:i+1,targetDate:'2026-11-'+String(day).padStart(2,'0'),up:.6,down:.4,upPct:60,downPct:40,parameterSha256:'same'}));return {version:'v6-night-scenario-history-1.0.0',note:'情境另存',groups:[{rootId:'first',anchorDate:'2026-11-06',anchorClose:100,assumption:'假設夜盤維持目前價位；非已驗證盤後預報',outcomes:{},snapshots:[{id:'first',savedAt:'2026-11-06T20:15:00+08:00',quoteAt:'2026-11-06T20:14:59+08:00',nightPrice:101,targets},{id:'second',savedAt:'2026-11-06T20:30:00+08:00',quoteAt:'2026-11-06T20:29:59+08:00',nightPrice:102,targets:targets.map(t=>({...t,up:.7,down:.3,upPct:70,downPct:30}))}]}]}}
test('saved scenario never becomes the default live forecast or renders hidden primary percentages',async()=>{
 const d=payload();d.series=[];d.scenarioHistory=scenarioHistory();helpers.validateHistory(d);const text=await html({payload:d});assert.ok(text.includes('夜盤條件情境研究'));for(const s of ['60.0%','70.0%','scenario-chart-scroll','最初上漲／下跌'])assert.ok(!text.includes(s),s)
 const archive=await renderToString(createSSRApp((await import(await component('V6ScenarioTimeline.vue'))).default,{value:d.scenarioHistory}));for(const s of ['2026-11-06 20:30:00','2026-11-09','2026-11-10','2026-11-11','最初上漲／下跌','60.0%','70.0%','+10.0 個百分點','待到期／待核對','T 收盤基準','舊版單點試算與幅度區間已撤離目前預報'])assert.ok(archive.includes(s),s)
})
test('scenario history cannot change target dates or leak into qualified 08:30 accuracy',async()=>{
 const d=payload();d.series=[];d.scenarioHistory=scenarioHistory();const actual=await html({payload:d,initialMode:'actual'});assert.match(actual,/class="direction-accuracy">—/);assert.ok(!actual.includes('scenario-chart-scroll'))
 d.scenarioHistory.groups[0].snapshots[1].targets[0].targetDate='2026-11-10';assert.throws(()=>helpers.validateHistory(d),/修訂不可更換/)
})
test('magnitude columns show cash-index median, signed point delta, empirical band and separate calculation timestamp',async()=>{
 const d=payload();d.series=[];d.scenarioHistory=scenarioHistory();const g=d.scenarioHistory.groups[0],v=g.snapshots[1];v.pointEstimate={version:'v6-night-points-research-1.0.0',role:'research_only',generatedAt:'2026-11-06T20:40:00+08:00',scenarioSavedAt:v.savedAt,anchorClose:100,isOriginalIssueTimeEstimate:false,note:'幅度另算',targets:v.targets.map(t=>({horizon:t.horizon,targetDate:t.targetDate,medianClose:101,medianChangePoints:1,medianReturnPct:1,low80Close:95,high80Close:105,low80ChangePoints:-5,high80ChangePoints:5,historicalMaePct:.8,historicalCoverage80Pct:78}))}
 helpers.validateHistory(d);const main=await html({payload:d});assert.ok(!main.includes('101 點'));const text=await renderToString(createSSRApp((await import(await component('V6ScenarioTimeline.vue'))).default,{value:d.scenarioHistory}));for(const s of ['預估收盤（中位）','預估漲跌點數','舊名目80%區間（盤中未驗證）','101 點','+1 點','95～105 點','漲跌 -5～+5 點','2026-11-06 20:40:00','不是當時已發佈的點數預報','不由上漲機率換算','歷史誤差與區間涵蓋'])assert.ok(text.includes(s),s)
 v.pointEstimate.targets[0].medianChangePoints=10;assert.throws(()=>helpers.validateHistory(d),/幅度估計點位/)
})

const presentation=JSON.parse(await readFile(new URL('../frontend/public/v6-research/forecast-presentation.json',import.meta.url),'utf8'))
const presentationHelpers=await import(url(await readFile(new URL('v6ForecastPresentation.ts',src),'utf8')))
test('conditional research keeps every probability hypothetical and withholds unvalidated interval',async()=>{
 const text=await renderToString(createSSRApp((await import(await component('V6NightConditions.vue'))).default,{payload:presentation}));for(const s of ['若完整夜盤收在不同價位','若收在','盤中準確率未驗證','未修正後續偏多漂移','暫不提供盤中80%預測區間','不是從假設夜盤價進場的獲利機率','EWMA','ATR14'])assert.ok(text.includes(s),s);assert.equal((text.match(/若收在/g)||[]).length,3)
 const bad=structuredClone(presentation);bad.conditions.isForecast=true;assert.throws(()=>presentationHelpers.validatePresentation(bad),/夜盤條件/)
})
test('audit displays open-based agreement separately from prior-close accuracy and rejected calibration values',async()=>{
 const text=await renderToString(createSSRApp((await import(await component('V6ForecastAudit.vue'))).default,{payload:presentation}));for(const s of ['76.5%','64.1%','49.3%','75.1%','59.1%','54.8%','+4.29','0.34%','未採用','log loss','滾動候選值','原始機率恰好50/50不判方向','50.0254%','50.0054%'])assert.ok(text.includes(s),s)
 const bad=structuredClone(presentation);bad.audit.rollingRows[0].latestTrainingOutcome=bad.audit.rollingRows[0].cutoff;assert.throws(()=>presentationHelpers.validatePresentation(bad),/未成熟答案/)
})
test('risk comparison no longer mounts any unconditional forecast block even for an old saved snapshot',async()=>{
 const code=await readFile(new URL('V6Comparison.vue',src),'utf8');for(const s of ['<V6DailyForecast','<V6SharedDistribution','<V6Remaining','<V6LegacyScenarios','共同歷史基準'])assert.ok(!code.includes(s),s)
})
