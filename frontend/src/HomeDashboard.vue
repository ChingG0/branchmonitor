<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import Icon from './Icon.vue'

type Stage={label:string;done:number;total:number|null}
type Detail={planned?:boolean;phase_label?:string;eta_seconds?:number|null;rate_per_min?:number|null;stages?:Record<string,Stage>}
type ModuleRow={name:string;status:'queued'|'running'|'complete'|'partial'|'failed';message:string;detail?:Detail}
type UpdateJob={status:string;message:string;started_at:string|null;finished_at:string|null;done:number;total:number;modules:ModuleRow[];schedule?:{enabled:boolean;time:string;last_attempt?:string}}
type RepairReport={job:null|{status:string;done:number;total:number;message:string};counts:Record<string,number>;retry:{due:number;waiting:number;exhausted:number;interrupted:number}}
const job=ref<UpdateJob|null>(null),repair=ref<RepairReport|null>(null),error=ref(''),starting=ref(false),retrying=ref<string|null>(null),repairing=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,repairTimer:ReturnType<typeof setTimeout>|undefined,alive=true
const moduleRunning=computed(()=>starting.value||retrying.value!==null||job.value?.status==='running')
const running=computed(()=>moduleRunning.value||repairing.value)
const progress=computed(()=>job.value?.total?Math.round(job.value.done/job.value.total*100):0)
const repairWaiting=computed(()=>Number(repair.value?.counts.queued||0)+Number(repair.value?.counts.running||0)+Number(repair.value?.counts.failed||0)+Number(repair.value?.counts.interrupted||0))
const labels:Record<string,string>={queued:'等待',running:'更新中',complete:'完成',partial:'部分完成',failed:'失敗',idle:'尚未更新'}
const stamp=(value:string|null|undefined)=>value?value.replace('T',' ').slice(0,16):'—'
const fmt=(value:number|null|undefined)=>Number(value||0).toLocaleString('zh-TW')
function moduleDetail(row:ModuleRow){
 const detail=row.detail;if(!detail?.stages)return ''
 const stages=Object.values(detail.stages).map(stage=>`${stage.label} ${fmt(stage.done)}/${stage.total===null?'盤點中':fmt(stage.total)}`).join(' · ')
 const eta=row.status==='running'&&detail.planned&&Number(detail.eta_seconds)>0?` · 剩餘約 ${Math.ceil(Number(detail.eta_seconds)/60)} 分鐘`:''
 return stages+eta
}
async function load(){clearTimeout(timer);try{const response=await localFetch('/api/update-all');const data=await readJson<UpdateJob>(response);if(!response.ok)throw new Error((data as any).error||'無法讀取更新狀態');if(!alive)return;job.value=data;error.value='';timer=setTimeout(load,data.status==='running'?1500:15000);if(data.status!=='running')retrying.value=null}catch(e){if(alive){error.value=(e as Error).message;retrying.value=null}}}
async function loadRepair(){
 clearTimeout(repairTimer)
 try{
  const response=await localFetch('/api/history-repair?limit=0');const data=await readJson<RepairReport&{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法讀取歷史缺口狀態')
  if(!alive)return
  repair.value=data
  if(data.job?.status==='running'){repairing.value=true;repairTimer=setTimeout(loadRepair,1500)}
  else repairing.value=false
 }catch(e){if(alive){error.value=(e as Error).message;repairing.value=false}}
}
async function updateAll(){starting.value=true;error.value='';try{const response=await localFetch('/api/update-all',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await readJson<UpdateJob>(response);if(!response.ok)throw new Error((data as any).error||'無法開始全部更新');job.value=data;await load()}catch(e){error.value=(e as Error).message}finally{starting.value=false}}
async function retryModule(name:string){
 if(moduleRunning.value||(repairing.value&&name==='追蹤分點'))return
 retrying.value=name;error.value='';clearTimeout(timer)
 try{
  const response=await localFetch('/api/update-module',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})
  const data=await readJson<UpdateJob>(response)
  if(!response.ok)throw new Error((data as any).error||`無法更新${name}`)
  job.value=data
  timer=setTimeout(load,500)
 }catch(e){error.value=(e as Error).message;retrying.value=null}
}
async function repairHistory(){
 if(moduleRunning.value||repairing.value)return
 repairing.value=true;error.value='';clearTimeout(repairTimer)
 try{
  const options={method:'POST',headers:{'Content-Type':'application/json'}}
  const planned=await localFetch('/api/history-repair/plan',{...options,body:JSON.stringify({through_date:null,refresh_audit:true})})
  const planData=await readJson<RepairReport&{error?:string}>(planned)
  if(!planned.ok)throw new Error(planData.error||'無法更新歷史缺口清單')
  repair.value=planData
  const pending=Math.max(1,Number(planData.counts.queued||0)+Number(planData.counts.failed||0)+Number(planData.counts.interrupted||0))
  const retried=await localFetch('/api/history-repair/retry',{...options,body:JSON.stringify({limit:pending,force:true})})
  const retryData=await readJson<RepairReport&{error?:string}>(retried)
  if(!retried.ok)throw new Error(retryData.error||'無法重新排入失敗的歷史缺口')
  repair.value=retryData
  const total=Math.max(1,Number(retryData.counts.queued||0)+Number(retryData.counts.interrupted||0))
  const started=await localFetch('/api/history-repair/run',{...options,body:JSON.stringify({limit:total,resume:true})})
  const runData=await readJson<RepairReport&{error?:string}>(started)
  if(!started.ok)throw new Error(runData.error||'無法開始歷史缺口回補')
  repair.value=runData
  if(runData.job?.status==='running')repairTimer=setTimeout(loadRepair,700);else repairing.value=false
 }catch(e){error.value=(e as Error).message;repairing.value=false}
}
onMounted(()=>{void load();void loadRepair()});onUnmounted(()=>{alive=false;clearTimeout(timer);clearTimeout(repairTimer)})
</script>

<template>
 <section class="home-page">
  <header class="home-hero panel"><div><div class="eyebrow">DAILY CONTROL CENTER</div><h1>今日市場資料中心</h1><p>更新所有一般資料分頁，含融資維持率與月份統計；V3／V6／V10／V11維持在對話中手動更新。</p></div><button class="primary update-all" :disabled="running" @click="updateAll"><Icon name="refresh" :size="20" :class="{spinning:running}"/>{{running?'全部資料更新中':'更新全部資料'}}</button></header>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="load">重新讀取</button></div>
  <p class="muted">{{job?.schedule?.enabled?`自動更新：週一至週五 ${job.schedule.time}（台北時間）`:'自動更新已關閉，可按「更新全部資料」'}} · 每天自動執行一次，來源未齊請手動更新。需程式開啟、電腦喚醒與網路可用。</p>
  <p v-if="!running&&(job?.status==='partial'||job?.status==='failed')" class="manual-update-notice panel" role="status">部分來源尚未齊全，本次不會自動重試。請在資料公布後，按「補齊此區」或「更新全部資料」手動更新。</p>
  <section class="home-status panel"><header><div><span>更新進度</span><h2>{{job?.message||'正在讀取本機資料狀態'}}</h2></div><strong>{{progress}}%</strong></header><div class="home-track"><i :style="{width:progress+'%'}"></i></div><div class="home-meta"><span>開始 {{stamp(job?.started_at)}}</span><span>完成 {{stamp(job?.finished_at)}}</span><span>{{job?.done||0}} / {{job?.total||18}} 項</span></div></section>
  <div class="module-grid"><article v-for="row in job?.modules||[]" :key="row.name" class="module-card panel" :class="row.status"><div class="module-icon"><Icon :name="row.status==='complete'?'check':row.status==='running'?'refresh':'database'" :size="18" :class="{spinning:row.status==='running'}"/></div><div><h3>{{row.name}}</h3><p>{{row.message}}</p><small v-if="row.detail&&moduleDetail(row)">{{moduleDetail(row)}}</small><small v-if="row.name==='追蹤分點'&&repair">歷史缺口等待 {{fmt(repairWaiting)}} 組 · 已完成 {{fmt(repair.counts.complete)}} 組<span v-if="repairing"> · {{repair.job?.message||'正在建立缺口清單'}}</span></small></div><div class="module-actions"><span>{{labels[row.status]||row.status}}</span><button v-if="row.status==='partial'||row.status==='failed'" class="module-retry" :disabled="moduleRunning||(repairing&&row.name==='追蹤分點')" :aria-label="`補齊${row.name}`" @click="retryModule(row.name)"><Icon name="refresh" :size="13"/>補齊此區</button><button v-if="row.name==='追蹤分點'&&repairWaiting>0" class="module-retry repair-history" :disabled="moduleRunning||repairing" aria-label="補齊全部分點歷史缺口" @click="repairHistory"><Icon name="database" :size="13"/>{{repairing?'全部缺口回補中':'補齊全部缺口'}}</button><span v-else-if="retrying===row.name" class="module-retrying">此區更新中</span></div></article></div>
 </section>
</template>

<style scoped>
.manual-update-notice{margin:0;padding:16px 20px;background:#faf3df;color:#84652e;font-size:.82rem;line-height:1.7}.home-page{display:grid;gap:22px}.home-hero{padding:38px 40px;display:flex;align-items:center;justify-content:space-between;gap:24px;background:linear-gradient(125deg,#fff 0%,#f4f8ee 100%);border-top:3px solid #799462}.home-hero h1{font-size:2rem;font-weight:520;color:#34513b;margin:10px 0}.home-hero p{color:#7f8f76;font-size:.86rem;margin:0}.update-all{padding:14px 22px;white-space:nowrap;display:flex;align-items:center;gap:9px}.home-status{padding:24px 28px}.home-status header{display:flex;align-items:center;justify-content:space-between;gap:18px}.home-status header span{font-size:.68rem;letter-spacing:.12em;color:#8b9b7e}.home-status h2{font-size:.95rem;font-weight:500;margin:7px 0}.home-status strong{font-size:1.6rem;font-weight:500;color:#5e774b}.home-track{height:7px;background:#edf1e9;border-radius:10px;overflow:hidden;margin:16px 0 11px}.home-track i{display:block;height:100%;background:#799462;transition:width .35s}.home-meta{display:flex;gap:24px;color:#8b9782;font-size:.7rem}.module-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.module-card{padding:17px 18px;display:grid;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:12px}.module-icon{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:#eef3e9;color:#6f875c}.module-card h3{font-size:.82rem;margin:0 0 6px;font-weight:550}.module-card p{font-size:.7rem;color:#87927e;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.module-card small{display:block;margin-top:6px;font-size:.63rem;color:#93a18d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.module-actions{justify-self:end;display:flex;align-items:center;gap:9px;white-space:nowrap;flex-wrap:wrap;justify-content:flex-end}.module-actions>span{font-size:.66rem;color:#8a977e}.module-retry{display:flex;align-items:center;gap:5px;padding:7px 10px;border:1px solid #d8e2cf;border-radius:9px;background:#f8faf5;color:#5f794b;font-size:.65rem}.module-retry:hover:not(:disabled){background:#eef4e8;border-color:#c8d8bc}.module-retry:disabled{opacity:.45;cursor:not-allowed}.repair-history{background:#f6f1e5;border-color:#e5d8ba;color:#8a6a2f}.module-retrying{color:#638047!important}.module-card.failed .module-icon{background:#faebe8;color:#b75e54}.module-card.partial .module-icon{background:#faf3df;color:#a98543}.module-card.running .module-icon{background:#edf2e3;color:#638047}@media(max-width:800px){.home-hero{align-items:flex-start;flex-direction:column}.module-grid{grid-template-columns:1fr}.module-card{grid-template-columns:36px minmax(0,1fr)}.module-actions{grid-column:2;justify-self:start;justify-content:flex-start}.home-meta{flex-wrap:wrap}.update-all{width:100%;justify-content:center}}
</style>
