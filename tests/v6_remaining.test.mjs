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
let code=compileScript(parse(await readFile(new URL('../frontend/src/V6Remaining.vue',import.meta.url),'utf8')).descriptor,{id:'remaining',inlineTemplate:true}).content
code=code.replace(/from ["']vue["']/g,'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
const helper=url((await readFile(new URL('../frontend/src/rememberDisclosure.ts',import.meta.url),'utf8')).replace(/import ['"].*\.css['"]/g,''))
code=code.replace("from './rememberDisclosure'",'from '+JSON.stringify(helper))
const component=(await import(url(code))).default
const classes=(a,b,c)=>['up','flat','down'].map((key,i)=>({key,label:['上漲','平盤','下跌'][i],count:[a,b,c][i],probability:[a,b,c][i]/100,probability_pct:[a,b,c][i]}))
const original=classes(45,20,35)
const revision={status:'reference',method_version:'test',root_id:'test',issued_at:'2026-11-03T08:35:00+08:00',market_as_of:'2026-11-02',target_date:'2026-11-04',remaining_sessions:2,current_close:99,realized_return_pct:-1,classes:classes(25,15,60),note:'沒有估計前段與後段依存關係',event:{status:'observed',occurred:true,label:'回落2%已發生',probability:null}}
const group={root_id:'test',original_market_as_of:'2026-10-30',original_issued_at:'2026-11-02T08:35:00+08:00',anchor:100,target_date:'2026-11-04',original_classes:original,updates:[revision],latest:revision}
const value=groups=>({groups,summary:{completed:0,prospective_qualified:0,original_mean_rps:null},note:'窗口有重疊，不代表勝率'})
const render=async v=>renderToString(createSSRApp(component,{value:v}))
test('remaining horizon keeps original anchor/deadline and explicitly separates observed event',async()=>{
 const s=await render(value([group]));for(const text of ['固定到期 2026-11-04','剩餘2個交易日','較最初 +25.0 百分點','回落2%已發生','已發生的觀察，不是未來機率','未證明預測力提升'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/100\.0%|NaN|Infinity/);assert.match(s,/class="history-scroll"/)
})
test('missing new sources show no new percentages; original remains in immutable history',async()=>{
 const u={...revision,status:'unavailable',classes:[],note:'外資尚未公布'};const s=await render(value([{...group,updates:[u],latest:u}]))
 assert.ok(s.includes('外資尚未公布'));assert.ok(s.includes('45.0%'));assert.doesNotMatch(s,/class="remaining-probabilities"/)
})
test('completed result never draws a future probability or invented path',async()=>{
 const u={...revision,status:'complete',remaining_sessions:0,classes:[],actual_class:'down',actual_return_pct:-2,verified_on:'2026-11-05'}
 const s=await render(value([{...group,updates:[u],latest:u}]))
 assert.ok(s.includes('已到期核對'));assert.ok(s.includes('實際下跌 -2.0%'));assert.doesNotMatch(s,/class="remaining-probabilities"|<svg/)
})
test('no genuine new-method history remains pending and exposes no auto-update',async()=>{
 const s=await render({...value([]),note:'尚無新方法的剩餘期間紀錄'});assert.ok(s.includes('尚無新方法'));assert.ok(s.includes('0 組'));assert.doesNotMatch(s,/<button/)
})
