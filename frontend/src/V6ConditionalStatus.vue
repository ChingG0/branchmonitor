<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {vRememberDisclosure} from './rememberDisclosure'
import {localFetch} from './http'
type Metric={n:number;brier:number|null;log_loss:number|null}
type Horizon={horizon:number;matured:number;up:number;down:number;elapsedSessions:number;eligible:boolean;active:boolean;newSinceSuspension:number|null;gates:Record<string,boolean>;metrics:Record<string,Metric>}
type Status={status:string;startedAt?:string;updatedAt?:string;withheld:boolean;note?:string;active:boolean;horizons:Horizon[];lastForecastSavedAt?:string;lastForecast?:{decision_date:string;targets:string[];currentTargets?:string[];models:Record<string,Record<string,{up_percent:number;down_percent:number}>>}}
const props=defineProps<{payload?:Status}>()
const data=ref<Status|null>(props.payload||null),error=ref(''),selectedHorizon=ref(1)
const minimumMatured=computed(()=>data.value?.horizons.length?Math.min(...data.value.horizons.map(h=>h.matured)):0)
const answer=computed(()=>data.value?.lastForecast&&!data.value.withheld?data.value.lastForecast.models[String(selectedHorizon.value)]?.M1:null)
const gates=[['elapsed60','經過60交易日'],['matured60','成熟60筆'],['eachClass20','正反例各20筆'],['brierAndLogLoss','Brier改善≥1%且log loss不劣']] as const
const time=(x?:string)=>x?.slice(0,16).replace('T',' ')||'尚無紀錄'
const metric=(x:number|null|undefined)=>x==null?'—':x.toFixed(4)
onMounted(async()=>{if(props.payload)return;try{
 const r=await localFetch('/v6-research/prospective-status.json');if(!r.ok)throw Error('前瞻狀態尚未部署')
 const v=await r.json();if(v.version!=='v6-conditional-prospective-1.0.0'||!Array.isArray(v.horizons))throw Error('前瞻狀態版本不符')
 data.value=v
}catch(e){error.value=(e as Error).message}})
</script>
<template>
 <section class="prospective-status" aria-label="條件預報前瞻觀察">
  <header><div><small>V6 · PROSPECTIVE VALIDATION</small><h2>條件預報 · 前瞻觀察</h2></div><b class="mode">{{data?.active?'依期限啟用':'觀察中 '+minimumMatured+'/60'}}</b></header>
  <p v-if="error" class="notice">{{error}}；暫停顯示條件機率，原有已存預報仍可查閱。</p>
  <template v-else-if="data">
   <p class="basis">觀察起跑 {{time(data.startedAt)}} · 最近對話更新 {{time(data.updatedAt)}}<br>僅08:30～08:44:59實際保存的當日預報列入；歷史回測不算入60筆。</p>
   <p v-if="data.withheld" class="notice">{{data.note}}。本次不顯示新的條件機率。</p>
   <div class="current-answer"><div class="answer-periods" role="group" aria-label="條件預報期限"><button v-for="h in [1,2,3]" :key="h" :aria-pressed="selectedHorizon===h" @click="selectedHorizon=h">T+{{h}}</button><span>夜盤模型 · {{data.active?'依期限顯示資格':'平行觀察，未取代正式預報'}}</span></div><div class="answer-numbers"><div><span>上漲</span><strong>{{answer?answer.up_percent:'—'}}<small v-if="answer">%</small></strong></div><div><span>下跌</span><strong>{{answer?answer.down_percent:'—'}}<small v-if="answer">%</small></strong></div></div><p v-if="answer">保存 {{time(data.lastForecastSavedAt)}} · 到期 {{(data.lastForecast!.currentTargets||data.lastForecast!.targets)[selectedHorizon-1]}}</p><p v-else>尚無本次合格條件預報；不以歷史範例代替。</p></div>
   <details v-remember-disclosure="'v6.conditional.progress.v2'" class="progress-details"><summary>觀察進度、評分與啟用條件</summary>
   <div class="horizons"><article v-for="h in data.horizons" :key="h.horizon">
    <div class="headline"><b>T+{{h.horizon}}</b><span>{{h.active?'M1已啟用':h.eligible?'符合門檻 · 等待對話啟用':'觀察中'}}</span></div>
    <p class="count"><strong>{{h.matured}}</strong> / 60 <small>成熟預報</small></p>
    <progress :value="Math.min(h.matured,60)" max="60" :aria-label="`T+${h.horizon}成熟進度`"/>
    <p>經過 {{h.elapsedSessions}} 交易日 · 上漲 {{h.up}}／下跌 {{h.down}} 筆</p>
    <p v-if="h.newSinceSuspension!=null">停用後新成熟 {{h.newSinceSuspension}} / 60 筆</p>
    <ul><li v-for="[key,label] in gates" :key="key" :class="{pass:h.gates[key]}"><span>{{h.gates[key]?'✓':'○'}}</span> {{label}}</li></ul>
    <div class="scores"><span>Brier：M0 {{metric(h.metrics.M0?.brier)}}／M1 {{metric(h.metrics.M1?.brier)}}</span><span>log loss：M0 {{metric(h.metrics.M0?.log_loss)}}／M1 {{metric(h.metrics.M1?.log_loss)}}</span></div>
   </article></div>
   <div v-if="data.lastForecast" class="last"><b>{{data.withheld?'上次已存紀錄（非本次預報）':'最近合格紀錄'}} · {{time(data.lastForecastSavedAt)}}</b><div class="last-grid"><p v-for="h in data.horizons" :key="h.horizon"><span>T+{{h.horizon}} · {{(data.lastForecast.currentTargets||data.lastForecast.targets)[h.horizon-1]}}</span><strong>M1 上漲 {{data.lastForecast.models[String(h.horizon)]?.M1?.up_percent}}%／下跌 {{data.lastForecast.models[String(h.horizon)]?.M1?.down_percent}}%</strong></p></div></div>
   </details>
   <p class="rules">夜盤模型平行觀察中；加入海外因子與修訂模型仍屬研究。啟用後若最近60筆Brier輸給同日期M0，對話更新時退回基準；恢復前需再累積60筆新成熟合格紀錄。來源或完整性失敗即停止新條件機率。</p>
   <p class="rules">此區是最近一次手動保存的狀態；重新整理只讀取紀錄，不補預報、不評答案，也不改交易部位。</p>
  </template>
 </section>
</template>
<style scoped>
.prospective-status{background:#fff;border:1px solid #dce6dc;border-radius:15px;margin:20px 0;padding:22px;color:#355348}.prospective-status header{display:flex;justify-content:space-between;gap:14px;align-items:center}.prospective-status h2{font-size:21px;margin:6px 0}.prospective-status header small{font-size:10px;letter-spacing:.1em;color:#7b8b73}.mode{font-size:12px;background:#edf3eb;padding:8px 12px;border-radius:20px}.basis,.rules{font-size:12px;line-height:1.8;color:#7d8c78}.notice{padding:12px;border-radius:8px;background:#fbf5e5;color:#967725;font-size:13px;line-height:1.7}.horizons{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.horizons article{min-width:0;background:#fbfcf9;border:1px solid #e1e8dc;border-radius:10px;padding:16px}.headline{display:flex;justify-content:space-between;gap:8px}.headline span{font-size:12px;color:#84917e}.count{font-size:14px}.count strong{font-size:32px;color:#426e60}.count small{margin-left:8px}progress{width:100%;height:7px;accent-color:#557f69}.horizons article>p{color:#7e8c78;font-size:12px}.horizons ul{list-style:none;padding:0;margin:13px 0;font-size:12px;line-height:2;color:#959987}.horizons .pass{color:#348064}.scores{border-top:1px solid #e7ece3;padding-top:10px;font-size:11px;line-height:1.8;overflow-wrap:anywhere}.scores span{display:block}.last{margin-top:15px;padding:15px;background:#f3f7f1;font-size:12px}.last-grid{display:flex;gap:20px;flex-wrap:wrap}.last-grid span,.last-grid strong{display:block;line-height:1.9}@media(max-width:720px){.prospective-status{padding:16px}.prospective-status header{align-items:start;flex-direction:column}.horizons{grid-template-columns:1fr}.last-grid{display:block}}
</style>

<style scoped>
.prospective-status{container-type:inline-size;max-width:100%;min-width:0}.prospective-status header{flex-wrap:wrap}.prospective-status header>div{min-width:0}.current-answer{padding:18px 0 8px}.answer-periods{display:flex;gap:7px;flex-wrap:wrap;align-items:center}.answer-periods button{border:1px solid #dce5d7;border-radius:7px;padding:7px 12px;font-size:12px}.answer-periods button[aria-pressed=true]{background:#4d735e;color:#fff}.answer-periods>span{font-size:11px;color:#84917b;margin-left:10px}.answer-numbers{display:flex;gap:70px;padding:19px 0 10px}.answer-numbers>div>span{display:block;font-size:12px;color:#8c9785}.answer-numbers strong{font-size:44px;font-weight:550;color:#bf6c5d}.answer-numbers>div+div strong{color:#508b76}.answer-numbers strong small{font-size:18px;margin-left:3px}.current-answer>p{font-size:12px;color:#8b9782}.progress-details{padding-top:8px;border-top:1px solid #e5ebdf}.progress-details>summary{padding:12px 0;font-size:12px;color:#778a70;cursor:pointer}.progress-details .horizons{padding-top:10px}@container(max-width:700px){.horizons{grid-template-columns:minmax(0,1fr)}.answer-periods>span{width:100%;margin:4px 0 0}.answer-numbers{gap:50px}.prospective-status header{flex-direction:column;align-items:start}}
</style>
