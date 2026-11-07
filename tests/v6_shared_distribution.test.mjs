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
const method={status:'ready',method_version:'test-method',note:'歷史基準',validation:{data_through:'2026-11-03',calibration_n:111,test_n:318,event_n:95,RPS:.238496,coverage95_pct:88.99,increment_adopted:false,claim:'回溯診斷，非真正前瞻成績'}}
const shared={status:'reference',method_version:'test-method',market_as_of:'2026-11-03',anchor:100,forecast_dates:['2026-11-04','2026-11-05','2026-11-06'],issued_at:'2026-11-03T21:00:00+08:00',classes:[{key:'up',label:'上漲',count:40,probability:.4,probability_pct:40},{key:'flat',label:'平盤',count:20,probability:.2,probability_pct:20},{key:'down',label:'下跌',count:40,probability:.4,probability_pct:40}],sample_n:100,sample_first:'2000-01-01',sample_last:'2026-10-29',last_outcome:'2026-11-03',median_level:101,intervals:[{central_pct:50,low_level:99,high_level:103},{central_pct:80,low_level:97,high_level:105},{central_pct:95,low_level:94,high_level:107}],validation:method.validation,note:'不推估逐日路徑',event_reference:{probability:.247,count:24,sample_n:100}}
const baseStrategy={score:65,score_change:2,threshold:65,alert:true,action:'觀察',missing:[],sources:[],factors:[],scenario:{status:'missing',classes:[],sample_n:0,note:'舊紀錄'},backtest:{completed:5,wins:3,win_rate_pct:60,net_points:100}}
const snapshot={id:'test',version:'test',prepared_at:'2026-11-03T21:00:00+08:00',market_as_of:'2026-11-03',anchor:100,forecast_dates:shared.forecast_dates,history:[],flat_bounds:[99.5,100.5],strategies:[{...baseStrategy,key:'legacy',label:'原版65分策略'},{...baseStrategy,key:'cross',label:'跨資產策略'}],method:'共用T1',execution_note:'模擬'}
async function html(file,props){return renderToString(createSSRApp((await import(await component(file))).default,props))}
test('retired unconditional probabilities are omitted while independent risk targets remain',async()=>{
 const s=await html('V6Comparison.vue',{report:{snapshot:{...snapshot,shared_distribution:shared},distribution_method:method,history:[]}})
 for(const text of ['雙策略風險監控','最多3日規則空單扣成本後獲利','3日內最低價回落至少2%','無條件歷史比例區塊已撤下'])assert.ok(s.includes(text),text)
 for(const text of ['共用三日收盤分布</h3>','中央95%','共同歷史基準 24.7%'])assert.ok(!s.includes(text),text)
 assert.doesNotMatch(s,/NaN|Infinity/)
})
test('old saved snapshots retain risk details but never restore retired forecast blocks',async()=>{
 const s=await html('V6Comparison.vue',{report:{snapshot,distribution_method:method,history:[]}})
 assert.ok(s.includes('原版65分策略'));assert.ok(s.includes('原始保存檔保留'));assert.ok(!s.includes('legacy-record'));assert.ok(!s.includes('概率區間'))
})
test('missing sources render no fabricated zero-risk percentages or chart',async()=>{
 const s=await html('V6SharedDistribution.vue',{method,value:{status:'unavailable',classes:[],note:'foreign不足'}})
 assert.ok(s.includes('foreign不足'));assert.ok(!s.includes('probability-track'));assert.ok(!s.includes('<svg'))
 const source=await readFile(new URL('../frontend/src/V6Comparison.vue',import.meta.url),'utf8')
 assert.doesNotMatch(source,/method:\s*['"]POST|更新按鈕/)
})
