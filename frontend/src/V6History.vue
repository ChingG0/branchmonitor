<script setup lang="ts">
import {indicatorPalette} from './v6IndicatorState'
import V6RiskPosition from './V6RiskPosition.vue'
import V6ObservationTrend from './V6ObservationTrend.vue'
import {vRememberDisclosure} from './rememberDisclosure'
import {computed,ref,watch,nextTick,onMounted,onBeforeUnmount} from 'vue'
import type {V6Historical} from './v6HistoryTypes'
import {formatObservation,formatRiskText} from './v6Format'
const props=defineProps<{history:V6Historical}>()
const currentHistory=computed(()=>props.history)
const selected=ref('')
const scrollArea=ref<HTMLElement|null>(null)
const points=computed(()=>currentHistory.value.points)
const index=computed(()=>Math.max(0,points.value.findIndex(p=>p.date===selected.value)>=0?points.value.findIndex(p=>p.date===selected.value):points.value.length-1))
const current=computed(()=>points.value[index.value])
const selectedDate=computed({get:()=>current.value?.date||'',set:(value:string)=>{selected.value=value}})
const prior=computed(()=>points.value[index.value-1])
const change=computed(()=>current.value?.score!=null&&prior.value?.score!=null?current.value.score-prior.value.score:null)
const width=computed(()=>Math.max(680,points.value.length*76+80))
const x=(i:number)=>48+(width.value-90)*i/Math.max(1,points.value.length-1)
function revealSelected(){
 const area=scrollArea.value
 if(area)area.scrollLeft=Math.max(0,x(index.value)-area.clientWidth/2)
}
watch(index,()=>nextTick(revealSelected))
let resize:ResizeObserver|undefined
onMounted(()=>{
 revealSelected()
 if(scrollArea.value){resize=new ResizeObserver(revealSelected);resize.observe(scrollArea.value)}
})
onBeforeUnmount(()=>resize?.disconnect())
const y=(score:number)=>220-score*1.8
const color=(score:number|null)=>score===null?'#a0aaa3':score>=70?'#b85c55':score>=65?'#b68a38':'#728f91'
const radius=(score:number|null)=>score===null?6:Math.sqrt(Math.max(0,Math.min(100,score)))*1.8
const status=(score:number|null)=>score===null?'資料不足':score>=65?'達 65 分觀察線':'未達 65 分觀察線'
const validUrl=(url:string)=>/^https?:\/\//i.test(url)?url:'#'
const lines=computed(()=>points.value.slice(1).flatMap((p,i)=>{
 const before=points.value[i]
 return p.score==null||before.score==null?[]:[{x1:x(i),y1:y(before.score),x2:x(i+1),y2:y(p.score)}]
}))
</script>
<template>
 <details v-remember-disclosure="'v6.history'" open class="historical-weather" v-if="points.length">
  <summary class="section-summary"><div><span class="badge">V6-2 · 歷史重算 · 非當時預報</span><h2>{{currentHistory.start.slice(5).replace('-','/')}} 起風險分數回溯</h2><p>{{currentHistory.start}} ～ {{currentHistory.end}} · {{points.length}} 個交易日 · 日期為台灣盤前 08:30</p></div><div class="history-controls" @click.stop @keydown.stop><label>查看日期<select v-model="selectedDate" aria-label="回溯觀測日期"><option v-for="point in points" :key="point.date" :value="point.date">{{point.date}}</option></select></label></div></summary>
  <div class="history-explainer">目前只追蹤 V6-2｜價格＋外資＋跨資產。泡泡面積與 V6-2 分數成比例，越大代表模型分數越高；觀察逐日變化與是否達線。原 V6 放空交易回測仍保留於下方「原V6歷史模擬」。分數不是下跌機率，也不同於上方人工新聞研究的警戒等級；低於門檻不表示安全。</div>
  <div ref="scrollArea" class="history-scroll" tabindex="0" aria-label="歷史風險分數圖，可左右捲動">
   <svg :viewBox="`0 0 ${width} 275`" :style="{width:width+'px'}" height="275" role="group" aria-label="逐日歷史重算分數">
    <g v-for="mark in [0,50,65,70,100]" :key="mark"><line x1="44" :x2="width-22" :y1="y(mark)" :y2="y(mark)" :stroke="mark===65?'#c6a76b':mark===70?'#d39d94':'#e5ebe5'" stroke-dasharray="4 5"/><text x="6" :y="y(mark)+4" class="axis">{{mark}}</text></g>
    <line v-for="(line,i) in lines" :key="i" v-bind="line" stroke="#8ca2a0" stroke-width="1.5"/>
    <g v-for="(point,i) in points" :key="point.date" role="button" tabindex="0" :aria-pressed="point.date===current?.date" :aria-label="`${point.date}，${point.score==null?'資料不足':point.score+' 分'}`" @click="selected=point.date" @keydown.enter.prevent="selected=point.date" @keydown.space.prevent="selected=point.date" class="day-bubble">
     <rect :x="x(i)-28" y="25" width="56" height="238" fill="transparent"/>
     <title>{{point.date}} · {{status(point.score)}} · {{point.score??'—'}} 分</title>
     <circle :cx="x(i)" :cy="y(point.score??0)" :r="radius(point.score)+4" fill="none" :stroke="point.date===current?.date?'#365e50':'transparent'" stroke-width="2"/>
     <circle :cx="x(i)" :cy="y(point.score??0)" :r="radius(point.score)" :fill="color(point.score)" fill-opacity=".25" :stroke="color(point.score)"/>
     <text :x="x(i)" :y="y(point.score??0)+4" text-anchor="middle" class="score-label">{{point.score==null?'?':point.score.toFixed(1)}}</text>
     <text :x="x(i)" y="252" text-anchor="middle" class="axis">{{point.date.slice(5).replace('-','/')}}</text>
    </g>
   </svg>
  </div>
  <div class="threshold-legend"><span>65 分：V6-2 觀察線</span><span>70 分：分數刻度，非另一套進場規則</span><span>灰色 ?：缺資料，不是零分</span></div>
  <div v-if="current" class="historical-details">
   <div class="day-summary"><div><small>回溯盤前 {{current.date}} 08:30</small><h3>{{current.score==null?'—':current.score.toFixed(2)}} <small>分／100</small></h3><p>{{status(current.score)}}</p></div><div><small>相較前個交易日</small><strong>{{change==null?'無可比較分數':(change>0?'+':'')+change.toFixed(2)+' 分'}}</strong><p>{{change==null?'':change>0?'模型分數上升':change<0?'模型分數下降':'模型分數持平'}}</p></div><div><small>台股收盤資料</small><strong>{{current.market_as_of}}</strong><p>不使用 {{current.date}} 的收盤結果</p></div></div>
   <V6RiskPosition :score="current.score" :prior-score="prior?.score??null" :date="current.date" :prior-date="prior?.date"/>
   <div class="indicator-legend" aria-label="觀測指標顏色圖例"><span v-for="(style,tone) in indicatorPalette" :key="tone" :style="{color:style.color,background:style.background}"><i :style="{background:style.color}"/>{{style.label}}</span></div>
   <p class="chart-reading">顏色依各指標的特性分級，並非下跌機率。價格、匯率與外資淨空使用近期相對位置；乖離、VIX、維持率與情緒使用自訂觀察區間。點「依據」可查看規則；沿用資料保留原狀態，不當成當日新訊號。灰色表示待判讀，不代表低風險。</p>
   <p v-if="current.missing_sources.length" class="missing">缺少評分來源：{{current.missing_sources.join('、')}}，未計分。</p>
   <details v-remember-disclosure="'v6.history.observations'" open><summary>{{current.date}} 觀測資料 · {{current.indicators.length}} 項</summary><div class="observed-grid"><article v-for="item in current.indicators" :key="item.category+item.label"><span>{{item.label}}</span><b :title="`${item.value} ${item.unit}`">{{formatObservation(item.value,item.unit,item.label)}}</b><small>{{item.as_of}} · <a :href="validUrl(item.url)" target="_blank" rel="noopener noreferrer">{{item.source}}</a></small><p>{{formatRiskText(item.reading)}}</p><V6ObservationTrend :item="item" :points="points" :current="current"/></article></div></details>
   <p class="missing">尚未回補：{{current.missing_context.join('、')}}。{{currentHistory.context_note||'DIX／GEX、融資為補充觀測，未納入V6-2固定模型分數。'}}</p>
  </div>
  <details v-remember-disclosure="'v6.history.method'" class="method"><summary>回溯方法與限制 · 建立於 {{currentHistory.generated_at.slice(0,16).replace('T',' ')}}</summary><p>{{currentHistory.note}}</p></details>
 </details>
</template>
<style scoped>
.historical-weather{background:white;border:1px solid #e1e7df;border-radius:16px;margin:18px 0;overflow:hidden;color:#344c40}.historical-weather>.section-summary{padding:22px 26px;display:flex;gap:18px;justify-content:space-between;align-items:center}.badge{font-size:11px;color:#917448;background:#f8f1e4;border-radius:20px;padding:5px 10px}h2{font-size:22px;margin:12px 0 5px}.section-summary p{font-size:12px;color:#839184;margin:0}.section-summary label{font-size:12px;color:#788978;display:flex;align-items:center;gap:8px}select{padding:8px 12px;background:#fafcf9;border:1px solid #dae3d8;border-radius:8px;color:#3d5748}.history-explainer{font-size:12px;line-height:1.8;color:#778a7a;margin:0 26px 12px}.history-scroll{overflow-x:auto;margin:0 20px}.history-scroll svg{display:block}.axis{font-size:10px;fill:#8b9b8e}.score-label{font-size:10px;fill:#3f5a51;font-weight:700}.day-bubble{cursor:pointer}.day-bubble:focus-visible{outline:1px solid #3f6b57}.threshold-legend{display:flex;flex-wrap:wrap;gap:16px;font-size:11px;color:#8a937c;padding:0 26px 18px}.historical-details{padding:18px 26px;background:#fafcf9;border-top:1px solid #e7ede5}.day-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-bottom:20px}.day-summary small{font-size:11px;color:#829080}.day-summary h3{font-size:28px;margin:5px 0;color:#52766d}.day-summary strong{display:block;margin-top:9px;font-size:18px}.day-summary p{font-size:12px;color:#798c7c;margin:5px 0}.observed-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:12px}.observed-grid article{background:#fff;padding:12px;border:1px solid #e3eae0;border-radius:9px;min-width:0}.observed-grid span{font-size:11px;color:#7e907f}.observed-grid b{display:block;margin:7px 0;font-size:17px;color:#3e5e4c;overflow-wrap:anywhere}.observed-grid small{display:block;font-size:10px;color:#98a192;line-height:1.7}.observed-grid a{color:#69897b;text-decoration:none}.observed-grid p{font-size:10px;line-height:1.6;color:#8c988d;margin-bottom:0}.chart-reading{font-size:11px;line-height:1.7;color:#83917f;margin:0 0 16px}.missing{font-size:11px;color:#998455;line-height:1.7}.method{padding:16px 26px;border-top:1px solid #e7ede5;font-size:11px;color:#8a9589}.method p{line-height:1.8}summary{cursor:pointer;font-size:12px;color:#7a8976}@media(max-width:900px){.observed-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.observed-grid{grid-template-columns:1fr}.historical-weather>.section-summary{align-items:start;flex-direction:column}.day-summary{grid-template-columns:1fr}.historical-weather>.section-summary,.historical-details{padding:18px 16px}.history-explainer{margin-inline:16px}.history-scroll{margin-inline:10px}}
.indicator-legend{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}.indicator-legend>span{display:flex;align-items:center;gap:5px;font-size:11px;padding:5px 9px;border-radius:20px}.indicator-legend i{width:6px;height:6px;border-radius:50%}.observed-grid article:has([data-tone='danger']){border-color:#edc6bf;background:#fffcfb}.observed-grid article:has([data-tone='warning']){border-color:#ecd4b8;background:#fffdfa}.observed-grid article:has([data-tone='caution']){border-color:#e9dfb1;background:#fffef8}.observed-grid article:has([data-tone='normal']){border-color:#d2e6d6}.observed-grid article:has([data-tone='tailwind']){border-color:#c9ddef;background:#fcfdff}.observed-grid article:has([data-tone='danger'])>b{color:#b34943}.observed-grid article:has([data-tone='warning'])>b{color:#b66924}.observed-grid article:has([data-tone='caution'])>b{color:#967311}.observed-grid article:has([data-tone='tailwind'])>b{color:#3b70a2}
</style>
<style scoped>
.history-controls,.history-controls label{min-width:0;max-width:100%}.history-controls select{min-width:0}
</style>
<style scoped>
.history-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:flex-end}.history-controls label{white-space:nowrap}.history-controls select{max-width:280px}.historical-weather>.section-summary{flex-wrap:wrap}@media(max-width:700px){.history-controls{flex-direction:column;align-items:flex-start;justify-content:flex-start;width:100%}.history-controls select{max-width:100%}}
</style>
