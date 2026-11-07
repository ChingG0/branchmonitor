<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import Icon from './Icon.vue'
import {localFetch,readJson} from './http'

type PriceEffect={status:'ready'|'pending'|'missing'|'unavailable';month?:string;return_pct?:number;direction?:'up'|'down'|'flat';start_date?:string;end_date?:string;start_open?:number;end_close?:number}
type History={wins:number;total:number;rate_pct:number|null}
type Change={year:number;month:number;market:string;market_name:string;symbol:string;company:string;holder:string;previous_shares:number;current_shares:number;change_shares:number;next_month_price:PriceEffect;history:History}
type Coverage={month:number;market:string;market_name:string;available:boolean}
type Report={year:number;rows:Change[];count:number;evaluated:number;price_through_month:string|null;coverage:Coverage[];fetched_at:string|null;source:string;job:{status:string;done:number;total:number;message:string}}

const report=ref<Report|null>(null),error=ref(''),busy=ref(false)
const query=ref(''),direction=ref('all'),market=ref('all'),month=ref('all'),page=ref(1)
const pageSize=100
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
const fmt=(n:number)=>n.toLocaleString('zh-TW')
const lots=(n:number)=>`${n>0?'+':''}${(n/1000).toLocaleString('zh-TW',{maximumFractionDigits:3})} 張`
const signedPct=(n:number)=>`${n>0?'+':''}${n.toLocaleString('zh-TW',{maximumFractionDigits:2})}%`
const effectName=(effect:PriceEffect)=>effect.status==='ready'?(effect.direction==='up'?'漲':effect.direction==='down'?'跌':'平盤'):effect.status==='pending'?'待下下月收盤':effect.status==='missing'?'行情缺漏':'無上市櫃行情'
const effectDetail=(effect:PriceEffect)=>effect.status==='ready'?signedPct(effect.return_pct||0):effect.status==='pending'?`${effect.month} 尚未結束`:effect.status==='missing'?'未列入統計':'未列入統計'
const effectTitle=(effect:PriceEffect)=>effect.status==='ready'?`${effect.start_date} 開盤 ${effect.start_open} → ${effect.end_date} 收盤 ${effect.end_close}；未還原除權息`:'只統計資料完整且已結束的上市、上櫃月份'
const filters=computed(()=>{
 const needle=query.value.trim().toLowerCase()
 return (report.value?.rows||[]).filter(row=>
  (direction.value==='all'||(direction.value==='up'?row.change_shares>0:row.change_shares<0))&&
  (market.value==='all'||row.market===market.value)&&
  (month.value==='all'||row.month===Number(month.value))&&
  (!needle||`${row.symbol} ${row.company} ${row.holder}`.toLowerCase().includes(needle)))
})
const pages=computed(()=>Math.max(1,Math.ceil(filters.value.length/pageSize)))
const visible=computed(()=>filters.value.slice((page.value-1)*pageSize,page.value*pageSize))
const increases=computed(()=>(report.value?.rows||[]).filter(row=>row.change_shares>0).length)
const decreases=computed(()=>(report.value?.rows||[]).filter(row=>row.change_shares<0).length)
const covered=computed(()=>report.value?.coverage.filter(item=>item.available).length||0)
const latestMonth=computed(()=>Math.max(0,...(report.value?.coverage.filter(item=>item.available).map(item=>item.month)||[])))
watch([query,direction,market,month],()=>{page.value=1})

async function load(){
 clearTimeout(timer)
 try{
  const response=await localFetch('/api/insider-changes')
  const data=await readJson<Report&{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法讀取內部人持股異動')
  if(!alive)return
  report.value=data;busy.value=data.job.status==='running';error.value=''
  if(busy.value)timer=setTimeout(load,1200)
 }catch(e){if(alive){error.value=(e as Error).message;busy.value=false}}
}
async function refresh(){
 busy.value=true;error.value='';clearTimeout(timer)
 try{
  const response=await localFetch('/api/insider-changes/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})})
  const data=await readJson<{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法更新內部人持股異動')
  timer=setTimeout(load,350)
 }catch(e){error.value=(e as Error).message;busy.value=false}
}
onMounted(async()=>{
 await load()
 if(!alive||!report.value||report.value.job.status==='running'||covered.value)return
 const key=`branch-monitor.insider-initial.${report.value.year}`
 if(sessionStorage.getItem(key))return
 sessionStorage.setItem(key,'1')
 await refresh()
})
onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>

<template>
 <section class="insider-page">
  <header class="page-header"><div><div class="eyebrow">MOPS · MONTHLY OWNERSHIP</div><h1>內部人持股異動</h1><p>依公開資訊觀測站「持股 10% 以上大股東最近異動情形」，列出 {{report?.year||'今年'}} 年每月持股有增減的申報。</p></div><button class="primary" :disabled="busy" @click="refresh"><Icon name="refresh" :class="{spinning:busy}"/>{{busy?'今年資料更新中':'更新今年資料'}}</button></header>
  <div v-if="error" class="error-banner" role="alert">{{error}} <button @click="load">重新讀取</button></div>
  <div v-if="report" class="stats">
   <div class="stat"><span>有異動申報</span><strong>{{fmt(report.count)}}</strong><small>同一股東每月分別列示</small></div>
   <div class="stat"><span>持股增加</span><strong class="up">{{fmt(increases)}}</strong><small>以異動股數判斷</small></div>
   <div class="stat"><span>持股減少</span><strong class="down">{{fmt(decreases)}}</strong><small>以異動股數判斷</small></div>
   <div class="stat"><span>最新申報月份</span><strong>{{latestMonth?`${report.year}/${String(latestMonth).padStart(2,'0')}`:'待更新'}}</strong><small>月報 {{covered}}／{{report.coverage.length}} 組 · 公告後已核對 {{fmt(report.evaluated)}} 筆</small></div>
  </div>
  <div v-if="report?.job.status==='running'" class="progress"><Icon name="refresh" class="spinning" :size="16"/>{{report.job.message}} · {{report.job.done}}／{{report.job.total||'準備中'}}</div>
  <div v-else-if="report?.job.status==='partial'||report?.job.status==='failed'" class="progress warning">{{report.job.message}}；可按「更新今年資料」補查缺漏。</div>
  <div v-else-if="report?.coverage.some(item=>!item.available)&&covered" class="progress warning">尚有 {{report.coverage.length-covered}} 個市場月報未取得，按「更新今年資料」可補查。</div>
  <div class="filters panel"><label>搜尋公司或大股東<input v-model="query" type="search" placeholder="代號、公司名稱、股東名稱"></label><label>方向<select v-model="direction"><option value="all">全部異動</option><option value="up">持股增加</option><option value="down">持股減少</option></select></label><label>市場<select v-model="market"><option value="all">全部市場</option><option value="sii">上市</option><option value="otc">上櫃</option><option value="rotc">興櫃</option><option value="pub">公開發行</option></select></label><label>月份<select v-model="month"><option value="all">今年全部</option><option v-for="m in 12" :key="m" :value="String(m)">{{m}} 月</option></select></label></div>
  <section class="panel data-panel">
   <header><div><h2>異動明細</h2><p>符合 {{fmt(filters.length)}} 筆 · 依異動股數絕對值由大到小</p></div><a :href="report?.source" target="_blank" rel="noopener noreferrer">官方原始資料 ↗</a></header>
   <div class="table-wrap"><table>
    <thead><tr><th>#</th><th>申報月</th><th>市場</th><th>公司</th><th>異動數</th><th>大股東</th><th>上月持股</th><th>本月持股</th><th title="月報於次月申報；以異動月下下個月首個交易日開盤至該月底收盤計算，未還原除權息">公告後下月</th><th title="同公司、同方向、異動月以前，公告後下月行情完整的月份；同月多位股東合併計一次">過去同向命中</th></tr></thead>
    <tbody>
     <tr v-for="(row,index) in visible" :key="`${row.year}-${row.month}-${row.market}-${row.symbol}-${row.holder}`">
      <td>{{(page-1)*pageSize+index+1}}</td><td>{{row.year}}/{{String(row.month).padStart(2,'0')}}</td><td>{{row.market_name}}</td>
      <td><b>{{row.company}}</b><small>{{row.symbol}}</small></td>
      <td class="change" :class="row.change_shares>0?'up':'down'"><b>{{lots(row.change_shares)}}</b><small>{{row.change_shares>0?'+':''}}{{fmt(row.change_shares)}} 股</small></td>
      <td>{{row.holder}}</td><td>{{fmt(row.previous_shares)}} 股</td><td>{{fmt(row.current_shares)}} 股</td>
      <td :title="effectTitle(row.next_month_price)" class="outcome" :class="row.next_month_price.status==='ready'?(row.next_month_price.direction==='up'?'up':row.next_month_price.direction==='down'?'down':''):''"><b>{{effectName(row.next_month_price)}}</b><small>{{effectDetail(row.next_month_price)}}</small></td>
      <td class="history"><b v-if="row.history.total">{{row.change_shares>0?'增持後漲':'減持後跌'}} {{row.history.rate_pct}}%</b><b v-else>—</b><small>{{row.history.total?`${row.history.wins}／${row.history.total} 次`:'尚無有效前例'}}</small></td>
     </tr>
     <tr v-if="!visible.length"><td colspan="10" class="empty">{{busy?'正在取得今年的官方月報…':report?.count?'沒有符合條件的異動。':'尚無已取得的異動；請按「更新今年資料」。'}}</td></tr>
    </tbody>
   </table></div>
   <footer v-if="pages>1" class="pager"><button :disabled="page<=1" @click="page--">上一頁</button><span>{{page}}／{{pages}}</span><button :disabled="page>=pages" @click="page++">下一頁</button></footer>
  </section>
  <p class="source-note"><Icon name="info" :size="15"/>此表依你提供的官方來源，只涵蓋持股 10% 以上大股東。異動可能包含市場外移轉，故以「增持／減持」表示。月報於異動次月申報；為避免算入公告前漲跌，統一從異動月下下個月首個交易日開盤，觀察至該月底收盤。這是保守的公告後觀察窗，未核對每筆實際公告時刻、交易成本或除權息，並非實際投資報酬。平盤不算命中；歷史比例只用同公司、同方向、該筆之前已完成且有行情的月份，同月多位股東合併一次；興櫃與非上市櫃缺可靠行情時不列入。</p>
 </section>
</template>

<style scoped>
.insider-page{display:grid;gap:18px}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.stat{background:#fff;border:1px solid #e3e9df;border-radius:15px;padding:18px 20px}.stat span,.stat small{display:block;color:#87967e;font-size:.7rem}.stat strong{display:block;color:#405a3a;font-size:1.5rem;margin:6px 0}.up{color:#bd5a4d!important}.down{color:#2c886f!important}.progress{background:#edf3e9;border:1px solid #dce9d5;border-radius:9px;padding:12px 16px;color:#4d6842;font-size:.75rem;display:flex;align-items:center;gap:7px}.progress.warning{background:#fff8e9;border-color:#efe2bb;color:#96722f}.filters{display:flex;gap:12px;align-items:end;flex-wrap:wrap;padding:16px}.filters label{display:grid;gap:5px;color:#829078;font-size:.68rem}.filters input,.filters select{min-height:37px;padding:8px 10px;border:1px solid #d9e2d3;border-radius:8px;background:#fff;color:#42583b}.filters input{min-width:240px}.data-panel{overflow:hidden}.data-panel>header{padding:18px 21px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e6ece1}.data-panel h2{margin:0 0 4px;font-size:1rem}.data-panel p{margin:0;color:#8a9782;font-size:.7rem}.data-panel a{font-size:.72rem;color:#637d52}.table-wrap{overflow:auto}.data-panel table{width:100%;border-collapse:collapse;font-size:.76rem}.data-panel th,.data-panel td{padding:13px 14px;border-bottom:1px solid #edf0e9;text-align:left;white-space:nowrap}.data-panel th{background:#f7f9f5;color:#829078;font-size:.67rem;font-weight:500}.data-panel td b,.data-panel td small{display:block}.data-panel td small{color:#97a18f;font-size:.64rem;margin-top:3px}.change{text-align:right!important}.outcome b,.history b{font-size:.76rem}.outcome small,.history small{white-space:nowrap}.empty{text-align:center!important;padding:50px!important;color:#8d9a84}.pager{display:flex;justify-content:center;align-items:center;gap:16px;padding:15px;color:#70816a;font-size:.72rem}.pager button{padding:8px 12px;border:1px solid #d9e4d2;border-radius:7px}.pager button:disabled{opacity:.4}.source-note{display:flex;align-items:flex-start;gap:7px;color:#829079;font-size:.69rem;line-height:1.7;margin:0 2px}@media(max-width:900px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.data-panel table{min-width:1220px}}@media(max-width:580px){.stats{grid-template-columns:1fr}.filters label,.filters input,.filters select{width:100%}.data-panel>header{align-items:flex-start;gap:8px;flex-direction:column}}
</style>
