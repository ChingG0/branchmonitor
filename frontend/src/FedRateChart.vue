<script setup lang="ts">
import {computed,ref} from 'vue'
import {rateWindow,rateAt,stepPath} from './rateChart'
import type {FedRateData} from './rateChart'
const props=defineProps<{data:FedRateData}>()
const years=ref(2),hover=ref('')
const view=computed(()=>rateWindow(props.data.history||[],props.data.observed_through||props.data.as_of,years.value))
const time=(d:string)=>Date.parse(d+'T00:00:00Z')
const x=(d:string)=>30+455*(time(d)-time(view.value.start))/Math.max(1,time(view.value.end)-time(view.value.start))
const interval=computed(()=>{const range=Math.max(...view.value.rows.map(r=>r.high))-Math.min(...view.value.rows.map(r=>r.low));return range<=1?.25:range<=3?.5:1})
const min=computed(()=>Math.floor(Math.min(...view.value.rows.map(r=>r.low))/interval.value)*interval.value)
const max=computed(()=>Math.ceil(Math.max(...view.value.rows.map(r=>r.high))/interval.value)*interval.value)
const y=(v:number)=>163-133*(v-min.value)/Math.max(.25,max.value-min.value)
const upper=computed(()=>stepPath(view.value.rows,x,y,view.value.end))
const lower=computed(()=>stepPath(view.value.rows,x,y,view.value.end,'low'))
const date=computed(()=>hover.value||view.value.end)
const current=computed(()=>rateAt(view.value.all,date.value))
const ticks=computed(()=>Array.from({length:Math.round((max.value-min.value)/interval.value)+1},(_,i)=>min.value+interval.value*i))
const dates=computed(()=>Array.from({length:5},(_,i)=>new Date(time(view.value.start)+(time(view.value.end)-time(view.value.start))*i/4).toISOString().slice(0,10)))
const fmt=(n:number)=>n.toFixed(2)
function move(event:PointerEvent){const rect=(event.currentTarget as SVGElement).getBoundingClientRect();const position=Math.max(0,Math.min(1,((event.clientX-rect.left)/rect.width*540-30)/455));hover.value=new Date(time(view.value.start)+(time(view.value.end)-time(view.value.start))*position).toISOString().slice(0,10)}
</script>
<template>
 <div class="fed-rates">
  <div class="rate-heading"><span>聯邦基金利率目標區間</span><strong>{{fmt(data.low)}}–{{fmt(data.high)}}<small>%</small></strong></div>
  <div class="rate-controls"><span>歷次調整 · 階梯圖</span><div><button v-for="n in [1,2,5]" :key="n" :class="{active:years===n}" @click="years=n;hover=''">{{n}} 年</button></div></div>
  <template v-if="view.rows.length">
   <div class="rate-hover">{{date}} <b>{{current?fmt(current.low)+'–'+fmt(current.high)+'%':'—'}}</b><small v-if="current">生效 {{current.date}}</small></div>
   <svg viewBox="0 0 540 198" role="img" aria-label="聯邦基金利率目標區間歷史階梯圖，上緣實線、下緣虛線" @pointermove="move" @pointerleave="hover=''">
    <g v-for="tick in ticks" :key="tick"><line x1="30" x2="485" :y1="y(tick)" :y2="y(tick)" stroke="#e5eae4" stroke-dasharray="2 3"/><text x="495" :y="y(tick)+4">{{tick.toFixed(2)}}%</text></g>
    <g v-for="d in dates" :key="d"><line :x1="x(d)" :x2="x(d)" y1="30" y2="163" stroke="#e9ede8" stroke-dasharray="2 3"/><text :x="x(d)" y="187" text-anchor="middle">{{d.slice(2,7).replace('-','/')}}</text></g>
    <path :d="lower" stroke="#a0b7d0" fill="none" stroke-width="1.6" stroke-dasharray="4 4"/>
    <path :d="upper" stroke="#4d82bc" fill="none" stroke-width="2.5" stroke-linejoin="round"/>
    <g v-if="current"><line :x1="x(date)" :x2="x(date)" y1="30" y2="163" stroke="#93a5b3" stroke-dasharray="3 4"/><circle :cx="x(date)" :cy="y(current.high)" r="4" fill="#4d82bc" stroke="white" stroke-width="1.5"/></g>
   </svg>
   <p class="rate-legend"><span>━━ 目標上緣</span><span>┄┄ 目標下緣</span><span>移到圖上查看日期與區間</span></p>
   <p v-if="data.last_change_bp!=null" class="rate-last">最近調整 {{data.as_of}} · {{data.last_change_bp>0?'升息':'降息'}} {{Math.abs(data.last_change_bp)}} 基點（{{(Math.abs(data.last_change_bp)/100).toFixed(2)}} 個百分點）</p>
  </template>
  <p v-else class="rate-last">歷史調整資料待補，保留已核對的現行區間。</p>
  <div class="next-meeting"><span>下次 FOMC 會議 · 美東日期</span><b>{{data.next_start}} ～ {{data.next_end}}</b><a :href="data.calendar_url" target="_blank" rel="noopener noreferrer">Fed 官方行事曆 ↗</a></div>
  <small v-if="data.observed_through" class="rate-source">核對至 {{data.observed_through}} 美東 · <a :href="data.history_url" target="_blank" rel="noopener noreferrer">Fed 官方歷史調整 ↗</a></small>
 </div>
</template>
<style scoped>
.fed-rates{padding:2px 22px 22px}.rate-heading>span{display:block;font-size:12px;color:#87977c}.rate-heading strong{display:block;font-size:32px;font-weight:500;color:#385a40;letter-spacing:-1px;margin:10px 0 17px}.rate-heading small{font-size:15px}.rate-controls{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#78907b}.rate-controls>div{display:flex;gap:3px;background:#f0f4ee;border-radius:7px;padding:3px}.rate-controls button{border:0;background:transparent;color:#8a9781;padding:5px 11px;border-radius:5px;cursor:pointer}.rate-controls button.active{background:white;color:#3e6f61;box-shadow:0 1px 4px #cbd5c8}.rate-hover{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:#849582;margin-top:16px}.rate-hover b{color:#46749c}.rate-hover small{color:#99a28e}.fed-rates svg{display:block;width:100%;height:auto;touch-action:pan-y}.fed-rates svg text{font-size:10px;fill:#8a9984}.rate-legend{display:flex;gap:15px;flex-wrap:wrap;font-size:10px;color:#6385a4;margin:0 0 14px}.rate-legend>span:last-child{color:#99a38d}.rate-last{font-size:11px;color:#677f69;line-height:1.7}.next-meeting{border-left:3px solid #91a76d;background:#f5f8ee;padding:12px 15px;display:flex;flex-direction:column;gap:7px;font-size:11px;color:#8b9a77;margin-top:15px}.next-meeting b{font-size:13px;color:#526d54}.next-meeting a,.rate-source a{color:#799465}.rate-source{display:block;font-size:10px;color:#95a18d;margin-top:12px}
</style>
