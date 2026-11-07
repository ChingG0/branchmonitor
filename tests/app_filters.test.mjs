import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL,fileURLToPath} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick,vModelText} from 'vue'
const require=createRequire(import.meta.url)
const root=new URL('../',import.meta.url)

test('actual App applies both numeric win filters, resets paging, and reports the applied conditions',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('frontend/src/App.vue',root),'utf8'))
 let code=compileScript(descriptor,{id:'app-filter-test'}).content
 code=code.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './table'",`from ${JSON.stringify(new URL('frontend/src/table.ts',root).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 component.render=()=>null
 const stocks=[
  {symbol:'A',name:'甲',win_rate:100,closed_count:1},
  {symbol:'B',name:'乙',win_rate:50,closed_count:10},
  {symbol:'C',name:'丙',win_rate:null,closed_count:0},
  {symbol:'D',name:'丁',win_rate:80,closed_count:5}
 ]
 const old={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch}
 globalThis.window={setInterval:()=>0,clearInterval:()=>{},setTimeout:()=>0,clearTimeout:()=>{}}
 globalThis.document={}
 globalThis.fetch=async()=>({ok:true,json:async()=>({stock_stats:stocks,daily:[],positions:[],cycles:[],closed:[],signals:[],settings:{},job:null})})
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component)
 const vm=app.mount({})
 t.after(()=>{app.unmount();Object.assign(globalThis,old)})
 for(let i=0;i<5;i++)await nextTick()
 const state=vm.$.setupState
 state.tab='stock_stats';await nextTick()
 assert.equal(state.rows.length,4)
 // Invoke Vue's real number input directive; it emits numbers, then '' when cleared.
 const field=(name)=>{
  const listeners={}
  const input={type:'number',value:'',addEventListener:(event,fn)=>{(listeners[event]??=[]).push(fn)}}
  vModelText.created(input,{modifiers:{}},{props:{type:'number','onUpdate:modelValue':value=>{state[name]=value}}})
  return async value=>{input.value=value;for(const fn of listeners.input||[])fn({target:input});await nextTick()}
 }
 const rate=field('minRate'),samples=field('minSamples')
 state.page=2
 await rate('60')
 assert.deepEqual(state.rows.map(r=>r.symbol),['A','D'])
 assert.equal(state.page,1)
 await samples('3')
 assert.deepEqual(state.visibleRows.map(r=>r.symbol),['D'])
 assert.equal(state.winFilterSummary,'勝率 ≥ 60.0% · 可評分清倉 ≥ 3 次')
 await samples('10')
 assert.equal(state.rows.length,0)
 assert.equal(state.pages,1)
 await samples('')
 assert.equal(state.rows.length,2)
 state.clearFilters();await nextTick()
 assert.equal(state.rows.length,4)
 assert.equal(state.winFilterSummary,'未限制最低勝率與清倉次數')
 state.d.fresh_buys=[{symbol:'A',name:'甲',buy:50,buy_value:500000}, {symbol:'B',name:'乙',buy:200,buy_value:2000000}, {symbol:'C',name:'丙',buy:150,buy_value:null}]
 state.d.streak_buys=[{symbol:'D',name:'丁',streak:2,buy:300}, {symbol:'E',name:'戊',streak:3,buy:50}]
 state.tab='fresh_buys';await nextTick()
 assert.deepEqual(state.rows.map(r=>r.symbol),['B','C'])
 const minimum=field('minBuy')
 await minimum('')
 assert.deepEqual(state.rows.map(r=>r.symbol),['B','A','C'])
 await minimum('200')
 assert.deepEqual(state.rows.map(r=>r.symbol),['B'])
 state.tab='streak_buys';await nextTick()
 assert.deepEqual(state.rows.map(r=>r.symbol),['E','D'])
 state.search='丁';await nextTick()
 assert.deepEqual(state.rows.map(r=>r.symbol),['D'])
})
