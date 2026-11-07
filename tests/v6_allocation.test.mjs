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
const url=s=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helperUrl=url(await readFile(new URL('v6EveningForecast.ts',src),'utf8'))
let code=compileScript(parse(await readFile(new URL('V6Allocation.vue',src),'utf8')).descriptor,{id:'allocation',inlineTemplate:true}).content
code=code.replace(/from ['"]vue['"]/g,'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
code=code.replace(/import\s*\{localFetch,readJson\}\s*from ['"]\.\/http['"];?/,'const localFetch=async()=>{throw Error("no fetch in SSR")};const readJson=async r=>r.json();')
code=code.replace(/import\s*\{vRememberDisclosure\}\s*from ['"]\.\/rememberDisclosure['"];?/,'const vRememberDisclosure={getSSRProps:()=>({})};')
code=code.replace(/from ['"]\.\/v6EveningForecast['"]/g,'from '+JSON.stringify(helperUrl))
const component=(await import(url(code))).default,helpers=await import(helperUrl)
const report=JSON.parse(await readFile(new URL('../frontend/public/v6-research/evening-forecast.json',import.meta.url),'utf8'))
const current={version:'v6-equity-risk-cap-1',anchorDate:report.latest.anchorDate,anchorClose:report.latest.anchorClose,equityPct:10,cashPct:90,changePp:null,action:'建立上限',binding:[{key:'drawdown',label:'距近一年高點回撤達20%',pct:10}],inputs:{annualVolPct:29.9,drawdownPct:-20.31,stretchAtr:-5.34},probabilityUsed:false,note:'固定規則，未驗證策略'}
test('initial preview is not presented as a previously published decision',async()=>{
 const s=await renderToString(createSSRApp(component,{report,study:{latestPreview:current,recentDecisions:[current],periods:{},note:'次日開盤，含成本'}}))
 for(const label of ['規則預覽','尚未與新機率同步保存','10%','90%','未明顯改善','非當時發布'])assert.ok(s.includes(label),label)
})
test('saved allocation and probability must share their actual version and cutoff',async()=>{
 const p=structuredClone(report);p.latest.allocation={...current,forecastId:p.latest.id,savedAt:p.latest.savedAt,informationCutoff:p.latest.informationCutoff}
 helpers.validateEvening(p)
 const s=await renderToString(createSSRApp(component,{report:p}));assert.ok(s.includes('與機率同次保存'));assert.ok(!s.includes('尚未與新機率同步保存'))
 for(const key of ['anchorDate','anchorClose','forecastId','savedAt','informationCutoff']){const bad=structuredClone(p);bad.latest.allocation[key]=key==='anchorClose'?1:'wrong';assert.throws(()=>helpers.validateEvening(bad))}
})
test('removed ETF plan has no navigation or automatic refresh entry',async()=>{
 const app=await readFile(new URL('App.vue',src),'utf8')
 assert.ok(!app.includes("id:'etf-plan'"));assert.ok(!app.includes('<StrategyResearch'))
 const text=await readFile(new URL('../backend/full_update.py',import.meta.url),'utf8')
 assert.ok(!text.includes('ETF 滾動配置'))
 const componentCode=await readFile(new URL('V6Allocation.vue',src),'utf8');assert.doesNotMatch(componentCode,/method\s*:\s*['"]POST|setInterval\(/)
})
