import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import ts from 'typescript'
const code=ts.transpileModule(await readFile(new URL('../frontend/src/v6Format.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {formatObservation,formatMoney,formatRiskText}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
test('money scales keep currency and original magnitude',()=>{
 assert.equal(formatObservation(94.24,'百萬美元'),'9,424 萬美元')
 assert.equal(formatObservation(6022.13,'百萬美元'),'60.22 億美元')
 assert.equal(formatMoney(-128000000),'-1.28 億元')
 assert.equal(formatMoney(10000),'1 萬元')
 assert.equal(formatMoney(0),'0 元')
 assert.equal(formatMoney(null),'—')
 assert.equal(formatObservation(47111.26,'點'),'47,111.26 點')
 assert.equal(formatObservation(9538664,'交易單位'),'953.87 萬交易單位')
})
test('percentage and score units are not confused',()=>{
 assert.equal(formatObservation(25.7144,'%'),'25.71%')
 assert.equal(formatObservation(3.75,'百分比'),'3.75%')
 assert.equal(formatObservation(-15.52,'百分點'),'-15.52 百分點')
 assert.equal(formatObservation(17.9383,'/100'),'17.94/100')
 assert.equal(formatObservation(5.098,'','美債殖利率代理'),'5.1%')
 assert.equal(formatObservation('尚未公布',''),'尚未公布')
 assert.equal(formatRiskText('前次 6,022.13 百萬美元，當日 94.24 百萬美元。'),'前次 60.22 億美元，當日 9,424 萬美元。')
})
