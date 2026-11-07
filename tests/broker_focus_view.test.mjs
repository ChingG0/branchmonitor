import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,nextTick} from 'vue'
const require=createRequire(import.meta.url)
test('focus ranks by branch count, filters groups, uses shared multi-label sectors and refreshes only turnover',async(t)=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/BrokerFocus.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'focus-test'}).content.replace(/import (\w+) from '(\.\/\w+\.vue)'/g,'const $1={render:()=>null}')
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default;component.render=()=>null
 const old=globalThis.fetch,calls=[]
 const report={consensus:{rows:[{symbol:'2330',name:'甲',buy_count:2,sell_count:3,buy_lots:10000,sell_lots:500,buy_value:1000,sell_value:500,net:-10,net_value:-100},{symbol:'2222',name:'乙',buy_count:3,sell_count:1,buy_lots:10,sell_lots:900,buy_value:10,sell_value:900,net:5,net_value:50}],available_dates:['2026-09-16']},turnover:{rows:[{symbol:'2330',name:'甲',amount:100},{symbol:'2222',name:'乙',amount:200}]},rs:{rows:[{symbol:'2330',name:'甲'}]},tags:{'2330':'AI、晶圓代工','2222':'晶圓代工'},sector_memberships:{'2330':['AI','晶圓代工'],'2222':['晶圓代工']},sector_classification:{snapshot:'2026-09-11',error:'',groups:[]},job:null}
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/tag')?JSON.parse(options.body):report}}
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 const app=renderer.createApp(component),s=app.mount({}).$.setupState;t.after(()=>{app.unmount();globalThis.fetch=old})
 for(let i=0;i<8;i++)await nextTick()
 assert.deepEqual(s.rankings[0].rows.map(r=>r.symbol),['2222','2330']);assert.equal(s.rankings[1].rows.length,1)
 s.rankMetric='value';await nextTick();assert.deepEqual(s.rankings[0].rows.map(r=>r.symbol),['2330','2222']);s.rankMetric='lots'
 s.minimum='3';assert.equal(s.rankings[0].rows.length,1);s.minimum='';assert.equal(s.minCount,2)
 s.search='AI';assert.deepEqual(s.rankings[0].rows.map(r=>r.symbol),['2330']);assert.equal(s.rsRows.length,1)
 s.choose('turnover');assert.equal(s.search,'');s.sector='AI';assert.equal(s.turnoverRows.length,1)
 s.sector='晶圓代工';assert.equal(s.turnoverRows.length,2);assert.deepEqual(s.sectorStats,[{name:'晶圓代工',amount:300,count:2},{name:'AI',amount:100,count:1}]);assert.equal(s.totalAmount,300);assert.equal(calls.some(c=>c.url.endsWith('/tag')),false)
 s.date='2026-09-16';s.turnoverDate='2026-09-01';await s.load();assert.match(calls.at(-1).url,/date=2026-09-16&turnover_date=2026-09-01/)
 await s.refresh();assert.equal(calls.at(-2).url,'/api/broker-focus/refresh')
 assert.equal(calls.some(c=>c.url.includes('/api/sync')||c.url.includes('/api/rs/refresh')),false)
 const original=s.report;globalThis.fetch=async()=>{throw new Error('offline')};await s.load();assert.equal(s.report,original);assert.match(s.error,/8765.*服務已停止/)
})
