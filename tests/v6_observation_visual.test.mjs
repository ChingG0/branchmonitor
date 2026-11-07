import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url)
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helper=moduleUrl(await readFile(new URL('../frontend/src/v6ObservationVisual.ts',import.meta.url),'utf8'))
const {numericReading,observationVisual,scoreDirection}=await import(helper)
const stateHelper=moduleUrl((await readFile(new URL('../frontend/src/v6IndicatorState.ts',import.meta.url),'utf8')).replace("from './v6ObservationVisual'",`from ${JSON.stringify(helper)}`))
const {indicatorState}=await import(stateHelper)
const item=(date,value,extra={})=>({category:'global',label:'VIX',unit:'',source:'Cboe',url:'https://example.com',reading:'',as_of:date,value,...extra})
const point=(date,items,score=50)=>({date,decision_at:date+'T08:30:00+08:00',market_as_of:date,score,indicators:items,missing_sources:[],missing_context:[]})
test('numeric/missing observations never become zero',()=>{
 for(const x of ['',null,undefined,NaN,Infinity,'待核對','17%','1,234'])assert.equal(numericReading(x),null)
 assert.equal(numericReading('18.44'),18.44);assert.equal(numericReading(0),0)
})
test('observation history excludes future reports, future publication, unrelated units and repeated source days',()=>{
 const a=point('2026-10-19',[item('2026-10-16',20)]),b=point('2026-10-20',[item('2026-10-19',18.44)]),c=point('2026-10-21',[item('2026-10-19',18.44)])
 const future=point('2026-10-22',[item('2026-10-20',50)])
 const wrongUnit=point('2026-10-18',[item('2026-10-15',900,{unit:'美元'})])
 const late=point('2026-10-17',[item('2026-10-14',100,{available_at:'2026-10-25T08:30:00+08:00'})])
 const v=observationVisual(c.indicators[0],[future,wrongUnit,a,c,b,late],c)
 assert.deepEqual(v.readings.map(r=>r.value),[20,18.44]);assert.equal(v.reused,true);assert.equal(v.position,0)
 assert.equal(v.prior.date,'2026-10-16')
 const priorView=observationVisual(b.indicators[0],[future,a,c,b],b);assert.equal(priorView.reused,false);assert.equal(priorView.readings.length,2)
})
test('flat and sparse ranges do not divide by zero or imply certainty',()=>{
 const a=point('2026-10-19',[item('2026-10-16',20)]),b=point('2026-10-20',[item('2026-10-19',20)])
 const flat=observationVisual(b.indicators[0],[a,b],b);assert.equal(flat.position,null);assert.equal(flat.delta,0)
 const sparse=observationVisual(a.indicators[0],[a],a);assert.equal(sparse.prior,undefined);assert.equal(sparse.position,null);assert.equal(sparse.delta,null)
})
test('direction uses model score change without treating missing as low risk',()=>{
 assert.equal(scoreDirection(44.21,53.28).tone,'easing');assert.equal(scoreDirection(75,60).tone,'rising');assert.equal(scoreDirection(60,60).tone,'neutral')
 assert.equal(scoreDirection(null,53).delta,null);assert.equal(scoreDirection(44,null).delta,null)
})
async function component(name){
 const {descriptor}=parse(await readFile(new URL('../frontend/src/'+name,import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'observation-test',inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replace("from './v6ObservationVisual'",`from ${JSON.stringify(helper)}`)
 const format=moduleUrl(await readFile(new URL('../frontend/src/v6Format.ts',import.meta.url),'utf8'));code=code.replace("from './v6Format'",`from ${JSON.stringify(format)}`)
 code=code.replace("from './v6IndicatorState'",`from ${JSON.stringify(stateHelper)}`)
 return (await import(moduleUrl(code))).default
}
test('position graph and per-indicator graph render dates, direction and reuse correctly',async()=>{
 const gauge=await component('V6RiskPosition.vue')
 const html=await renderToString(createSSRApp(gauge,{score:44.21,priorScore:53.28,date:'2026-10-21',priorDate:'2026-10-20'}))
 for(const text of ['往較低風險端移動','當日 44.21 分','前日 53.28 分','分數不是下跌機率'])assert.ok(html.includes(text),text)
 const missing=await renderToString(createSSRApp(gauge,{score:null,priorScore:null,date:'2026-10-21'}));assert.ok(missing.includes('資料不足，暫不標示位置'));assert.ok(!missing.includes('當日 0.00 分'))
 const trend=await component('V6ObservationTrend.vue');const a=point('2026-10-20',[item('2026-10-19',18.44)]),b=point('2026-10-21',[item('2026-10-19',18.44)])
 const reused=await renderToString(createSSRApp(trend,{item:b.indicators[0],points:[a,b],current:b}));assert.ok(reused.includes('沿用來源日'));assert.ok(reused.includes('尚無足夠歷史資料繪圖'))
})

const classify=(label,value)=>{const row=item('2026-10-21',value,{label});const p=point('2026-10-21',[row]);return indicatorState(row,observationVisual(row,[p],p))}
test('deviation is directional and flags overheating instead of treating every positive value as favorable',()=>{
 for(const [value,tone] of [[-6,'danger'],[-3.9,'warning'],[-3,'caution'],[-1,'normal'],[.39,'normal'],[1,'normal'],[2,'tailwind'],[3,'tailwind'],[6,'caution'],[6.01,'warning']])assert.equal(classify('加權指數 20 日乖離',value).tone,tone,String(value))
})
test('maintenance preserves low-coverage and overheating warnings with distinct midrange states',()=>{
 for(const [value,tone] of [[150,'danger'],[158,'warning'],[165,'caution'],[166,'normal'],[178.93,'normal'],[183,'tailwind'],[190,'danger']])assert.equal(classify('融資維持率估值',value).tone,tone,String(value))
 for(const [value,tone] of [[10,'danger'],[27.2,'warning'],[50,'normal'],[70,'tailwind'],[90,'caution'],[101,'unknown']])assert.equal(classify('恐懼貪婪',value).tone,tone,String(value))
})
test('relative distribution needs sufficient distinct earlier readings and reverses the price/volatility direction',()=>{
 const points=Array.from({length:11},(_,i)=>point('2026-10-'+String(i+1).padStart(2,'0'),[item('2026-09-'+String(i+1).padStart(2,'0'),i+1,{label:'美元／台幣'})]))
 const current=point('2026-10-21',[item('2026-10-20',30,{label:'美元／台幣'})])
 const visual=observationVisual(current.indicators[0],[...points,current],current)
 assert.equal(indicatorState(current.indicators[0],visual).tone,'danger')
 assert.equal(indicatorState({...current.indicators[0],label:'前一交易日加權收盤'},visual).tone,'tailwind')
 assert.equal(indicatorState(current.indicators[0],visual,1).tone,'tailwind')
 const early=point('2026-10-02',[item('2026-10-01',30,{label:'美元／台幣'})]);const insufficient=observationVisual(early.indicators[0],[points[0],early,current],early)
 assert.equal(indicatorState(early.indicators[0],insufficient).tone,'unknown')
 const flat={...visual,readings:points.map((p,i)=>({date:'2026-09-'+String(i+1).padStart(2,'0'),value:10}))}
 assert.equal(indicatorState(current.indicators[0],flat).tone,'unknown')
})
test('ambiguous context stays gray even at range extremes and color assessment does not mutate the stored readings',()=>{
 for(const label of ['融資餘額','當沖金額占比','布蘭特原油','美債殖利率代理','美國 DIX 暗池指標','美國 GEX 估值','小台非三大法人部位比'])assert.equal(classify(label,100000).tone,'unknown')
 const row=item('2026-10-20',18.44);const p=point('2026-10-21',[row]);const v=observationVisual(row,[p],p);const before=JSON.stringify({row,p,v});indicatorState(row,v);assert.equal(JSON.stringify({row,p,v}),before)
})

test('VIX uses absolute volatility bands so an ordinary level is not red merely because a short baseline was quiet',()=>{
 for(const [v,tone] of [[11,'tailwind'],[12,'normal'],[18.44,'normal'],[20,'caution'],[25,'warning'],[30,'danger'],[-1,'unknown']])assert.equal(classify('VIX',v).tone,tone,String(v))
})
