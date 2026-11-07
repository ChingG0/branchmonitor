<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import {vRememberDisclosure} from './rememberDisclosure'
import {validateEvening} from './v6EveningForecast'
import type {EveningReport,EquityAllocation} from './v6EveningForecast'
const props=defineProps<{report?:EveningReport;study?:any}>()
const report=ref<EveningReport|null>(props.report?validateEvening(props.report):null),study=ref<any>(props.study||null),error=ref('')
const saved=computed(()=>report.value?.latest?.allocation)
const current=computed<EquityAllocation|undefined>(()=>saved.value||study.value?.latestPreview)
const preview=computed(()=>!saved.value)
const f=(v:number|undefined,d=1)=>v==null?'—':v.toLocaleString('zh-TW',{maximumFractionDigits:d})
const pct=(v:number|undefined)=>f(v)+'%'
const signed=(v:number)=>`${v>0?'+':''}${f(v,2)}%`
const stamp=(v?:string)=>v?.slice(0,19).replace('T',' ')||'未保存'
const periods=computed(()=>Object.entries(study.value?.periods||{}).map(([key,value])=>({key,label:({all:'2000年起',2020:'2020年',2022:'2022年',recent:'近期10/1起'} as Record<string,string>)[key]||key,value:value as any})))
const path=computed(()=>{
 const actual=(report.value?.history||[]).filter(f=>f.allocation).map(f=>({date:f.anchorDate,close:f.anchorClose,equityPct:f.allocation!.equityPct,savedAt:f.savedAt}))
 const by=new Map(actual.map(r=>[r.date,r]))
 const rows=by.size?[...by.values()]:((study.value?.recentDecisions||[]) as EquityAllocation[]).map(r=>({date:r.anchorDate,close:r.anchorClose,equityPct:r.equityPct,savedAt:''}))
 if(!rows.length)return null
 const lo=Math.min(...rows.map(r=>r.close))*.99,hi=Math.max(...rows.map(r=>r.close))*1.01
 const chart=rows.slice(-60).map((r,i,all)=>({...r,x:60+i/Math.max(1,all.length-1)*760,y:30+(hi-r.close)/(hi-lo)*90,wy:265-r.equityPct/100*100}))
 return {rows:chart,price:chart.map(r=>`${r.x},${r.y}`).join(' '),weight:chart.map(r=>`${r.x},${r.wy}`).join(' '),actual:by.size>0}
})
onMounted(async()=>{
 const results=await Promise.allSettled([
  props.report?Promise.resolve(props.report):localFetch('/v6-research/evening-forecast.json').then(readJson<EveningReport>).then(validateEvening),
  props.study?Promise.resolve(props.study):localFetch('/api/v6-allocation-study').then(readJson<any>),
 ])
 if(results[0].status==='fulfilled')report.value=results[0].value
 if(results[1].status==='fulfilled')study.value=results[1].value
 if(results.some(r=>r.status==='rejected'))error.value='部分已保存資料未讀取成功，未產生新配置。'
})
</script>

<template>
 <section class="allocation-panel panel" aria-label="V6股票與現金配置">
  <header><div><small>V6 · EQUITY EXPOSURE LIMIT</small><h2>股票配置觀察與回撤診斷</h2><p>配置隨對話中的V6機率更新一起保存。減碼立即反映；加碼每交易日最多5個百分點，同日重複更新不增加額度。</p></div><span>{{preview?'規則預覽':'與機率同次保存'}}</span></header>
  <p v-if="error" class="notice">{{error}}</p>
  <template v-if="current">
   <p class="notice">此配置規則尚未通過優於舊版的驗證。近期回撤未明顯改善，長期報酬受交易成本與保守配置拖累；請勿把上限當成已驗證的操作策略。</p>
   <p v-if="preview" class="notice">下方是 {{current.anchorDate}} 收盤資料的規則預覽，尚未與新機率同步保存，並非當時已發布的配置。下次在對話更新V6時，會和三個期限的機率一起保存。</p>
   <p v-else class="basis">{{current.anchorDate}} 加權指數 · 資訊截點 {{stamp(current.informationCutoff)}} · 保存 {{stamp(current.savedAt)}}</p>
   <div class="allocation-values"><div><span>股票觀察上限</span><strong>{{pct(current.equityPct)}}</strong></div><div><span>現金至少保留</span><strong>{{pct(current.cashPct)}}</strong></div><div><span>{{preview?'診斷狀態':'較前次上限'}}</span><strong>{{preview?'價格防護層':current.changePp==null?'首次保存':`${current.changePp>0?'+':''}${f(current.changePp)} 個百分點`}}</strong></div></div>
   <div class="weight-bar" role="img" :aria-label="`股票上限${current.equityPct}%，現金至少${current.cashPct}%`"><i :style="{width:current.equityPct+'%'}"></i></div>
   <div class="drivers"><b>{{preview?'目前觸發的限制':current.action}}</b><span v-for="(r,i) in current.binding" :key="r.key+i">{{r.label}}：上限 {{pct(r.pct)}}</span></div>
   <p class="basis">年化波動 {{pct(current.inputs.annualVolPct)}} · 距近一年高點 {{signed(current.inputs.drawdownPct)}} · 距20MA {{f(current.inputs.stretchAtr,2)}} 倍ATR</p>
   <p class="basis">{{current.note}} {{current.probabilityUsed?'本次已納入同一份三期限機率與q10情境，只作減碼約束。':'此預覽只含價格防護，未把過期機率或歷史比例當作新方向。'}}</p>
  </template>
  <p v-else class="notice">尚無保存的配置或研究預覽；請在對話更新V6。</p>
  <div v-if="path" class="chart-wrap"><h3>{{path.actual?'實際保存的配置上限':'價格防護規則的歷史回放（非當時發布）'}}</h3><div class="chart-scroll"><svg viewBox="0 0 880 305" role="img" aria-label="加權指數與股票配置上限走勢"><text x="10" y="20">加權指數</text><polyline :points="path.price" fill="none" stroke="#8a9b9e" stroke-width="2"/><line x1="60" x2="820" y1="165" y2="165" stroke="#e2e8dc"/><line x1="60" x2="820" y1="265" y2="265" stroke="#e2e8dc"/><text x="5" y="170">100%</text><text x="16" y="268">0%</text><text x="5" y="148">股票上限</text><polyline :points="path.weight" fill="none" stroke="#537e69" stroke-width="2.5" :stroke-dasharray="path.actual?undefined:'5 3'"/><g v-for="(r,i) in path.rows" :key="r.date"><circle :cx="r.x" :cy="r.wy" r="3" fill="#537e69"><title>{{r.date}} · 指數 {{f(r.close)}} · 股票上限 {{pct(r.equityPct)}}{{r.savedAt?' · 保存 '+stamp(r.savedAt):' · 研究回放'}}</title></circle><text v-if="i===0||i===path.rows.length-1||i%10===0" :x="r.x" y="292" text-anchor="middle">{{r.date.slice(5)}}</text></g></svg></div></div>
  <details v-if="study" v-remember-disclosure="'v6-allocation-diagnostics'" class="allocation-details"><summary>風控規則、歷史回撤與報酬對照</summary><p>{{study.note}}</p><div class="table-scroll"><table><thead><tr><th>期間</th><th>新價格防護：報酬／最大回撤</th><th>固定60%：報酬／最大回撤</th><th>舊版實際保存：報酬／最大回撤</th></tr></thead><tbody><tr v-for="p in periods" :key="p.key"><td>{{p.label}}</td><td>{{signed(p.value.protective.returnPct)}} ／ {{signed(p.value.protective.maxDrawdownPct)}}</td><td>{{signed(p.value.fixed60.returnPct)}} ／ {{signed(p.value.fixed60.maxDrawdownPct)}}</td><td>{{p.value.archived?signed(p.value.archived.returnPct)+' ／ '+signed(p.value.archived.maxDrawdownPct):'無同期保存紀錄'}}</td></tr></tbody></table></div><p>波動增加時減少曝險參考 <a href="https://www.nber.org/papers/w22208" target="_blank" rel="noopener">Moreira／Muir的研究</a>；本頁門檻是固定觀察規則，並非該論文在台股驗證的最佳參數。不能保證避開高點或將最大回撤鎖在某個百分比。</p><p>方法 {{study.version}} · 規則指紋 {{study.policySha256?.slice(0,12)}} · 歷史機率覆蓋尚不足，沒有用現在的機率回填過去績效。</p></details>
 </section>
</template>

<style scoped>
.allocation-panel{padding:26px;min-width:0}.allocation-panel header{display:flex;justify-content:space-between;align-items:start;gap:18px}.allocation-panel header small{font-size:10px;letter-spacing:.13em;color:#879779}.allocation-panel h2{font-size:21px;margin:9px 0;color:#355641}.allocation-panel header p,.basis,.allocation-details p{font-size:12px;line-height:1.8;color:#7e8e78}.allocation-panel header>span{white-space:nowrap;font-size:11px;padding:8px 12px;background:#f0f5ec;border-radius:18px;color:#4c715c}.notice{padding:12px 15px;background:#faf3df;border-radius:9px;color:#8a703b;font-size:12px;line-height:1.8}.allocation-values{display:grid;grid-template-columns:1fr 1fr 1.2fr;gap:18px;margin:24px 0 14px}.allocation-values span{display:block;font-size:12px;color:#839278}.allocation-values strong{display:block;margin-top:10px;font-size:27px;color:#3b6650;font-weight:550}.allocation-values div:last-child strong{font-size:20px}.weight-bar{height:12px;background:#e7ede2;border-radius:8px;overflow:hidden}.weight-bar i{display:block;height:100%;background:#527e67}.drivers{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:12px;margin-top:20px;color:#4b6754}.drivers span{padding:7px 10px;border-radius:7px;background:#f7f2e6;color:#8c723a}.chart-wrap{border-top:1px solid #e5eadf;margin-top:20px;padding-top:14px}.chart-wrap h3,.allocation-details summary{font-size:13px;color:#53715b}.chart-scroll,.table-scroll{overflow-x:auto;max-width:100%}.chart-scroll svg{min-width:720px;width:100%;display:block}.chart-scroll text{font-size:10px;fill:#89977e}.allocation-details{margin-top:16px;padding-top:17px;border-top:1px solid #e5eadf}.allocation-details summary{cursor:pointer;font-weight:600}.allocation-details table{width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap}.allocation-details th,.allocation-details td{padding:12px;border-bottom:1px solid #e5eadf;text-align:left}.allocation-details a{color:#55775e}@media(max-width:650px){.allocation-panel{padding:17px}.allocation-values{grid-template-columns:1fr 1fr}.allocation-values>div:last-child{grid-column:1/-1}.allocation-panel header{flex-wrap:wrap}}
</style>
