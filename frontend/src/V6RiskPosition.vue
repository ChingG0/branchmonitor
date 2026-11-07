<script setup lang="ts">
import {computed} from 'vue'
import {scoreDirection} from './v6ObservationVisual'
const props=defineProps<{score:number|null;priorScore:number|null;date:string;priorDate?:string}>()
const direction=computed(()=>scoreDirection(props.score,props.priorScore))
const valid=(v:number|null)=>v!==null&&Number.isFinite(v)
const x=(v:number)=>35+Math.max(0,Math.min(100,v))*6.3
const currentX=computed(()=>x(props.score??0))
const priorX=computed(()=>x(props.priorScore??0))
const nowLabelX=computed(()=>Math.max(90,Math.min(610,currentX.value)))
const thenLabelX=computed(()=>Math.max(90,Math.min(610,priorX.value)))
</script>
<template>
 <div class="risk-position" :class="direction.tone" aria-label="模型風險位置與變化方向">
  <div class="position-heading"><div><h3>模型警戒位置</h3><p>左側相對較低，右側相對較高</p></div><strong>{{direction.label}}</strong></div>
  <svg viewBox="0 0 700 160" role="img" :aria-label="`${date}：${score??'資料不足'} 分；${direction.label}`">
   <text x="35" y="15" class="end-label">較低風險</text><text x="665" y="15" text-anchor="end" class="end-label">較高風險</text>
   <rect x="35" y="62" width="409.5" height="18" rx="9" fill="#dcebe3"/>
   <rect x="444.5" y="62" width="31.5" height="18" fill="#eeddb6"/>
   <path d="M476 62H656A9 9 0 0 1 656 80H476Z" fill="#ebc5bf"/>
   <g v-for="mark in [0,50,65,70,100]" :key="mark"><line :x1="x(mark)" :x2="x(mark)" y1="60" y2="87" stroke="#829183" stroke-width=".8"/><text :x="x(mark)" :y="mark===70?112:100" text-anchor="middle" class="tick">{{mark}}</text></g>
   <template v-if="valid(priorScore)"><line :x1="priorX" :x2="priorX" y1="76" y2="121" stroke="#85948f" stroke-dasharray="3 3"/><circle :cx="priorX" cy="71" r="7" fill="#fff" stroke="#85948f" stroke-width="2"/><text :x="thenLabelX" y="138" text-anchor="middle" class="prior-label">前日 {{priorScore?.toFixed(2)}} 分</text></template>
   <template v-if="valid(score)">
    <template v-if="valid(priorScore)&&direction.delta!==0"><line :x1="priorX" :x2="currentX" y1="71" y2="71" :stroke="direction.tone==='easing'?'#3f7d65':'#b45f54'" stroke-width="3"/><path :d="`M${currentX+(direction.tone==='easing'?8:-8)},66 L${currentX},71 L${currentX+(direction.tone==='easing'?8:-8)},76`" fill="none" :stroke="direction.tone==='easing'?'#3f7d65':'#b45f54'" stroke-width="2"/></template>
    <line :x1="currentX" :x2="currentX" y1="46" y2="62" stroke="#3b6655"/><circle :cx="currentX" cy="71" r="8" fill="#3b6655" stroke="#fff" stroke-width="2"/><text :x="nowLabelX" y="39" text-anchor="middle" class="now-label">當日 {{score?.toFixed(2)}} 分</text>
   </template>
   <text v-else x="350" y="44" text-anchor="middle" class="now-label">資料不足，暫不標示位置</text>
  </svg>
  <div class="position-footer"><span>● 當日 {{date}}　○ 前日 {{priorDate||'無資料'}}</span><span>65／70 分為既有觀察線；分數不是下跌機率，也不表示低分就安全。</span></div>
 </div>
</template>
<style scoped>
.risk-position{border:1px solid #e1e9df;border-radius:12px;background:#fff;padding:18px 20px;margin:0 0 20px}.position-heading{display:flex;align-items:center;justify-content:space-between;gap:14px}.position-heading h3{margin:0;font-size:16px;color:#3b5748}.position-heading p{font-size:11px;color:#819080;margin:5px 0 0}.position-heading strong{font-size:14px;color:#778879}.easing .position-heading strong{color:#3f7d65}.rising .position-heading strong{color:#b45f54}svg{display:block;width:100%;max-width:950px;margin:12px auto 0}.end-label{font-size:12px;fill:#758776}.tick{font-size:10px;fill:#89958a}.now-label{font-size:14px;fill:#3b6655;font-weight:700}.prior-label{font-size:12px;fill:#85948f}.position-footer{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;color:#849184;font-size:10px;line-height:1.7}@media(max-width:600px){.risk-position{padding:14px 12px}.position-heading{align-items:start;flex-direction:column;gap:9px}svg{height:100px;min-height:0}.end-label{display:none}.now-label{font-size:30px}.prior-label{font-size:27px}.tick{font-size:24px}.position-footer{display:block}.position-footer span{display:block}}
</style>
