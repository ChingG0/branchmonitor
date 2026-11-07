<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import ModelCurve from './ModelCurve.vue'
import StockHistory from './StockHistory.vue'
import type {V11Model} from './v11Types'
const props=defineProps<{model?:V11Model}>()
const day=ref('')
watch(()=>props.model?.revision,()=>{day.value=props.model?.as_of||''},{immediate:true})
const dates=computed(()=>Object.keys(props.model?.days||{}).sort().reverse())
const selected=computed(()=>props.model?.days[day.value])
const exits=computed(()=>props.model?.trades.filter(row=>row.exit_date===day.value)||[])
const series=computed(()=>[{key:'v11',label:'V11 手動帳戶',color:'#6974a8',curve:props.model?.curve||[]}])
const n=(v:number|null|undefined,d=0)=>v==null?'—':v.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(v:number|null|undefined)=>v==null?'—':`${v>=0?'+':''}${n(v,2)}%`
const stamp=(v:string|null|undefined)=>v?new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v)):'—'
const tone=(v:number)=>v>=0?'gain':'loss'
</script>
<template>
 <section class="v11-panel">
  <header><div><small>V11 · 手動 α 趨勢</small><h2>用超額強度尋找延續標的</h2><p>上市櫃獨立選股 · 正 α 的 HAC t 值排序＋趨勢確認 · 最多三檔</p></div><span>與 V3、V6、V10 一起依對話更新</span></header>
  <div class="notice">{{model?.update_policy}}<br><strong>收盤資料 {{model?.as_of||'—'}} · 保存 {{stamp(model?.recorded_at)}} · 第 {{model?.revision||0}} 版</strong></div>
  <p v-if="!model?.available" class="help">尚未建立 V11 手動帳戶；到對話要求更新後才會保存名單。</p>
  <template v-else>
   <div class="metrics">
    <article><small>獨立帳戶淨值</small><b>NT$ {{n(model.equity)}}</b><span :class="tone(model.portfolio_return_pct)">{{pct(model.portfolio_return_pct)}} · 初始 {{n(model.initial)}} 元</span></article>
    <article><small>現金／已持有</small><b>NT$ {{n(model.cash)}}</b><span>{{model.holdings.length}} 檔持股 · {{model.pending_orders.length}} 檔待執行計畫</span></article>
    <article><small>已實現／未實現損益</small><b :class="tone(model.realized_pnl)">{{n(model.realized_pnl)}} 元</b><span :class="tone(model.unrealized_pnl)">未實現 {{n(model.unrealized_pnl)}} 元</span></article>
    <article><small>完成交易勝率／最大回撤</small><b>{{model.hit_rate==null?'尚無完成交易':n(model.hit_rate,1)+'%'}}</b><span>{{model.wins}}／{{model.trades.length}} 筆 · 回撤 {{pct(model.max_drawdown_pct)}}</span></article>
   </div>
   <div class="chart"><h3>從建立日起記錄的帳戶績效</h3><p>建立 {{stamp(model.created_at)}}；研究回測獲利不放入帳戶。股息應收 {{n(model.dividend_receivable)}} 元計入淨值，暫不當作可買股票的現金。</p><ModelCurve :series="series" :activity="model.activity"/></div>
   <div class="section-head"><h3>最新市場與資料狀態</h3><strong>{{model.review?.context.blocked?'雙指數低於20MA，暫停新買進':'大盤允許研究買進'}}</strong></div>
   <div class="market" v-if="model.review"><article v-for="(index,key) in model.review.context.indices" :key="key"><small>{{key==='TAIEX'?'加權指數':'櫃買指數'}}</small><b>{{n(index.close,2)}}</b><span>20MA {{n(index.ma20,2)}} · {{index.above?'均線之上':'均線之下'}}</span></article><article><small>本次選股涵蓋</small><b>{{model.review.context.coverage.qualified}} 檔通過</b><span>{{model.review.context.coverage.ordinary}} 檔普通股 · 處置排除 {{model.review.context.coverage.disposition_excluded}} 檔</span></article></div>
   <p class="help analysis">{{model.review?.analysis}}</p>
   <div class="section-head"><div><h3>候選與帳戶紀錄</h3><p>候選、已保存計畫與實際持股分開顯示；排名不是買進確認。</p></div><label>查看日期 <select v-model="day"><option v-for="date in dates" :key="date" :value="date">{{date}}</option></select></label></div>
   <template v-if="selected">
    <p class="help">{{day}} · {{selected.kind}} · 帳戶 {{n(selected.equity)}} 元 · 當日買入：{{selected.buy.join('、')||'無'}} ／ 賣出：{{selected.sell.join('、')||'無'}}</p>
    <div class="table-wrap"><table><thead><tr><th>候選股票</th><th>日 α（歷史）</th><th>α HAC t 值</th><th>β</th><th>20 日超額</th><th>收盤／預計股數</th><th>初始停損參考</th><th>第一壓力觀察區</th><th>狀態</th></tr></thead><tbody>
     <tr v-for="row in selected.candidates" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><small v-for="flag in row.flags" :key="flag">{{flag}}</small><StockHistory model-key="v11" :symbol="row.symbol" :as-of="day"/></td><td>{{pct(row.alpha_pct)}}<small>近似95%區間 {{pct(row.alpha_ci_pct[0])}}～{{pct(row.alpha_ci_pct[1])}}</small></td><td>{{n(row.alpha_t,2)}}</td><td>{{n(row.beta,2)}}<small>R² {{n(row.r2,2)}}</small></td><td>{{pct(row.excess20_pct)}}</td><td>{{n(row.close,2)}} 元<small>約 {{n(row.planned_shares)}} 股</small></td><td>{{n(row.initial_stop_ref,2)}}<small>收盤價試算，成交後重算</small></td><td><template v-if="row.resistance">{{n(row.resistance.low,2)}}～{{n(row.resistance.high,2)}}<small>近 {{row.resistance.lookback}} 日高價區</small></template><template v-else>近120日無上方前高</template></td><td>{{row.status}}</td></tr>
     <tr v-if="!selected.candidates.length"><td colspan="9" class="empty">本日沒有保存新的候選。核對日只處理先前計畫，不事後補入當日排名。</td></tr>
    </tbody></table></div>
    <p class="help">預計股數以收盤價與每筆淨值 1/3 試算；不是已成交股數。壓力區供觀察，沒有固定目標價。α、t 值及近似區間均描述歷史，沒有多重比較校正，不是下次上漲機率。</p>
    <div class="section-head"><h3>{{day}} 已持有部位 · {{selected.holdings.length}} 檔</h3></div>
    <div class="table-wrap"><table><thead><tr><th>股票</th><th>進場日／價</th><th>股數</th><th>評價收盤</th><th>當日／下交易日防線</th><th>持有</th><th>未實現損益／報酬</th></tr></thead><tbody>
     <tr v-for="row in selected.holdings" :key="row.symbol"><td>{{row.name}}<small>{{row.symbol}}</small><StockHistory model-key="v11" :symbol="row.symbol" :as-of="day"/></td><td>{{row.entry_date}}<small>{{n(row.entry_price,2)}} 元</small></td><td>{{n(row.qty)}} 股</td><td>{{n(row.mark_price,2)}}<small>{{row.mark_date}}</small></td><td>{{n(row.stop,2)}}<small>下交易日 {{n(row.next_stop,2)}} · {{row.protection_active?'2ATR 保護':'3ATR 追蹤'}}</small><small v-if="row.pending_exit">等待可成交開盤退出</small></td><td>{{row.holding_sessions}} 日</td><td :class="tone(row.pnl)">{{n(row.pnl)}} 元<small>{{pct(row.return_pct)}}</small></td></tr>
     <tr v-if="!selected.holdings.length"><td colspan="7" class="empty">當日空手；候選或買入計畫尚不算持股。</td></tr>
    </tbody></table></div>
    <div v-if="exits.length" class="help"><strong>當日已退出</strong><p v-for="row in exits" :key="row.symbol+row.entry_date">{{row.name}} {{row.symbol}} · {{row.entry_date}} → {{row.exit_date}} · 賣價 {{n(row.exit_price,2)}} · {{n(row.pnl)}} 元（{{pct(row.return_pct)}}）</p></div>
   </template>
   <div class="section-head"><h3>最新已保存的買入計畫</h3><span>只在下一次對話更新時核對成交</span></div>
   <div class="table-wrap"><table><thead><tr><th>股票</th><th>訊號收盤日</th><th>保存時間</th><th>可試算的開盤</th><th>預算上限</th></tr></thead><tbody><tr v-for="row in model.pending_orders" :key="row.symbol"><td>{{row.name}} {{row.symbol}}</td><td>{{row.signal_date}}</td><td>{{stamp(row.recorded_at)}}</td><td>{{row.not_before_date}} 之後首個交易日<small>不回填保存前已過的開盤</small></td><td>NT$ {{n(row.budget)}}</td></tr><tr v-if="!model.pending_orders.length"><td colspan="5" class="empty">沒有待執行買入計畫</td></tr></tbody></table></div>
   <details class="rules" open><summary>V11 選股與執行規則</summary><ol>
    <li>四碼普通股、收盤至少10元；最近20日官方實際平均成交值至少2,000萬元；完整120日含息報酬，殘差日波動至少0.05%。上市櫃共同使用加權含息基準，不混排不同基準。</li>
    <li>日α為正，依該120日回歸自身HAC（5日）t值排序。收盤高於20MA、20MA高於五日前、最近20日β調整超額為正、20MA乖離不超過20%。處置中或狀態未知者不新增；除權、減資、分割未核對者不估算α。</li>
    <li>最多選前三名與持有三檔，100萬元起跑，不借款。每筆預算取淨值1/3與可用現金較小者，股數不超過訊號日成交量1%。排名變更不強迫換股，候選不足保留現金。</li>
    <li>加權與櫃買同時低於20MA時不新增。買入計畫保存後才生效；盤後資料只能用於之後的開盤，不回填保存前的交易。兩次手動更新之間不以後來排名產生新買單。</li>
    <li>初始防線取買價減3ATR14與買價92%較高者；以已完成收盤更新追蹤防線，最高收盤達買價+8%後轉2ATR，防線只升不降（除息作相應調整）。開盤跳空或無法成交，損失可能超過8%；盤中以日K近似觸發，當日盤中賣出款不回頭買同日開盤。</li>
    <li>買入成本0.2855%、賣出0.5855%；未平倉扣預估賣出成本。現金股利列應收、不提前再投資；未核對的非現金公司事件會阻止新快照保存，不假裝已完成損益。</li>
   </ol><p>{{model.execution_note}}</p></details>
   <details class="rules"><summary>研究限制與本次來源</summary><p>{{model.research_note}}</p><p>手動執行與每日規則回測不同；沒有沿用9月起回測獲利，不承諾高報酬或穩定勝率。</p><ul v-if="model.review"><li v-for="source in model.review.sources" :key="source.url"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{source.title}}</a></li></ul></details>
  </template>
 </section>
</template>
<style scoped>
.v11-panel{border:1px solid #dce3dd;border-radius:16px;background:#fff;color:#405345;overflow:hidden}.v11-panel>header,.section-head{padding:22px 24px;display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:1px solid #e9eee7}.v11-panel h2,.v11-panel h3{margin:6px 0}.v11-panel p{font-size:13px;color:#788573;line-height:1.8}.v11-panel header small{color:#6974a8;font-weight:700}.v11-panel header span,.section-head>span{font-size:12px;color:#7e887b}.notice{margin:18px 24px;background:#f3f5fa;border-radius:10px;padding:14px 18px;font-size:13px;line-height:1.9}.metrics{display:grid;grid-template-columns:repeat(4,1fr)}.metrics article{padding:22px;display:flex;flex-direction:column;gap:10px;border-right:1px solid #e9eee7}.metrics small,.metrics span,.market small,.market span{font-size:12px;color:#7f8c7b}.metrics b{font-size:25px}.gain{color:#ba5b50!important}.loss{color:#288163!important}.chart{padding:20px 24px}.help{padding:10px 24px;font-size:13px;color:#7b8774;line-height:1.8}.analysis{white-space:pre-line}.market{display:grid;grid-template-columns:repeat(3,1fr);padding:12px 24px;gap:16px}.market article{display:flex;flex-direction:column;gap:7px;background:#f7f9f4;padding:16px;border-radius:10px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{white-space:nowrap;padding:16px;text-align:right;border-bottom:1px solid #e9eee7}th{background:#f5f7f2;color:#7c8a73}th:first-child,td:first-child{text-align:left}td small{display:block;font-size:11px;color:#8a9481;margin-top:5px}.empty{text-align:center!important;white-space:normal;color:#87927d}.section-head label{white-space:nowrap;font-size:13px}select{padding:8px;border:1px solid #dce3d5;border-radius:8px;background:#fff;color:#465441}.rules{margin:20px 24px;padding:18px;background:#f7f8f3;border-radius:12px;font-size:13px;line-height:1.9}.rules summary{cursor:pointer;font-weight:700}.rules li{margin:9px 0}.rules a{color:#397f80}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){.metrics,.market{grid-template-columns:1fr}.v11-panel>header,.section-head{align-items:flex-start;flex-direction:column}.rules{margin:14px}}
</style>
