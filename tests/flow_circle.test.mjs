import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer} from 'vue'
const require=createRequire(import.meta.url)
test('circle distinguishes pending, buying, selling and flat with amounts and date in its accessible description',async()=>{
 const {descriptor}=parse(await readFile(new URL('../frontend/src/FlowCircle.vue',import.meta.url),'utf8'))
 let code=compileScript(descriptor,{id:'flow-test'}).content
 code=code.replaceAll("from 'vue'",`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 code=code.replaceAll("from './http'",`from ${JSON.stringify(new URL('../frontend/src/http.ts',import.meta.url).href)}`)
 code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const component=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default
 component.render=()=>null
 const renderer=createRenderer({createComment:()=>({}),insert:()=>{},remove:()=>{},parentNode:()=>null,nextSibling:()=>null,setText:()=>{},setElementText:()=>{},createText:()=>({}),createElement:()=>({}),patchProp:()=>{}})
 for(const [buy,sell,state,label,net] of [[500,300,'buy','買',200],[300,500,'sell','賣',-200],[500,500,'flat','平',0],[0,0,'flat','平',0],[null,null,'pending','待',null],[500,null,'pending','待',null],[NaN,0,'pending','待',null]]){
  const app=renderer.createApp(component,{buy,sell,period:'2026-09-04 ～ 2026-09-10',scope:'五日主力名單'})
  const s=app.mount({}).$.setupState
  assert.equal(s.state,state);assert.equal(s.label,label);assert.equal(s.net,net)
  assert.match(s.description,/2026-09-04 ～ 2026-09-10/)
  if(net===null)assert.match(s.description,/尚未載入/)
  else if(buy+sell>0)assert.match(s.ring.background,new RegExp(String(100*buy/(buy+sell))))
  app.unmount()
 }
})
