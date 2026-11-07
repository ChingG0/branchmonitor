<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'
import type {Quote,Flow} from './types'
const props=defineProps<{quotes:Quote[];flows:Flow[]}>()
const data=computed(()=>props.quotes)
const scrollArea=ref<HTMLDivElement|null>(null)
const visibleStart=ref(Math.max(0,props.quotes.length-40))
const visibleData=computed(()=>data.value.slice(visibleStart.value,visibleStart.value+40))
const chartWidth=computed(()=>Math.max(680,data.value.length/40*680))
const widthPercent=computed(()=>Math.max(100,data.value.length/40*100)+'%')
const min=computed(()=>Math.min(...visibleData.value.map(q=>q.low||q.close))*0.985)
const max=computed(()=>Math.max(...visibleData.value.map(q=>q.high||q.close))*1.015)
const x=(i:number)=>22+i/Math.max(1,data.value.length-1)*(chartWidth.value-90)
const y=(v:number)=>178-(v-min.value)/(max.value-min.value||1)*155
const barWidth=computed(()=>Math.min(11,(chartWidth.value-120)/Math.max(1,data.value.length)*.72))
const nets=computed(()=>Object.fromEntries(props.flows.map(f=>[f.date,f.net])))
const peak=computed(()=>Math.max(1,...visibleData.value.map(q=>Math.abs(nets.value[q.date]||0))))
const ticks=computed(()=>data.value.map((_,i)=>i).filter(i=>i===0||i===data.value.length-1||i%10===0))
function scrolled(){const el=scrollArea.value;if(!el)return;const maxScroll=el.scrollWidth-el.clientWidth;visibleStart.value=maxScroll?Math.round(el.scrollLeft/maxScroll*Math.max(0,data.value.length-40)):0}
watch(()=>[data.value.length,data.value.at(-1)?.date],async()=>{visibleStart.value=Math.max(0,data.value.length-40);await nextTick();if(scrollArea.value)scrollArea.value.scrollLeft=scrollArea.value.scrollWidth},{immediate:true})
</script>
<template><div v-if="data.length" ref="scrollArea" class="candles candles-scroll" aria-label="日期捲軸，預設顯示最近 40 個交易日" @scroll="scrolled"><svg :viewBox="`0 0 ${chartWidth} 300`" :style="{width:widthPercent}" role="img" aria-label="個股日K線與分點淨買賣超，紅買綠賣">
 <g v-for="v in [max,(min+max)/2,min]" :key="v"><line x1="12" :x2="chartWidth-55" :y1="y(v)" :y2="y(v)" stroke="#e5eae7" stroke-dasharray="3 4"/><text :x="chartWidth-45" :y="y(v)+4" font-size="12" fill="#819087">{{v.toFixed(1)}}</text></g>
 <g v-for="(q,i) in data" :key="q.date"><title>{{q.date}} 收 {{q.close}} / 淨買超 {{nets[q.date]??'未記錄'}} 張</title><line :x1="x(i)" :x2="x(i)" :y1="y(q.high||q.close)" :y2="y(q.low||q.close)" :stroke="q.close>=(q.open||q.close)?'#c55c50':'#2b8a65'"/><rect :x="x(i)-barWidth/2" :y="Math.min(y(q.open||q.close),y(q.close))" :width="barWidth" :height="Math.max(1,Math.abs(y(q.close)-y(q.open||q.close)))" :fill="q.close>=(q.open||q.close)?'#c55c50':'#2b8a65'"/>
 <rect v-if="nets[q.date]" :x="x(i)-barWidth/2" :y="nets[q.date]>0?244-nets[q.date]/peak*38:244" :width="barWidth" :height="Math.abs(nets[q.date])/peak*38" :fill="nets[q.date]>0?'#c55c50':'#2b8a65'"/></g>
 <line x1="12" :x2="chartWidth-55" y1="244" y2="244" stroke="#cdd6d0"/><text :x="chartWidth-45" y="248" font-size="12" fill="#819087">0 張</text><text v-for="i in ticks" :key="data[i].date" :x="x(i)" y="299" :text-anchor="i===0?'start':i===data.length-1?'end':'middle'" font-size="12" fill="#819087">{{data[i].date}}</text>
 </svg></div><div v-else class="chart-empty">缺少行情資料</div></template>
