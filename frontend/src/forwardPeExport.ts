import type {ForwardValuation,ForwardGroup,ForwardRow} from './forwardPeTypes'
import type {SectorReport,SectorRow} from './forwardPeSectors'

// CSV is built from the exact displayed response. No re-fetch can substitute
// a newer source midway through the download. Numeric negatives stay numeric.
export function csv(rows:unknown[][]):string{
 const cell=(v:unknown)=>{let s=v==null?'':String(v);if(typeof v==='string'&&/^\s*[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"'}
 return '\uFEFF'+rows.map(r=>r.map(cell).join(',')).join('\r\n')+'\r\n'
}
export function periodRows(group:ForwardGroup|undefined,base:ForwardRow[]):ForwardRow[]{
 return group?.rows||base.map(r=>({...r,estimate:null,revision:null,individual_pe:null,qualified:false,issues:['missing_forecast']}))
}
export function stockCsv(v:ForwardValuation,group:ForwardGroup|undefined,rows:ForwardRow[],period:string):string{
 const h=['報告ID','快照ID','方法版本','規則SHA','資訊截點','計算保存','行情日','權重日','預估期間','來源口徑','排名','代碼','股票','原月底權重%','收盤','股數','預估EPS','個股預估本益比','已核對','缺口','前版EPS','文件變化','文件變化%','變化口徑已核對','預估發布','來源預估日','實際入庫','預估ID','來源ID','來源網址','預估來源SHA','行情ID','行情來源ID','行情來源SHA','行情實際入庫']
 return csv([h,...rows.map(r=>{const e=r.estimate,rev=r.revision;return [v.id,v.snapshot_id,v.method_version,v.policy_sha256,v.information_cutoff,v.computed_at,v.price_date,v.weight_date,period,JSON.stringify(group?.series||{}),r.rank,r.symbol,r.name,r.weight_pct,r.quote?.close,r.quote?.shares,e?.value,r.individual_pe,r.qualified,r.issues.join('|'),rev?.previous_value,rev?.change,rev?.change_pct,rev?.basis_comparability_verified,e?.published_at,e?.forecast_as_of,e?.known_at,e?.id,e?.source_id,e?.source_url,v.source_manifest?.find(s=>s.id===e?.source_id)?.sha256,r.quote?.id,r.quote?.source_id,v.source_manifest?.find(s=>s.id===r.quote?.source_id)?.sha256,r.quote?.known_at]})])
}
export function sectorCsv(report:SectorReport,rows:SectorRow[]):string{
 return csv([['行情日','前一交易日','族群','大類','成員','兩日價格檔數','等權漲跌%','漲','跌','平','成交金額元','金額檔數','金額完整','當日比重%','前日比重%','比重變化百分點','四碼股票成交分母元','分類快照','分類SHA','方法版本','當日前日來源','範圍與限制'],...rows.map(r=>[report.date,report.previous_date,r.name,r.parent,r.members,r.quoted,r.return_pct,r.advancers,r.decliners,r.unchanged,r.amount_twd,r.amount_names,r.amount_complete,r.amount_share_pct,r.previous_amount_share_pct,r.share_change_pp,report.market_turnover_twd,report.classification.snapshot,report.classification.sha256,report.version,JSON.stringify(report.sources||{}),[report.scope,...report.notes,...report.warnings].join('；')])])
}
export function downloadCsv(content:string,name:string){
 const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
export function issueLabel(s:string):string{
 return ({missing_forecast:'缺預估EPS',basic_eps_basis_not_verified:'基本EPS口徑未核對',share_basis_not_verified:'股數調整口徑未核對',current_per_share_adjustment_not_verified:'缺每股調整口徑與證據',per_share_adjustment_date_not_aligned:'股數調整日期未對齊',forecast_outside_freshness_window:'預估不在有效日期範圍',basis_evidence_missing:'缺每股調整證據',provider_forecast_date_missing:'缺來源預估日期',share_basis_date_not_aligned:'股數調整日期未對齊',forecast_stale:'預估過期',conflicting_latest_estimates:'同版本預估衝突',missing_or_invalid_price:'缺收盤價',missing_or_invalid_shares:'缺發行股數'} as Record<string,string>)[s]||s
}
