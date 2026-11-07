<script setup lang="ts">
import {vRememberDisclosure} from './rememberDisclosure'
import V6EveningForecast from './V6EveningForecast.vue'
import V6Allocation from './V6Allocation.vue'
import V6ConditionalStatus from './V6ConditionalStatus.vue'
import V6ProbabilityHistory from './V6ProbabilityHistory.vue'
import V6ConditionalResearch from './V6ConditionalResearch.vue'
const emit=defineEmits<{weather:[]}>()
</script>

<template>
 <div class="three-day-page">
  <header class="three-day-heading"><div><small>V6 · THREE-DAY FORECAST</small><h1>3日模型預測</h1><p>T+1／T+2／T+3的漲跌機率、預估收盤點數、修訂走勢與到期核對集中在這裡。T為預報使用的現貨收盤日，後續依台股交易日計算。</p><p>仍由你在對話要求「更新V6」時一併更新；開啟分頁只讀取已保存資料。</p></div><button @click="emit('weather')">查看股災氣象球</button></header>
  <V6EveningForecast/>
  <V6Allocation/>
  <details v-remember-disclosure="'v6.legacy-0830.archive.v1'" class="forecast-archive"><summary>舊08:30制度、盤後研究與歷史紀錄（已停止更新）</summary><V6ConditionalStatus/><V6ProbabilityHistory/><details v-remember-disclosure="'v6.three-day.conditional-research'" class="forecast-archive"><summary>條件預報研究與歷史驗證</summary><V6ConditionalResearch/></details></details>
 </div>
</template>

<style scoped>
.three-day-page{display:grid;gap:20px;min-width:0;color:#35523f}.three-day-heading{display:flex;align-items:start;justify-content:space-between;gap:25px}.three-day-heading small{font-size:11px;letter-spacing:.14em;color:#819575}.three-day-heading h1{font-size:30px;margin:10px 0 14px}.three-day-heading p{font-size:13px;color:#809077;line-height:1.9;margin:5px 0;max-width:850px}.three-day-heading button{border:1px solid #dce5d5;background:white;border-radius:9px;padding:11px 15px;white-space:nowrap;color:#53724f;font-size:12px;cursor:pointer}.forecast-archive{min-width:0;border-top:1px solid #e1e8db;padding-top:15px;font-size:12px;color:#849177}.forecast-archive>summary{cursor:pointer;line-height:1.8;padding:7px 0}@media(max-width:650px){.three-day-heading{flex-direction:column;gap:10px}.three-day-heading h1{font-size:26px}}
</style>
