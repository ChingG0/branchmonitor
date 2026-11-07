<script setup lang="ts">
import {computed} from 'vue'
import {readingComparison} from './marketComparison'
import type {ValueReading} from './marketComparison'
const props=defineProps<{value?:number|null;date:string;history?:ValueReading[]}>()
const comparison=computed(()=>readingComparison(props.history||[],props.date,props.value))
const fmt=(value:number|null|undefined)=>value==null?'—':value.toLocaleString('zh-TW',{maximumFractionDigits:2})
const position=(n:number)=>Math.max(0,Math.min(100,n))
const sentiment=computed(()=>props.value==null?'等待來源':props.value<25?'極度恐懼':props.value<45?'恐懼':props.value<=55?'中性':props.value<=75?'貪婪':'極度貪婪')
</script>
<template>
 <div class="fear-comparison">
  <div class="fear-value"><strong>{{fmt(value)}}</strong><span>/ 100 · {{sentiment}}</span></div>
  <div class="fear-track" role="img" :aria-label="`當日 ${fmt(value)} 分，前日 ${fmt(comparison.previous?.value)} 分`">
   <i v-if="comparison.previous" class="prior" :style="{left:position(comparison.previous.value)+'%'}" :title="`前日 ${comparison.previous.date}：${fmt(comparison.previous.value)} 分`"/>
   <i v-if="value!=null" class="current" :style="{left:position(value)+'%'}" :title="`當日 ${date}：${fmt(value)} 分`"/>
  </div>
  <div class="fear-labels"><span>極度恐懼</span><span>中性</span><span>極度貪婪</span></div>
  <div v-if="comparison.previous" class="fear-days"><span><i class="prior-dot"/>前日 {{fmt(comparison.previous.value)}} 分<small>{{comparison.previous.date}}</small></span><span><i class="current-dot"/>當日 {{fmt(value)}} 分<small>{{date}}</small></span></div>
  <p v-if="comparison.delta!==null" class="fear-change">{{comparison.delta>0?'↑':comparison.delta<0?'↓':'→'}} 較前日 {{comparison.delta>0?'+':''}}{{fmt(comparison.delta)}} 分 · {{comparison.delta>0?'往貪婪端移動':comparison.delta<0?'往恐懼端移動':'情緒持平'}}</p>
  <p v-else class="fear-change">待累積前一交易日資料</p>
  <small class="fear-calendar">日期採美股來源日；休市日比較前一個有資料的交易日。</small>
 </div>
</template>
<style scoped>
.fear-comparison{padding:4px 26px 22px}.fear-value{text-align:center;margin:16px 0 39px;color:#466b42}.fear-value strong{display:block;font-size:3.7rem;font-weight:500;line-height:1.2;letter-spacing:-.05em}.fear-value>span{display:block;margin-top:8px;color:#8a9d74}.fear-track{position:relative;height:12px;border-radius:10px;background:linear-gradient(90deg,#ca7465,#e5d89e,#c9dcae,#507957);margin:0 4px 15px}.fear-track i{position:absolute;top:50%;transform:translate(-50%,-50%);box-sizing:border-box;border-radius:50%;width:18px;height:18px}.prior{background:white;border:3px solid #879795;z-index:1}.current{background:#345c45;border:3px solid white;box-shadow:0 0 0 1px #345c45;z-index:2}.fear-labels{display:flex;justify-content:space-between;font-size:11px;color:#8d9b80}.fear-days{display:flex;justify-content:space-between;gap:12px;margin-top:25px;font-size:12px;color:#4d6757}.fear-days>span:last-child{text-align:right}.fear-days i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.prior-dot{border:2px solid #879795;background:white}.current-dot{background:#345c45}.fear-days small{display:block;margin-top:6px;font-size:10px;color:#8b9986}.fear-change{font-size:12px;text-align:center;margin:18px 0 10px;color:#486756;line-height:1.7}.fear-calendar{display:block;color:#919c89;font-size:10px;line-height:1.7}
</style>
