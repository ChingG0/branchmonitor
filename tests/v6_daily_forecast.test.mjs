import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),cache=new Map()
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
async function component(file){
 if(cache.has(file))return cache.get(file)
 const text=await readFile(new URL('../frontend/src/'+file,import.meta.url),'utf8')
 let code=compileScript(parse(text).descriptor,{id:file,inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 const matches=[...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]
 for(const m of matches){const name=m[1];let url
  if(name.endsWith('.vue'))url=await component(name)
  else {let raw=await readFile(new URL('../frontend/src/'+name+'.ts',import.meta.url),'utf8');raw=raw.replace(/import ['"].*\.css['"]/g,'');url=moduleUrl(raw)}
  code=code.replace(m[0],'from '+JSON.stringify(url))
 }
 const url=moduleUrl(code);cache.set(file,url);return url
}
async function html(file,props){return renderToString(createSSRApp((await import(await component(file))).default,props))}

const classes=(up=40,flat=20,down=40)=>[{key:'up',probability:up/100,probability_pct:up},{key:'flat',probability:flat/100,probability_pct:flat},{key:'down',probability:down/100,probability_pct:down}]
const initial=h=>({status:'reference',horizon:h,stage:'initial',issued_at:'2026-11-04T22:00:00+08:00',market_as_of:'2026-11-04',target_date:['2026-11-05','2026-11-06','2026-11-09'][h-1],original_target_date:['2026-11-05','2026-11-06','2026-11-09'][h-1],anchor:100,current_close:100,remaining_sessions:h,elapsed_sessions:0,classes:classes(),sample_n:6600,sample_first:'2000-01-01',sample_last:'2026-10-30',last_outcome:'2026-11-04',median_level:101,intervals:[{central_pct:50,low_level:99,high_level:103},{central_pct:80,low_level:97,high_level:105},{central_pct:95,low_level:94,high_level:107}],note:'歷史基準',schedule_changes:[]})
const group=()=>({root_id:'test',origin:'2026-11-04',anchor:100,issued_at:'2026-11-04T22:00:00+08:00',timing_qualified:false,timing_note:'提前保存，不列08:30前瞻',flat_bounds:[99.5,100.5],alternative_saved_versions:0,targets:[1,2,3].map(h=>({horizon:h,initial:initial(h),latest:initial(h),last_reference:initial(h),updates:[]}))})
const current={status:'reference',version:'test',method_version:'test',targets:[1,2,3].map(initial),note:'歷史基準'}
const report=g=>({method_version:'test',groups:[g],summary:{completed_origins:0,completed_targets:0,prospective_origins:0,by_horizon:[]},note:'只對話保存'})
test('three initial deadlines display original anchor bounds and initial probabilities; no update action',async()=>{
 const s=await html('V6DailyForecast.vue',{current,value:report(group())})
 assert.equal((s.match(/data-horizon=/g)||[]).length,3)
 for(const t of ['2026-11-05','2026-11-06','2026-11-09','99.50','100.50','最初40.0%','歷史基準','不作買賣訊號','僅對話要求更新時保存'])assert.ok(s.includes(t),t)
 assert.doesNotMatch(s,/NaN|Infinity|<button/)
})
test('same-deadline revision shows prior difference and initial separately, not rebased bounds',async()=>{
 const g=group(),t=g.targets[2],r={...initial(3),stage:'revision',market_as_of:'2026-11-05',issued_at:'2026-11-06T08:35:00+08:00',remaining_sessions:2,elapsed_sessions:1,current_close:98,classes:classes(24,25,51),change_previous_pp:{up:-16,flat:5,down:11},change_initial_pp:{up:-16,flat:5,down:11},previous_issued_at:initial(3).issued_at,previous_market_as_of:'2026-11-04'}
 t.latest=r;t.last_reference=r;t.updates=[r]
 const s=await html('V6DailyForecast.vue',{current,value:report(g)})
 for(const text of ['51.0%','+11.0百分點','最初40.0%','剩餘2日','2026-11-09','99.50','100.50'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/NaN|Infinity/)
})
test('late outcome keeps original deadline, last forecast and grading date; absent sources do not fabricate zero',async()=>{
 const g=group(),t=g.targets[0];t.latest={...initial(1),status:'complete',stage:'outcome',classes:[],actual_close:98,actual_return_pct:-2,actual_class:'down',original_rps:.3,verified_on:'2026-11-09',market_as_of:'2026-11-09'};t.updates=[t.latest]
 const missing=g.targets[2];missing.latest={...initial(3),status:'unavailable',stage:'missing',classes:[],note:'foreign來源不足'};missing.updates=[missing.latest]
 const s=await html('V6DailyForecast.vue',{current:{...current,status:'unavailable',note:'foreign來源不足'},value:report(g)})
 for(const text of ['到期前最後保存值','2026-11-05實際下跌','-2.0%','2026-11-09保存核對','0.3000','foreign來源不足','本次不顯示新比例'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/NaN|Infinity/)
})
test('calendar shifts retain original date and only render safe provenance links',async()=>{
 const g=group(),t=g.targets[0];t.latest={...initial(1),stage:'calendar_revision',target_date:'2026-11-06',schedule_changes:[{from_date:'2026-11-05',to_date:'2026-11-06',calendar_changed_dates:['2026-11-05'],calendar_saved_at:'2026-11-05T07:00:00+08:00',observed_at:'2026-11-05T08:35:00+08:00',source:'javascript:alert(1)',note:'官方休市'}]};t.last_reference=t.latest;t.updates=[t.latest]
 const s=await html('V6DailyForecast.vue',{current,value:report(g)})
 assert.ok(s.includes('原定 2026-11-05'));assert.ok(s.includes('日曆已更正'));assert.ok(s.includes('官方休市'));assert.doesNotMatch(s,/href="javascript:/)
})

test('binary deadlines show two direction columns, zero boundary and small-return/tie definition',async()=>{
 const g=group();g.method_version='v6-daily-direction-baseline-2.0.0';delete g.flat_bounds;g.direction_boundary=100;
 for(const t of g.targets){const r={...t.initial,classes:[{key:'up',probability:.512,probability_pct:51.2},{key:'down',probability:.488,probability_pct:48.8}],direction_boundary:100};t.initial=r;t.latest=r;t.last_reference=r}
 const binaryCurrent={...current,version:'v6-daily-direction-2',method_version:'v6-daily-direction-baseline-2.0.0'};
 const s=await html('V6DailyForecast.vue',{current:binaryCurrent,value:{...report(g),method_version:binaryCurrent.method_version}})
 for(const t of ['小漲歸上漲、小跌歸下跌','恰好收平','原起點漲跌分界 100.00','51.2%','48.8%','未新增方向訊號','新舊版本成績分開'])assert.ok(s.includes(t),t)
 assert.doesNotMatch(s,/>平盤<|原起點平盤界線|99\.50|100\.50|NaN|Infinity/)
 const head=s.match(/<thead>([\s\S]*?)<\/thead>/)[1];assert.equal((head.match(/<th(?:\s|>)/g)||[]).length,5)
})
test('binary late outcome uses two-column span and legacy archive keeps original three classes',async()=>{
 const g=group();g.method_version='v6-daily-direction-baseline-2.0.0';g.targets[0].latest={...initial(1),status:'complete',classes:[],actual_class:'up',actual_return_pct:0,actual_close:100,original_rps:.2,verified_on:'2026-11-05'};g.targets[0].updates=[g.targets[0].latest]
 const s=await html('V6DailyForecast.vue',{current:{...current,version:'v6-daily-direction-2'},value:{...report(g),method_version:g.method_version}})
 assert.ok(s.includes('colspan="2"'));assert.ok(s.includes('2026-11-05實際收平（歸上漲側）')); assert.doesNotMatch(s,/>平盤</)
 const old=await html('V6DailyForecast.vue',{value:report(group()),archived:true});assert.ok(old.includes('舊版三分類紀錄'));assert.ok(old.includes('原起點平盤界線'));assert.doesNotMatch(old,/等待下一次對話更新保存逐日期限預報/)
})

const live={version:'v6-live-context-1.0.0',captured_at:'2026-11-06T00:50:00+08:00',market_as_of:'2026-11-05',status:'complete',night:[{contract:'202611',label:'台指期 202611',session:'夜盤盤中',price:39972,high:42298,low:39365,change_day_points:-1943,change_day_pct:-4.64,change_reference_points:-1910,change_reference_pct:-4.56,day_close:41915,reference:41882,session_range_pct:7,vs_closed_cash_points:-1602.72,day_basis_points:340.28,quote_at:'2026-11-06T00:47:00+08:00',included:true,state:'ready',move_in_atr14:1.5,source:'臺灣期貨交易所',url:'https://mis.taifex.com.tw/futures/',note:'非同步價差'}],overseas:[{key:'tsm',label:'台積電ADR',price:363.46,previous_close:395.75,change_pct:-8.16,state:'ready',included:true,quote_at:'2026-11-06T00:48:00+08:00',source:'Yahoo Finance',url:'https://finance.yahoo.com/quote/TSM/'}],volatility:{as_of:'2026-11-05',range5_pct:3.08,range20_pct:2.04,previous20_range_pct:1.58,atr14_points:1188,atr14_pct:2.86,std20_daily_pct:2.02,ewma_daily_pct:2.1,note:'僅作描述'},assessment:{state:'夜盤與海外同步偏弱',reading:'目前承壓',drivers:['同合約比較'],watch_next:['觀察低點'],note:'歷史百分比未納入盤中訊息',updates_probabilities:false}}
test('live context distinguishes same-contract close, reference and non-synchronous cash; dates and volatility visible',async()=>{
 const s=await html('V6LiveAssessment.vue',{value:live})
 for(const text of ['2026-11-06 00:50:00','台股收盤 2026-11-05','夜盤與海外同步偏弱','39,972','-1,943','-1,910','41,915','41,882','非同步價差','台積電ADR','-8.16%','2026-11-06 00:48:00','近5日日均振幅','3.08%','ATR14','1,188','重整頁面不取新價'])assert.ok(s.includes(text),text)
 assert.ok(s.includes('href="https://mis.taifex.com.tw/futures/"'));assert.doesNotMatch(s,/NaN|Infinity|<button/)
 const forecast=await html('V6DailyForecast.vue',{current,value:report(group()),liveContext:true});assert.ok(forecast.includes('未納入'))
})
test('missing live sources remain explicit and unsafe links cannot execute',async()=>{
 const missing=await html('V6LiveAssessment.vue',{value:{...live,night:[],overseas:[],volatility:null,status:'partial',errors:[{key:'night',reason:'來源不足'}]}})
 assert.ok(missing.includes('尚無可核對'));assert.ok(missing.includes('來源不足'));assert.doesNotMatch(missing,/NaN|Infinity/)
 const unsafe=await html('V6LiveAssessment.vue',{value:{...live,night:[{...live.night[0],url:'javascript:alert(1)'}]}});assert.doesNotMatch(unsafe,/href="javascript:/)
})

test('intraday research context displays even when original forecast cannot be republished after open',async()=>{
 const s=await html('V6Comparison.vue',{report:{snapshot:null,history:[]},liveAssessment:live,researchNotice:'下一交易日已開盤，不倒填盤前預報'})
 for(const text of ['本次夜盤與海外評估','夜盤與海外同步偏弱','不倒填盤前預報'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/NaN|Infinity/)
 const old=await html('V6Comparison.vue',{report:{snapshot:null,history:[]}});assert.ok(!old.includes('本次夜盤與海外評估'))
})
