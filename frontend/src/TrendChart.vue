<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

type Point={date:string;value:number|null;secondary?:number|null}
const props=defineProps<{points:Point[];unit?:string;color?:string;primaryLabel?:string;secondaryLabel?:string;min?:number;max?:number}>()
const hover=ref<number|null>(null)
const scrollArea=ref<HTMLDivElement|null>(null)
const visibleStart=ref(Math.max(0,props.points.length-40))
const visiblePoints=computed(()=>props.points.slice(visibleStart.value,visibleStart.value+40))
const valid=computed(()=>visiblePoints.value.flatMap(p=>[p.value,p.secondary]).filter((v):v is number=>v!==null&&v!==undefined))
const lower=computed(()=>props.min??Math.min(0,...valid.value))
const upper=computed(()=>props.max??Math.max(1,...valid.value))
const range=computed(()=>upper.value-lower.value||1)
const chartWidth=computed(()=>Math.max(760,props.points.length/40*760))
const plotWidth=computed(()=>chartWidth.value-100)
const widthPercent=computed(()=>Math.max(100,props.points.length/40*100)+'%')
const x=(i:number)=>20+i/Math.max(1,props.points.length-1)*plotWidth.value
const y=(v:number)=>180-(v-lower.value)/range.value*155
function buildPaths(key:'value'|'secondary'){
 const segments:string[]=[];let run=''
 props.points.forEach((p,i)=>{const value=p[key];if(value===null||value===undefined){if(run)segments.push(run);run=''}else run+=`${run?' L':'M'}${x(i)},${y(value)}`})
 if(run)segments.push(run);return segments
}
const paths=computed(()=>buildPaths('value'))
const secondaryPaths=computed(()=>buildPaths('secondary'))
const selected=computed(()=>hover.value!==null?props.points[hover.value]:null)
const ticks=computed(()=>props.points.map((_,i)=>i).filter(i=>i===0||i===props.points.length-1||i%10===0))
const format=(v:number)=>Math.abs(v)>=10000?(v/10000).toFixed(1)+'萬':v.toLocaleString('zh-TW',{maximumFractionDigits:1})
function move(e:PointerEvent){const rect=(e.currentTarget as SVGElement).getBoundingClientRect();const v=(e.clientX-rect.left)/rect.width*chartWidth.value;hover.value=Math.max(0,Math.min(props.points.length-1,Math.round((v-20)/plotWidth.value*(props.points.length-1))))}
function scrolled(){const el=scrollArea.value;if(!el)return;const maxScroll=el.scrollWidth-el.clientWidth;visibleStart.value=maxScroll?Math.round(el.scrollLeft/maxScroll*Math.max(0,props.points.length-40)):0}
watch(()=>[props.points.length,props.points.at(-1)?.date],async()=>{visibleStart.value=Math.max(0,props.points.length-40);await nextTick();if(scrollArea.value)scrollArea.value.scrollLeft=scrollArea.value.scrollWidth},{immediate:true})
</script>
<template>
 <div class="trend-chart">
  <div v-if="!valid.length" class="chart-empty"><span>尚無可繪製的資料</span><small>同步完成後，走勢會顯示在這裡</small></div>
  <template v-else>
   <div class="chart-tooltip" v-if="selected">{{selected.date}} <strong>{{primaryLabel}} {{selected.value===null?'資料不足':format(selected.value)}} {{unit}}</strong><strong v-if="secondaryLabel">{{secondaryLabel}} {{selected.secondary===null||selected.secondary===undefined?'資料不足':format(selected.secondary)}} {{unit}}</strong></div>
   <div ref="scrollArea" class="trend-scroll" aria-label="日期捲軸，預設顯示最近 40 個交易日" @scroll="scrolled">
    <svg :viewBox="`0 0 ${chartWidth} 225`" :style="{width:widthPercent}" role="img" :aria-label="`${unit||''}歷史趨勢，共${points.length}日`" @pointermove="move" @pointerleave="hover=null">
     <g v-for="v in [upper,(upper+lower)/2,lower]" :key="v"><line x1="20" :x2="chartWidth-80" :y1="y(v)" :y2="y(v)" stroke="#e9edeb" stroke-dasharray="3 4"/><text :x="chartWidth-62" :y="y(v)+4" fill="#87938c" font-size="12">{{format(v)}}</text></g>
     <line v-if="lower<0" x1="20" :x2="chartWidth-80" :y1="y(0)" :y2="y(0)" stroke="#c4cec8"/>
     <path v-for="(path,i) in paths" :key="'p'+i" :d="path" fill="none" :stroke="color||'#2d6c4f'" stroke-width="2.5" stroke-linejoin="round"/>
     <path v-for="(path,i) in secondaryPaths" :key="'s'+i" :d="path" fill="none" stroke="#b58a43" stroke-width="2.5" stroke-linejoin="round"/>
     <circle v-for="(point,i) in points" v-show="point.value!==null" :key="'pd'+i" :cx="x(i)" :cy="point.value===null?0:y(point.value)" r="2.8" :fill="color||'#2d6c4f'" stroke="white" stroke-width="1.2"/>
     <circle v-for="(point,i) in points" v-show="point.secondary!==null&&point.secondary!==undefined" :key="'sd'+i" :cx="x(i)" :cy="point.secondary===null||point.secondary===undefined?0:y(point.secondary)" r="2.8" fill="#b58a43" stroke="white" stroke-width="1.2"/>
     <g v-if="selected&&hover!==null"><line :x1="x(hover)" :x2="x(hover)" y1="16" y2="185" stroke="#a8b4ac" stroke-dasharray="3 3"/><circle v-if="selected.value!==null" :cx="x(hover)" :cy="y(selected.value)" r="4" fill="#2d6c4f" stroke="white" stroke-width="2"/><circle v-if="selected.secondary!==null&&selected.secondary!==undefined" :cx="x(hover)" :cy="y(selected.secondary)" r="4" fill="#b58a43" stroke="white" stroke-width="2"/></g>
     <text v-for="i in ticks" :key="i" :x="x(i)" y="218" :text-anchor="i===0?'start':i===points.length-1?'end':'middle'" fill="#87938c" font-size="12">{{points[i]?.date.slice(5).replace('-','/')}}</text>
    </svg>
   </div>
  </template>
 </div>
</template>
