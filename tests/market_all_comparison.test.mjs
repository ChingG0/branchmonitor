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
const helpers={};for(const name of ['marketComparison','v6Format','rateChart'])helpers[name]=url(await readFile(new URL('../frontend/src/'+name+'.ts',import.meta.url),'utf8'))
const {rateWindow,stepPath,rateAt}=await import(helpers.rateChart)
const history=[{date:'2024-09-19',low:4.75,high:5,change_bp:-50},{date:'2025-12-11',low:3.5,high:3.75,change_bp:-25},{date:'2026-09-17',low:3.75,high:4,change_bp:25}]
test('rate windows anchor prior known policy and use horizontal/vertical steps without future policy',()=>{
 const view=rateWindow([...history,{date:'2026-12-01',low:5,high:5.25,change_bp:25}],'2026-10-21',2)
 assert.equal(view.rows[0].date,'2024-10-21');assert.equal(view.rows[0].high,5);assert.equal(view.rows.at(-1).high,4)
 assert.equal(rateAt(view.all,'2026-08-01').high,3.75)
 assert.equal(stepPath([{date:'1',high:5},{date:'2',high:4}],Number,n=>n,'3'),'M 1 5 H 2 V 4 H 3')
 assert.equal(stepPath([],Number,n=>n,'3'),'')
})
async function component(name){
 const {descriptor}=parse(await readFile(new URL('../frontend/src/'+name,import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'market-all',inlineTemplate:true}).content.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 for(const [name,helper] of Object.entries(helpers))code=code.replace("from './"+name+"'",`from ${JSON.stringify(helper)}`)
 return (await import(url(code))).default
}
test('generic cards display percentage-point deltas and do not invent missing data',async()=>{
 const C=await component('MarketValueTrend.vue')
 const html=await renderToString(createSSRApp(C,{value:24.09,date:'2026-10-21',unit:'%',history:[{date:'2026-10-20',value:21.2}]}))
 for(const text of ['21.2%','24.09%','+2.89 個百分點','2026-10-20'])assert.ok(html.includes(text),text)
 assert.equal((html.match(/<circle /g)||[]).length,2)
 const missing=await renderToString(createSSRApp(C,{value:16,date:'2026-10-21',history:[]}));assert.ok(missing.includes('前值尚未取得'));assert.ok(!missing.includes('<circle'))
})
test('official rate chart labels upper/lower range, recent change and next meeting',async()=>{
 const C=await component('FedRateChart.vue')
 const html=await renderToString(createSSRApp(C,{data:{as_of:'2026-09-17',observed_through:'2026-10-21',low:3.75,high:4,history,last_change_bp:25,next_start:'2026-10-27',next_end:'2026-10-28'}}))
 for(const text of ['3.75–4.00','目標上緣','目標下緣','升息 25 基點','2026-10-27','2026-10-28','Fed 官方'])assert.ok(html.includes(text),text)
 assert.equal((html.match(/<path /g)||[]).length,2)
})
