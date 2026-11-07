import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helperUrl=moduleUrl(await readFile(new URL('../frontend/src/marketLayout.ts',import.meta.url),'utf8'))
const comparisonUrl=moduleUrl(await readFile(new URL('../frontend/src/marketComparison.ts',import.meta.url),'utf8'))
const {normalizeOrder,moveCard}=await import(helperUrl)
test('layout validates old saved values, appends new cards and moves in both directions',()=>{
 assert.deepEqual(normalizeOrder(['b','b',null,'removed'],['a','b','new']),['b','a','new'])
 assert.deepEqual(normalizeOrder({},['a','b']),['a','b'])
 assert.deepEqual(moveCard(['a','b','c'],'a','c'),['b','c','a'])
 assert.deepEqual(moveCard(['a','b','c'],'c','a'),['c','a','b'])
 assert.deepEqual(moveCard(['a','b'],'missing','b'),['a','b'])
})
test('dashboard remount restores order, reads saved data only, and manual refresh targets one card',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/MarketMonitor.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'market-test'}).content.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`).replace("from './marketLayout'",`from ${JSON.stringify(helperUrl)}`)
 code=code.replaceAll("from './marketComparison'",`from ${JSON.stringify(comparisonUrl)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 const component=(await import(moduleUrl(code))).default;component.render=()=>null
 const oldFetch=globalThis.fetch,oldStorage=globalThis.localStorage,calls=[],saved=new Map()
 globalThis.localStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)}
 const report={cards:[{id:'tw_news',title:'新聞',data:{items:[]}},{id:'twd',title:'台幣',data:{value:31.8}}],job:{running:false}}
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>report}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component),s=app.mount({}).$.setupState
 t.after(()=>{globalThis.fetch=oldFetch;globalThis.localStorage=oldStorage})
 for(let i=0;i<8;i++)await nextTick()
 assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/market-monitor');assert.equal(calls[0].options,undefined)
 s.move('twd',-1);assert.deepEqual(s.cards.map(c=>c.id),['twd','tw_news']);app.unmount()
 const again=renderer.createApp(component),a=again.mount({}).$.setupState;t.after(()=>again.unmount())
 for(let i=0;i<8;i++)await nextTick()
 assert.deepEqual(a.cards.map(c=>c.id),['twd','tw_news']);assert.equal(calls.length,2)
 await a.refresh('twd');assert.equal(calls[2].url,'/api/market-monitor/refresh');assert.deepEqual(JSON.parse(calls[2].options.body),{card:'twd'});assert.equal(calls.length,4)
 const original=a.report;globalThis.fetch=async()=>{throw new Error('offline')};await a.refresh();assert.equal(a.report,original);assert.match(a.error,/8765.*服務已停止/)
 a.reset();assert.deepEqual(a.order,['tw_news','twd'])
})
