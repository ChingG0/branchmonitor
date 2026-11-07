import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick,createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url)
async function compile(file,inline=false){
 let source=await readFile(new URL('../frontend/src/'+file,import.meta.url),'utf8')
 if(inline)source=source.replace('</script>','\ndefineExpose({nav})\n</script>')
 const {descriptor}=parse(source)
 let code=compileScript(descriptor,{id:'rs-test',inlineTemplate:inline}).content
 code=code.replace(/import (\w+) from ['"]\.\/\w+\.vue['"]/g,"const $1={inheritAttrs:false,render:()=>'$1'}")
 code=code.replace(/from ['"]vue['"]/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replace("from './table'",`from ${JSON.stringify(pathToFileURL(new URL('../frontend/src/table.ts',import.meta.url).pathname).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const c=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 if(!inline)c.render=()=>null
 return c
}
const flush=async()=>{for(let i=0;i<8;i++)await nextTick()}
for(const etf of [false,true])test(`${etf?'ETF':'Stock'} RS switching reads its own cache and refresh endpoint`,async(t)=>{
 const old=globalThis.fetch;t.after(()=>{globalThis.fetch=old})
 const calls=[]
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({available:true,as_of:'2026-09-10',mode:url.split('=').at(-1),left:[],right:[],equal:[],job:null})}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(await compile('RsRanking.vue'),{etf});const s=app.mount({}).$.setupState;t.after(()=>app.unmount());await flush()
 const endpoint=etf?'/api/etf-rs':'/api/rs'
 assert.equal(calls[0].url,endpoint+'?period=20')
 s.period='60';await flush();assert.equal(s.report.mode,'60')
 s.period='ytd';await flush();assert.equal(s.report.mode,'ytd')
 assert.equal(calls.filter(c=>c.options).length,0)
 await s.refresh()
 assert.deepEqual(calls.filter(c=>c.options).map(c=>c.url),[endpoint+'/refresh'])
 assert.equal(calls.some(c=>c.url.includes('/api/sync')||c.url.includes('/brokers')),false)
})
for(const view of ['rs','etf-rs'])test(`${view} tab renders its own surface without overview or settings content`,async()=>{
 const component={...await compile('App.vue',true)};const setup=component.setup
 component.setup=(props,context)=>{
  let exposed
  const result=setup(props,{...context,expose:value=>{exposed=value;context.expose(value)}})
  exposed.nav(view);return result
 }
 const html=await renderToString(createSSRApp(component))
 assert.match(html,/RsRanking/)
 assert.doesNotMatch(html,/aria-label="主要統計"|class="settings-grid"|id="data-table"/)
})
test('broker leaders tab replaces the main surface without showing RS or overview',async()=>{
 const component={...await compile('App.vue',true)};const setup=component.setup
 component.setup=(props,context)=>{
  let exposed
  const result=setup(props,{...context,expose:value=>{exposed=value;context.expose(value)}})
  exposed.nav('leaders');return result
 }
 const html=await renderToString(createSSRApp(component))
 assert.match(html,/BrokerLeaders/)
 assert.doesNotMatch(html,/RsRanking|aria-label="主要統計"|class="settings-grid"|id="data-table"/)
})
for(const [view,name] of [['etf-plan','StrategyResearch'],['market','MarketMonitor'],['broker-focus','BrokerFocus'],['sector-flow','SectorFlow']])test(`${view} owns its surface without overview`,async()=>{
 const component={...await compile('App.vue',true)},setup=component.setup
 component.setup=(props,context)=>{let exposed;const result=setup(props,{...context,expose:value=>{exposed=value;context.expose(value)}});exposed.nav(view);return result}
 const html=await renderToString(createSSRApp(component))
 assert.match(html,new RegExp(name));assert.doesNotMatch(html,/aria-label="主要統計"|class="settings-grid"|id="data-table"/)
})

test('retired research and futures forecast tabs are absent from navigation',async()=>{
 const source=await readFile(new URL('../frontend/src/App.vue',import.meta.url),'utf8')
 assert.doesNotMatch(source,/id:'(?:research|tx-forecast)'|ResearchReport|FuturesForecast/)
})
