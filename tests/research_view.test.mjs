import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
test('market v4 exposes only news, allocation and futures research',async()=>{
 const app=await readFile(new URL('../frontend/src/App.vue',import.meta.url),'utf8')
 const research=await readFile(new URL('../frontend/src/ResearchReport.vue',import.meta.url),'utf8')
 const futures=await readFile(new URL('../frontend/src/FuturesForecast.vue',import.meta.url),'utf8')
 assert.doesNotMatch(app,/nav\('stock-backtest'\)|view==='stock-backtest'/)
 assert.match(app,/FuturesForecast/)
 assert.match(futures,/\/api\/futures/)
 assert.doesNotMatch(futures,/選股回測|實際持股|\/api\/backtest/)
 assert.match(research,/個股、ETF 名單與選股回測不會送進此報告/)
 assert.match(research,/整體股票配置％與台指開收盤預測/)
 assert.doesNotMatch(research,/v-for="g in evidence\.sector_inflow/)
})
test('research reads only on mount, renders citation segments safely, sends generation explicitly and clears credentials',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/ResearchReport.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'research-test'}).content.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default;component.render=()=>null
 const old=globalThis.fetch,calls=[]
 const evidence={date:'2026-09-18',candidates:[],warnings:[],dates:{}}
 const report={config:{configured:false,model:'chat-latest',source:'尚未設定'},history:[],selected:null,running:null,dates:['2026-09-18','2026-09-17']}
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url.includes('/context')?evidence:url.endsWith('/generate')?{id:'one',cached:false}:report}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component),s=app.mount({}).$.setupState;t.after(()=>{app.unmount();globalThis.fetch=old})
 for(let i=0;i<16;i++)await nextTick()
 assert.equal(s.date,'2026-09-18');assert.equal(calls.filter(c=>c.options).length,0)
 s.state={...report,selected:{status:'complete',context:evidence,result:{parts:[{text:'## 研究\n新聞'},{text:'公告',url:'https://example.org'},{text:'<script>bad</script>',url:'javascript:alert(1)'}]}}}
 assert.equal(s.blocks[0].heading,true);assert.equal(s.blocks[1].parts[2].url,undefined);assert.equal(s.blocks[1].parts[2].text,'<script>bad</script>')
 s.apiKey='sk-never-print-this';await s.saveConfig();assert.equal(s.apiKey,'');assert.equal(calls.filter(c=>c.url.endsWith('/config')).length,1)
 await s.generate(true);assert.deepEqual(JSON.parse(calls.find(c=>c.url.endsWith('/generate')).options.body),{date:'',force:true})
 assert.equal(calls.some(c=>c.url.includes('/api/sync')),false)
 s.date='2026-09-17';for(let i=0;i<12;i++)await nextTick();assert.equal(s.selectedId,'');assert.ok(calls.some(c=>c.url.includes('/context?date=2026-09-17')))
})
