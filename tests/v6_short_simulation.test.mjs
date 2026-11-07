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
const helper=moduleUrl(await readFile(new URL('../frontend/src/v6Format.ts',import.meta.url),'utf8'))
const {descriptor}=parse(await readFile(new URL('../frontend/src/V6Panel.vue',import.meta.url),'utf8'))
let code=compileScript(descriptor,{id:'v6-primary-test',inlineTemplate:true}).content
code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
code=code.replace("from './v6Format'",`from ${JSON.stringify(helper)}`).replace("import ModelCurve from './ModelCurve.vue'","const ModelCurve={setup(){return ()=>null}}")
const component=(await import(moduleUrl(code))).default
const base={key:'v6',version:'test',label:'test',status:'complete',readiness:'ready',as_of:'2026-11-04',score:80.18,score_threshold:65,position:0,completed:52,wins:28,hit_rate:53.8,pnl_points:10907.65,max_drawdown_pct:-10,max_drawdown_points:2813.95,portfolio_return_pct:47,curve:[],trades:[{signal_date:'2026-10-07',entry_date:'2026-10-08',exit_date:'2026-10-13',signal_score:71.34,signal_drawdown_pct:.3253,entry:49766,exit:48798,contract:'202611',points:962.0287,held:3,reason:'profit',pnl_twd:192406}]}
test('primary panel uses V6-2 labels, one strategy and its own trade scores',async()=>{
 const html=await renderToString(createSSRApp(component,{model:{...base,strategy_key:'v6-2',simulation_kind:'historical_replay',cooldown_days:2,generated_at:'2026-11-05T13:50:00+08:00',basis:'固定8%限制'}}))
 for(const text of ['V6-2 · 主策略','V6-2 累積績效','V6-2 歷史回放交易','71.3','0.33%','2026-10-08','末日歷史分數','模擬保存','固定8%限制'])assert.ok(html.includes(text),text)
 assert.doesNotMatch(html,/70 分並行觀察|65 分原設定|宏觀與籌碼風險觀察|NaN|Infinity/)
})
test('original model keeps independent 65/70 historical view',async()=>{
 const html=await renderToString(createSSRApp(component,{model:base}))
 for(const text of ['原V6 · 歷史模型','65 分原設定','70 分並行觀察','原V6 累積績效'])assert.ok(html.includes(text),text)
 assert.doesNotMatch(html,/V6-2 歷史回放交易/)
})
