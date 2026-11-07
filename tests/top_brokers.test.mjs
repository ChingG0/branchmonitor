import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
async function component(){
 const {descriptor}=parse(await readFile(new URL('../frontend/src/TopBrokers.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'broker-test'}).content
 code=code.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 component.render=()=>null;return component
}
const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
const flush=async()=>{for(let i=0;i<8;i++)await nextTick()}
test('individual broker detail posts only its stock/date, polls and preserves complete cache',async(t)=>{
 const old={fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout}
 t.after(()=>Object.assign(globalThis,old))
 let tick,cleared=false,complete=false;const calls=[];let names
 globalThis.setTimeout=fn=>{tick=fn;return 9};globalThis.clearTimeout=id=>{cleared=id===9}
 globalThis.fetch=async(url,options)=>{
  calls.push({url,options})
  return {ok:true,json:async()=>complete?{status:'complete',payload:{top:[{name:'元大證券'},{name:'凱基-台北'}]}}:{status:options?'running':calls.length>2?'running':'idle',message:'loading'}}
 }
 const app=renderer.createApp(await component(),{symbol:'6770',date:'2026-09-10',onLoaded:v=>{names=v}})
 const vm=app.mount({});await flush()
 const posts=calls.filter(c=>c.options?.method==='POST')
 assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/main-watch/brokers')
 assert.deepEqual(JSON.parse(posts[0].options.body),{symbol:'6770',date:'2026-09-10'})
 assert.equal(calls.some(c=>c.url.includes('/api/sync')),false)
 complete=true;tick();await flush()
 assert.deepEqual(names,['元大證券','凱基-台北'])
 await vm.$.setupState.load()
 assert.equal(calls.filter(c=>c.options).length,1)
 app.unmount();assert.equal(cleared,true)
})
test('leaving stock detail ignores its late response and does not enqueue a stale stock',async(t)=>{
 const old=globalThis.fetch;t.after(()=>{globalThis.fetch=old})
 let resolve;const calls=[]
 globalThis.fetch=(url,options)=>{calls.push({url,options});return new Promise(r=>{resolve=r})}
 const app=renderer.createApp(await component(),{symbol:'6770',date:'2026-09-09'})
 app.mount({});app.unmount()
 resolve({ok:true,json:async()=>({status:'idle'})});await flush()
 assert.equal(calls.length,1);assert.match(calls[0].url,/date=2026-09-09/)
})
