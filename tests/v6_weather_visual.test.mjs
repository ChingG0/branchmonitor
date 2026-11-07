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
const disclosure=moduleUrl((await readFile(new URL('../frontend/src/rememberDisclosure.ts',import.meta.url),'utf8')).replace("import './rememberDisclosure.css'",''))
const helper=moduleUrl(await readFile(new URL('../frontend/src/v6WeatherVisual.ts',import.meta.url),'utf8'))
const {sampleStats,stageStyle,priorReports,compareReports}=await import(helper)
const report=(id,prepared_at,stage,market_as_of='2026-10-16')=>({id,prepared_at,stage,market_as_of})
const a=report('a','2026-10-18T13:00:00+08:00','資料不足')
const b=report('b','2026-10-18T13:30:00+08:00','高風險')
const c=report('c','2026-10-18T13:45:00+08:00','高風險')

test('Wilson calculation uses counts and retains uncertainty at zero/all successes',()=>{
 const result=sampleStats({count:41,declines:22})
 assert.ok(Math.abs(result.rate-53.6585366)<0.00001)
 assert.ok(result.low>38 && result.low<40)
 assert.ok(result.high>67 && result.high<69)
 assert.equal(sampleStats({count:0,declines:0}),null)
 for(const sample of [{count:4,declines:5},{count:-1,declines:0},{count:3,declines:1.5},{count:Infinity,declines:3}])assert.equal(sampleStats(sample),null)
 assert.ok(sampleStats({count:10,declines:0}).high>27)
 assert.ok(sampleStats({count:10,declines:10}).low<73)
})
test('history uses only older reports and does not treat missing data as zero risk',()=>{
 assert.deepEqual(priorReports(b,[c,a,b]).map(r=>r.id),['a'])
 assert.deepEqual(priorReports(a,[c,a,b]),[])
 assert.equal(compareReports(a).label,'首份預報')
 assert.equal(compareReports(b,a).label,'補充資料後重新評估')
 assert.equal(compareReports(c,b).label,'警戒等級持平')
 assert.match(compareReports(c,b).note,/不能當成跨日/)
 assert.equal(compareReports(a,c).label,'尚無法比較')
 const later=report('d','2026-10-20T13:00:00+08:00','平穩','2026-10-19')
 assert.equal(compareReports(later,c).label,'警戒降低')
 assert.doesNotMatch(compareReports(later,c).note,/同一行情日/)
 assert.equal(stageStyle('減弱').level,null)
 assert.equal(stageStyle('增強').level,null)
 assert.equal(stageStyle('資料不足').level,null)
})
test('visual renders saved proportions, baseline, missing category and revision notice',async()=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/V6RiskVisual.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'v6-risk-test',inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replace("from './v6WeatherVisual'",`from ${JSON.stringify(helper)}`)
 code=code.replace("from './rememberDisclosure'",`from ${JSON.stringify(disclosure)}`)
 const component=(await import(moduleUrl(code))).default
 const horizons=[{key:'d1',label:'下一日',definition:'下一日收盤',at_least65:{count:41,declines:22,rate_pct:53.7},at_least70:{count:17,declines:9,rate_pct:52.9},baseline:{count:100,declines:45,rate_pct:45}}]
 const props={current:c,history:[c,b,a],horizons,categories:[{key:'global',label:'海外'},{key:'ownership',label:'大戶'}],indicators:[{category:'global',as_of:'2026-10-16'}]}
 const html=await renderToString(createSSRApp(component,props))
 for(const text of ['警戒等級持平','同一行情日','53.7%','22／41','基準 45.0%','1／2 類','待補資料','Wilson 95%'])assert.ok(html.includes(text),text)
 const empty=await renderToString(createSSRApp(component,{...props,current:a,horizons:[]}))
 assert.ok(empty.includes('首份預報'))
 assert.ok(empty.includes('暫不繪製比例'))
 assert.ok(!empty.includes('13:45'))
})
