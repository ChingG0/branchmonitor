<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import Icon from './Icon.vue'
import {localFetch,readJson} from './http'

type RepairStatus='queued'|'running'|'complete'|'failed'|'interrupted'
type RepairTier='R0_OPEN'|'R1_LATEST'|'R2_RECENT'|'R3_RECONCILE'|'R4_ARCHIVE'
type RepairRow={
 broker:string;branch:string;symbol:string;name:string;repair_tier:RepairTier;start:string;end:string
 status:RepairStatus;attempts:number;last_error:string;error_kind:string;next_retry_at:string|null
 retryable:number;checkpoint_at:string|null
}
type RepairRun={
 id:string;started_at:string;finished_at:string|null;status:string;done:number;total:number
 succeeded:number;failed:number;message:string;errors:string[];checkpoint_at:string|null;checkpoint_item:string|null
}
type RepairReport={
 plan:null|{id:string;created_at:string;through_date:string|null;target_date:string;selected:number;summary:{selected:number;inserted:number;by_tier:Record<RepairTier,number>;network_requests:number;range:string}}
 job:RepairRun|null
 counts:Record<RepairStatus,number>
 queued_by_tier:Record<RepairTier,number>
 rows:RepairRow[]
 retry:{due:number;waiting:number;exhausted:number;interrupted:number}
 retry_policy:{max_attempts:number;backoff_minutes:number[]}
 statuses:RepairStatus[];repair_tiers:RepairTier[];activated?:number
}

const report=ref<RepairReport|null>(null)
const loading=ref(true),working=ref(''),error=ref(''),notice=ref('')
const batchSize=ref(50),throughDate=ref('2026-09-16')
const statusFilter=ref(''),tierFilter=ref('')
let poll:ReturnType<typeof setTimeout>|undefined

const tiers:{key:RepairTier;title:string;hint:string}[]=[
 {key:'R0_OPEN',title:'尚有部位',hint:'優先補齊仍有推估持股的股票'},
 {key:'R1_LATEST',title:'最新榜單',hint:'最近榜單再次出現的股票'},
 {key:'R2_RECENT',title:'近期活躍',hint:'近期仍有買賣紀錄的股票'},
 {key:'R3_RECONCILE',title:'待核對',hint:'含期初未知流出的歷史候選'},
 {key:'R4_ARCHIVE',title:'歷史封存',hint:'較早且不活躍，最後處理'},
]
const statusText:Record<string,string>={queued:'等待中',running:'執行中',complete:'已完成',failed:'失敗',interrupted:'已中斷',partial:'部分完成'}
const total=computed(()=>Object.values(report.value?.counts||{}).reduce((sum,value)=>sum+Number(value||0),0))
const queued=computed(()=>report.value?.counts.queued||0)
const active=computed(()=>report.value?.job?.status==='running')
const progress=computed(()=>{
 const job=report.value?.job
 return job&&job.total?Math.min(100,job.done/job.total*100):0
})
const lastError=computed(()=>report.value?.job?.errors?.at(-1)||report.value?.rows.find(row=>row.last_error)?.last_error||'')
const fmt=(value:number|null|undefined)=>Number(value||0).toLocaleString('zh-TW')
const stamp=(value:string|null|undefined)=>value?value.slice(0,16).replace('T',' '):'—'
const checkpoint=computed(()=>{
 const raw=report.value?.job?.checkpoint_item
 if(!raw)return null
 try{return JSON.parse(raw) as {symbol?:string;start?:string;end?:string;status?:string;attempt?:number}}
 catch{return null}
})

async function request(path:string,body?:unknown){
 const response=await localFetch(path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const result=await readJson<RepairReport&{error?:string}>(response)
 if(!response.ok)throw new Error(result.error||'無法讀取歷史修復狀態')
 return result
}
function query(){
 const params=new URLSearchParams({limit:'20'})
 if(statusFilter.value)params.set('status',statusFilter.value)
 if(tierFilter.value)params.set('repair_tier',tierFilter.value)
 return '/api/history-repair?'+params
}
function armPoll(){
 clearTimeout(poll)
 if(active.value)poll=setTimeout(()=>void load(false),1600)
}
async function load(showLoading=true){
 if(showLoading)loading.value=true
 try{
  report.value=await request(query());error.value=''
  if(report.value.plan?.through_date)throughDate.value=report.value.plan.through_date
 }catch(exc){error.value=(exc as Error).message}
 finally{loading.value=false;armPoll()}
}
async function act(kind:'plan'|'retry'|'run'){
 working.value=kind;notice.value='';error.value=''
 try{
  const path=kind==='plan'?'/api/history-repair/plan':kind==='retry'?'/api/history-repair/retry':'/api/history-repair/run'
  const body=kind==='plan'?{through_date:throughDate.value||null}:kind==='retry'?{limit:500,force:false}:{limit:Number(batchSize.value),resume:true}
  report.value=await request(path,body)
  notice.value=kind==='plan'?'修復清單已用本機盤點更新；尚未連線抓取。':kind==='retry'?`已將 ${fmt(report.value.activated)} 組到期或中斷項目移回等待清單。`:'已開始執行下一批歷史修復。'
  await load(false)
 }catch(exc){error.value=(exc as Error).message}
 finally{working.value=''}
}

watch([statusFilter,tierFilter],()=>void load())
onMounted(()=>void load())
onUnmounted(()=>clearTimeout(poll))
</script>

<template>
 <section class="panel repair-panel" aria-labelledby="repair-title">
  <header class="repair-heading">
   <div><span class="eyebrow">HISTORY REPAIR QUEUE</span><h2 id="repair-title">歷史尾端修復</h2><p>修補過去中斷後停在舊日期的分點個股。開啟本頁只讀取本機狀態；按下執行才會連線抓資料。</p></div>
   <button class="repair-refresh" :disabled="loading||!!working" @click="load()"><Icon name="refresh" :class="{spinning:loading}" :size="16"/>重新讀取</button>
  </header>

  <div v-if="error" class="repair-message error" role="alert">{{error}}<button @click="load()">重試</button></div>
  <div v-if="notice" class="repair-message" role="status">{{notice}}</div>

  <div class="repair-summary">
   <article><span>全部修復組合</span><strong>{{fmt(total)}}</strong><small>目前清單中的分點 × 個股區間</small></article>
   <article><span>等待處理</span><strong>{{fmt(queued)}}</strong><small>{{fmt(report?.counts.running)}} 組執行中</small></article>
   <article><span>已完成</span><strong>{{fmt(report?.counts.complete)}}</strong><small>每完成一組立即保存</small></article>
   <article><span>需要注意</span><strong>{{fmt((report?.counts.failed||0)+(report?.counts.interrupted||0))}}</strong><small>到期 {{fmt(report?.retry.due)}} · 等待 {{fmt(report?.retry.waiting)}} · 達上限 {{fmt(report?.retry.exhausted)}}</small></article>
  </div>

  <div class="repair-tier-grid">
   <button v-for="tier in tiers" :key="tier.key" :class="{selected:tierFilter===tier.key}" @click="tierFilter=tierFilter===tier.key?'':tier.key">
    <span>{{tier.title}}<small>{{tier.key}}</small></span><strong>{{fmt(report?.queued_by_tier[tier.key])}}</strong><em>{{tier.hint}}</em>
   </button>
  </div>

  <div v-if="active" class="repair-running" role="status">
   <div><Icon name="refresh" class="spinning" :size="17"/><span><strong>{{report?.job?.message}}</strong><small>成功 {{fmt(report?.job?.succeeded)}} · 失敗 {{fmt(report?.job?.failed)}} · 共 {{fmt(report?.job?.total)}} 組</small></span></div>
   <div class="repair-track"><i :style="{width:progress+'%'}"></i></div>
  </div>

  <div class="repair-controls">
   <div class="repair-plan-control"><label>只納入最後成功日在此日以前<input v-model="throughDate" type="date" aria-label="歷史修復停滯日期"></label><button :disabled="!!working||active" @click="act('plan')">{{working==='plan'?'更新中':'更新修復清單'}}</button><small>只重新盤點本機資料，不會連線抓取。</small></div>
   <div class="repair-run-control"><label>每批處理<select v-model.number="batchSize" aria-label="每批修復筆數"><option :value="10">10 組</option><option :value="25">25 組</option><option :value="50">50 組</option><option :value="100">100 組</option><option :value="250">250 組</option></select></label><button class="primary" :disabled="!!working||active||queued===0" @click="act('run')"><Icon name="refresh" :class="{spinning:working==='run'||active}" :size="16"/>{{active?'修復執行中':working==='run'?'啟動中':'執行下一批'}}</button><small>只處理一批；完成後不會自動接續下一批。</small></div>
   <div class="repair-retry-control"><button :disabled="!!working||active||!((report?.retry.due||0)+(report?.retry.interrupted||0))" @click="act('retry')">{{working==='retry'?'重排中':'重排到期／中斷項目'}}</button><small>依退避時間重排，最多嘗試 {{report?.retry_policy.max_attempts||5}} 次；仍在等待的項目不會提前。</small></div>
  </div>

  <div class="repair-meta">
   <span>清單建立 {{stamp(report?.plan?.created_at)}}</span><span>停滯界線 {{report?.plan?.through_date||'未限制'}}</span><span>目標日期 {{report?.plan?.target_date||'—'}}</span><span>上次批次 {{statusText[report?.job?.status||'']||'尚未執行'}}</span>
  </div>
  <div v-if="report?.job" class="repair-checkpoint">
   <Icon name="database" :size="17"/><div><strong>最後檢查點 · {{stamp(report.job.checkpoint_at)}}</strong><p v-if="checkpoint">{{checkpoint.symbol||'—'}} · {{checkpoint.start}}～{{checkpoint.end}} · {{statusText[checkpoint.status||'']||checkpoint.status}} · 第 {{checkpoint.attempt||0}} 次</p><p v-else>這一批尚未完成第一組。</p><p v-if="lastError" class="checkpoint-error">最近錯誤：{{lastError}}</p></div>
  </div>

  <div class="repair-list-heading"><div><h3>修復明細</h3><p>顯示前 20 組；可依狀態與優先級縮小範圍。</p></div><label>狀態<select v-model="statusFilter"><option value="">全部狀態</option><option v-for="value in report?.statuses||[]" :key="value" :value="value">{{statusText[value]}}</option></select></label><label>優先級<select v-model="tierFilter"><option value="">全部優先級</option><option v-for="tier in tiers" :key="tier.key" :value="tier.key">{{tier.title}}</option></select></label></div>
  <div class="repair-table"><table><thead><tr><th>分點／個股</th><th>待補區間</th><th>優先級</th><th>狀態</th><th>嘗試／下次重試</th></tr></thead><tbody><tr v-for="row in report?.rows||[]" :key="row.broker+row.branch+row.symbol+row.start"><td><strong>{{row.name}} {{row.symbol}}</strong><small>{{row.broker}} / {{row.branch}}</small></td><td>{{row.start}}<small>～ {{row.end}}</small></td><td><span class="repair-pill">{{tiers.find(t=>t.key===row.repair_tier)?.title}}</span></td><td><span class="repair-state" :class="row.status">{{statusText[row.status]}}</span><small v-if="row.last_error" :title="row.last_error">{{row.last_error}}</small></td><td>{{row.attempts}} / {{report?.retry_policy.max_attempts||5}}<small>{{row.next_retry_at?'可重試 '+stamp(row.next_retry_at):row.checkpoint_at?'保存 '+stamp(row.checkpoint_at):'尚未執行'}}</small></td></tr><tr v-if="!loading&&!report?.rows.length"><td colspan="5" class="repair-empty">這個篩選範圍沒有修復項目。</td></tr></tbody></table></div>
 </section>
</template>

<style scoped>
.repair-panel{margin:24px 0;padding:0;overflow:hidden;color:#465c3d}.repair-heading{display:flex;justify-content:space-between;gap:24px;padding:28px 30px 23px;border-bottom:1px solid #e8eee2}.repair-heading h2{margin:7px 0 8px;font-size:1.15rem}.repair-heading p{margin:0;max-width:720px;color:#809075;font-size:.78rem;line-height:1.75}.eyebrow{font-size:.64rem;letter-spacing:.13em;color:#8b9d7d}.repair-refresh,.repair-controls button{align-self:start;border:1px solid #d8e1d1;border-radius:7px;background:white;color:#5d754e;padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer;white-space:nowrap}.repair-refresh:disabled,.repair-controls button:disabled{opacity:.48;cursor:not-allowed}.repair-message{margin:18px 30px 0;padding:12px 15px;border-radius:7px;background:#f0f5ea;font-size:.76rem;display:flex;justify-content:space-between;gap:15px}.repair-message.error{background:#fbefeb;color:#a45d4e}.repair-message button{color:inherit;text-decoration:underline}.repair-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;padding:24px 30px 14px}.repair-summary article{border:1px solid #e5ebdf;border-radius:9px;padding:18px;background:#fbfcf9}.repair-summary span,.repair-summary small{display:block;color:#87967c;font-size:.69rem;line-height:1.55}.repair-summary strong{display:block;font-size:1.65rem;font-weight:500;margin:10px 0 8px}.repair-tier-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;padding:0 30px 24px}.repair-tier-grid button{border:1px solid #e1e8da;background:white;border-radius:8px;padding:14px;text-align:left;color:#4a603f;cursor:pointer;display:grid;grid-template-columns:1fr auto;gap:8px}.repair-tier-grid button.selected{border-color:#7f9c68;background:#f3f7ee}.repair-tier-grid span{font-size:.76rem}.repair-tier-grid span small{display:block;color:#9aa691;font-size:.58rem;margin-top:4px}.repair-tier-grid strong{font-size:1.15rem;font-weight:500}.repair-tier-grid em{grid-column:1/-1;color:#8d9a84;font-size:.62rem;line-height:1.5;font-style:normal}.repair-running{margin:0 30px 22px;padding:16px 18px;border:1px solid #dce7d4;border-radius:8px;background:#f8fbf5}.repair-running>div:first-child{display:flex;align-items:center;gap:12px}.repair-running span,.repair-running small{display:block;font-size:.76rem}.repair-running small{color:#89977f;margin-top:5px}.repair-track{height:5px;background:#e5ecdf;border-radius:5px;margin-top:14px;overflow:hidden}.repair-track i{display:block;height:100%;background:#718e5c;transition:width .25s}.repair-controls{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:18px;padding:22px 30px;border-top:1px solid #e8eee2;border-bottom:1px solid #e8eee2;background:#fafbf8}.repair-controls>div{display:flex;align-items:end;align-content:start;gap:10px;flex-wrap:wrap}.repair-controls label{display:grid;gap:7px;font-size:.68rem;color:#819077}.repair-controls input,.repair-controls select,.repair-list-heading select{border:1px solid #d9e1d2;border-radius:6px;background:#fff;color:#49603e;padding:9px 10px;font:inherit}.repair-controls .primary{background:#6e8959;color:white;border-color:#6e8959}.repair-controls small{width:100%;font-size:.62rem;color:#8c9983;line-height:1.5}.repair-meta{display:flex;gap:12px 25px;flex-wrap:wrap;padding:17px 30px;color:#7f8e75;font-size:.67rem}.repair-checkpoint{display:flex;gap:12px;margin:0 30px 20px;padding:15px 17px;border-radius:7px;background:#f5f8f1}.repair-checkpoint strong{font-size:.73rem}.repair-checkpoint p{margin:6px 0 0;font-size:.66rem;color:#829077;line-height:1.55}.repair-checkpoint .checkpoint-error{color:#a46658}.repair-list-heading{display:flex;align-items:end;gap:14px;padding:23px 30px 14px}.repair-list-heading>div{margin-right:auto}.repair-list-heading h3{font-size:.9rem;margin:0 0 6px}.repair-list-heading p{font-size:.67rem;color:#8b9981;margin:0}.repair-list-heading label{display:grid;gap:6px;font-size:.65rem;color:#829178}.repair-table{overflow:auto}.repair-table table{width:100%;border-collapse:collapse;font-size:.73rem}.repair-table th,.repair-table td{text-align:left;padding:14px 18px;border-top:1px solid #e9eee4;vertical-align:top}.repair-table th{background:#f8faf5;color:#89987e;font-weight:400;white-space:nowrap}.repair-table td:first-child{padding-left:30px}.repair-table th:first-child{padding-left:30px}.repair-table td:last-child,.repair-table th:last-child{padding-right:30px}.repair-table strong,.repair-table small{display:block}.repair-table small{color:#8d9a83;font-size:.62rem;margin-top:6px;max-width:310px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.repair-pill,.repair-state{display:inline-block;padding:4px 7px;border-radius:5px;background:#eef3e8;font-size:.64rem;white-space:nowrap}.repair-state.failed,.repair-state.interrupted{background:#f7e9e5;color:#a35d4d}.repair-state.running{background:#edf2df;color:#6d824b}.repair-state.complete{background:#e7f2e9;color:#4b7b57}.repair-empty{text-align:center!important;padding:36px!important;color:#8b9982}.spinning{animation:repair-spin 1s linear infinite}@keyframes repair-spin{to{transform:rotate(360deg)}}@media(max-width:1100px){.repair-summary{grid-template-columns:repeat(2,1fr)}.repair-tier-grid{grid-template-columns:repeat(3,1fr)}.repair-controls{grid-template-columns:1fr}}@media(max-width:650px){.repair-heading{flex-direction:column}.repair-summary{grid-template-columns:1fr}.repair-tier-grid{grid-template-columns:1fr}.repair-list-heading{align-items:stretch;flex-direction:column}.repair-table table{min-width:780px}}
</style>
