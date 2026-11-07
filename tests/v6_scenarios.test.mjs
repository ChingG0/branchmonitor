import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url)
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helper=moduleUrl(await readFile(new URL('../frontend/src/v6ScenarioPlot.ts',import.meta.url),'utf8'))
const {scenarioPlot}=await import(helper)
const snapshot={anchor:100,market_as_of:'2026-10-30',history:[{date:'2026-10-29',close:102},{date:'2026-10-30',close:100}],forecast_dates:['2026-11-02','2026-11-03','2026-11-04'],flat_bounds:[99.5,100.5]}
const scenario={classes:[{key:'up',label:'上漲',probability_pct:40,path:[{step:0,return_pct:0,level:100},{step:1,return_pct:1,level:101},{step:2,return_pct:2,level:102},{step:3,return_pct:3,level:103}]},{key:'flat',label:'平盤',probability_pct:20,path:[]},{key:'down',label:'下跌',probability_pct:40,path:[{step:0,return_pct:0,level:100},{step:3,return_pct:-5,level:95}]}]}
test('chart anchors future at last actual close and fits all scenario endpoints',()=>{
 const p=scenarioPlot(snapshot,scenario)
 assert.equal(p.anchor,100);assert.equal(p.anchorX,397);assert.ok(p.high>103);assert.ok(p.low<95)
 for(const b of p.branches)assert.doesNotMatch(b.d,/NaN|Infinity/)
 assert.equal(p.branches[1].d,'');assert.match(p.branches[0].d,/^M397/)
})
test('rendered chart distinguishes actual and three future sessions without invented flat path',async()=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/V6ScenarioChart.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'v6-scenario-test',inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`).replace("from './v6ScenarioPlot'",`from ${JSON.stringify(helper)}`)
 const component=(await import(moduleUrl(code))).default
 const html=await renderToString(createSSRApp(component,{snapshot,scenario,label:'跨資產'}))
 for(const text of ['11-02','11-03','11-04','歷史情境路徑','非預測每天一定如此','跨資產'])assert.ok(html.includes(text),text)
 assert.equal((html.match(/class="branch"/g)||[]).length,2)
 assert.doesNotMatch(html,/NaN|Infinity/)
})
test('V6 alone displays frozen comparison and exposes no update endpoint',async()=>{
 const component=await readFile(new URL('../frontend/src/V6Comparison.vue',import.meta.url),'utf8')
 assert.ok(component.includes('/api/v6-comparison'));assert.doesNotMatch(component,/method:\s*['"]POST/)
 assert.doesNotMatch(await readFile(new URL('../frontend/src/MarketMonitor.vue',import.meta.url),'utf8'),/<V6Comparison/)
 const backend=await readFile(new URL('../backend/v6_comparison.py',import.meta.url),'utf8');assert.match(backend,/def report/)
})
