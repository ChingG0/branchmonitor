<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'

type Point={date:string,value:number|null;open_positions?:number;closed_positions?:number;pending_signals?:number;realized_value?:number|null;unrealized_value?:number}
type Series={key:string,label:string,color:string,curve:Point[]}
type Activity={date:string;buy:string[];sell:string[];hold?:string[]}
const props=defineProps<{series:Series[];unit?:string;activity?:Activity[]}>()
const hover=ref<number|null>(null),scrollArea=ref<HTMLDivElement|null>(null)
const dates=computed(()=>[...new Set(props.series.flatMap(series=>series.curve.map(point=>point.date)))].sort())
const values=computed(()=>props.series.flatMap(series=>series.curve.map(point=>point.value)).filter((value):value is number=>typeof value==='number'&&Number.isFinite(value)))
const lower=computed(()=>Math.min(0,...values.value)),upper=computed(()=>Math.max(1,...values.value))
const range=computed(()=>upper.value-lower.value||1)
const chartWidth=computed(()=>Math.max(760,dates.value.length/40*760)),plotWidth=computed(()=>chartWidth.value-100)
const widthPercent=computed(()=>Math.max(100,dates.value.length/40*100)+'%')
const hasActivity=computed(()=>Boolean(props.activity?.length))
const hasHoldActivity=computed(()=>Boolean(props.activity?.some(row=>row.hold!==undefined)))
const chartHeight=computed(()=>hasActivity.value?292:225)
const dateLabelY=computed(()=>hasActivity.value?284:218)
const x=(index:number)=>20+index/Math.max(1,dates.value.length-1)*plotWidth.value
const y=(value:number)=>180-(value-lower.value)/range.value*155
const activityByDate=computed(()=>new Map((props.activity||[]).map(row=>[row.date,row])))
const activityMax=computed(()=>Math.max(1,...dates.value.flatMap(date=>{const row=activityByDate.value.get(date);return [row?.buy.length||0,row?.sell.length||0]})))
const activityHeight=(count:number)=>count/activityMax.value*40
const barWidth=computed(()=>Math.max(3,Math.min(6,plotWidth.value/Math.max(1,dates.value.length-1)*.32)))
const rows=computed(()=>props.series.map(series=>{
 const pointByDate=new Map(series.curve.map(point=>[point.date,point])),byDate=new Map(series.curve.filter(point=>typeof point.value==='number'&&Number.isFinite(point.value)).map(point=>[point.date,point.value as number]))
 let path='',run=false
 dates.value.forEach((date,index)=>{const value=byDate.get(date);if(value===undefined){run=false;return}path+=`${run?' L':' M'}${x(index)},${y(value)}`;run=true})
 return {...series,byDate,pointByDate,path}
}))
const selected=computed(()=>{
 const index=hover.value;if(index===null)return null
 const date=dates.value[index]
 return {date,values:rows.value.map(row=>({...row,value:row.byDate.get(date),point:row.pointByDate.get(date)})),activity:activityByDate.value.get(date)}
})
const ticks=computed(()=>dates.value.map((_,index)=>index).filter(index=>index===0||index===dates.value.length-1||index%10===0))
const format=(value:number)=>value.toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2})
function move(event:PointerEvent){const rect=(event.currentTarget as SVGElement).getBoundingClientRect();const value=(event.clientX-rect.left)/rect.width*chartWidth.value;hover.value=Math.max(0,Math.min(dates.value.length-1,Math.round((value-20)/plotWidth.value*(dates.value.length-1))))}
watch(()=>[dates.value.length,dates.value.at(-1)],async()=>{await nextTick();if(scrollArea.value)scrollArea.value.scrollLeft=scrollArea.value.scrollWidth},{immediate:true})
</script>

<template>
 <div class="model-curve">
  <div v-if="!dates.length" class="chart-empty"><strong>尚無完整持有期間</strong><small>資料成熟後會顯示累積績效</small></div>
  <template v-else>
   <div v-if="selected" class="tooltip"><b>{{selected.date}}</b><span v-for="row in selected.values" :key="row.key"><i :style="{background:row.color}"></i>{{row.label}} {{row.value===undefined?'—':format(row.value)+(unit||'%')}}<small v-if="typeof row.point?.realized_value==='number'">已實現率 {{format(row.point.realized_value)}}% · 未實現率 {{format(row.point.unrealized_value||0)}}%</small><small v-if="row.point?.open_positions!==undefined">持有中 {{row.point.open_positions}} · 已完成 {{row.point.closed_positions||0}}<template v-if="row.point.pending_signals"> · 等待 {{row.point.pending_signals}}</template></small></span><template v-if="selected.activity"><template v-if="selected.activity.hold!==undefined"><template v-if="selected.activity.buy.length||selected.activity.sell.length"><span v-if="selected.activity.buy.length" class="activity-tip buy"><i></i>買入 {{selected.activity.buy.length}} 檔：{{selected.activity.buy.join('、')}}</span><span v-if="selected.activity.sell.length" class="activity-tip sell"><i></i>賣出 {{selected.activity.sell.length}} 檔：{{selected.activity.sell.join('、')}}</span></template><span v-else class="activity-tip hold"><i></i>當前持有 {{selected.activity.hold.length}} 檔：{{selected.activity.hold.join('、')||'無'}}</span></template><template v-else><span class="activity-tip buy"><i></i>買入 {{selected.activity.buy.length}} 檔：{{selected.activity.buy.join('、')||'無'}}</span><span class="activity-tip sell"><i></i>賣出 {{selected.activity.sell.length}} 檔：{{selected.activity.sell.join('、')||'無'}}</span></template></template></div>
   <div v-if="hasActivity" class="activity-legend"><b>每日買賣數</b><span class="buy"><i></i>買入</span><span class="sell"><i></i>賣出</span><span v-if="hasHoldActivity" class="hold"><i></i>無交易時顯示持有</span><small>移動滑鼠查看代號</small></div>
   <div ref="scrollArea" class="scroll">
    <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" :style="{width:widthPercent}" role="img" aria-label="模型累積績效走勢與每日買賣訊號數" @pointermove="move" @pointerleave="hover=null">
     <g v-for="value in [upper,(upper+lower)/2,lower]" :key="value"><line x1="20" :x2="chartWidth-80" :y1="y(value)" :y2="y(value)" stroke="#e9edeb" stroke-dasharray="3 4"/><text :x="chartWidth-62" :y="y(value)+4" fill="#87938c" font-size="12">{{format(value)}}%</text></g>
     <line v-if="lower<0" x1="20" :x2="chartWidth-80" :y1="y(0)" :y2="y(0)" stroke="#c4cec8"/>
     <path v-for="row in rows" :key="row.key" :d="row.path" fill="none" :stroke="row.color" stroke-width="2.7" stroke-linejoin="round"/>
     <template v-for="row in rows" :key="row.key+'dots'"><circle v-for="(date,index) in dates" v-show="row.byDate.has(date)" :key="date" :cx="x(index)" :cy="y(row.byDate.get(date)??0)" r="2.8" :fill="row.color" stroke="white" stroke-width="1.2"/></template>
     <g v-if="hasActivity">
      <line x1="20" :x2="chartWidth-80" y1="205" y2="205" stroke="#edf0ed"/>
      <line x1="20" :x2="chartWidth-80" y1="254" y2="254" stroke="#cbd4ce"/>
      <template v-for="(date,index) in dates" :key="date+'activity'">
       <rect :x="x(index)-barWidth-1" :y="254-activityHeight(activityByDate.get(date)?.buy.length||0)" :width="barWidth" :height="activityHeight(activityByDate.get(date)?.buy.length||0)" rx="1.5" fill="#2d7657"/>
       <rect :x="x(index)+1" :y="254-activityHeight(activityByDate.get(date)?.sell.length||0)" :width="barWidth" :height="activityHeight(activityByDate.get(date)?.sell.length||0)" rx="1.5" fill="#bf5b50"/>
      </template>
      <text :x="chartWidth-62" y="218" fill="#87938c" font-size="11">{{activityMax}} 檔</text>
      <text :x="chartWidth-62" y="258" fill="#87938c" font-size="11">0</text>
     </g>
     <line v-if="hover!==null" :x1="x(hover)" :x2="x(hover)" y1="16" :y2="hasActivity?254:185" stroke="#a8b4ac" stroke-dasharray="3 3"/>
     <text v-for="index in ticks" :key="index" :x="x(index)" :y="dateLabelY" :text-anchor="index===0?'start':index===dates.length-1?'end':'middle'" fill="#87938c" font-size="12">{{dates[index]?.slice(5).replace('-','/')}}</text>
    </svg>
   </div>
  </template>
 </div>
</template>

<style scoped>
.model-curve{position:relative;min-height:235px}.scroll{overflow-x:auto;overflow-y:hidden;padding:5px 12px 0}.scroll svg{display:block;min-width:760px}.tooltip{position:absolute;z-index:3;right:18px;top:8px;max-width:min(760px,calc(100% - 36px));background:#fffefbea;border:1px solid #dde4da;border-radius:10px;padding:8px 10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:11px;color:#667363;box-shadow:0 5px 18px #40504018;pointer-events:none}.tooltip span{white-space:nowrap}.tooltip span small{display:block;margin:2px 0 0 12px;color:#8a958d}.tooltip i,.activity-legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}.activity-tip.buy i,.activity-legend .buy i{background:#2d7657}.activity-tip.sell i,.activity-legend .sell i{background:#bf5b50}.activity-tip.hold i,.activity-legend .hold i{background:#7d8eaa}.activity-legend{display:flex;align-items:center;gap:12px;padding:4px 20px 0;color:#6d7a70;font-size:11px}.activity-legend b{color:#44554a}.activity-legend small{color:#929c94}.chart-empty{height:220px;display:flex;flex-direction:column;justify-content:center;align-items:center;color:#829080;gap:5px}.chart-empty small{color:#9aa49a}@media(max-width:760px){.tooltip{position:static;margin:10px;max-width:none}.activity-legend{flex-wrap:wrap}}
</style>
