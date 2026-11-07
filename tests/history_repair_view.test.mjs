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

test('history repair view is read-only on mount and requires explicit actions for plan, retry and run',async(t)=>{
 const source=await readFile(new URL('../frontend/src/HistoryRepair.vue',import.meta.url),'utf8')
 const {descriptor}=parse(source)
 let code=compileScript(descriptor,{id:'history-repair-test'}).content
 code=code.replace("import Icon from './Icon.vue'","const Icon={render:()=>null}")
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 const component=(await import(moduleUrl(code))).default
 component.render=()=>null
 const calls=[]
 const payload={
  plan:{id:'p1',created_at:'2026-09-24T09:00:00',through_date:'2026-09-16',target_date:'2026-09-23',selected:11044,summary:{selected:11044,inserted:11044,by_tier:{R0_OPEN:9375,R1_LATEST:87,R2_RECENT:963,R3_RECONCILE:528,R4_ARCHIVE:91},network_requests:0,range:'tail_after_last_success'}},
  job:null,
  counts:{queued:11044,running:0,complete:0,failed:0,interrupted:0},
  queued_by_tier:{R0_OPEN:9375,R1_LATEST:87,R2_RECENT:963,R3_RECONCILE:528,R4_ARCHIVE:91},
  rows:[],retry:{due:0,waiting:0,exhausted:0,interrupted:0},retry_policy:{max_attempts:5,backoff_minutes:[15,60,240,720,1440]},
  statuses:['queued','running','complete','failed','interrupted'],repair_tiers:['R0_OPEN','R1_LATEST','R2_RECENT','R3_RECONCILE','R4_ARCHIVE']
 }
 const oldFetch=globalThis.fetch
 globalThis.fetch=async(url,options)=>{calls.push({url:String(url),options});return {ok:true,status:200,json:async()=>({...payload,...(String(url).endsWith('/retry')?{activated:3}:{})})}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component),state=app.mount({}).$.setupState
 t.after(()=>{app.unmount();globalThis.fetch=oldFetch})
 for(let i=0;i<5;i++)await nextTick()
 assert.equal(calls.length,1)
 assert.equal(calls[0].url,'/api/history-repair?limit=20')
 assert.equal(calls[0].options,undefined)

 state.throughDate='2026-09-16'
 await state.act('plan')
 assert.equal(calls[1].url,'/api/history-repair/plan')
 assert.deepEqual(JSON.parse(calls[1].options.body),{through_date:'2026-09-16'})
 assert.equal(calls[2].url,'/api/history-repair?limit=20')

 await state.act('retry')
 assert.equal(calls[3].url,'/api/history-repair/retry')
 assert.deepEqual(JSON.parse(calls[3].options.body),{limit:500,force:false})
 assert.equal(calls[4].url,'/api/history-repair?limit=20')

 state.batchSize=25
 await state.act('run')
 assert.equal(calls[5].url,'/api/history-repair/run')
 assert.deepEqual(JSON.parse(calls[5].options.body),{limit:25,resume:true})
 assert.equal(calls[6].url,'/api/history-repair?limit=20')
})
