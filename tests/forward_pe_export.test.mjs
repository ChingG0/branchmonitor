import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import ts from 'typescript'
const text=await fs.readFile(new URL('../frontend/src/forwardPeExport.ts',import.meta.url),'utf8')
const code=ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const {csv,stockCsv,sectorCsv,periodRows,issueLabel}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
test('CSV keeps true zero and negative numbers, quotes correctly, neutralizes text formulas',()=>{
 const result=csv([[0,-2,null,'=HYPERLINK("https://bad")','+text','-text',' @SUM(1)','a,b\nnext']])
 assert.ok(result.startsWith('\uFEFF"0","-2","","\'=HYPERLINK('))
 assert.ok(result.includes('"\'+text"'));assert.ok(result.includes('"\'-text"'));assert.ok(result.includes('"\' @SUM(1)"'))
 assert.ok(result.includes('"a,b\nnext"'))
})
test('NTM with missing group cannot borrow CY EPS or its revision',()=>{
 const base=[{symbol:'2330',estimate:{value:20},revision:{previous_value:10,change:10},individual_pe:30,qualified:true,issues:[]}]
 const rows=periodRows(undefined,base)
 assert.equal(rows[0].estimate,null);assert.equal(rows[0].revision,null);assert.equal(rows[0].individual_pe,null)
 assert.deepEqual(rows[0].issues,['missing_forecast']);assert.equal(base[0].estimate.value,20)
})
test('stock CSV preserves the displayed report and source identities plus missing stocks',()=>{
 const v={id:'saved-report',snapshot_id:'saved-snapshot',method_version:'method1',policy_sha256:'ruleSHA',information_cutoff:'cutoff',computed_at:'saved',price_date:'2026-11-06',weight_date:'2026-10-30',source_manifest:[{id:'source1',sha256:'sourceSHA'}]}
 const group={series:{provider:'A',period:'2026'}}
 const row={rank:1,symbol:'2330',name:'台積電',weight_pct:40,quote:{close:100,shares:50},estimate:{value:-2,id:'eps1',source_id:'source1',source_url:'https://source.example',published_at:'published',known_at:'known'},qualified:false,issues:['unknown'],revision:{previous_value:0,change:-2,change_pct:null,basis_comparability_verified:false}}
 const out=stockCsv(v,group,[row,{...row,symbol:'2454',estimate:null,revision:null,issues:['missing_forecast']}],'2026 年當年年度')
 for(const s of ['saved-report','saved-snapshot','method1','ruleSHA','sourceSHA','published','known','eps1','source1','2454','"-2"','"0"','missing_forecast'])assert.ok(out.includes(s),s)
 assert.equal(out.split('\r\n').length,4)
})
test('sector export follows the selected rows/date, with PP units and taxonomy/source provenance',()=>{
 const report={date:'2026-11-05',previous_date:'2026-11-04',classification:{snapshot:'dictionary1',sha256:'taxonomySHA'},version:'sector1',sources:{current:{TWSE:{id:'q1',sha256:'quoteSHA'}}},market_turnover_twd:1000,scope:'四碼股票',notes:['多標籤不可相加'],warnings:['部分金額']}
 const out=sectorCsv(report,[{name:'晶圓代工',parent:'電子',members:7,quoted:7,return_pct:-5.9,advancers:0,decliners:7,unchanged:0,amount_twd:20,amount_names:7,amount_complete:true,amount_share_pct:2,previous_amount_share_pct:1,share_change_pp:1}])
 for(const s of ['2026-11-05','2026-11-04','晶圓代工','百分點','taxonomySHA','quoteSHA','多標籤不可相加','部分金額','"-5.9"'])assert.ok(out.includes(s),s)
 assert.equal(out.split('\r\n').length,3)
})
test('actual source gaps are explained in Chinese',()=>{
 for(const s of ['current_per_share_adjustment_not_verified','per_share_adjustment_date_not_aligned','provider_forecast_date_missing','basic_eps_basis_not_verified'])assert.notEqual(issueLabel(s),s)
})
