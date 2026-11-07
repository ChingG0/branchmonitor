<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import ModelCurve from './ModelCurve.vue'
import StockHistory from './StockHistory.vue'
import type {V1Model} from './v8Types'
const props=defineProps<{model?:V1Model,selectedDate?:string,compact?:boolean,manual?:boolean}>()
const modelName=computed(()=>props.manual?'V10':'V1')
const day=ref('')
const dates=computed(()=>Object.keys(props.model?.days||{}).sort().reverse())
watch(()=>[props.selectedDate,props.model?.as_of],()=>{day.value=props.selectedDate&&props.model?.days[props.selectedDate]?props.selectedDate:props.model?.as_of||''},{immediate:true})
const selected=computed(()=>props.model?.days[day.value])
const series=computed(()=>[{key:modelName.value.toLowerCase(),label:modelName.value+' 固定本金總報酬',color:'#2d6c4f',curve:props.model?.curve||[]},{key:'taiex',label:'加權指數（同起日）',color:'#788a96',curve:props.model?.benchmark_curve||[]}])
const fmt=(n:number|null|undefined,d=0)=>n==null?'—':n.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(n:number|null|undefined)=>n==null?'—':`${n>=0?'+':''}${fmt(n,2)}%`
const tone=(n:number)=>n>=0?'gain':'loss'
const exits=computed(()=>props.model?.trades.filter(row=>row.exit_date===day.value)||[])
</script>

<template>
 <section class="v1-panel">
  <header><div><small>{{manual?'V10 · 手動 V1':'V1 · 獲利保護研究版'}}</small><h2>三檔強度重排＋獲利保護</h2><p>沿用 V2 選股，最多三檔；最高收盤較買價達 +8% 後，下一交易日起以 2ATR 追蹤防線保護獲利。</p></div><span>{{model?.start}} → {{model?.as_of}}</span></header>
  <p v-if="!model?.available" class="notice">{{model?.error||'V1 資料尚未完成'}}</p>
  <template v-else>
   <div class="metrics">
    <article><small>固定本金總報酬（含未實現）</small><b :class="tone(model.portfolio_return_pct)">{{pct(model.portfolio_return_pct)}}</b><span>初始 NT$ {{fmt(model.initial)}} → {{fmt(model.equity)}}</span></article>
    <article><small>已實現損益</small><b :class="tone(model.realized_pnl)">NT$ {{fmt(model.realized_pnl)}}</b><span>{{model.trades.length}} 筆完成 · 勝率 {{fmt(model.hit_rate,1)}}%</span></article>
    <article><small>未實現損益</small><b :class="tone(model.unrealized_pnl)">NT$ {{fmt(model.unrealized_pnl)}}</b><span>持有 {{model.days[model.as_of]?.holdings.length||0}} 檔 · 現金 {{fmt(model.cash)}}</span></article>
    <article><small>帳戶最大回撤</small><b class="loss">{{pct(model.max_drawdown_pct)}}</b><span>依每日帳戶淨值計算</span></article>
   </div>
   <div class="curve-head"><h3>固定本金累積績效</h3><p>總報酬＝（現金＋持股扣除預估賣出成本的價值－初始本金）÷初始本金。大盤線為同期價格漲跌幅。</p></div>
   <ModelCurve :series="series" :activity="model.activity"/>
   <p class="notice">{{model.research_note}}</p>

   <details v-if="model.validation" class="rules" open><summary>V1 與 V2 驗證比較 · 凍結至 {{model.validation.as_of}}</summary>
    <p>{{model.validation.note}}</p>
    <div class="table-wrap"><table><thead><tr><th>情境／模型</th><th>帳戶總報酬</th><th>最大回撤</th><th>完成勝率</th><th>獲利／完成筆數</th></tr></thead><tbody>
     <template v-for="item in [{key:'full',label:'完整區間'},{key:'assessment',label:'9/1 空手重啟'},{key:'stress',label:'買賣各多 0.3% 成本'},{key:'delayed',label:'盤中觸發延至次日開盤'}]" :key="item.key">
      <tr v-for="kind in ['baseline','selected'] as const" :key="kind"><td>{{item.label}} · {{kind==='baseline'?'V2':'V1'}}</td><td>{{pct(model.validation[kind][item.key]?.return_pct)}}</td><td>{{pct(model.validation[kind][item.key]?.max_drawdown_pct)}}</td><td>{{fmt(model.validation[kind][item.key]?.win_rate,1)}}%</td><td>{{model.validation[kind][item.key]?.wins}}／{{model.validation[kind][item.key]?.completed}}</td></tr>
     </template>
    </tbody></table></div>
    <p>10 組固定假設先以 5–8 月選擇；9 月起重啟帳戶只有 2 筆完成交易且兩者相同。全期勝率超過 50% 尚非穩定性證明，並未進行 2020–2022 年回測。V1 平均虧損交易較 V2 大，仍需持續觀察。</p>
    <p>文章啟發來自持續驗證與行情轉弱時保護部位；本模型沒有加入營收、EPS 或運價資料，不等同重現作者的基本面策略。</p>
   </details>
   <template v-if="!compact">
    <div class="date-heading"><div><h3>每日候選與帳戶部位</h3><p>候選不代表已買進；滿倉時保留觀察，有空位才依排名進場。</p></div><label>查看日期 <select v-model="day"><option v-for="date in dates" :key="date" :value="date">{{date}}</option></select></label></div>
    <template v-if="selected">
     <p class="day-summary">{{day}} · {{selected.blocked?'雙指數低於 SMA20，暫停新買進':'大盤允許進場'}} · 帳戶 NT$ {{fmt(selected.equity)}} · 現金 NT$ {{fmt(selected.cash)}}<br>當日買入：{{selected.buy.join('、')||'無'}} ／ 賣出：{{selected.sell.join('、')||'無'}}</p>
     <div class="table-wrap"><table><thead><tr><th>候選股票</th><th>分數</th><th>收盤價</th><th>初始停損參考</th><th>第一壓力觀察區</th><th>20 日淨買占量</th><th>買超持續性</th><th>20 日市場調整強度</th><th>壓縮比</th><th>狀態</th></tr></thead><tbody><tr v-for="row in selected.candidates" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><StockHistory :model-key="modelName.toLowerCase()" :symbol="row.symbol" :as-of="day"/></td><td>{{fmt(row.score,1)}}</td><td>{{fmt(row.close,2)}}</td><td><template v-if="row.status==='已持有'">—<small>見下方持有防線</small></template><template v-else>{{fmt(row.initial_stop_ref,2)}}<small v-if="row.initial_stop_ref!=null">收盤價試算 · {{pct((row.initial_stop_ref/row.close-1)*100)}}</small></template></td><td><template v-if="row.resistance">{{fmt(row.resistance.low,2)}}<template v-if="row.resistance.high>row.resistance.low">–{{fmt(row.resistance.high,2)}}</template><small>近 {{row.resistance.lookback}} 日高價區</small></template><template v-else>—<small>近 120 日無上方前高</small></template></td><td>{{fmt(row.pressure_pct,2)}}%</td><td>{{fmt(row.consistency_pct,1)}}%</td><td>{{pct(row.excess20_pct)}}</td><td>{{fmt(row.squeeze,2)}}</td><td>{{row.status}}</td></tr><tr v-if="!selected.candidates.length"><td colspan="10" class="empty">當日沒有符合全部條件的候選；不補滿三檔。缺少同日 RS 或分點資料也不入選。</td></tr></tbody></table></div>
     <p class="stop-note">初始停損參考是假設下一交易日開盤價等於本日收盤價，取「假設買價－3×ATR14」與「假設買價×92%」較高者；實際買入後依成交開盤價重算。第一壓力觀察區取當日及先前 20／60／120 日最接近的上方高價，半個 ATR 內合併；僅供觀察，不是停利或保證目標。跳空時不保證以參考價成交。</p>
     <h3 class="subheading">{{day}} 持有部位 · {{selected.holdings.length}} 檔</h3>
     <div class="table-wrap"><table><thead><tr><th>股票</th><th>進場日／價</th><th>股數</th><th>評價收盤</th><th>持有天數</th><th>當日／下交易日防線</th><th>未實現損益</th><th>成本後報酬</th></tr></thead><tbody><tr v-for="row in selected.holdings" :key="row.symbol"><td><strong>{{row.name}}</strong><small>{{row.symbol}}</small><StockHistory :model-key="modelName.toLowerCase()" :symbol="row.symbol" :as-of="day"/></td><td>{{row.entry_date}}<small>{{fmt(row.entry_price,2)}} 元</small></td><td>{{fmt(row.qty)}} 股</td><td>{{fmt(row.mark_price,2)}}</td><td>{{row.holding_sessions}} 日</td><td>{{fmt(row.stop,2)}}<small>下交易日 {{fmt(row.next_stop,2)}}</small><small>{{row.next_protection_active?'下交易日 2ATR 保護':'尚未達 +8% 收盤門檻'}}</small><small v-if="row.pending_exit">待可成交開盤退出</small></td><td :class="tone(row.pnl)">{{fmt(row.pnl)}}</td><td :class="tone(row.return_pct)">{{pct(row.return_pct)}}</td></tr><tr v-if="!selected.holdings.length"><td colspan="8" class="empty">當日空手</td></tr></tbody></table></div>
     <div v-if="exits.length" class="day-summary"><strong>當日已退出</strong><p v-for="row in exits" :key="row.symbol">{{row.symbol}} {{row.name}} · {{row.entry_date}} → {{row.exit_date}} · 賣價 {{fmt(row.exit_price,2)}} · 損益 {{fmt(row.pnl)}} 元（{{pct(row.return_pct)}}）</p></div>
    </template>
    <details open class="rules"><summary>{{modelName}} 選股、配置與退出規則</summary>
     <ol>
      <li><strong>基本資格：</strong>四碼普通股（排除 0 開頭），收盤 ≥ 10 元；近 20 日平均成交金額 ≥ 2,000 萬；當天 RS20、RS60、YTD 均有資料。不設定額外 RS 高分門檻。</li>
      <li><strong>吸籌：</strong>本機追蹤分點合計近 20 日淨買超為正，買超日占有效觀察日至少 55%，當日也要淨買超。至少 15 日分點資料；缺漏不當成零。</li>
      <li><strong>整理轉強：</strong>前一日的 10 日高低幅度相對近 40 日中位數 ≤ 1；今天收盤至少達前 20 日最高收盤的 98%，站上 SMA20，正乖離 ≤ 20%。</li>
      <li><strong>扣除市場影響：</strong>以截至前日的 60 日 Beta（至少 40 日、限制 -1～3）調整每日股票報酬，近 20 日累計必須為正。此數值是市場調整後強度，不是可保證的 alpha。</li>
      <li><strong>排名：</strong>20 日淨買超占成交量的百分位 × 40% ＋ 買超持續性百分位 × 30% ＋ 5 日市場調整強度／波動的百分位 × 30%。以上為原 V8 分數，占新分數 70%；另加近 20 日市場調整報酬／（其日波動 × √20）的百分位，占 30%。百分位在基本資格股票中計算，再取符合全部條件的前 3 檔。</li>
      <li><strong>大盤濾網：</strong>加權與櫃買同時低於各自 SMA20 才暫停新增部位；既有部位仍依退出規則處理。</li>
      <li><strong>資金：</strong>固定初始 100 萬、不追加、不借款，最多同時 3 檔，同股票不重複加碼。每筆預算取可用現金與帳戶淨值 1/3 的較小值，整數股且不超過訊號日成交股數的 1%。候選不足保留現金。</li>
      <li><strong>買進：</strong>收盤後產生候選，隔日開盤成交；先處理開盤跳空退出，再以開盤補空位；盤中賣出的錢不能回頭買同日開盤。開盤剛賣出的股票當日不買回。開盤或成交資料缺漏、全日無價格區間的標的不假設成交。</li>
      <li><strong>退出：</strong>初始防線取「買進價－3×訊號日 ATR14」與「買進價 × 92%」較高者。ATR14 為真實波幅的簡單平均。每天開盤前以截至前日最高收盤－3×前日 ATR14 更新，只升不降，依股價跳動單位向下取整。開盤低於防線按開盤價，盤中觸及則按防線近似成交；無法成交延後退出。買入當天也可能觸發。當最高收盤達買價的 108%，下一交易日起改用「最高收盤－2×前日 ATR14」，之後維持 2ATR 且防線只升不降。不使用當日最高價回頭提高同日停損；不固定持有日、不設獲利上限。8% 是保護啟動門檻，不是固定賣出目標或保證獲利。</li>
      <li><strong>成本與報酬：</strong>買進成本率 0.2855%、賣出成本率 0.5855%（含滑價假設）。持股以最後可用收盤扣除預估賣出成本評價；已實現與未實現都以金額列示，總報酬除以初始本金。持有天數為進場後經過的交易日，進場當天為 0 日。</li>
     </ol>
    </details>
   </template>
   <footer>{{model.source_note}} <template v-if="manual">此為對話保存的 V1 策略快照；行情、分點及 RS 更新後，仍顯示上次手動保存的日期與結果。複製前績效屬 V1 歷史重播。</template><template v-else>更新行情、分點或 RS 後，重新開啟本頁會重算；歷史資料修復可能改變重播結果。</template></footer>
  </template>
 </section>
</template>

<style scoped>
.v1-panel{border:1px solid #dfe5dc;border-radius:16px;background:white;overflow:hidden;margin-bottom:20px;color:#465441}.v1-panel>header,.date-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:24px;border-bottom:1px solid #edf0e9}.v1-panel h2,.v1-panel h3{margin:0 0 8px}.v1-panel p{margin:6px 0;line-height:1.6}.v1-panel header small{color:#307c7c;font-weight:700;letter-spacing:1px}.v1-panel header span,.v1-panel p,.metrics small,.metrics span{color:#7c8878;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(4,1fr)}.metrics article{padding:22px;border-right:1px solid #edf0e9;display:flex;flex-direction:column;gap:8px}.metrics b{font:700 27px Georgia,serif}.gain{color:#bc5a4e}.loss{color:#288163}.curve-head{padding:22px 24px 4px}.v1-panel .notice{margin:16px 24px;padding:12px 16px;background:#faf6eb;border-radius:10px;color:#8d744b}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:16px;text-align:right;border-bottom:1px solid #e9eee5;white-space:nowrap}th{background:#f5f7f2;color:#7e8c75}th:first-child,td:first-child{text-align:left}td small{display:block;color:#899382;margin-top:4px}.empty{text-align:center!important;white-space:normal;color:#8a9483}.day-summary,.subheading{padding:16px 24px}.stop-note{padding:0 24px 12px}.date-heading select{border:1px solid #dce3d5;border-radius:8px;padding:8px;background:white;color:#465441}.date-heading label{white-space:nowrap}.rules{margin:24px;padding:20px;background:#f7f8f3;border-radius:12px}.rules summary{font-weight:700;cursor:pointer}.rules ol{padding-left:22px;line-height:1.85;font-size:14px}.rules li{margin:9px 0}footer{padding:16px 24px;color:#899382;background:#fafbf8;font-size:12px;line-height:1.7}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.metrics{grid-template-columns:1fr}.v1-panel>header,.date-heading{align-items:flex-start;flex-direction:column}.rules{margin:12px;padding:15px}}
</style>
