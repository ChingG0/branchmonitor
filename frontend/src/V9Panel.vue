<script setup lang="ts">
import {computed} from 'vue'
import ModelCurve from './ModelCurve.vue'
import type {V9Model} from './v9Types'

const props=defineProps<{model?:V9Model,refreshing?:boolean,message?:string}>()
const emit=defineEmits<{refresh:[]}>()
const fmt=(value:number|null|undefined,d=0)=>value==null?'—':value.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(value:number|null|undefined)=>value==null?'—':`${value>0?'+':''}${fmt(value,2)}%`
const points=(value:number|null|undefined)=>value==null?'—':`${value>0?'+':''}${fmt(value,0)} 點`
const tone=(value:number|null|undefined)=>value==null?'neutral':value>0?'gain':value<0?'loss':'neutral'
const series=computed(()=>[
 {key:'v9',label:'V9 台指期反手',color:'#536da3',curve:props.model?.curve||[]},
 {key:'tx-hold',label:'持有轉倉台指期',color:'#b58a43',curve:props.model?.hold_tx_curve||[]},
 {key:'taiex',label:'加權指數',color:'#607d72',curve:props.model?.index_curve||[]},
])
const recent=computed(()=>[...(props.model?.activity||[])].reverse().slice(0,12))
</script>

<template>
 <section class="v9-panel">
  <header><div><small>V9 · 台指期買賣模型</small><h2>20 日高點回檔反手</h2><p>加權指數首次跌破前 20 個交易日高點的 8% 回檔線，下一交易日日盤開盤由多翻空；再下一日日盤開盤恢復多單。</p></div><button :disabled="refreshing" @click="emit('refresh')">{{refreshing?'更新中…':'更新 V9 行情'}}</button></header>
  <p v-if="message" class="note">{{message}}</p>
  <p v-if="model?.status==='failed'" class="note error">{{model.error||'V9 行情暫時無法計算'}}</p>
  <template v-else-if="model?.as_of">
   <div class="signal"><div><small>最新收盤訊號 · {{model.signal_date}}</small><strong>{{model.next_action}}</strong><span>下一次執行契約 {{model.target_contract}} · 加權指數收盤 {{fmt(model.index_close,2)}}</span></div><p v-if="model.missing_dates?.length">有 {{model.missing_dates.length}} 個新交易日缺少台指期日盤行情，績效停在 {{model.as_of}}，沒有跳過缺口。</p><p v-else>績效行情截至 {{model.as_of}} · 目前模型部位 {{model.position===-1?'空單':'多單'}}</p></div>
   <div class="metrics"><article><small>{{model.period}} 模型報酬</small><b :class="tone(model.portfolio_return_pct)">{{pct(model.portfolio_return_pct)}}</b><span>{{points(model.pnl_points)}} · 一口約 NT$ {{fmt(model.pnl_twd_per_contract)}}</span></article><article><small>加權指數同期間</small><b :class="tone(model.taiex_pct)">{{pct(model.taiex_pct)}}</b><span>價格指數，不含股利</span></article><article><small>持有並轉倉台指期</small><b :class="tone(model.hold_tx_pct)">{{pct(model.hold_tx_pct)}}</b><span>同樣扣每邊 {{fmt(model.cost_points_per_side)}} 點</span></article><article><small>模型最大回撤</small><b :class="tone(model.max_drawdown_pct)">{{pct(model.max_drawdown_pct)}}</b><span>以一口名目點數評價</span></article></div>
   <div class="curve-head"><h3>台指期買賣與兩個基準</h3><p>三條線都從 {{model.period}} 的同一起點計算；反手、轉倉與成本已計入。</p></div>
   <ModelCurve :series="series" unit="%"/>
   <p class="note">{{model.basis}} {{model.validation}}</p>
   <div class="tables"><div><h3>逐年核對</h3><div class="scroll"><table><thead><tr><th>年度</th><th>V9</th><th>持有台指期</th><th>加權指數</th><th>相對台指期</th></tr></thead><tbody><tr v-for="year in [...model.years].reverse()" :key="year.year"><td>{{year.year}}<small v-if="year.year===2026">截至 {{year.to}}</small></td><td :class="tone(year.model_pct)">{{pct(year.model_pct)}}</td><td>{{pct(year.hold_tx_pct)}}</td><td>{{pct(year.taiex_pct)}}</td><td :class="tone(year.edge_vs_hold_tx_pp)">{{pct(year.edge_vs_hold_tx_pp)}}</td></tr></tbody></table></div></div><div><h3>最近執行紀錄</h3><div class="scroll"><table><thead><tr><th>日盤開盤</th><th>動作</th><th>契約</th><th>開盤點位</th></tr></thead><tbody><tr v-for="row in recent" :key="row.date+'-'+row.contract"><td>{{row.date}}<small>前日訊號 {{row.signal_date}}</small></td><td>{{row.action}}</td><td>{{row.contract}}</td><td>{{fmt(row.open)}}</td></tr><tr v-if="!recent.length"><td colspan="4">尚無執行紀錄</td></tr></tbody></table></div></div></div>
   <footer>研究版規則已固定；收盤後形成訊號，隔日日盤開盤執行。2026 年曾被用於探索門檻，歷史績效不能當成獨立樣本或未來保證。</footer>
  </template>
 </section>
</template>

<style scoped>
.v9-panel{background:#fff;border:1px solid #dfe6dc;border-radius:16px;overflow:hidden;margin-bottom:20px;color:#485548}.v9-panel>header{padding:22px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px}.v9-panel h2,.v9-panel h3{margin:4px 0 8px}.v9-panel header small{color:#536da3;font-weight:800;letter-spacing:1px}.v9-panel header p{max-width:820px;color:#778575;font-size:13px;line-height:1.7;margin:0}.v9-panel button{border:0;border-radius:10px;background:#536da3;color:white;padding:10px 16px;cursor:pointer;white-space:nowrap}.v9-panel button:disabled{opacity:.5;cursor:wait}.signal{display:flex;justify-content:space-between;gap:20px;padding:18px 24px;background:#f3f6fb;border-top:1px solid #e5eaf2}.signal small,.signal span{display:block;color:#78869a;font-size:12px}.signal strong{display:block;font-size:21px;margin:5px 0}.signal p{max-width:400px;color:#78869a;font-size:12px;line-height:1.6}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border-block:1px solid #e9eee8}.metrics article{padding:18px 20px;border-right:1px solid #edf0eb}.metrics article:last-child{border-right:0}.metrics small,.metrics span{display:block;color:#7c8879;font-size:12px}.metrics b{display:block;font:700 26px Georgia,serif;margin:8px 0}.gain{color:#be5b50}.loss{color:#2d8060}.neutral{color:#485548}.curve-head{padding:18px 24px 4px}.curve-head p{color:#7d8a7b;font-size:12px;margin:0}.note{margin:12px 24px;padding:11px 14px;background:#f7f8f4;border-radius:8px;font-size:12px;line-height:1.7;color:#758170}.note.error{color:#b34f48;background:#fff4f2}.tables{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:20px 24px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:12px 9px;border-bottom:1px solid #e9eee8;text-align:right;white-space:nowrap}th{background:#f5f7f3;color:#748170}th:first-child,td:first-child{text-align:left}td small{display:block;color:#94a092;margin-top:3px}footer{padding:14px 24px;background:#fafbf8;color:#859080;font-size:12px;line-height:1.6}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,1fr)}.tables{grid-template-columns:1fr}}@media(max-width:620px){.v9-panel>header,.signal{flex-direction:column;align-items:flex-start}.metrics{grid-template-columns:1fr}.metrics article{border-bottom:1px solid #edf0eb}}
</style>
