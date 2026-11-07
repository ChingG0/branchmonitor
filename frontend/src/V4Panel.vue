<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import ModelCurve from './ModelCurve.vue'
import StockHistory from './StockHistory.vue'
import type {V4Model} from './v8Types'

const props=defineProps<{model?:V4Model,selectedDate?:string,compact?:boolean}>()
const day=ref('')
const dates=computed(()=>Object.keys(props.model?.days||{}).sort().reverse())
watch(()=>[props.selectedDate,props.model?.as_of],()=>{
 day.value=props.selectedDate&&props.model?.days[props.selectedDate]?props.selectedDate:props.model?.as_of||''
},{immediate:true})
const selected=computed(()=>props.model?.days[day.value])
const series=computed(()=>[
 {key:'v4',label:'V4 百萬挑戰・最多 1.5 倍',color:'#ae6d3d',curve:props.model?.curve||[]},
 {key:'v1-same-start',label:'V1 同日起跑・不借款',color:'#2d6c4f',curve:props.model?.unlevered_curve||[]},
 {key:'taiex',label:'加權指數・同日起跑',color:'#8393a2',curve:props.model?.benchmark_curve||[]},
])
const fmt=(value:number|null|undefined,d=0)=>value==null?'—':value.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(value:number|null|undefined)=>value==null?'—':`${value>=0?'+':''}${fmt(value,2)}%`
const tone=(value:number|null|undefined)=>value!=null&&value<0?'loss':'gain'
const exits=computed(()=>props.model?.trades.filter(row=>row.exit_date===day.value)||[])
</script>

<template>
<section class="v4-panel">
 <header><div><small>V4 · 百萬挑戰</small><h2>複製 V1 選股，槓桿最多 1.5 倍</h2><p>本金十萬元，2026-10-16 空手起跑；V1 三檔排名與停損規則相同。沒有符合條件的股票就保留現金。</p></div><span>本金 NT$ 100,000 · {{model?.as_of||'等待起跑日收盤'}}</span></header>
 <div class="metrics">
  <article><small>帳戶淨值／總報酬</small><b :class="tone(model?.portfolio_return_pct)">NT$ {{fmt(model?.equity)}}</b><span>{{pct(model?.portfolio_return_pct)}} · 已扣買賣成本及借款利息</span></article>
  <article><small>目前借款／借款利息累計</small><b>NT$ {{fmt(model?.borrowed)}}</b><span>利息 NT$ {{fmt(model?.financing_cost,2)}} · 年率 6% 試算</span></article>
  <article><small>股票曝險／實際槓桿</small><b>{{fmt(model?.actual_leverage,2)}} 倍</b><span>股票市值 NT$ {{fmt(model?.gross_exposure)}} · 新買入上限 1.5 倍</span></article>
  <article><small>同期不借款 V1／最大回撤</small><b>{{pct(model?.unlevered_return_pct)}}</b><span>V4 回撤 {{pct(model?.max_drawdown_pct)}}</span></article>
 </div>
 <template v-if="!model?.available">
  <p class="notice">{{model?.error||'尚無起跑日收盤資料；目前空手持有十萬元。'}}</p>
  <section v-if="!compact&&model?.prestart_preview" class="prestart-preview">
   <div class="date-head"><div><h3>{{model.prestart_preview.intended_date}} 預選觀察名單</h3><p>依 {{model.prestart_preview.signal_date}} 收盤資料產生；預計股數以收盤價、最多 1.5 倍及成交量 1% 上限試算，不記為買入。</p></div></div>
   <p class="summary">{{model.prestart_preview.blocked?'兩指數低於 20MA，暫停新買進；以下僅供觀察。':'大盤濾網通過；以下僅供起跑前觀察，尚未建立 V4 部位。'}}</p>
   <div class="table-wrap"><table><thead><tr><th>候選股票</th><th>分數</th><th>收盤價</th><th>預計股數</th><th>初始停損參考</th><th>第一壓力觀察區</th><th>壓縮比</th><th>狀態</th></tr></thead><tbody><tr v-for="row in model.prestart_preview.candidates" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><StockHistory model-key="v4" :symbol="row.symbol" :as-of="model.prestart_preview.signal_date"/></td><td>{{fmt(row.score,1)}}</td><td>{{fmt(row.close,2)}}</td><td>{{fmt(row.planned_shares)}} 股<small>{{model.prestart_preview.blocked?'暫停買進・僅試算':'收盤價試算'}}</small></td><td>{{fmt(row.initial_stop_ref,2)}}</td><td><template v-if="row.resistance">{{fmt(row.resistance.low,2)}}<template v-if="row.resistance.high>row.resistance.low">–{{fmt(row.resistance.high,2)}}</template><small>近 {{row.resistance.lookback}} 日高價區</small></template><template v-else>—</template></td><td>{{fmt(row.squeeze,2)}}</td><td>{{row.status}}</td></tr><tr v-if="!model.prestart_preview.candidates.length"><td class="empty" colspan="8">{{model.prestart_preview.signal_date}} 無合格候選，不補滿三檔。</td></tr></tbody></table></div>
  </section>
 </template>
 <template v-else>
  <div class="curve-head"><h3>十萬元挑戰績效</h3><p>三條曲線從同一起跑日計算；借款利息依實際負現金與日曆天數計入 V4 淨值。</p></div>
  <ModelCurve :series="series" :activity="model.activity"/>
  <p class="notice">{{model.research_note}}</p>
  <template v-if="!compact">
   <div class="date-head"><div><h3>每日候選與部位</h3><p>收盤選股，下一交易日開盤試算；預計股數以當日收盤價及成交量 1% 上限估算，候選不代表已買進。</p></div><label>查看日期 <select v-model="day"><option v-for="date in dates" :key="date" :value="date">{{date}}</option></select></label></div>
   <template v-if="selected">
    <p class="summary">{{day}} · {{selected.blocked?'兩指數低於 20MA，暫停新買進':'大盤允許進場'}} · 淨值 NT$ {{fmt(selected.equity)}} · 借款 NT$ {{fmt(selected.borrowed)}} · 實際槓桿 {{fmt(selected.actual_leverage,2)}} 倍<br>買入：{{selected.buy.join('、')||'無'}} ／ 賣出：{{selected.sell.join('、')||'無'}}</p>
    <div class="table-wrap"><table><thead><tr><th>候選股票</th><th>分數</th><th>收盤價</th><th>預計股數</th><th>初始停損參考</th><th>第一壓力觀察區</th><th>壓縮比</th><th>狀態</th></tr></thead><tbody><tr v-for="row in selected.candidates" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><StockHistory model-key="v4" :symbol="row.symbol" :as-of="day"/></td><td>{{fmt(row.score,1)}}</td><td>{{fmt(row.close,2)}}</td><td>{{fmt(row.planned_shares)}} 股<small>{{selected.blocked?'暫停買進・僅試算':row.status==='已持有'?'已持有・不加碼':'收盤價試算'}}</small></td><td>{{fmt(row.initial_stop_ref,2)}}</td><td><template v-if="row.resistance">{{fmt(row.resistance.low,2)}}<template v-if="row.resistance.high>row.resistance.low">–{{fmt(row.resistance.high,2)}}</template><small>近 {{row.resistance.lookback}} 日高價區</small></template><template v-else>—</template></td><td>{{fmt(row.squeeze,2)}}</td><td>{{row.status}}</td></tr><tr v-if="!selected.candidates.length"><td class="empty" colspan="8">當日無合格候選，不補滿三檔。</td></tr></tbody></table></div>
    <h3 class="subhead">持有部位 · {{selected.holdings.length}} 檔</h3>
    <div class="table-wrap"><table><thead><tr><th>股票</th><th>進場日／價</th><th>股數</th><th>收盤評價</th><th>當日／下日防線</th><th>成本後報酬</th></tr></thead><tbody><tr v-for="row in selected.holdings" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><StockHistory model-key="v4" :symbol="row.symbol" :as-of="day"/></td><td>{{row.entry_date}}<small>{{fmt(row.entry_price,2)}}</small></td><td>{{fmt(row.qty)}} 股</td><td>{{fmt(row.mark_price,2)}}</td><td>{{fmt(row.stop,2)}}<small>下日 {{fmt(row.next_stop,2)}}</small></td><td :class="tone(row.return_pct)">{{pct(row.return_pct)}}</td></tr><tr v-if="!selected.holdings.length"><td class="empty" colspan="6">當日空手</td></tr></tbody></table></div>
    <div v-if="exits.length" class="summary"><strong>當日退出</strong><p v-for="row in exits" :key="row.symbol+row.entry_date">{{row.name}} {{row.symbol}} · {{row.entry_date}} → {{row.exit_date}} · {{pct(row.return_pct)}}（NT$ {{fmt(row.pnl)}}）</p></div>
   </template>
   <details class="rules"><summary>V4 借款與 V1 規則</summary><p>候選、排序、最多三檔、隔日開盤買入、3ATR／8% 初始防線，以及獲利達 +8% 後下一交易日起 2ATR 保護，均沿用 V1。每檔買入預算最多為當時淨值的 50%；新買入時股票總曝險不超過淨值的 150%，借款不超過淨值的 50%。</p><p>負現金視為模擬借款，按假設年息 6% 與日曆天數逐日計息。個股買賣成本與 V1 相同。這不是實際券商融資帳戶：未模擬融資資格、追繳、強制平倉與零股撮合；持有後市場波動可能使實際槓桿高於 1.5 倍。</p></details>
  </template>
 </template>
 <footer>{{model?.source_note||'尚未開始交易。'}} V4 不會改寫 V1 或 V3 的資料。</footer>
</section>
</template>

<style scoped>
.v4-panel{border:1px solid #e3e7de;border-radius:16px;background:white;overflow:hidden;margin-bottom:20px;color:#465441}.v4-panel>header,.date-head{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:24px;border-bottom:1px solid #edf0e9}.v4-panel h2,.v4-panel h3{margin:0 0 8px}.v4-panel p{line-height:1.6}.v4-panel header small{color:#ae6d3d;font-weight:700}.v4-panel header span,.v4-panel p,.metrics small,.metrics span{font-size:13px;color:#7c8878}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}.metrics article{padding:20px;border-right:1px solid #edf0e9;display:flex;flex-direction:column;gap:8px}.metrics b{font-size:25px}.gain{color:#bc5a4e}.loss{color:#288163}.curve-head{padding:22px 24px 4px}.notice{margin:16px 24px;padding:12px 16px;background:#faf6eb;border-radius:10px}.prestart-preview{border-top:1px solid #edf0e9}.prestart-preview .date-head{padding:16px 24px}.summary,.subhead{padding:14px 24px}.date-head select{border:1px solid #dce3d5;border-radius:8px;padding:8px;background:white}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:14px;text-align:right;border-bottom:1px solid #e9eee5;white-space:nowrap}th{background:#f5f7f2;color:#7e8c75}th:first-child,td:first-child{text-align:left}td small{display:block;color:#899382;margin-top:4px}.empty{text-align:center!important;white-space:normal}.rules{margin:24px;padding:18px;background:#f7f8f3;border-radius:12px}.rules summary{cursor:pointer;font-weight:700}footer{padding:16px 24px;color:#899382;background:#fafbf8;font-size:12px}@media(max-width:1100px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.metrics{grid-template-columns:1fr}.v4-panel>header,.date-head{align-items:flex-start;flex-direction:column}.rules{margin:12px}}
</style>
