import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),src=new URL('../frontend/src/',import.meta.url)
const dataUrl=s=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const cache=new Map()
async function load(name){
 if(cache.has(name))return cache.get(name)
 let code=await readFile(new URL(name,src),'utf8')
 if(name.endsWith('.vue'))code=compileScript(parse(code).descriptor,{id:name,inlineTemplate:true}).content
 code=code.replace(/import ['"].*\.css['"]/g,'').replace(/from ['"]vue['"]/g,'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
 for(const m of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]){
  const next=m[1]==='http'?dataUrl('export const localFetch=()=>{throw Error("Display must not fetch with initial data")}; export const readJson=async r=>r.json();'):await load(m[1]+(m[1].endsWith('.vue')?'':'.ts'))
  code=code.replace(m[0],'from '+JSON.stringify(next))
 }
 const result=dataUrl(code);cache.set(name,result);return result
}
const Chart=(await import(await load('BusinessCycleChart.vue'))).default
const Page=(await import(await load('BusinessCycle.vue'))).default
const item=(key,region,kind,unit)=>({key,region,kind,unit,label:key==='tw_pmi'?'製造業 PMI（季調）':key,source:'官方來源',url:'https://www.ndc.gov.tw',description:'測試口徑',points:[{month:'2026-09',value:63.4},{month:'2026-10',value:62.5}],summary:{reading:'擴張放慢',color:'#4d8a70',change:-.9},checked_at:'2026-11-08T04:30:00+08:00'})
test('monthly chart splits a missing month instead of joining across it',async()=>{
 const html=await renderToString(createSSRApp(Chart,{points:[{month:'2026-08',value:3.4},{month:'2026-10',value:3.7}],kind:'inflation',unit:'%',label:'CPI',color:'#4d8a70'}))
 assert.equal((html.match(/<path /g)||[]).length,2)
 assert.ok(html.includes('2026-08'));assert.ok(html.includes('2026-10'));assert.ok(!html.includes('NaN'))
})
test('saved monthly view distinguishes regions, data dates, unknown release and manual source',async()=>{
 const series=[item('tw_signal','台灣','signal','分'),item('tw_pmi','台灣','pmi','點'),item('tw_cpi','台灣','inflation','%'),item('tw_orders','台灣','orders','%'),{...item('us_pmi','美國','pmi','點'),manual:true,error:'公開來源限制，手動補齊',released_at:'2026-11-02'},item('us_cpi','美國','inflation','%')]
 const html=await renderToString(createSSRApp(Page,{initial:{series,coverage:{available:6,total:6},checked_at:'2026-11-08T04:30:00+08:00',warnings:[],job:{status:'partial',message:'手動補齊'},note:'月資料不是股市機率'}}))
 for(const s of ['景氣循環分析','台灣','美國','擴張放慢','-0.9 點','百分點','資料 2026-10','官方查核保存','發布 來源未提供','2026-11-02','公開來源限制','近36個月份數值對照'])assert.ok(html.includes(s),s)
 assert.equal((html.match(/class="indicator-card"/g)||[]).length,6)
 assert.ok(!html.includes('NaN'));assert.ok(!html.includes('Infinity'))
})
