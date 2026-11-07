<script setup lang="ts">
import {computed} from 'vue'
import {readingComparison,flowChange,flowReadings} from './marketComparison'
import type {FlowReading} from './marketComparison'
const props=defineProps<{name:string;net:number;date:string;history?:FlowReading[]}>()
const comparison=computed(()=>readingComparison(flowReadings(props.history||[],props.name),props.date,props.net))
const fmt=(n:number)=>`${n>0?'+':''}${(n/1e8).toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2})}`
const low=computed(()=>Math.min(0,...comparison.value.readings.map(r=>r.value)))
const high=computed(()=>Math.max(0,...comparison.value.readings.map(r=>r.value)))
const x=(i:number)=>8+224*i/Math.max(1,comparison.value.readings.length-1)
const y=(n:number)=>high.value===low.value?33:57-48*(n-low.value)/(high.value-low.value)
const line=computed(()=>comparison.value.readings.map((r,i)=>`${x(i)},${y(r.value)}`).join(' '))
const color=computed(()=>props.net>0?'#bd6255':props.net<0?'#258465':'#829087')
</script>
<template>
 <section class="flow-trend">
  <template v-if="comparison.previous">
   <div class="flow-previous"><span>前日 {{fmt(comparison.previous.value)}} 億<small>{{comparison.previous.date}}</small></span><span class="flow-change">{{flowChange(comparison.previous.value,net)}}<b>較前日 {{fmt(comparison.delta!)}} 億</b></span></div>
   <svg viewBox="0 0 240 68" role="img" :aria-label="`${name}最近 ${comparison.readings.length} 個來源日買賣超；${flowChange(comparison.previous.value,net)}`">
    <line x1="8" x2="232" :y1="y(0)" :y2="y(0)" stroke="#dce5db" stroke-dasharray="3 4"/>
    <polyline :points="line" fill="none" stroke="#91a4ad" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle :cx="x(comparison.readings.length-2)" :cy="y(comparison.previous.value)" r="3.5" fill="white" stroke="#869996" stroke-width="1.8"/>
    <circle :cx="x(comparison.readings.length-1)" :cy="y(net)" r="4.2" :fill="color" stroke="white" stroke-width="1.5"/>
   </svg>
   <div class="flow-period"><span>{{comparison.readings[0].date.slice(5)}}～{{date.slice(5)}} · {{comparison.readings.length}} 個來源日</span><span>○ 前日　● 當日 · 虛線為零</span></div>
  </template>
  <small v-else>待累積前一交易日資料</small>
 </section>
</template>
<style scoped>
.flow-trend{width:100%;margin-top:15px;padding:12px 0 2px;border-top:1px dashed #e2e8dc}.flow-previous{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#72836d}.flow-previous small{display:block;margin-top:5px;font-size:10px;color:#93a08a}.flow-change{text-align:right;color:#496759}.flow-change b{display:block;font-size:11px;font-weight:500;margin-top:5px}.flow-trend svg{display:block;width:100%;height:65px;margin-top:7px}.flow-period{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;color:#95a18e;font-size:9px}.flow-trend>small{color:#95a18e;font-size:11px}
</style>
