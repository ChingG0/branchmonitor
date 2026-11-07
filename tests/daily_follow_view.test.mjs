import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'

const require=createRequire(import.meta.url)
const horizon=(status,hit=null)=>({status,hit,net_return_pct:hit===null?null:hit?1:-1})
let signalId=0
const signal=(one,five)=>({symbol:String(++signalId),name:'測試',backtest:{status:'ready',entry_price:100,horizons:{'1':one,'5':five,'10':horizon('pending'),'20':horizon('pending'),'40':horizon('pending')}}})
const saved=(date,buy,sell)=>({date,created_at:date+'T18:00:00',mode:'forward',coverage:{observed:20,total:20,missing:[]},buy,sell,warnings:[],round_trip_cost_pct:.6})

test('daily follow calculators keep V1 and V4 capital rules separate',async(t)=>{
 const source=await readFile(new URL('../frontend/src/DailyFollow.vue',import.meta.url),'utf8')
 const {descriptor}=parse(source)
 let code=compileScript(descriptor,{id:'daily-follow-test'}).content
 code=code.replace(/import (\w+) from ['"]\.\/\w+\.vue['"]/g,"const $1={render:()=>null}")
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 component.render=()=>null
 const reports=[
  saved('2026-09-24',[signal(horizon('scored',true),horizon('pending')),signal(horizon('pending'),horizon('pending'))],[signal(horizon('scored',false),horizon('pending')),signal(horizon('gap'),horizon('pending'))]),
  saved('2026-09-23',[signal(horizon('scored',true),horizon('scored',false)),signal(horizon('scored',false),horizon('pending'))],[signal(horizon('scored',true),horizon('scored',true)),signal(horizon('scored',false),horizon('pending'))]),
 ]
 reports[0].v4={buy:[
  {symbol:'v41',name:'V4一',close:100,strategies:{hybrid20:{status:'scored',entry_price:100,net_return_pct:10}}},
  {symbol:'v42',name:'V4二',close:100,strategies:{hybrid20:{status:'unfilled'}}},
 ],sell:[]}
 const payload={available:true,selected:reports[0],reports,saved_dates:reports.map(r=>r.date),eligible_dates:[],summary:{'1':{},'5':{},'10':{},'20':{},'40':{}},model_performance:{models:{v1:{curve:[{date:'2026-09-23',value:1},{date:'2026-09-24',value:2}]},v4b:{curve:[{date:'2026-09-24',value:1}]},v9:{label:'V9 台指期回檔反手',curve:[]}},comparison:{}},method:'',round_trip_cost_pct:.6}
 const old=globalThis.fetch
 globalThis.fetch=async()=>({ok:true,json:async()=>payload})
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component),state=app.mount({}).$.setupState
 t.after(()=>{app.unmount();globalThis.fetch=old})
 for(let i=0;i<8;i++)await nextTick()
 assert.deepEqual(state.v1Activity.map(row=>[row.date,row.buy.length,row.sell.length]),[['2026-09-23',2,2],['2026-09-24',2,2]])
 state.scoreSide='buy';await nextTick();assert.equal(state.scoreSideLabel,'買方')
 state.scoreSide='sell';await nextTick();assert.equal(state.scoreSideLabel,'賣方')
 state.trialSide='buy';state.trialAmount=100000;await nextTick()
 assert.deepEqual([state.trialStats['1'].report_days,state.trialStats['1'].positions,state.trialStats['1'].invested,state.trialStats['1'].profit,state.trialStats['1'].return_pct],[1,2,100000,0,0])
 assert.equal(state.trialStats['5'].report_days,0)
 assert.deepEqual(state.modelTabs.map(row=>row.key),['v1','v10','v11','v2','v3','v4','v5','v8','v9'])
 state.activeModel='v4b';await nextTick()
 assert.deepEqual(state.v4Activity.map(row=>[row.date,row.buy.map(signal=>signal),row.sell.length]),[['2026-09-24',['v41','v42'],0]])
 assert.deepEqual([state.v4TrialStats.report_days,state.v4TrialStats.positions,state.v4TrialStats.unfilled,state.v4TrialStats.invested,state.v4TrialStats.profit,state.v4TrialStats.return_pct],[1,1,1,50000,5000,5])
 assert.equal(state.v4TrialRows.length,2)
 state.data.selected.v4.buy[0].strategies.hybrid20={status:'pending'};await nextTick()
 assert.deepEqual([state.v4TrialRows[0].trial_price,state.v4TrialRows[0].trial_shares,state.v4TrialRows[0].trial_price_estimated],[95,526,true])
 state.activeModel='v9';await nextTick();assert.equal(state.activeModelStats.label,'V9 台指期回檔反手')
 state.trialAmount='錯誤';await nextTick();assert.equal(state.trialCapital,0)
})
