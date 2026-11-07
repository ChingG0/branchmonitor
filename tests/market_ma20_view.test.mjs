import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url)
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const helper=await import(moduleUrl(await readFile(new URL('../frontend/src/marketLayout.ts',import.meta.url),'utf8')))
test('new MA20 card follows river once and respects later saved custom order',()=>{
 assert.deepEqual(helper.normalizeMarketOrder(['fear','pe_river','twd'],['pe_river','ma20','fear','twd']),['fear','pe_river','ma20','twd'])
 assert.deepEqual(helper.normalizeMarketOrder(['ma20','fear','pe_river','twd'],['pe_river','ma20','fear','twd']),['ma20','fear','pe_river','twd'])
})
test('MA20 display identifies conditional days, dynamic threshold and missing ranges',async()=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/MarketMa20.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'ma20-test',inlineTemplate:true}).content
 code=code.replace(/from ['"]vue['"]/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 const component=(await import(moduleUrl(code))).default
 const window={window:20,samples:20,required:20,start:'2026-10-01',end:'2026-10-28',ready:true,average_points:10,average_pct:10,amplitude_units:1.05,scenarios:[{fraction:.5,net_up_points:5,sessions:2},{fraction:1,net_up_points:10,sessions:1}],reason:''}
 const data={available:true,as_of:'2026-10-28',both_below:true,warnings:[],note:'',indices:[{symbol:'TAIEX',title:'加權指數',as_of:'2026-10-28',close:90,ma20:100.5,above:false,distance_points:-10.5,distance_pct:-10.4478,remaining_up_points:10.5,remaining_up_pct:11.6667,next_close_threshold:99.48,next_required_up_points:9.48,ma_ready:true,ma_samples:20,range_windows:{'20':window,'5':{...window,window:5}}}]}
 const html=await renderToString(createSSRApp(component,{data}))
 for(const text of ['雙指數均低於20MA','加權指數','10.50','99.48','約 2','約 1','個交易日','半個','一個','不是到達天數','沒有賦予發生機率'])assert.ok(html.includes(text),text)
 const missing=structuredClone(data);missing.indices[0].range_windows['20']={...window,ready:false,average_points:null,average_pct:null,amplitude_units:null,samples:19,reason:'高低點不足19/20',scenarios:[{fraction:.5,net_up_points:null,sessions:null},{fraction:1,net_up_points:null,sessions:null}]}
 const bad=await renderToString(createSSRApp(component,{data:missing}));assert.ok(bad.includes('無法估算'));assert.ok(bad.includes('高低點不足19/20'));assert.ok(!bad.includes('NaN'))
})
