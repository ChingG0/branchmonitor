<script setup lang="ts">
import {computed} from 'vue'
import {readingComparison} from './marketComparison'
import type {ValueReading} from './marketComparison'
import {formatObservation,formatObservationChange} from './v6Format'
const props=withDefaults(defineProps<{value?:number|null;date:string;history?:ValueReading[];unit?:string;digits?:number;intraday?:boolean;zeroLine?:boolean;label?:string}>(),{unit:'',intraday:false,zeroLine:false,label:''})
const view=computed(()=>readingComparison(props.history||[],props.date,props.value))
const format=(n:number)=>props.digits!=null?`${n.toLocaleString('zh-TW',{maximumFractionDigits:props.digits})}${props.unit==='%'?'%':props.unit?' '+props.unit:''}`:formatObservation(n,props.unit,props.label)
const delta=computed(()=>view.value.delta===null?'':props.digits!=null&&props.unit!=='%'?`${view.value.delta>0?'+':''}${format(view.value.delta)}`:formatObservationChange(view.value.delta,props.unit,props.label))
const low=computed(()=>Math.min(...view.value.readings.map(r=>r.value),...(props.zeroLine?[0]:[])))
const high=computed(()=>Math.max(...view.value.readings.map(r=>r.value),...(props.zeroLine?[0]:[])))
const x=(i:number)=>8+224*i/Math.max(1,view.value.readings.length-1)
const y=(n:number)=>high.value===low.value?31:54-44*(n-low.value)/(high.value-low.value)
const line=computed(()=>view.value.readings.map((r,i)=>`${x(i)},${y(r.value)}`).join(' '))
</script>
<template>
 <div class="value-trend">
  <template v-if="view.previous&&value!=null">
   <div class="value-comparison"><span>○ 前值 {{format(view.previous.value)}}<small>{{view.previous.date}}</small></span><span>● {{intraday?'目前':'當日'}} {{format(value)}}<small>{{date}}</small></span></div>
   <p class="value-change">{{view.delta!>0?'↑':view.delta!<0?'↓':'→'}} 較前一來源日 {{delta}}</p>
   <svg viewBox="0 0 240 66" role="img" :aria-label="`${label}前值 ${format(view.previous.value)}，目前 ${format(value)}，增減 ${delta}`">
    <line x1="8" x2="232" :y1="zeroLine?y(0):59" :y2="zeroLine?y(0):59" stroke="#dce5dd" :stroke-dasharray="zeroLine?'3 4':undefined"/>
    <polyline :points="line" fill="none" stroke="#91a5ae" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <circle :cx="x(view.readings.length-2)" :cy="y(view.previous.value)" r="3.7" fill="white" stroke="#879b98" stroke-width="1.8"/>
    <circle :cx="x(view.readings.length-1)" :cy="y(value)" r="4.2" fill="#42766c" stroke="white" stroke-width="1.4"/>
   </svg>
   <small class="value-period">{{view.readings[0].date.slice(5)}}～{{date.slice(5)}} · {{view.readings.length}} 個來源日{{intraday?' · 含目前報價':''}}</small>
  </template>
  <p v-else class="history-pending">前值尚未取得，後續更新會持續累積同口徑資料。</p>
 </div>
</template>
<style scoped>
.value-trend{margin:16px 0 0;padding-top:13px;border-top:1px solid #e6ece3}.value-comparison{display:flex;justify-content:space-between;gap:10px;font-size:11px;color:#7c8b77}.value-comparison>span{min-width:0;overflow-wrap:anywhere}.value-comparison>span:last-child{text-align:right;color:#476a59}.value-comparison small{display:block;font-size:9px;margin-top:5px;color:#93a08b}.value-change{font-size:11px;color:#466e60;margin:11px 0 4px;line-height:1.7}.value-trend svg{display:block;width:100%;height:68px}.value-period,.history-pending{font-size:10px;line-height:1.6;color:#8d9c86}.history-pending{margin:0}
</style>
