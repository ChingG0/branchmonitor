<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import type {ValueReading} from './marketComparison'
const props=defineProps<{points:ValueReading[];label:string;unit:string}>()
const active=ref<number|null>(null)
watch(()=>props.points,()=>{active.value=null})
const rows=computed(()=>props.points.filter(p=>Number.isFinite(p.value)).slice(-60))
const low=computed(()=>Math.min(...rows.value.map(p=>p.value)))
const high=computed(()=>Math.max(...rows.value.map(p=>p.value)))
const pad=computed(()=>Math.max((high.value-low.value)*.12,props.unit==='bp'?1:.05))
const x=(i:number)=>28+244*i/Math.max(1,rows.value.length-1)
const y=(v:number)=>92-70*(v-low.value+pad.value)/(high.value-low.value+pad.value*2)
const line=computed(()=>rows.value.map((p,i)=>`${x(i)},${y(p.value)}`).join(' '))
const shown=computed(()=>rows.value[active.value??rows.value.length-1])
const pointIndex=computed(()=>active.value??rows.value.length-1)
const fmt=(v:number)=>v.toLocaleString('zh-TW',{maximumFractionDigits:props.unit==='bp'?1:3})
function move(event:PointerEvent){const rect=(event.currentTarget as SVGElement).getBoundingClientRect();const at=(event.clientX-rect.left)/rect.width*300;active.value=Math.max(0,Math.min(rows.value.length-1,Math.round((at-28)/244*(rows.value.length-1))))}
</script>
<template>
 <div class="bond-chart" v-if="shown">
  <div class="chart-reading"><span>{{shown.date}}</span><strong>{{fmt(shown.value)}} {{unit}}</strong></div>
  <svg viewBox="0 0 300 116" role="img" tabindex="0" :aria-label="`${label}，${rows[0]?.date}至${rows[rows.length-1]?.date}，共${rows.length}個來源日`" @pointermove="move" @pointerleave="active=null" @blur="active=null" @keydown.left.prevent="active=Math.max(0,pointIndex-1)" @keydown.right.prevent="active=Math.min(rows.length-1,pointIndex+1)">
   <line x1="28" x2="272" :y1="y(high)" :y2="y(high)" stroke="#e4e9e0" stroke-dasharray="3 4"/>
   <line x1="28" x2="272" :y1="y(low)" :y2="y(low)" stroke="#e4e9e0" stroke-dasharray="3 4"/>
   <text x="28" y="12">{{fmt(high)}}</text><text x="28" y="106">{{fmt(low)}}</text>
   <polyline :points="line" fill="none" stroke="#748c98" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
   <line v-if="active!==null" :x1="x(pointIndex)" :x2="x(pointIndex)" y1="18" y2="94" stroke="#9da99b" stroke-dasharray="2 3"/>
   <circle :cx="x(pointIndex)" :cy="y(shown.value)" r="4" fill="#426e61" stroke="white" stroke-width="1.5"/>
  </svg>
  <div class="chart-period"><span>{{rows[0]?.date.slice(5)}}</span><span>{{rows.length}} 日{{rows.length===1?' · 待累積':''}}</span><span>{{rows[rows.length-1]?.date.slice(5)}}</span></div>
 </div>
</template>
<style scoped>
.bond-chart{margin-top:12px;padding-top:10px;border-top:1px solid #e3e9de}.chart-reading,.chart-period{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:#8c9985}.chart-reading strong{font-weight:500;color:#526e62;font-variant-numeric:tabular-nums}.bond-chart svg{display:block;width:100%;height:auto;max-height:140px;outline-offset:2px}.bond-chart text{font:9px sans-serif;fill:#9ba697}.chart-period{font-size:9px}
</style>
