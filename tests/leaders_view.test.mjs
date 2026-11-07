import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
test('leaderboard switches ranking metrics, keeps null last and recalculates only local data',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/BrokerLeaders.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'leaders-test'}).content.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default;component.render=()=>null
 const old=globalThis.fetch;const calls=[]
 const rows=[{name:'甲',paired_rate:80,paired_samples:10,cycle_rate:20,cycle_samples:3,turnover:100,cycle_pnl:-100},{name:'乙',paired_rate:80,paired_samples:20,cycle_rate:60,cycle_samples:5,turnover:50,cycle_pnl:-200},{name:'缺值',paired_rate:null,paired_samples:0,cycle_rate:null,cycle_samples:0,turnover:null,cycle_pnl:null}]
 Object.assign(rows[0],{forward_5_rate:60,forward_5_return:-2,forward_5_samples:2,forward_10_rate:50,forward_10_return:3,forward_10_samples:20})
 Object.assign(rows[1],{forward_5_rate:60,forward_5_return:-1,forward_5_samples:3,forward_10_rate:80,forward_10_return:2,forward_10_samples:4})
 Object.assign(rows[2],{forward_5_rate:null,forward_5_return:null,forward_5_samples:0,forward_10_rate:null,forward_10_return:null,forward_10_samples:0})
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({as_of:options?.body?JSON.parse(options.body).date||'2026-09-10':url.includes('2026-09-09')?'2026-09-09':'2026-09-10',available_dates:['2026-09-10','2026-09-09'],rows,job:{status:'running'}})}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component);const s=app.mount({}).$.setupState
 t.after(()=>{app.unmount();globalThis.fetch=old})
 for(let i=0;i<8;i++)await nextTick()
 assert.deepEqual(s.rows.map(r=>r.name),['乙','甲','缺值'])
 s.sort='turnover';assert.deepEqual(s.rows.map(r=>r.name),['甲','乙','缺值'])
 s.sort='cycle_pnl';assert.deepEqual(s.rows.map(r=>r.name),['甲','乙','缺值'])
 for(const metric of ['forward_5_rate','forward_5_return','forward_10_rate']){s.sort=metric;assert.deepEqual(s.rows.map(r=>r.name),['乙','甲','缺值'])}
 s.sort='forward_10_return';assert.deepEqual(s.rows.map(r=>r.name),['甲','乙','缺值'])
 rows[1].forward_10_rate=50;s.sort='forward_10_rate';assert.deepEqual(s.rows.map(r=>r.name),['甲','乙','缺值'])
 s.date='2026-09-09';await s.changeDate();assert.equal(calls.at(-1).url,'/api/leaders?date=2026-09-09')
 await s.load(true);assert.equal(calls.at(-1).url,'/api/leaders/refresh')
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{date:'2026-09-09'})
 assert.equal(calls.some(c=>c.url.includes('/api/sync')||c.url.includes('/brokers')),false)
 // Remounting the tab and changing a sort must not fetch again, even during sync.
 app.unmount();const previousCalls=calls.length
 const again=renderer.createApp(component);const againState=again.mount({}).$.setupState
 t.after(()=>again.unmount())
 for(let i=0;i<8;i++)await nextTick()
 assert.equal(calls.length,previousCalls)
 assert.equal(againState.report.as_of,'2026-09-10')
 againState.date='2026-09-09';await againState.changeDate()
 assert.equal(calls.length,previousCalls)
 await againState.load(true);assert.equal(calls.length,previousCalls+1)
 const original=againState.report
 globalThis.fetch=async()=>{throw new Error('offline')}
 await againState.load(true);assert.equal(againState.report,original)
 assert.match(againState.error,/8765.*服務已停止/)
})
