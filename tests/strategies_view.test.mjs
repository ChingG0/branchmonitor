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
 const source=await readFile(new URL('../frontend/src/StrategyResearch.vue',import.meta.url),'utf8')
 assert.equal(source.includes('價差回歸配對'),false);assert.equal(source.includes('defineProps'),false);assert.equal(source.includes('borrow_pct'),false)
 const {descriptor}=parse(source)
 let code=compileScript(descriptor,{id:'strategy-test'}).content.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`).replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const c=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default;c.render=()=>null;return c
}
test('ETF plan uses saved snapshot on mount and refreshes only after an explicit action',async t=>{
 const calls=[],old=globalThis.fetch
 const settings={etfs:['0050'],auto_ytd_count:5,auto_ytd_min_rs:90,stock_value:800001,cash_value:1000000,allocation_count:3}
 const state={settings,snapshot:{etfs:[{symbol:'0050',name:'Test',ytd_total:10},{symbol:'0056',name:'Other',ytd_total:null}],warnings:[]},history:[],job:{status:'complete',message:'完成'},etf_method:'method'}
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>state}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(await component()),s=app.mount({}).$.setupState;t.after(()=>{app.unmount();globalThis.fetch=old})
 for(let i=0;i<16;i++)await nextTick()
 assert.deepEqual(calls.map(c=>c.url),['/api/strategies']);assert.equal(calls[0].options!==undefined&&calls[0].options.method==='POST',false)
 assert.equal(s.etfs[0].symbol,'0050');s.search='Other';assert.equal(s.etfs.length,1);s.search=''
 s.history='saved-version';await s.load();assert.equal(calls.at(-1).url,'/api/strategies?id=saved-version')
 await s.save();assert.deepEqual(JSON.parse(calls.find(c=>c.url.endsWith('/settings')).options.body),settings)
 s.target='2026-09-18';await s.refresh();assert.deepEqual(JSON.parse(calls.find(c=>c.url.endsWith('/refresh')).options.body),{date:'2026-09-18',force:false})
 await s.refresh(true);assert.deepEqual(JSON.parse(calls.filter(c=>c.url.endsWith('/refresh')).at(-1).options.body),{date:'2026-09-18',force:true})
 assert.equal(calls.some(c=>c.url.includes('/sync')||c.url.includes('/research/generate')),false)
})
