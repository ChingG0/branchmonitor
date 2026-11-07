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
const url=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helper=url(await readFile(new URL('../frontend/src/marketComparison.ts',import.meta.url),'utf8'))
const {readingComparison,flowChange,flowReadings}=await import(helper)
const format=url(await readFile(new URL('../frontend/src/v6Format.ts',import.meta.url),'utf8'))
const {formatObservationChange}=await import(format)
test('comparisons use prior source day, exclude future values, and keep zero valid',()=>{
 const r=readingComparison([{date:'2026-10-21',value:99},{date:'2026-10-20',value:27.2},{date:'2026-10-22',value:90}], '2026-10-21',31.37)
 assert.deepEqual(r.previous,{date:'2026-10-20',value:27.2});assert.equal(r.readings.length,2);assert.ok(Math.abs(r.delta-4.17)<1e-9)
 assert.equal(readingComparison([],'2026-10-21',null).delta,null)
 assert.equal(readingComparison([{date:'2026-10-20',value:0}],'2026-10-21',0).delta,0)
})
test('net flow explanations handle sign flips and shrinking sales without percent division',()=>{
 for(const [before,now,label] of [[-3,2,'由賣轉買'],[3,-2,'由買轉賣'],[-3,-1,'賣超減少'],[-1,-3,'賣超增加'],[1,3,'買超增加'],[3,1,'買超減少'],[0,1,'轉為買超'],[1,0,'買賣持平'],[0,0,'淨額持平']])assert.equal(flowChange(before,now),label)
 assert.deepEqual(flowReadings([{date:'2026-10-20',rows:[{name:'投信',net:10},{name:'外資',net:-20}]}],'外資'),[{date:'2026-10-20',value:-20}])
 assert.equal(formatObservationChange(3.92,'%'),'+3.92 個百分點')
 assert.equal(formatObservationChange(4.17,'/100'),'+4.17 分')
 assert.equal(formatObservationChange(-3e8,'元'),'-3 億元')
})
async function component(name){
 const {descriptor}=parse(await readFile(new URL('../frontend/src/'+name,import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'comparison-test',inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replace("from './marketComparison'",`from ${JSON.stringify(helper)}`)
 return (await import(url(code))).default
}
test('fear and flow cards render dated hollow/solid markers and exact changes',async()=>{
 const gauge=await component('FearGauge.vue')
 const html=await renderToString(createSSRApp(gauge,{date:'2026-10-21',value:31.37,history:[{date:'2026-10-20',value:27.2}]}))
 for(const text of ['前日 27.2 分','當日 31.37 分','+4.17 分','2026-10-20','往貪婪端移動','class="prior"','class="current"'])assert.ok(html.includes(text),text)
 const flow=await component('MarketFlowTrend.vue')
 const result=await renderToString(createSSRApp(flow,{name:'外資',date:'2026-10-21',net:2e8,history:[{date:'2026-10-20',rows:[{name:'外資',net:-3e8}]}]}))
 for(const text of ['前日 -3.00 億','由賣轉買','較前日 +5.00 億','2026-10-20','虛線為零'])assert.ok(result.includes(text),text)
 assert.equal((result.match(/<circle /g)||[]).length,2)
})

test('quote percentage and absolute delta share the dated prior even when source percentage uses another baseline',async()=>{
 const {quoteComparison}=await import(helper)
 const compared=quoteComparison([{date:'2026-10-21',value:32.5776}],'2026-10-22',32.48,.2562)
 assert.equal(compared.label,'較前值');assert.ok(compared.value<0)
 assert.deepEqual(quoteComparison([],'2026-10-22',32.48,.2562),{label:'來源漲跌',value:.2562})
})
