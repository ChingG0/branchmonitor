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
let code=compileScript(parse(await readFile(new URL('V6EveningForecast.vue',src),'utf8')).descriptor,{id:'evening',inlineTemplate:true}).content
code=code.replace(/from ['"]vue['"]/g,'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
for(const m of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)])code=code.replace(m[0],'from '+JSON.stringify(url((await readFile(new URL(m[1]+'.ts',src),'utf8')).replace(/import ['"].*\.css['"]/g,''))))
const component=(await import(url(code))).default,helpers=await import(url(await readFile(new URL('v6EveningForecast.ts',src),'utf8')))
const payload=JSON.parse(await readFile(new URL('../frontend/public/v6-research/evening-forecast.json',import.meta.url),'utf8'))
const html=()=>renderToString(createSSRApp(component,{payload}))

test('all three dates have finite probabilities and coherent quantiles',()=>{helpers.validateEvening(payload);for(const t of payload.latest.targets){assert.equal(t.up+t.down,1);assert.ok(t.q10Close<=t.medianClose&&t.medianClose<=t.q90Close);assert.ok(t.q10Close<payload.latest.anchorClose&&payload.latest.anchorClose<t.q90Close);assert.equal(t.model,'OBS');assert.equal(t.calibrated,false)}})
test('primary is uncalibrated observation rather than a baseline forecast',async()=>{const s=await html();for(const t of ['2026-11-09','2026-11-10','2026-11-11','未校準觀察版','非合格時段','第10～90百分位觀察範圍','資訊截點','實際保存','EST','每次更新的漲跌機率變化'])assert.ok(s.includes(t),t);assert.ok(!s.includes('機率待更新'));assert.ok(!s.includes('NaN'));assert.ok(!s.includes('Infinity'));assert.equal((s.match(/data-horizon=/g)||[]).length,3)})
test('seasonal context and five same-progress years show divergent actual futures',async()=>{const s=await html();for(const t of ['月份位置','1967-02','本月迄今','今年迄今','完整歷年月均','月底報酬最高','最低','同進度比較','之後20交易日','之後60交易日','相似度不是機率','未訓練／未校準'])assert.ok(s.includes(t),t);assert.equal(payload.latest.observation.yearAnalogs.cases.length,5);assert.ok(!s.includes('同時刻模型歷史回溯'))})
test('unmatured observation never inherits the baseline hit rate',async()=>{const s=await html();for(const t of ['已成熟 0筆','觀察版到期核對','不套用舊模型命中率','尚無到期樣本','80%實際涵蓋'])assert.ok(s.includes(t),t);assert.ok(!s.includes('100.0%（0/0）'));for(const g of payload.progress)for(const h of g.horizons)assert.equal(h.n,0)})
test('invalid or contradictory probabilities and quantiles are rejected',()=>{for(const edit of [d=>d.latest.targets[0].up=null,d=>d.latest.targets[0].q10Close=d.latest.targets[0].q90Close+1,d=>d.latest.targets[0].medianChangePoints=123456,d=>d.latest.targets[0].direction=d.latest.targets[0].direction==='up'?'down':'up']){const d=structuredClone(payload);edit(d);assert.throws(()=>helpers.validateEvening(d))}})
test('primary view only reads saved JSON and old0830 is clearly archived',async()=>{const s=await readFile(new URL('V6EveningForecast.vue',src),'utf8');assert.doesNotMatch(s,/method\s*:\s*['"]POST|setInterval\(/);assert.ok(s.includes('/v6-research/evening-forecast.json'));const weather=await readFile(new URL('V6Weather.vue',src),'utf8');assert.ok(!weather.includes('<V6EveningForecast/>'));const forecastPage=await readFile(new URL('V6ThreeDayForecast.vue',src),'utf8');assert.ok(forecastPage.includes('<V6EveningForecast/>'));assert.ok(forecastPage.includes('舊08:30制度、盤後研究與歷史紀錄（已停止更新）'));const app=await readFile(new URL('App.vue',src),'utf8');assert.ok(app.includes("id:'three-day-forecast',label:'3日模型預測'"));const research=await readFile(new URL('V6ConditionalResearch.vue',src),'utf8');assert.ok(research.includes('舊08:30夜盤模型 · 歷史回放'))})
