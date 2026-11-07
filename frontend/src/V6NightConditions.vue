<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {localFetch} from './http'
import {validatePresentation} from './v6ForecastPresentation'
import type {NightConditions,Presentation} from './v6ForecastPresentation'
const props=defineProps<{payload?:Presentation}>(),data=ref<NightConditions|null>(props.payload?validatePresentation(props.payload).conditions||null:null),error=ref('')
const p=(x:number)=>'約 '+Math.round(x*100)+'%',n=(x:number)=>x.toLocaleString('zh-TW',{maximumFractionDigits:2}),time=(s:string)=>s.slice(0,19).replace('T',' ')
onMounted(async()=>{if(props.payload)return;try{const r=await localFetch('/v6-research/forecast-presentation.json');if(!r.ok)throw Error('條件研究尚未匯出');const d=validatePresentation(await r.json());data.value=d.conditions||null;if(d.conditionsError)throw Error(d.conditionsError)}catch(e){error.value=(e as Error).message}})
</script>
<template>
 <section class="night-conditions" aria-label="假設完整夜盤收盤價之條件研究">
  <p v-if="error">{{error}}；暫不顯示條件值。</p>
  <template v-else-if="data">
   <h3>若完整夜盤收在不同價位</h3><p class="warning">完整夜盤模型的假設試算，盤中準確率未驗證。未修正後續偏多漂移，不是目前可操作的勝率。</p>
   <p>{{data.note}}</p><p>條件來源 {{time(data.sourceSavedAt)}} · 報價 {{time(data.quoteAt)}} · 本表計算 {{time(data.computedAt)}}。沿用已存資料，並非此刻新報價。</p>
   <p>T+1 到期 {{data.targetDate}}，上漲指加權收盤高於／等於 {{data.anchorDate}} 的 {{n(data.anchorClose)}} 點，不是從假設夜盤價進場的獲利機率。</p>
   <div class="table-scroll"><table><thead><tr><th>假設完整夜盤收盤</th><th>條件來源</th><th>模型條件上漲值</th><th>模型條件下跌值</th></tr></thead><tbody><tr v-for="row in data.rows" :key="row.nightCloseAssumed"><td>若收在 {{n(row.nightCloseAssumed)}} 點</td><td>{{row.label}}</td><td>{{p(row.up)}}</td><td>{{p(row.down)}}</td></tr></tbody></table></div>
   <p>固定 TX {{data.contract}}，同合約日盤 {{n(data.dayClose)}} 點；假設夜盤高／低仍為 {{n(data.fixedHigh)}}／{{n(data.fixedLow)}} 點。這三種假設不是三種情境發生機率；尚未公布的消息不預填。</p>
   <h4>波動與區間檢查</h4>
   <p>原情境使用60日日報酬波動 {{n(data.dailySigma60Pct)}}%；來源另記錄20日日波動 {{data.volatility.std20_daily_pct==null?'—':n(Number(data.volatility.std20_daily_pct))}}%、EWMA {{data.volatility.ewma_daily_pct==null?'—':n(Number(data.volatility.ewma_daily_pct))}}%、ATR14／前收 {{data.volatility.atr14_pct==null?'—':n(Number(data.volatility.atr14_pct))}}%。報酬波動與日內振幅衡量不同，不能直接當成相同區間寬度。</p>
   <p class="warning">暫不提供盤中80%預測區間。原區間只做過完整夜盤歷史檢查，尚無此時段的涵蓋率證據；要先驗證部分夜盤與近期波動的區間方法，不能任意放大。舊區間僅留在下方原值核對。</p>
  </template><p v-else>尚無可核對的已存夜盤條件。</p>
 </section>
</template>
<style scoped>
.night-conditions{font-size:12px;line-height:1.9;color:#788477;padding:14px 0;min-width:0}.night-conditions h3,.night-conditions h4{color:#647462;font-size:15px}.warning{padding:12px;background:#f5f3ec;border-radius:8px;color:#827757}.table-scroll{overflow-x:auto;max-width:100%}table{border-collapse:collapse;width:100%;white-space:nowrap;color:#737f72}th,td{padding:12px;border-bottom:1px solid #e4e8df;text-align:left}th{background:#f5f7f1;font-size:11px}td{font-variant-numeric:tabular-nums}
</style>
