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
async function component(){
 const {descriptor}=parse(await readFile(new URL('../frontend/src/V11Panel.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'v11-test',inlineTemplate:true}).content
 code=code.replace(/import ModelCurve from ['"]\.\/ModelCurve.vue['"]/,'const ModelCurve={render:()=>null}')
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 return (await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
}
test('V11 renders saved alpha candidates separately from holdings and has no update button',async()=>{
 const comp=await component(),candidate={symbol:'2881',name:'富邦金',close:160,alpha_pct:.47,alpha_t:2.76,alpha_ci_pct:[.13,.81],beta:1.2,r2:.1,excess20_pct:6,avg_amount:2e9,ma20:150,atr:4,initial_stop_ref:148,resistance:null,flags:[],status:'大盤暫停買進',planned_shares:2000}
 const model={available:true,label:'V11 手動 α 趨勢',revision:1,created_at:'2026-10-29T13:00:00+08:00',recorded_at:'2026-10-29T13:00:00+08:00',as_of:'2026-10-28',initial:1e6,equity:1e6,cash:1e6,dividend_receivable:0,portfolio_return_pct:0,max_drawdown_pct:0,realized_pnl:0,unrealized_pnl:0,hit_rate:null,wins:0,holdings:[],pending_orders:[],trades:[],curve:[{date:'2026-10-28',value:0}],activity:[],days:{'2026-10-28':{candidates:[candidate],holdings:[],buy:[],sell:[],cash:1e6,equity:1e6,kind:'手動決策',blocked:true}},review:{analysis:'只保存收盤觀察，不回填買點',sources:[],context:{blocked:true,indices:{TAIEX:{close:100,ma20:110,above:false}},coverage:{qualified:100,ordinary:1900,disposition_excluded:2},candidates:[candidate]}},update_policy:'僅依對話要求手動更新',execution_note:'依保存後的開盤核對'}
 const html=await renderToString(createSSRApp(comp,{model}))
 for(const text of ['V11','富邦金','0.47%','2.76','日 α（歷史）','α HAC t 值','大盤暫停買進','當日空手','沒有待執行買入計畫','研究回測獲利不放入帳戶','2026-10-28'])assert.ok(html.includes(text),text)
 assert.ok(!html.includes('<button'));assert.ok(!html.includes('NaN'));assert.ok(!html.includes('66.7%'))
 const empty=await renderToString(createSSRApp(comp,{}));assert.ok(empty.includes('尚未建立 V11 手動帳戶'))
})
