import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),src=new URL('../frontend/src/',import.meta.url)
const dataUrl=s=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const cache=new Map()
async function load(name){
 if(cache.has(name))return cache.get(name)
 let code=await readFile(new URL(name,src),'utf8')
 if(name.endsWith('.vue'))code=compileScript(parse(code).descriptor,{id:name,inlineTemplate:true}).content
 code=code.replace(/import ['"].*\.css['"]/g,'').replace(/from ['"]vue['"]/g,'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
 for(const m of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]){
  const next=m[1]==='http'?dataUrl('export const localFetch=()=>{throw Error("Display must not fetch with initial data")}; export const readJson=async r=>r.json();'):await load(m[1]+(m[1].endsWith('.vue')?'':'.ts'))
  code=code.replace(m[0],'from '+JSON.stringify(next))
 }
 const result=dataUrl(code);cache.set(name,result);return result
}
const Page=(await import(await load('ForwardPeValuation.vue'))).default
const Chart=(await import(await load('ForwardPeRiverChart.vue'))).default
const source={source_provider:'licensed',provider:'共識來源A',eps_basis:'basic',share_basis:'current_ordinary_share_adjusted',currency:'TWD',period_kind:'calendar_year',statistic:'median'}
const point=(month,pe,period='2026',cuts=null)=>({month,pe,forecast_period:period,cuts,reasons:[],price_date:month+'-27',information_cutoff:month+'-28T18:00:00+08:00',qualified_names:pe===null?0:50,report_id:'a'.repeat(32)})
function fixture(ready=false){
 const agg={names:ready?50:0,matched_subset_pe:ready?20:null,adopted_subset_pe:ready?20:null,exact_top50_pe:ready?20:null,gate_passed:ready,original_basket_weight_coverage_pct:ready?100:0,revalued_basket_cap_coverage_pct:ready?100:0}
 const rows=Array.from({length:50},(_,i)=>({rank:i+1,symbol:i===0?'2330':String(1000+i),name:i===0?'台積電':'股票'+i,weight_pct:2,quote:{close:100,shares:1000},estimate:i<8||ready?{value:5,published_at:'2026-11-05T18:00:00+08:00',known_at:'2026-11-08T18:00:00+08:00',source_url:'https://example.com/eps',forecast_as_of:null}:null,qualified:ready,issues:ready?[]:['basic_eps_basis_not_verified']}))
 const group={series:{...source,period:'2026'},rows,qualified:agg,mechanical_preview:{...agg,names:8,matched_subset_pe:30.41,original_basket_weight_coverage_pct:11.53,revalued_basket_cap_coverage_pct:11.07}}
 const win=n=>({years:n,status:ready?'ready':'current_source_gap',points:[point('2026-11',ready?20:null)],observed_months:ready?1:0,reference:{required_months:n*12,available_months:ready?n*12:0,missing_months:ready?[]:['2026-10'],mean:ready?19:null,sd:ready?2:null,cuts:ready?[17,18,20,21]:null},position:{z:ready?.5:null,percentile:ready?60:null,label:ready?'中間區間':null,color:ready?'#d3bc6b':null}})
 return {status:'ok',version:'forward-pe-river.v1',as_of:'2026-11-08T19:00:00+08:00',warnings:[],notes:['五色不是上漲機率'],valuation:{id:'a'.repeat(32),method_version:'v1',policy_sha256:'policyA',information_cutoff:'2026-11-08T18:00:00+08:00',computed_at:'2026-11-08T18:01:00+08:00',price_date:'2026-11-06',weight_date:'2026-10-30',quote_names:50,basket_names:50,periods:[{period_kind:'calendar_year',period:'2026',groups:[group],document_names:8}]},series:[{id:'seriesA',key:{...source,role:'current_year',method_version:'v1',policy_sha256:'policyA'},windows:{3:win(3),5:win(5)}}]}
}
test('missing sources remain visible without invented gauge or historical line',async()=>{
 const html=await renderToString(createSSRApp(Page,{initial:fixture()}))
 for(const s of ['預估本益比河流圖','前50權值股','尚無合格歷史曲線','尚不能評價','未核對試算','30.41 倍','11.07%','台積電','缺預估','待核對','60 個合格保存月']){
  assert.ok(html.includes(s),s)
 }
 assert.equal((html.match(/<tbody><tr|<tr>/g)||[]).length>=50,true)
 assert.ok(!html.includes('class="position-scale"'))
 assert.ok(!html.includes('<svg'))
 assert.ok(!html.includes('<details open'))
 assert.ok(!/NaN|Infinity/.test(html))
})
test('qualified history has a real position and never says history is missing',async()=>{
 const html=await renderToString(createSSRApp(Page,{initial:fixture(true)}))
 for(const s of ['中間區間','歷史位階 0.50 個標準差','以先前保存月份計算的相對估值位置','20.00 倍'])assert.ok(html.includes(s),s)
 assert.ok(!html.includes('連續歷史不足，尚不標示便宜或昂貴'))
 assert.ok(!html.includes('尚不能評價'))
})
test('historical curves split on a missing month and at an annual rollover',async()=>{
 const points=[point('2025-11',20,'2025'),point('2025-12',21,'2025'),point('2026-01',22),point('2026-02',null),point('2026-03',23)]
 const html=await renderToString(createSSRApp(Chart,{window:{years:3,points,reference:{required_months:36}},annual:true}))
 assert.equal((html.match(/<path /g)||[]).length,3)
 assert.equal((html.match(/<circle /g)||[]).length,4)
 assert.ok(html.includes('年度切換也斷線'))
 assert.ok(!/NaN|Infinity/.test(html))
})
test('five colored bands render only where historical cuts exist',async()=>{
 const html=await renderToString(createSSRApp(Chart,{window:{years:5,points:[point('2026-10',20,'2026',[17,18,20,21]),point('2026-11',22,'2026',[18,19,21,22])],reference:{required_months:60}},annual:true}))
 assert.equal((html.match(/fill-opacity="(?:0)?\.28"/g)||[]).length,5)
 assert.equal((html.match(/<path /g)||[]).length,6)
 assert.ok(html.includes('完整 60 個保存月'))
 assert.ok(!/NaN|Infinity/.test(html))
})
test('a saved old source cannot borrow current values from a different methodology',async()=>{
 const f=fixture(true);f.series[0].key.policy_sha256='oldPolicy'
 const html=await renderToString(createSSRApp(Page,{initial:f}))
 assert.ok(html.includes('預估資料尚未齊全'))
 assert.ok(!html.includes('已核對</span>'))
 assert.ok(!html.includes('class="position-scale"'))
 assert.ok(!html.includes('5.00<a'))
})
test('unverified saved EPS changes stay grey and missing previous versions are not invented',async()=>{
 const f=fixture();f.valuation.periods[0].groups[0].rows[0].revision={previous_value:4,change:1,change_pct:25,basis_comparability_verified:false}
 const html=await renderToString(createSSRApp(Page,{initial:f}))
 assert.ok(html.includes('文件變化 +1.00'));assert.ok(html.includes('口徑未核對'));assert.ok(html.includes('前版 4.00'))
 assert.equal((html.match(/class="eps-revision"/g)||[]).length,1)
})
test('stale valuation suppresses current position but retains stock and sector observations',async()=>{
 const f=fixture(true)
 const initialDashboard={river:f,sectors:{date:'2026-11-06',previous_date:'2026-11-05',dates:['2026-11-06'],rows:[],classification:{groups:670,snapshot:'dict'},warnings:[],notes:[]},freshness:{status:'stale',latest_verified_market_date:'2026-11-09',price_date:'2026-11-06'},errors:['舊行情請手動保存']}
 const html=await renderToString(createSSRApp(Page,{initialDashboard}))
 for(const s of ['行情日期待更新','2026-11-09','670 組','舊行情請手動保存','匯出50檔 CSV','資料缺口與更新方式'])assert.ok(html.includes(s),s)
 assert.ok(!html.includes('class="position-scale"'))
 assert.ok(!html.includes('歷史位階 0.50 個標準差'))
})
test('zero EPS documents still display the full saved fifty-stock basket',async()=>{
 const f=fixture();f.valuation.basket_rows=f.valuation.periods[0].groups[0].rows.map(r=>({...r,estimate:null,revision:null,issues:['missing_forecast']}));f.valuation.periods[0].groups=[];f.valuation.periods[0].document_names=0;f.series=[]
 const html=await renderToString(createSSRApp(Page,{initial:f}))
 assert.equal((html.match(/缺預估<\/span>/g)||[]).length,50)
 assert.ok(html.includes('缺預估 50'));assert.ok(html.includes('台積電'));assert.ok(html.includes('匯出50檔 CSV'))
})
