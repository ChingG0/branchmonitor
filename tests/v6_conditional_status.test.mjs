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
const url=c=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(c,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
const source=await readFile(new URL('V6ConditionalStatus.vue',src),'utf8')
let code=compileScript(parse(source).descriptor,{id:'prospective',inlineTemplate:true}).content
code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
code=code.replace(/from ['"]\.\/http['"]/,`from ${JSON.stringify(url(await readFile(new URL('http.ts',src),'utf8')))}`)
code=code.replace(/from ['\"]\.\/rememberDisclosure['\"]/,`from ${JSON.stringify(url((await readFile(new URL('rememberDisclosure.ts',src),'utf8')).replace(/import ['\"].*\.css['\"]/g,'')))}`)
const component=(await import(url(code))).default
const payload={status:'parallel',withheld:true,note:'等待第一份合格盤前資料',active:false,horizons:[1,2,3].map(h=>({horizon:h,matured:0,up:0,down:0,elapsedSessions:0,eligible:false,active:false,newSinceSuspension:null,gates:{},metrics:{M0:{brier:null,log_loss:null},M1:{brier:null,log_loss:null}}}))}
const render=p=>renderToString(createSSRApp(component,{payload:p}))
test('new enrollment renders three empty progress cards without fabricated forecasts',async()=>{
 const html=await render(payload);assert.equal((html.match(/<progress/g)||[]).length,3);assert.ok(html.includes('觀察中 0/60'));assert.ok(html.includes('等待第一份合格盤前資料'));assert.ok(!html.includes('最近合格紀錄'));assert.ok(html.includes('M0 —／M1 —'))
})
test('passing a threshold does not claim automatic deployment',async()=>{
 const p=structuredClone(payload);p.horizons[0].eligible=true;p.horizons[0].matured=60
 const html=await render(p);assert.ok(html.includes('符合門檻 · 等待對話啟用'));assert.ok(!html.includes('M1已啟用'))
})
test('withheld snapshot is labeled as previous record, with current calendar deadline',async()=>{
 const p=structuredClone(payload);p.lastForecast={targets:['2026-11-03','2026-11-04','2026-11-05'],currentTargets:['2026-11-04','2026-11-05','2026-11-06'],models:Object.fromEntries([1,2,3].map(h=>[h,{M1:{up_percent:60,down_percent:40}}]))}
 const html=await render(p);assert.ok(html.includes('上次已存紀錄（非本次預報）'));assert.ok(html.includes('2026-11-06'));assert.ok(html.includes('修訂模型仍屬研究'))
})
test('view only reads saved state and has no publication endpoint',()=>{
 assert.ok(source.includes("localFetch('/v6-research/prospective-status.json')"));assert.ok(!source.includes('POST'));assert.ok(!source.includes('/api/'));assert.ok(!source.includes('activate('))
})
