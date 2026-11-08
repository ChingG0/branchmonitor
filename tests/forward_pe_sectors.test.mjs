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
const Component=(await import(await load('ForwardPeSectors.vue'))).default
const {selectSectors}=await import(await load('forwardPeSectors.ts'))
const row=(name,ret,amount,more={})=>({name,parent:'我們的大類',members:3,quoted:3,return_coverage_pct:100,return_pct:ret,advancers:1,decliners:2,unchanged:0,amount_twd:amount,amount_names:3,amount_complete:true,amount_share_pct:20,previous_amount_share_pct:14.5,share_change_pp:5.5,...more})
const report=(rows)=>({status:'ok',date:'2026-11-06',previous_date:'2026-11-05',dates:['2026-11-06','2026-11-05'],rows,classification:{groups:670,snapshot:'2026-09-11'},warnings:[],notes:['多重歸類，比重不可相加','當日官方成交金額，不用收盤乘量'],scope:'上市櫃四碼股票',market_turnover_twd:1e12,market_names:1983})
const options=(more={})=>({sort:'amount',quality:'standard',parent:'',search:'',...more})
test('default quality excludes small and sparse groups without treating missing quotes as zero',()=>{
 const rows=[row('完整',-3,100),row('單檔',8,300,{members:1,quoted:1}),row('缺價',9,500,{members:10,quoted:2,return_coverage_pct:20})]
 assert.deepEqual(selectSectors(rows,options()).map(r=>r.name),['完整'])
 assert.equal(selectSectors(rows,options({quality:'all'})).length,3)
})
test('amount, gain and loss sorting keep null last and do not mutate source order',()=>{
 const rows=[row('A',3,100),row('B',-5,300),row('C',null,null,{quoted:0,return_coverage_pct:0}),row('D',-2,200)]
 const expected={amount:['B','D','A','C'],rising:['A','D','B','C'],falling:['B','D','A','C']}
 for(const sort of Object.keys(expected))assert.deepEqual(selectSectors(rows,options({sort,quality:'all'})).map(r=>r.name),expected[sort])
 assert.deepEqual(rows.map(r=>r.name),['A','B','C','D'])
})
test('search and parent filter preserve our custom taxonomy',()=>{
 const rows=[row('我們的晶圓代工',2,100),row('我們的記憶體',-2,200,{parent:'另一個自訂大類'})]
 assert.deepEqual(selectSectors(rows,options({search:' 晶圓 '})).map(r=>r.name),['我們的晶圓代工'])
 assert.deepEqual(selectSectors(rows,options({parent:'另一個自訂大類'})).map(r=>r.name),['我們的記憶體'])
 assert.equal(selectSectors(rows,options({search:'  '})).length,2)
})
test('saved date, actual amounts, previous-day shares and percentage points are distinguished',async()=>{
 const html=await renderToString(createSSRApp(Component,{compact:true,initial:report([row('我們的晶圓代工',-5.94,4e8)])}))
 for(const s of ['自訂族群 · 670 組','2026-11-06','前日 2026-11-05','我們的晶圓代工','-5.94%','4.00 億','20.00%','14.50%','+5.50 百分點','成交比重增加只代表交易集中，不等於淨買入','多重歸類，比重不可相加','全部族群與前日比較'])assert.ok(html.includes(s),s)
 assert.ok(!/NaN|Infinity/.test(html))
})
test('partial group amount and missing shares are disclosed instead of fabricated zeros',async()=>{
 const html=await renderToString(createSSRApp(Component,{compact:true,initial:report([row('部分金額',-1,30000,{members:4,quoted:4,amount_names:3,amount_complete:false,amount_share_pct:null,previous_amount_share_pct:null,share_change_pp:null})])}))
 for(const s of ['3.00 萬','部分金額 3／4檔','已知部分金額','成交比重 —','比重變化資料未齊'])assert.ok(html.includes(s),s)
 assert.ok(!html.includes('0.00%'))
})
test('zero turnover and unchanged shares are actual zeros, not missing',async()=>{
 const html=await renderToString(createSSRApp(Component,{initial:report([row('停留',0,0,{amount_share_pct:0,previous_amount_share_pct:0,share_change_pp:0})])}))
 for(const s of ['0 元','成交比重 0.00%','較前日 0.00 百分點'])assert.ok(html.includes(s),s)
})
test('missing official source shows manual update guidance and no silent retry control',async()=>{
 const data=report([]);data.status='partial';data.warnings=['當日官方來源未齊：TPEX；請手動補齊市場資料中心，不自動重試。']
 const html=await renderToString(createSSRApp(Component,{initial:data}))
 assert.ok(html.includes(data.warnings[0]))
 assert.ok(html.includes('沒有符合條件的族群'))
 assert.ok(!html.includes('自動重試中'))
})
