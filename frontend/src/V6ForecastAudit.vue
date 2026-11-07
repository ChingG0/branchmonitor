<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {localFetch} from './http'
import {vRememberDisclosure} from './rememberDisclosure'
import {validatePresentation} from './v6ForecastPresentation'
import type {ForecastAudit,Presentation} from './v6ForecastPresentation'
const props=defineProps<{payload?:Presentation}>(),data=ref<ForecastAudit|null>(props.payload?validatePresentation(props.payload).audit||null:null),error=ref(''),horizon=ref(1)
const rows=computed(()=>data.value?.rollingRows.filter(r=>r.horizon===horizon.value).slice().reverse()||[])
const p=(x:number)=> (100*x).toFixed(1)+'%',m=(x:number)=>x.toFixed(4),signed=(x:number)=>(x>0?'+':'')+x.toFixed(2)
const targets=[['M1_vs_previous_close','現貨收盤相對前收'],['night_sign_vs_previous_close','只取已知夜盤方向 → 現貨對前收'],['M1_vs_cash_gap','現貨開盤相對前收（跳空）'],['M1_vs_cash_open_to_close','現貨開盤到收盤（盤中）'],['M1_vs_same_contract_futures_regular_open_to_close','同合約期貨一般盤開盤到收盤（盤中）']] as const
onMounted(async()=>{if(props.payload)return;try{const r=await localFetch('/v6-research/forecast-presentation.json');if(!r.ok)throw Error('成績檢查尚未匯出');const d=validatePresentation(await r.json());data.value=d.audit||null;if(d.auditError)throw Error(d.auditError)}catch(e){error.value=(e as Error).message}})
</script>
<template>
 <details v-remember-disclosure="'v6.forecast.target-calibration-audit.v1'" class="forecast-audit"><summary>模型成績與校準檢查 · 對前收／對開盤分開看</summary>
  <p v-if="error">{{error}}</p><template v-else-if="data">
   <p class="warning">以下是已開啟歷史的重新分析，不是新的盲測或前瞻成績。原訊號的目標是前收到收盤；不能將其機率當成開盤後獲利機率。</p>
   <h3>相同 {{data.all.n}} 筆日期的方向核對</h3><div class="table-scroll"><table><thead><tr><th>比較端點</th><th>方向一致率</th><th>符合／比較筆數</th></tr></thead><tbody><tr v-for="[key,label] in targets" :key="key"><td>{{label}}</td><td>{{p(data.directions[key]!.fraction)}}</td><td>{{data.directions[key]!.matches}}／{{data.directions[key]!.n}}</td></tr></tbody></table></div>
   <p>開盤到收盤兩列只診斷同一訊號的方向一致程度，不是另訓練且校準過的盤中模型，也未扣成本與滑價；現貨指數無法直接成交。</p>
   <h3>偏多漂移與校準研究</h3><p>固定模型平均上漲預報 {{p(data.all.mean_p)}}，實際 {{p(data.all.actual_up)}}，偏多 {{signed((data.all.mean_p-data.all.actual_up)*100)}} 個百分點。全期 Brier {{m(data.all.brier)}}；最近60筆 {{m(data.last60.brier)}}。</p>
   <p>原夜盤模型已有訓練後的固定 sigmoid 校準。以下候選只以當時已成熟答案滾動調整截距，並非從未校準；T+1改善不足既定1%門檻，尚未採用，不直接將全期偏差扣到今天。</p>
   <div class="table-scroll"><table><thead><tr><th>期限／共同筆數</th><th>固定／滾動平均上漲值</th><th>實際上漲</th><th>Brier 固定／滾動</th><th>log loss 固定／滾動</th><th>Brier降低</th></tr></thead><tbody><tr v-for="r in data.rollingComparison" :key="r.horizon"><td>T+{{r.horizon}}／{{r.rolling_common_dates.fixed.n}}</td><td>{{p(r.rolling_common_dates.fixed.mean_forecast)}}／{{p(r.rolling_common_dates.rolling.mean_forecast)}}</td><td>{{p(r.rolling_common_dates.fixed.actual_up)}}</td><td>{{m(r.rolling_common_dates.fixed.brier)}}／{{m(r.rolling_common_dates.rolling.brier)}}</td><td>{{m(r.rolling_common_dates.fixed.log_loss)}}／{{m(r.rolling_common_dates.rolling.log_loss)}}</td><td>{{signed(-100*r.rolling_common_dates.relative_brier_change)}}% · 未採用</td></tr></tbody></table></div>
   <details class="rolling-values"><summary>查看逐日原值與滾動校準候選值（研究）</summary><label>期限 <select v-model.number="horizon"><option v-for="h in [1,2,3]" :key="h" :value="h">T+{{h}}</option></select></label><div class="table-scroll rolling-table"><table><thead><tr><th>研究預報日</th><th>固定校準值</th><th>滾動候選值</th><th>修正</th><th>實際</th><th>最近使用答案可得時間</th></tr></thead><tbody><tr v-for="r in rows" :key="r.date"><td>{{r.date}}</td><td>{{p(r.fixed)}}</td><td>{{p(r.rolling)}}</td><td>{{signed(100*(r.rolling-r.fixed))}} 百分點</td><td>{{r.up?'上漲':'下跌'}}</td><td>{{r.latestTrainingOutcome.slice(0,16).replace('T',' ')}}</td></tr></tbody></table></div></details>
   <h3>50% 的處理</h3><p>原始機率恰好50/50不判方向，不列方向命中率，但保留Brier。本次原始恰50%為 {{data.exact50N}} 筆；顯示50.0%有 {{data.rounded50.length}} 筆，是四捨五入，不能事後當成無方向刪除錯誤紀錄。</p><ul><li v-for="r in data.rounded50" :key="r.date">{{r.date}} 原始上漲 {{(r.p*100).toFixed(4)}}% · 接近無方向；原始判讀看漲，實際{{r.y?'上漲':'下跌'}}。</li></ul>
  </template><p v-else>尚無可核對的成績檢查。</p>
 </details>
</template>
<style scoped>
.forecast-audit{border-top:1px solid #e1e8dc;margin-top:20px;padding-top:16px;color:#7a8974;font-size:12px;line-height:1.9;min-width:0}.forecast-audit summary{cursor:pointer;font-weight:600}.forecast-audit h3{font-size:15px;color:#536f58;margin-top:20px}.warning{padding:12px;background:#f7f4e9;border-radius:8px;color:#897b55}.table-scroll{overflow:auto;max-width:100%;min-width:0}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:11px}td,th{text-align:left;padding:11px;border-bottom:1px solid #e5ebdf}th{background:#f6f8f2}.rolling-values{margin-top:18px}.rolling-values label{display:block;margin:14px 0}.rolling-table{max-height:340px}.rolling-table th{position:sticky;top:0}select{border:1px solid #dbe4d5;border-radius:6px;padding:5px;background:white;color:inherit}
</style>
