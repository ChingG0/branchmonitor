import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
const example=day=>({as_of:day,available_dates:['2026-09-10','2026-09-09'],market:{available:true,job:null,buy_top:[{symbol:'lots'}],sell_top:[],buy_value_top:[{symbol:'value'}],sell_value_top:[]},watched:{totals:{total_branches:19},branches:[],buy_top:[{symbol:'watched-lots'}],sell_top:[],buy_value_top:[{symbol:'watched-value'}],sell_value_top:[]}})

test('main watch selects exact dates, rejects stale responses and refreshes only the market endpoint',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/MainWatch.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'main-watch-test'}).content
 code=code.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 component.render=()=>null
 const old={window:globalThis.window,fetch:globalThis.fetch}
 const calls=[];let interval=null,cleared=false,delayed=false,resolveOld
 globalThis.window={setInterval:fn=>{interval=fn;return 7},clearInterval:id=>{cleared=id===7}}
 globalThis.fetch=async(url,options)=>{
  calls.push({url,options})
  if(url.includes('2026-09-09')&&delayed)return new Promise(resolve=>{resolveOld=()=>resolve({ok:true,json:async()=>example('2026-09-09')})})
  return {ok:true,json:async()=>options?{status:'running'}:example(url.includes('2026-09-09')?'2026-09-09':'2026-09-10')}
 }
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component,{branchSyncRunning:false});const vm=app.mount({})
 t.after(()=>{app.unmount();Object.assign(globalThis,old);assert.equal(cleared,true)})
 for(let i=0;i<5;i++)await nextTick()
 const s=vm.$.setupState
 assert.equal(s.report.as_of,'2026-09-10');assert.equal(typeof interval,'function')
 assert.equal(s.marketBuyRows[0].symbol,'lots');s.rankMetric='value';await nextTick();assert.equal(s.marketBuyRows[0].symbol,'value');assert.equal(s.watchedBuyRows[0].symbol,'watched-value');s.rankMetric='lots'
 s.date='2026-09-09';await s.selectDate()
 assert.equal(s.report.as_of,'2026-09-09')
 delayed=true;s.date='2026-09-09';const pending=s.selectDate()
 assert.equal(s.report,null);assert.equal(s.availableDates.length,2)
 s.date='2026-09-10';await s.selectDate();resolveOld();await pending
 assert.equal(s.report.as_of,'2026-09-10')
 await s.refreshMarket()
 const posts=calls.filter(c=>c.options?.method==='POST')
 assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/main-watch/refresh');assert.equal(posts[0].options.body,'{}')
 assert.equal(calls.some(c=>c.url.includes('/api/sync')),false)
})
