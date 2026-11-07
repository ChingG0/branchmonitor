<script setup lang="ts">
import {computed} from 'vue'
import type {HistoricalIndicator,HistoricalPoint} from './v6HistoryTypes'
import {indicatorState,indicatorPalette} from './v6IndicatorState'
import {observationVisual} from './v6ObservationVisual'
import {formatObservation,formatObservationChange} from './v6Format'
const props=defineProps<{item:HistoricalIndicator;points:HistoricalPoint[];current:HistoricalPoint}>()
const visual=computed(()=>observationVisual(props.item,props.points,props.current))
const status=computed(()=>indicatorState(props.item,visual.value))
const palette=computed(()=>indicatorPalette[status.value.tone])
const barGradient=computed(()=>{const v=visual.value;if(v.min===null||v.max===null||v.min===v.max)return '#e1e8eb';return 'linear-gradient(90deg,'+Array.from({length:21},(_,i)=>{const sample=v.min!+(v.max!-v.min!)*i/20;return indicatorPalette[indicatorState(props.item,v,sample).tone].color+' '+i*5+'%'}).join(',')+')'})
const x=(i:number)=>10+220*i/Math.max(1,visual.value.readings.length-1)
const y=(v:number)=>{const {min,max}=visual.value;return min===null||max===null||min===max?26:42-32*(v-min)/(max-min)}
const polyline=computed(()=>visual.value.readings.map((row,i)=>`${x(i)},${y(row.value)}`).join(' '))
const trend=computed(()=>visual.value.reused?'沿用來源日，無新觀測':visual.value.delta===null?'歷史不足，待累積':visual.value.delta>0?'↑ 數值走高':visual.value.delta<0?'↓ 數值走低':'→ 數值持平')
const format=(n:number)=>formatObservation(n,props.item.unit,props.item.label)
const change=computed(()=>visual.value.delta===null?'':formatObservationChange(visual.value.delta,props.item.unit,props.item.label))
const priorIndex=computed(()=>visual.value.readings.findIndex(row=>row.date===visual.value.prior?.date))
const priorPosition=computed(()=>{const v=visual.value;return v.prior&&v.min!==null&&v.max!==null&&v.max>v.min?(v.prior.value-v.min)/(v.max-v.min)*100:null})
</script>
<template>
 <div class="observation-trend" :data-tone="status.tone" :style="{'--indicator-color':palette.color,'--indicator-bg':palette.background}" :aria-label="`${item.label}：${palette.label}；${status.reason}；${trend}`">
  <div class="status-row"><span class="status-badge">{{palette.label}}</span><span v-if="visual.reused" class="source-reused">沿用</span><details class="status-rule"><summary :aria-label="`${item.label}的顏色判讀依據`">依據</summary><p>{{status.basis}}</p><a v-if="item.label==='VIX'" href="https://cdn.cboe.com/resources/vix/SandP%20A%20Practitioners%20Guide%20to%20Reading%20VIX.pdf" target="_blank" rel="noopener noreferrer">S&amp;P／Cboe 判讀說明 ↗</a></details></div>
  <p class="status-reason">{{status.reason}}</p>
  <div class="trend-caption"><span>{{trend}}</span><small>{{visual.position===null?'':visual.position>=75?'近期高位':visual.position<=25?'近期低位':'近期區間內'}}</small></div>
  <div v-if="visual.prior&&visual.value!==null" class="comparison-values"><span>○ 前值 {{format(visual.prior.value)}}<small>{{visual.prior.date}}</small></span><span>● 當值 {{format(visual.value)}}<small>{{item.as_of}}</small></span></div>
  <p v-if="visual.delta!==null" class="comparison-change">較前一來源日 {{change}}</p>
  <template v-if="visual.readings.length>=2&&visual.value!==null">
   <svg viewBox="0 0 240 54" role="img" :aria-label="`截至 ${item.as_of}，最近 ${visual.readings.length} 筆來源觀測走勢`"><line x1="10" x2="230" y1="46" y2="46" stroke="#e7ede5"/><polyline :points="polyline" fill="none" stroke="#8a9eaa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle v-if="visual.prior" :cx="x(priorIndex)" :cy="y(visual.prior.value)" r="3.5" fill="white" stroke="#879b98" stroke-width="1.7"/><circle :cx="x(visual.readings.length-1)" :cy="y(visual.value)" r="4" :fill="palette.color" stroke="white" stroke-width="1.5"/></svg>
   <div class="range-track" :style="{background:barGradient}" aria-label="目前數值在近期高低範圍的位置"><i v-if="priorPosition!==null" class="prior-position" :style="{left:priorPosition+'%'}"/><i v-if="visual.position!==null" :style="{left:visual.position+'%'}"/><i v-else class="flat"/></div>
   <div class="range-labels"><span>低 {{format(visual.min!)}}</span><span>高 {{format(visual.max!)}}</span></div>
   <small class="range-period">{{visual.readings[0].date.slice(5)}}～{{item.as_of.slice(5)}} · {{visual.readings.length}} 筆來源日</small>
  </template>
  <p v-else>尚無足夠歷史資料繪圖</p>
 </div>
</template>
<style scoped>
.observation-trend{margin-top:11px;padding-top:10px;border-top:1px solid #edf1eb}.trend-caption,.range-labels{display:flex;justify-content:space-between;align-items:center;gap:8px}.trend-caption span{font-size:11px;color:#536d7a;font-weight:600}.trend-caption small{font-size:10px;color:#8a978a;white-space:nowrap}svg{display:block;width:100%;height:60px}.range-track{position:relative;height:4px;margin:4px 4px 8px;border-radius:4px;background:#e1e8eb}.range-track i{position:absolute;top:-3px;width:3px;height:10px;background:var(--indicator-color);border:1px solid white;box-sizing:content-box;border-radius:2px;transform:translateX(-50%)}.status-row{display:flex;align-items:center;gap:6px}.status-badge{font-size:11px;font-weight:700;color:var(--indicator-color);background:var(--indicator-bg);border-radius:20px;padding:4px 9px}.source-reused{font-size:9px;color:#8c9690}.status-rule{position:relative;margin-left:auto;font-size:10px;color:#7d8b7b}.status-rule summary{cursor:pointer;list-style:none;text-decoration:underline;text-underline-offset:3px}.status-rule summary::-webkit-details-marker{display:none}.status-rule[open]{position:static}.status-rule p{font-size:10px;line-height:1.7;color:#748170;overflow-wrap:anywhere}.status-row:has(.status-rule[open]){flex-wrap:wrap}.status-rule[open]{flex:1 0 100%;order:3}.status-reason{font-size:11px!important;line-height:1.6;color:var(--indicator-color)!important;margin:7px 0 10px}.range-track i.flat{left:50%;background:#adb9af}.range-labels span{font-size:9px;color:#8e9b8c;overflow-wrap:anywhere;min-width:0}.range-labels span:last-child{text-align:right}.range-period{display:block;font-size:9px;color:#95a18f;margin-top:5px}.observation-trend p{font-size:10px;color:#95a18f}
.comparison-values{display:flex;justify-content:space-between;gap:8px;margin-top:11px;font-size:10px;color:#718475}.comparison-values>span{min-width:0;overflow-wrap:anywhere}.comparison-values>span:last-child{text-align:right;color:var(--indicator-color)}.comparison-values small{display:block;font-size:9px;color:#97a18f;margin-top:4px}.observation-trend .comparison-change{font-size:11px;color:var(--indicator-color);font-weight:600;margin:7px 0 0}.range-track i.prior-position{width:7px;height:7px;top:-3px;border:2px solid #8a9c98;background:white;border-radius:50%;z-index:1}.range-track i:not(.prior-position){z-index:2}
</style>
