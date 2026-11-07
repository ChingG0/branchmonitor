<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {localFetch,readJson} from './http'
type Point={date:string;close:number;estimated_pe:number;anchor_date:string;bands:number[]}
type River={available:boolean;as_of:string|null;points:Point[];multiples:number[];official:{date:string;pe:number}|null;warning:string;note:string;source_url:string;index_source_url:string}
const props=defineProps<{data?:River;embedded?:boolean}>()
const saved=ref<River|null>(null),error=ref(''),busy=ref(false),loading=ref(false),period=ref<3|12>(3),hover=ref<number|null>(null)
let alive=true
const model=computed(()=>props.data||saved.value)
const points=computed(()=>{
 const all=model.value?.points||[];if(!all.length)return []
 const last=new Date(all[all.length-1].date+'T00:00:00Z'),day=last.getUTCDate();last.setUTCDate(1);last.setUTCMonth(last.getUTCMonth()-period.value)
 const end=new Date(Date.UTC(last.getUTCFullYear(),last.getUTCMonth()+1,0)).getUTCDate();last.setUTCDate(Math.min(day,end))
 return all.filter(p=>p.date>=last.toISOString().slice(0,10))
})
const latest=computed(()=>points.value[points.value.length-1]),selected=computed(()=>points.value[hover.value??points.value.length-1])
const percentile=computed(()=>latest.value?100*points.value.filter(p=>p.estimated_pe<=latest.value!.estimated_pe).length/points.value.length:0)
const position=computed(()=>percentile.value>=80?'相對高位':percentile.value<=20?'相對低位':'中間區間')
const upper=computed(()=>Math.ceil(Math.max(1,...points.value.flatMap(p=>[p.close,...p.bands]))/5000)*5000)
const colors=['#7c96bc','#8cb9ce','#ffe0a0','#ffbe89','#f39781']
const fmt=(n:number|undefined,d=2)=>n==null?'—':n.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const x=(i:number)=>58+i*846/Math.max(1,points.value.length-1),y=(v:number)=>300-v/upper.value*270
const line=computed(()=>points.value.map((p,i)=>(i?'L':'M')+x(i)+','+y(p.close)).join(' '))
const area=(k:number)=>points.value.map((p,i)=>(i?'L':'M')+x(i)+','+y(p.bands[k])).join(' ')+[...points.value].reverse().map((p,i)=>'L'+x(points.value.length-1-i)+','+y(p.bands[k+1])).join(' ')+'Z'
const ticks=computed(()=>[...new Set([0,Math.floor((points.value.length-1)/3),Math.floor((points.value.length-1)*2/3),points.value.length-1])].filter(i=>i>=0))
function move(event:PointerEvent){const box=(event.currentTarget as SVGElement).getBoundingClientRect(),px=(event.clientX-box.left)/box.width*960;hover.value=Math.min(points.value.length-1,Math.max(0,Math.round((px-58)/846*(points.value.length-1))))}
function key(event:KeyboardEvent){if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();hover.value=Math.max(0,Math.min(points.value.length-1,(hover.value??points.value.length-1)+(event.key==='ArrowLeft'?-1:1)))}}
async function load(){loading.value=true;try{const response=await localFetch('/api/market-pe'),data=await readJson(response);if(!response.ok)throw new Error(data.error||'讀取失敗');if(alive)saved.value=data}catch(e){if(alive)error.value=String(e)}finally{if(alive)loading.value=false}}
async function refresh(){
 busy.value=true;error.value=''
 try{
  const response=await localFetch('/api/market-monitor/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card:'pe_river'})});const result=await readJson(response);if(!response.ok)throw new Error(result.error||'更新未啟動')
  for(let i=0;i<180&&alive;i++){await new Promise(resolve=>setTimeout(resolve,1000));if(!alive)return;const r=await localFetch('/api/market-monitor'),data=await readJson(r);if(!r.ok)throw new Error(data.error||'讀取更新狀態失敗');if(!data.job.running){await load();error.value=data.cards.find((c:any)=>c.id==='pe_river')?.error||'';return}}
  if(alive)error.value='資料仍在背景更新，稍後重新開啟此頁即可查看。'
 }catch(e){if(alive)error.value=String(e)}finally{if(alive)busy.value=false}
}
watch([period,model],()=>hover.value=null)
onMounted(()=>{if(!props.data)load()});onUnmounted(()=>{alive=false})
</script>
<template>
 <section class="pe-river" :class="{embedded}" aria-label="大盤本益比河流">
  <header><div><small v-if="!embedded" class="eyebrow">TAIEX VALUATION</small><h3 v-if="!embedded">大盤本益比河流</h3><p>官方月估值基準 × 每日加權指數，觀察短波段的相對高低。</p></div><div class="controls"><div class="periods"><button v-for="n in ([3,12] as const)" :key="n" :aria-pressed="period===n" @click="period=n">{{n===3?'近一季':'近一年'}}</button></div><button v-if="!embedded" class="refresh" :disabled="busy" @click="refresh">{{busy?'更新中…':'更新河流圖'}}</button></div></header>
  <p v-if="error||model?.warning" class="warning" role="status">{{error||model?.warning}}</p>
  <template v-if="latest">
   <div class="metrics"><div><span>加權指數收盤</span><strong>{{fmt(latest.close)}}</strong><small>{{latest.date}}</small></div><div><span>官方月末本益比</span><strong>{{fmt(model?.official?.pe)}}<em> 倍</em></strong><small>{{model?.official?.date}}</small></div><div><span>最新推估本益比</span><strong>{{fmt(latest.estimated_pe)}}<em> 倍</em></strong><small>估值基準 {{latest.anchor_date}}</small></div><div><span>所選期間估值分位</span><strong>{{fmt(percentile,1)}}<em>%</em></strong><small>{{position}} · {{points.length}} 個交易日</small></div></div>
   <div class="legend"><span><i class="close-key"></i>每日指數收盤</span><span v-for="(color,i) in colors" :key="color"><i :style="{background:color}"></i>{{model?.multiples[i]}}–{{model?.multiples[i+1]}} 倍</span></div>
   <div class="chart-scroll"><svg viewBox="0 0 960 350" role="img" tabindex="0" aria-label="加權指數與本益比河流，移動滑鼠或使用左右方向鍵查看日期" @pointermove="move" @pointerleave="hover=null" @keydown="key">
    <g v-for="step in [0,1,2,3]" :key="step"><line x1="58" x2="904" :y1="y(upper*step/3)" :y2="y(upper*step/3)" stroke="#e6ece5" stroke-dasharray="4 4"/><text x="48" :y="y(upper*step/3)+4" text-anchor="end">{{fmt(upper*step/3,0)}}</text></g>
    <path v-for="(color,k) in colors" :key="color" :d="area(k)" :fill="color" fill-opacity=".7"/>
    <path :d="line" stroke="#b85349" stroke-width="2.5" fill="none"/>
    <text v-for="i in ticks" :key="i" :x="x(i)" y="327" text-anchor="middle">{{points[i].date}}</text>
    <g v-if="selected&&hover!==null"><line :x1="x(hover)" :x2="x(hover)" y1="25" y2="300" stroke="#647960" stroke-dasharray="4 4"/><circle :cx="x(hover)" :cy="y(selected.close)" r="4" fill="#b85349" stroke="white"/></g>
   </svg></div>
   <div v-if="selected" class="detail" aria-live="polite"><b>{{selected.date}}</b><span>指數 {{fmt(selected.close)}}</span><span>推估本益比 {{fmt(selected.estimated_pe)}} 倍</span><span>月末基準 {{selected.anchor_date}}</span></div>
   <p v-if="points.length<(period===3?45:180)" class="warning">此期間資料尚未齊全，目前顯示 {{points.length}} 個交易日；缺漏資料不以零或前值補齊。</p>
   <footer><p>{{model?.note}}</p><p>近一季／近一年切換觀察期間，倍數刻度維持一致。估值分位反映所選期間的推估本益比排序，並非買賣訊號。</p><a :href="model?.source_url" target="_blank" rel="noopener noreferrer">證交所本益比月報 ↗</a><a :href="model?.index_source_url" target="_blank" rel="noopener noreferrer">證交所每日指數 ↗</a></footer>
  </template>
  <p v-else class="empty">{{loading?'正在讀取已保存的河流圖…':'尚無河流資料，請按更新取得官方月報與每日指數。'}}</p>
 </section>
</template>
<style scoped>
.pe-river{background:white;border:1px solid #e1e7de;border-radius:14px;margin:20px 0;overflow:hidden;color:#425440}.pe-river.embedded{border:0;margin:0;border-radius:0}.pe-river header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:24px;flex-wrap:wrap}.eyebrow{letter-spacing:.15em;color:#8b9a83;font-size:10px}.pe-river h3{font-size:20px;margin:7px 0}.pe-river header p{font-size:12px;color:#7d8d77;margin:5px 0}.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.periods{display:flex;padding:4px;background:#f0f3ed;border-radius:9px}.periods button,.refresh{font:inherit;font-size:13px;padding:9px 15px;border:0;border-radius:6px;color:#64755d;background:transparent;cursor:pointer}.periods button[aria-pressed=true]{background:white;color:#31593d;box-shadow:0 1px 4px #263d2420}.refresh{border:1px solid #dce4d7;background:white}.refresh:disabled{opacity:.6;cursor:wait}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid #edf0e9;border-bottom:1px solid #edf0e9}.metrics>div{padding:20px 24px;border-right:1px solid #edf0e9}.metrics span,.metrics small{display:block;font-size:11px;color:#8a9782}.metrics strong{display:block;font-size:25px;font-variant-numeric:tabular-nums;margin:9px 0;color:#3d6147;font-weight:600}.metrics em{font-size:14px;font-style:normal;font-weight:400}.legend{display:flex;flex-wrap:wrap;gap:14px;padding:21px 24px 8px;font-size:11px;color:#7b8973}.legend span{display:flex;align-items:center;gap:6px}.legend i{display:inline-block;width:12px;height:8px;border-radius:2px}.legend i.close-key{background:#b85349;height:3px}.chart-scroll{overflow-x:auto;padding:0 14px}.chart-scroll svg{display:block;width:100%;min-width:650px;touch-action:pan-y}.chart-scroll text{fill:#86917f;font-size:11px}.detail{display:flex;gap:20px;flex-wrap:wrap;margin:0 24px 20px;padding:13px 16px;background:#f5f8f2;border-radius:7px;font-size:12px}.detail span:last-child{color:#8b9683}.pe-river footer{padding:15px 24px 21px;border-top:1px solid #edf0e9;font-size:11px;color:#8a9681;line-height:1.8}.pe-river footer p{margin:0 0 6px}.pe-river footer a{margin-right:18px;color:#607d52;text-decoration:underline}.warning{margin:10px 24px;color:#92723f;background:#faf4e7;padding:10px;font-size:12px;line-height:1.7}.empty{padding:35px 24px;color:#8a9681;font-size:13px}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.metrics>div{border-bottom:1px solid #edf0e9}}@media(max-width:500px){.metrics{grid-template-columns:1fr}.pe-river header{padding:18px}.controls{width:100%}.detail{gap:10px}.metrics>div{padding:15px 20px}}
</style>
