<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'
type Point={month:string;value:number}
const props=defineProps<{points:Point[];kind:string;unit:string;label:string;color:string}>()
const scroll=ref<HTMLElement|null>(null)
const months=computed(()=>{const rows=props.points;if(!rows.length)return [];const out:{month:string;value:number|null}[]=[];const by=new Map(rows.map(p=>[p.month,p.value]));let d=new Date(rows[0].month+'-01T00:00:00Z');const end=rows.at(-1)!.month;while(d.toISOString().slice(0,7)<=end){const m=d.toISOString().slice(0,7);out.push({month:m,value:by.get(m)??null});d.setUTCMonth(d.getUTCMonth()+1)}return out})
const width=computed(()=>Math.max(620,months.value.length*38+85))
const reference=computed(()=>props.kind==='pmi'?50:props.kind==='signal'?null:0)
const values=computed(()=>props.points.map(p=>p.value))
const lower=computed(()=>props.kind==='signal'?9:Math.min(...values.value,reference.value??Infinity)-Math.max(1,(Math.max(...values.value)-Math.min(...values.value))*.12))
const upper=computed(()=>props.kind==='signal'?45:Math.max(...values.value,reference.value??-Infinity)+Math.max(1,(Math.max(...values.value)-Math.min(...values.value))*.12))
const x=(i:number)=>48+i*(width.value-75)/Math.max(1,months.value.length-1)
const y=(v:number)=>190-(v-lower.value)/(upper.value-lower.value||1)*164
const paths=computed(()=>{const out:string[]=[];let path='';months.value.forEach((p,i)=>{if(p.value===null){if(path)out.push(path);path=''}else path+=`${path?' L':'M'}${x(i)},${y(p.value)}`});if(path)out.push(path);return out})
const ticks=computed(()=>[...new Set([0,...months.value.map((_,i)=>i).filter(i=>i%Math.max(1,Math.floor(months.value.length/8))===0),months.value.length-1])])
const bands=[{lo:9,hi:16,color:'#dce9f6'},{lo:16,hi:22,color:'#e7ece7'},{lo:22,hi:31,color:'#deeee5'},{lo:31,hi:37,color:'#f8edce'},{lo:37,hi:45,color:'#f5ded9'}]
watch(()=>props.points,async()=>{await nextTick();if(scroll.value)scroll.value.scrollLeft=scroll.value.scrollWidth},{immediate:true})
</script>
<template>
 <div v-if="points.length" class="chart-wrap"><small>可左右捲動，指向節點查看月份與數值</small><div ref="scroll" class="chart-scroll" tabindex="0" :aria-label="label+'月資料走勢，可左右捲動'"><svg :viewBox="`0 0 ${width} 229`" :style="{width:width+'px'}" height="229" role="img" :aria-label="`${label}，${points.length}筆月資料`">
 <g v-if="kind==='signal'"><rect v-for="b in bands" :key="b.lo" x="48" :width="width-75" :y="y(b.hi)" :height="y(b.lo)-y(b.hi)" :fill="b.color"/></g>
 <g v-for="mark in [lower,(lower+upper)/2,upper]" :key="mark"><line x1="48" :x2="width-27" :y1="y(mark)" :y2="y(mark)" stroke="#dce5db" stroke-dasharray="3 4"/><text x="3" :y="y(mark)+4">{{mark.toFixed(1)}}</text></g>
 <g v-if="reference!==null"><line x1="48" :x2="width-27" :y1="y(reference)" :y2="y(reference)" stroke="#a38a60" stroke-dasharray="5 4"/><text :x="width-31" :y="y(reference)-5" text-anchor="end">{{reference}}{{unit}} 參考線</text></g>
 <path v-for="(p,i) in paths" :key="i" :d="p" fill="none" :stroke="color" stroke-width="2.5" stroke-linejoin="round"/>
 <template v-for="(p,i) in months" :key="p.month"><circle v-if="p.value!==null" :cx="x(i)" :cy="y(p.value)" r="3.5" :fill="color" stroke="white" stroke-width="1.3"><title>{{p.month}}：{{p.value.toLocaleString('zh-TW',{maximumFractionDigits:2})}}{{unit}}</title></circle></template>
 <text v-for="i in ticks" :key="i" :x="x(i)" y="217" :text-anchor="i===0?'start':i===months.length-1?'end':'middle'">{{months[i]?.month}}</text>
 </svg></div></div>
 <p v-else class="empty">尚無可繪製的月資料，請手動更新。</p>
</template>
<style scoped>
.chart-wrap{margin-top:17px;min-width:0}.chart-wrap>small{font-size:10px;color:#8a9885}.chart-scroll{overflow-x:auto;max-width:100%;margin-top:7px;overscroll-behavior-x:contain}.chart-scroll svg{display:block;max-width:none}.chart-scroll text{font:10px sans-serif;fill:#85917d}.empty{font-size:12px;color:#8d9887;margin:25px 0}
</style>
