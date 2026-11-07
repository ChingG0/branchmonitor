<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,ref,watch} from 'vue'
type Point={date:string;close:number;ma20?:number|null;ma60?:number|null;ma239?:number|null;low10:number;low25:number;median:number;high75:number;high90:number;total_100m:number;day_trade_100m:number;after_day_trade_100m:number;day_trade_pct:number;turnover_ratio:number;percentile:number;window_count:number;provisional:boolean;financing_units?:number|null;short_units?:number|null;margin_finalized?:boolean;maintenance_pct?:number|null;maintenance_coverage_pct?:number|null;maintenance_estimated?:boolean|null;maintenance_source?:'twse'|'reference'|null}
type River={available:boolean;as_of:string|null;points:Point[];missing_dates:string[];margin_missing_dates?:string[];margin_warning?:string;maintenance_missing_dates?:string[];maintenance_warning?:string;warning:string;note:string;source_url:string;day_trade_source_url:string;margin_source_url?:string;maintenance_source_url?:string;maintenance_price_source_url?:string;maintenance_reference_url?:string}
const props=defineProps<{data?:River}>()
const hover=ref<number|null>(null),period=ref<3|12>(3)
const volumeMode=ref<'after'|'dayTrade'>('after')
const volumeLabel=computed(()=>volumeMode.value==='after'?'扣當沖後成交額':'當沖金額（單邊）')
const volumeAmount=(point:Point)=>volumeMode.value==='after'?point.after_day_trade_100m:point.day_trade_100m
const windowSize=30,startIndex=ref(0)
const chartViewport=ref<HTMLElement|null>(null),viewportWidth=ref(960)
const scrollTrack=ref<HTMLElement|null>(null),scrollFraction=ref(1)
const allPoints=computed(()=>props.data?.points||[])
const cutoff=computed(()=>{
 if(!allPoints.value.length)return ''
 const last=new Date(allPoints.value[allPoints.value.length-1].date+'T00:00:00Z'),day=last.getUTCDate()
 last.setUTCDate(1);last.setUTCMonth(last.getUTCMonth()-period.value)
 const end=new Date(Date.UTC(last.getUTCFullYear(),last.getUTCMonth()+1,0)).getUTCDate()
 last.setUTCDate(Math.min(day,end))
 return last.toISOString().slice(0,10)
})
const availablePoints=computed(()=>allPoints.value.filter(p=>p.date>=cutoff.value))
const maximumStart=computed(()=>Math.max(0,availablePoints.value.length-windowSize))
const points=computed(()=>availablePoints.value.slice(startIndex.value,startIndex.value+windowSize))
const pointStep=computed(()=>(viewportWidth.value-120)/(windowSize-1))
const chartWidth=computed(()=>viewportWidth.value+maximumStart.value*pointStep.value)
const thumbWidth=computed(()=>Math.min(100,windowSize/Math.max(windowSize,availablePoints.value.length)*100))
const thumbLeft=computed(()=>(100-thumbWidth.value)*scrollFraction.value)
const missingVisible=computed(()=>props.data?.missing_dates?.filter(d=>d>=cutoff.value)||[])
const marginMissingVisible=computed(()=>props.data?.margin_missing_dates?.filter(d=>d>=cutoff.value)||[])
const maintenanceMissingVisible=computed(()=>props.data?.maintenance_missing_dates?.filter(d=>d>=cutoff.value)||[])
const latest=computed(()=>availablePoints.value[availablePoints.value.length-1])
const windowEnd=computed(()=>points.value[points.value.length-1])
const selected=computed(()=>hover.value===null?latest.value:availablePoints.value[hover.value])
const relative=computed(()=>!selected.value?'—':selected.value.percentile>=90?'高於九成分位':selected.value.percentile<=10?'低於一成分位':selected.value.percentile>=75?'偏高':selected.value.percentile<=25?'偏低':'中間區間')
const priceValues=computed(()=>points.value.flatMap(p=>[p.low10,p.high90,p.close,p.ma20,p.ma60,p.ma239].filter((n):n is number=>typeof n==='number')))
const minimum=computed(()=>Math.min(...priceValues.value)*.995)
const maximum=computed(()=>Math.max(...priceValues.value)*1.005)
const volumeMaximum=computed(()=>Math.max(1,...points.value.map(volumeAmount)))
const ratioSeries=computed(()=>(availablePoints.value.map(p=>p.financing_units&&p.short_units!=null?p.short_units/p.financing_units*100:null)))
const ratioMedianSeries=computed(()=>ratioSeries.value.map((_,index)=>{
 const window=ratioSeries.value.slice(Math.max(0,index-29),index+1)
 if(window.length<30||window.some(value=>value==null))return null
 const ordered=(window as number[]).slice().sort((a,b)=>a-b)
 return (ordered[14]+ordered[15])/2
}))
const selectedIndex=computed(()=>selected.value?availablePoints.value.findIndex(p=>p.date===selected.value?.date):-1)
const selectedRatio=computed(()=>selectedIndex.value<0?null:ratioSeries.value[selectedIndex.value])
const selectedRatioMedian=computed(()=>selectedIndex.value<0?null:ratioMedianSeries.value[selectedIndex.value])
const ratioMaximum=computed(()=>Math.max(6,...points.value.map(p=>p.financing_units&&p.short_units!=null?p.short_units/p.financing_units*110:0)))
const maintenanceValues=computed(()=>points.value.map(p=>p.maintenance_pct).filter((value):value is number=>typeof value==='number'))
const maintenanceBoundaries=[
 {value:190,level:'L1',color:'#b65e54'},
 {value:183,level:'L2',color:'#bb8550'},
 {value:166,level:'L3',color:'#6c9369'},
 {value:158,level:'L4',color:'#a98754'},
 {value:150,level:'L5',color:'#737fa3'},
] as const
const maintenanceGuides={
 level1:{level:'Level 1',range:'≥190%',title:'軋空與過熱區（暫不追高）',icon:'❌',tone:'level1',prompt:'空手者先觀望、不追高；留意高檔獲利了結的賣壓。'},
 level2:{level:'Level 2',range:'183%～未滿190%',title:'多頭續航區（順勢短線）',icon:'↗️',tone:'level2',prompt:'順勢交易可觀察剛突破關鍵價位、量價配合的股票，並留意短線風險。'},
 level3:{level:'Level 3',range:'166%～未滿183%',title:'多空分水嶺（觀察支撐）',icon:'⚖️',tone:'level3',prompt:'先觀察股價能否在支撐止跌回升；確認後再評估小部位試單。'},
 transition:{level:'過渡區',range:'高於158%～未滿166%',title:'等待方向確認',icon:'·',tone:'transition',prompt:'原設定沒有涵蓋這段數值；先觀察價格、融資餘額與量能，不硬套相鄰級別。'},
 level4:{level:'Level 4',range:'高於150%～158%',title:'散戶套牢區（等待賣壓落底）',icon:'⚠️',tone:'level4',prompt:'空手先等賣壓落底，不急著接下跌中的價格；留意反彈時的解套賣壓。'},
 level5:{level:'Level 5',range:'≤150%',title:'斷頭引爆前夕（準備撿便宜）',icon:'🎯',tone:'level5',prompt:'提高關注融資大減與股價止跌是否同時出現；條件確認後再評估現股機會。此級別名稱是自訂情境，不代表已達法定斷頭門檻。'},
} as const
const maintenanceGuide=computed(()=>{
 const value=selected.value?.maintenance_pct
 if(value==null)return null
 if(value>=190)return maintenanceGuides.level1
 if(value>=183)return maintenanceGuides.level2
 if(value>=166)return maintenanceGuides.level3
 if(value>158)return maintenanceGuides.transition
 if(value>150)return maintenanceGuides.level4
 return maintenanceGuides.level5
})
const maintenanceMinimum=computed(()=>Math.floor((Math.min(150,...maintenanceValues.value)-4)/5)*5)
const maintenanceMaximum=computed(()=>Math.ceil((Math.max(190,...maintenanceValues.value)+4)/5)*5)
const fmt=(n:number|null|undefined,d=1)=>n==null?'—':n.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const x=(i:number)=>58+i*pointStep.value
const y=(p:number)=>285-(p-minimum.value)/(maximum.value-minimum.value)*245
const barHeight=(p:number)=>p/volumeMaximum.value*70
const ratioY=(value:number)=>550-value/ratioMaximum.value*88
const maintenanceY=(value:number)=>770-(value-maintenanceMinimum.value)/Math.max(1,maintenanceMaximum.value-maintenanceMinimum.value)*180
const seriesLine=(values:(number|null)[],map:(value:number)=>number)=>{let active=false;return values.map((value,i)=>{if(value==null){active=false;return ''}const part=(active?'L':'M')+x(i)+','+map(value);active=true;return part}).join(' ')}
const ratioMedianLine=computed(()=>seriesLine(ratioMedianSeries.value,ratioY))
const maintenanceLine=computed(()=>seriesLine(availablePoints.value.map(p=>p.maintenance_pct??null),maintenanceY))
const line=computed(()=>availablePoints.value.map((p,i)=>(i?'L':'M')+x(i)+','+y(p.close)).join(' '))
const midline=computed(()=>availablePoints.value.map((p,i)=>(i?'L':'M')+x(i)+','+y(p.median)).join(' '))
const averageLine=(field:'ma20'|'ma60'|'ma239')=>{let active=false;return availablePoints.value.map((p,i)=>{const value=p[field];if(value==null){active=false;return ''}const segment=(active?'L':'M')+x(i)+','+y(value);active=true;return segment}).join(' ')}
const area=(lower:keyof Point,upper:keyof Point)=>availablePoints.value.map((p,i)=>(i?'L':'M')+x(i)+','+y(p[lower] as number)).join(' ')+[...availablePoints.value].reverse().map((p,i)=>'L'+x(availablePoints.value.length-1-i)+','+y(p[upper] as number)).join(' ')+'Z'
const ticks=computed(()=>availablePoints.value.map((_,i)=>i).filter(i=>i===availablePoints.value.length-1||(i%10===0&&i<availablePoints.value.length-5)))
function move(event:PointerEvent){const box=(event.currentTarget as SVGElement).getBoundingClientRect(),px=event.clientX-box.left;hover.value=Math.min(availablePoints.value.length-1,Math.max(0,Math.round((px-58)/pointStep.value)))}
function key(event:KeyboardEvent){if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();const next=Math.min(availablePoints.value.length-1,Math.max(0,(hover.value??startIndex.value+windowSize-1)+(event.key==='ArrowLeft'?-1:1)));hover.value=next;if(chartViewport.value&&next<startIndex.value)chartViewport.value.scrollLeft=next*pointStep.value;if(chartViewport.value&&next>=startIndex.value+windowSize)chartViewport.value.scrollLeft=(next-windowSize+1)*pointStep.value}}
function onScroll(event:Event){const target=event.target as HTMLElement;const remaining=target.scrollWidth-target.clientWidth;scrollFraction.value=remaining>0?target.scrollLeft/remaining:0;startIndex.value=Math.min(maximumStart.value,Math.max(0,Math.round(target.scrollLeft/pointStep.value)));hover.value=null}
function scrollToFraction(fraction:number){if(!chartViewport.value)return;const target=chartViewport.value;target.scrollLeft=Math.min(1,Math.max(0,fraction))*(target.scrollWidth-target.clientWidth)}
let dragOffset=0,dragging=false
function dragScroll(event:PointerEvent){const track=scrollTrack.value;if(!track||maximumStart.value===0)return;const bounds=track.getBoundingClientRect(),usable=bounds.width*(1-thumbWidth.value/100);scrollToFraction((event.clientX-bounds.left-dragOffset)/Math.max(1,usable))}
function startDrag(event:PointerEvent){const track=scrollTrack.value;if(!track||maximumStart.value===0)return;const bounds=track.getBoundingClientRect(),thumbStart=bounds.left+bounds.width*thumbLeft.value/100,thumbEnd=thumbStart+bounds.width*thumbWidth.value/100;dragOffset=event.clientX>=thumbStart&&event.clientX<=thumbEnd?event.clientX-thumbStart:bounds.width*thumbWidth.value/200;dragging=true;track.setPointerCapture(event.pointerId);dragScroll(event);event.preventDefault()}
function moveDrag(event:PointerEvent){if(dragging)dragScroll(event)}
function stopDrag(event:PointerEvent){dragging=false;if(scrollTrack.value?.hasPointerCapture(event.pointerId))scrollTrack.value.releasePointerCapture(event.pointerId)}
function scrollKey(event:KeyboardEvent){const step=event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:event.key==='PageUp'?-10:event.key==='PageDown'?10:event.key==='Home'?-Infinity:event.key==='End'?Infinity:0;if(!step)return;event.preventDefault();const next=step===-Infinity?0:step===Infinity?maximumStart.value:Math.min(maximumStart.value,Math.max(0,startIndex.value+step));if(chartViewport.value)chartViewport.value.scrollLeft=next*pointStep.value}
let resizeObserver:ResizeObserver|undefined
watch(chartViewport,(element)=>{resizeObserver?.disconnect();if(!element)return;resizeObserver=new ResizeObserver(()=>{const width=element.clientWidth;if(width===viewportWidth.value)return;const current=startIndex.value,atLatest=scrollFraction.value>=.999;viewportWidth.value=width;nextTick(()=>{element.scrollLeft=atLatest?element.scrollWidth-element.clientWidth:current*pointStep.value})});resizeObserver.observe(element)},{flush:'post'})
onBeforeUnmount(()=>resizeObserver?.disconnect())
watch([()=>props.data,period],async()=>{await nextTick();if(chartViewport.value)viewportWidth.value=chartViewport.value.clientWidth;await nextTick();startIndex.value=maximumStart.value;scrollFraction.value=maximumStart.value?1:0;hover.value=null;if(chartViewport.value)chartViewport.value.scrollLeft=chartViewport.value.scrollWidth-chartViewport.value.clientWidth},{immediate:true,flush:'post'})
watch(startIndex,()=>hover.value=null)
</script>
<template>
 <section class="liquidity-river" aria-label="加權指數價量河流">
  <div class="period-bar"><span>觀察期間</span><div><button :aria-pressed="period===3" @click="period=3">近一季</button><button :aria-pressed="period===12" @click="period=12">近一年</button></div><small>左右捲動圖表，每次查看 30 個交易日</small></div>
  <p v-if="data?.warning" class="warning">{{data.warning}}</p>
  <template v-if="selected">
   <div class="metrics"><div><span>加權指數收盤</span><strong>{{fmt(selected.close,2)}}</strong><small>{{selected.date}}</small></div><div><span>近 60 日趨勢中位</span><strong>{{fmt(selected.median,0)}}</strong><small>一成～九成 {{fmt(selected.low10,0)}}～{{fmt(selected.high90,0)}}</small></div><div><span>近 60 日相對位置</span><strong>{{fmt(selected.percentile,0)}}<em>%</em></strong><small>{{relative}} · {{selected.window_count}} 個交易日</small></div><div><span>{{volumeLabel}}</span><strong>{{fmt(volumeAmount(selected),0)}}<em> 億</em></strong><small v-if="volumeMode==='after'">當沖占 {{fmt(selected.day_trade_pct)}}% · 量比 {{fmt(selected.turnover_ratio,2)}} 倍</small><small v-else>占總成交 {{fmt(selected.day_trade_pct)}}% · 總額 {{fmt(selected.total_100m,0)}} 億</small></div></div>
   <div class="legend"><span><i class="close"></i>每日收盤</span><span><i class="median"></i>趨勢中位</span><span><i class="ma20"></i>20MA</span><span><i class="ma60"></i>60MA</span><span><i class="ma239"></i>239MA</span><span><i class="blue"></i>10～25%</span><span><i class="green"></i>25～75%</span><span><i class="gold"></i>75～90%</span><span><i class="bar" :class="{daytrade:volumeMode==='dayTrade'}"></i>{{volumeLabel}}</span><span><i class="ratio-key"></i>資券比 · 融券／融資</span><span><i class="ratio-median-key"></i>近 30 日中位</span><span><i class="maintenance-key"></i>融資維持率估算</span><div class="volume-toggle" role="group" aria-label="量柱顯示"><button :aria-pressed="volumeMode==='after'" @click="volumeMode='after'">扣當沖後</button><button :aria-pressed="volumeMode==='dayTrade'" @click="volumeMode='dayTrade'">當沖金額</button></div></div>
   <div class="chart-frame"><svg class="chart-axis" viewBox="0 0 58 825" aria-hidden="true"><text v-for="step in [0,1,2,3]" :key="step" x="48" :y="44+step*81" text-anchor="end">{{fmt(maximum-step*(maximum-minimum)/3,0)}}</text><text x="50" y="332" text-anchor="end">{{volumeMode==='after'?'扣當沖':'當沖'}}</text><text x="50" y="346" text-anchor="end">億元</text><text x="50" y="482" text-anchor="end">資券比</text><text x="50" y="496" text-anchor="end">%</text><text x="50" y="574" text-anchor="end">融資維持</text><text x="50" y="588" text-anchor="end">%</text><text v-for="boundary in maintenanceBoundaries" :key="boundary.level" x="50" :y="maintenanceY(boundary.value)+4" text-anchor="end" :style="{fill:boundary.color,fontWeight:600}">{{boundary.level}} {{boundary.value}}</text></svg>
   <div ref="chartViewport" class="chart-scroll" aria-label="左右捲動加權指數價量河流、資券比與融資維持率" @scroll.passive="onScroll"><svg :viewBox="`0 0 ${chartWidth} 825`" :style="{width:chartWidth+'px'}" role="img" tabindex="0" :aria-label="`加權指數價量河流、${volumeLabel}、資券比及融資維持率；左右捲動圖表，移動滑鼠或使用方向鍵查看日期`" @pointermove="move" @pointerleave="hover=null" @keydown="key" @blur="hover=null">
    <defs><clipPath id="river-ratio-clip"><rect x="58" y="460" :width="chartWidth-118" height="90"/></clipPath><clipPath id="river-maintenance-clip"><rect x="58" y="590" :width="chartWidth-118" height="180"/></clipPath></defs>
    <g v-for="step in [0,1,2,3]" :key="step"><line x1="58" :x2="chartWidth-60" :y1="40+step*81" :y2="40+step*81" stroke="#e8ede6" stroke-dasharray="4 4"/></g>
    <path :d="area('low10','low25')" fill="#adc7d8" fill-opacity=".68"/><path :d="area('low25','median')" fill="#b8d6cb" fill-opacity=".72"/><path :d="area('median','high75')" fill="#d9e7c9" fill-opacity=".82"/><path :d="area('high75','high90')" fill="#f6d7aa" fill-opacity=".72"/>
    <path :d="midline" fill="none" stroke="#4e8069" stroke-width="1.4" stroke-dasharray="5 3"/><path :d="averageLine('ma239')" fill="none" stroke="#b4832b" stroke-width="2.3"/><path :d="averageLine('ma60')" fill="none" stroke="#3b80bd" stroke-width="2.2"/><path :d="averageLine('ma20')" fill="none" stroke="#8060ac" stroke-width="2.2"/><path :d="line" fill="none" stroke="#b95248" stroke-width="2.5"/>
    <line x1="58" :x2="chartWidth-60" y1="344" y2="344" stroke="#d9e2d6"/>
    <rect v-for="(p,i) in availablePoints" :key="p.date" :x="x(i)-Math.min(5,pointStep*.35)" :y="430-barHeight(volumeAmount(p))" :width="Math.min(10,pointStep*.7)" :height="barHeight(volumeAmount(p))" :fill="volumeMode==='dayTrade'?'#8c79ad':i&&p.close<availablePoints[i-1].close?'#bd7b70':'#5e9c7b'" opacity=".83"/>
    <line x1="58" :x2="chartWidth-60" y1="452" y2="452" stroke="#e2e9df"/>
    <line x1="58" :x2="chartWidth-60" :y1="ratioY(5)" :y2="ratioY(5)" stroke="#ba695f" stroke-dasharray="5 5"/><line x1="58" :x2="chartWidth-60" :y1="ratioY(2)" :y2="ratioY(2)" stroke="#5c8fae" stroke-dasharray="5 5"/>
    <g clip-path="url(#river-ratio-clip)"><rect v-for="(p,i) in availablePoints" v-show="ratioSeries[i]!=null" :key="`ratio-${p.date}`" :x="x(i)-Math.min(5,pointStep*.35)" :y="ratioY(ratioSeries[i]||0)" :width="Math.min(10,pointStep*.7)" :height="550-ratioY(ratioSeries[i]||0)" :fill="(ratioSeries[i]||0)>=5?'#ba695f':(ratioSeries[i]||0)<=2?'#5c8fae':'#8c79ad'" opacity=".8"/><path :d="ratioMedianLine" fill="none" stroke="#40886b" stroke-width="2"/></g>
    <line x1="58" :x2="chartWidth-60" y1="550" y2="550" stroke="#d9e2d6"/>
    <text :x="chartWidth-61" :y="ratioY(5)-4" text-anchor="end" fill="#a65d56">高點 5%</text><text :x="chartWidth-61" :y="ratioY(2)-4" text-anchor="end" fill="#527c9a">低點 2%</text>
    <line x1="58" :x2="chartWidth-60" y1="570" y2="570" stroke="#e2e9df"/>
    <line v-for="boundary in maintenanceBoundaries" :key="boundary.level" x1="58" :x2="chartWidth-60" :y1="maintenanceY(boundary.value)" :y2="maintenanceY(boundary.value)" :stroke="boundary.color" stroke-opacity=".65" stroke-dasharray="5 5"/>
    <path :d="maintenanceLine" fill="none" stroke="#bc813c" stroke-width="2.5" clip-path="url(#river-maintenance-clip)"/>
    <line x1="58" :x2="chartWidth-60" y1="770" y2="770" stroke="#d9e2d6"/>
    <text :x="chartWidth-61" y="596" text-anchor="end" fill="#9a764c">{{fmt(maintenanceMaximum,0)}}%</text><text :x="chartWidth-61" y="766" text-anchor="end" fill="#9a764c">{{fmt(maintenanceMinimum,0)}}%</text>
    <text v-for="i in ticks" :key="i" :x="x(i)" y="807" :text-anchor="i===0?'start':i===availablePoints.length-1?'end':'middle'">{{availablePoints[i].date}}</text>
    <g v-if="selected&&hover!==null"><line :x1="x(hover)" :x2="x(hover)" y1="36" y2="770" stroke="#536b58" stroke-dasharray="4 4"/><circle :cx="x(hover)" :cy="y(selected.close)" r="4" fill="#b95248" stroke="white"/><circle v-if="selected.maintenance_pct!=null" :cx="x(hover)" :cy="maintenanceY(selected.maintenance_pct)" r="4" fill="#bc813c" stroke="white"/></g>
   </svg></div></div>
   <div class="time-scroll" aria-label="價量河流日期捲動">
    <div class="time-scroll-heading"><span>左右捲動上方圖表 · 畫面約 30 個交易日</span><strong>{{points[0]?.date}} → {{windowEnd?.date}}</strong><small>共 {{availablePoints.length}} 日可回看</small></div>
    <div ref="scrollTrack" class="date-scrollbar" role="scrollbar" tabindex="0" aria-label="左右移動價量河流日期" aria-orientation="horizontal" aria-valuemin="0" :aria-valuemax="maximumStart" :aria-valuenow="startIndex" :aria-valuetext="`${points[0]?.date} 至 ${windowEnd?.date}`" @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="stopDrag" @keydown="scrollKey"><div class="date-scrollbar-thumb" :style="{width:thumbWidth+'%',left:thumbLeft+'%'}"></div></div>
   </div>
   <div v-if="selected" class="detail" aria-live="polite"><b>{{selected.date}}<small v-if="selected.provisional"> 當沖待 T+2 定稿</small></b><span>收盤 {{fmt(selected.close,2)}}</span><span>20MA {{fmt(selected.ma20,0)}} · 60MA {{fmt(selected.ma60,0)}} · 239MA {{fmt(selected.ma239,0)}}</span><span>中位 {{fmt(selected.median,0)}} · 10～90% {{fmt(selected.low10,0)}}～{{fmt(selected.high90,0)}}</span><span>總額 {{fmt(selected.total_100m,0)}} 億 − 當沖 {{fmt(selected.day_trade_100m,0)}} 億 = 扣除後 {{fmt(selected.after_day_trade_100m,0)}} 億</span><span>資券比 {{fmt(selectedRatio,2)}}% · 近 30 日中位 {{fmt(selectedRatioMedian,2)}}% <small>（融券 {{fmt(selected.short_units,0)}} ÷ 融資 {{fmt(selected.financing_units,0)}} 交易單位；≤2% 低點、≥5% 高點）</small></span><span>融資維持率{{selected.maintenance_source==='reference'?'歷史參考':'推算'}} {{fmt(selected.maintenance_pct,2)}}%<small v-if="selected.maintenance_coverage_pct!=null"> · 收盤價涵蓋 {{fmt(selected.maintenance_coverage_pct,2)}}% 融資單位</small><small v-if="selected.financing_units!=null&&!selected.margin_finalized"> · 最新日可能修正</small></span></div>
   <div v-if="maintenanceGuide" class="maintenance-guide" :class="maintenanceGuide.tone" aria-live="polite"><div class="maintenance-guide-heading"><strong>{{maintenanceGuide.icon}} {{maintenanceGuide.level}}：{{maintenanceGuide.title}}</strong><span>{{selected.date}} · {{fmt(selected.maintenance_pct,2)}}% · {{maintenanceGuide.range}}</span></div><p>{{maintenanceGuide.prompt}}</p><small>自訂觀察分級，須與價格、融資餘額及量能一起核對；不是已驗證的獨立交易訊號，也不是法定追繳或斷頭門檻。</small></div>
   <p v-if="availablePoints.length<(period===3?55:220)||missingVisible.length" class="warning">{{availablePoints.length<(period===3?55:220)?'所選期間有效交易日尚未齊全；':''}}{{missingVisible.length?'當沖或大盤資料缺漏 '+missingVisible.length+' 日，不以原成交額補值。':''}}</p>
   <p v-if="marginMissingVisible.length||data?.margin_warning" class="margin-status">資券比：{{availablePoints.length-marginMissingVisible.length}}／{{availablePoints.length}} 日已取得{{marginMissingVisible.length?'，缺漏日請按市場觀測更新手動補齊':''}}{{data?.margin_warning?' · '+data.margin_warning:''}}</p>
   <p v-if="maintenanceMissingVisible.length||data?.maintenance_warning" class="margin-status">融資維持率：{{availablePoints.length-maintenanceMissingVisible.length}}／{{availablePoints.length}} 日已計算{{maintenanceMissingVisible.length?'，缺少的日期請按市場觀測更新手動補齊':''}}{{data?.maintenance_warning?' · '+data.maintenance_warning:''}}</p>
   <footer><p>{{data?.note}}</p><p>近一季與近一年決定橫向捲動可回看的日期；圖上每次顯示約 30 個交易日。每日河流只使用當日及更早的近 60 個交易日。扣除當沖後成交額不等於留倉資金或指數買進成本。</p><p>資券比＝同日融券餘額÷融資餘額×100%，綠線為截至當日近 30 個交易日中位數；2% 與 5% 是觀察參考線。融資維持率推算＝Σ（各檔融資餘額×當日或先前有效收盤價）÷上市市場融資金額×100%。僅含上市市場，並含可融資 ETF 等標的，使分子與借款分母口徑一致；缺價時顯示涵蓋率，部分歷史值採公開整理資料並逐日標示來源。此市場推算值不等於個別帳戶的法定整戶擔保維持率。最新日數值可能修正。維持率分界線是自訂觀察區間，158%～166% 之間另列過渡區。</p><a :href="data?.source_url" target="_blank" rel="noopener noreferrer">證交所市場成交資訊 ↗</a><a :href="data?.day_trade_source_url" target="_blank" rel="noopener noreferrer">證交所當沖統計 ↗</a><a :href="data?.margin_source_url" target="_blank" rel="noopener noreferrer">證交所融資融券餘額 ↗</a><a :href="data?.maintenance_price_source_url" target="_blank" rel="noopener noreferrer">證交所每日收盤行情 ↗</a><a :href="data?.maintenance_reference_url" target="_blank" rel="noopener noreferrer">歷史參考值來源 ↗</a></footer>
  </template>
  <p v-else class="empty">價量資料待更新。請按上方更新取得官方大盤與當沖成交資料。</p>
 </section>
</template>
<style scoped>
.liquidity-river{color:#425440}.period-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:0 24px 18px;font-size:12px;color:#809078}.period-bar>div{display:flex;background:#f0f3ed;border-radius:7px;padding:3px}.period-bar button{border:0;border-radius:5px;background:transparent;color:#6b7d63;padding:7px 13px;cursor:pointer;font:inherit}.period-bar button[aria-pressed=true]{background:white;color:#31593d;box-shadow:0 1px 4px #263d2420}.period-bar small{font-size:11px;color:#8a9681}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid #edf0e9;border-bottom:1px solid #edf0e9}.metrics>div{padding:20px 24px;border-right:1px solid #edf0e9;min-width:0}.metrics span,.metrics small{display:block;font-size:11px;color:#8a9782}.metrics strong{display:block;font-size:25px;font-variant-numeric:tabular-nums;margin:9px 0;color:#3d6147;font-weight:600}.metrics em{font-size:14px;font-style:normal;font-weight:400}.legend{display:flex;flex-wrap:wrap;gap:13px;padding:20px 24px 8px;font-size:11px;color:#7b8973}.legend span{display:flex;align-items:center;gap:6px}.legend i{display:inline-block;width:12px;height:8px;border-radius:2px}.legend .close{background:#b95248;height:3px}.legend .median{background:#4e8069;height:2px}.legend .blue{background:#adc7d8}.legend .green{background:#b8d6cb}.legend .gold{background:#f6d7aa}.legend .bar{background:#5e9c7b}.chart-scroll{overflow-x:auto;padding:0 14px}.chart-scroll svg{display:block;width:100%;min-width:650px;touch-action:pan-y}.chart-scroll text{fill:#86917f;font-size:11px}.detail{display:flex;gap:15px;flex-wrap:wrap;align-items:center;margin:0 24px 20px;padding:13px 16px;background:#f5f8f2;border-radius:7px;font-size:12px}.detail b small{font-size:10px;color:#a17644}.detail span:last-child{font-weight:600}.liquidity-river footer{padding:15px 24px 21px;border-top:1px solid #edf0e9;font-size:11px;color:#8a9681;line-height:1.8}.liquidity-river footer p{margin:0 0 6px}.liquidity-river footer a{margin-right:18px;color:#607d52;text-decoration:underline}.warning{margin:10px 24px;color:#92723f;background:#faf4e7;padding:10px;font-size:12px;line-height:1.7}.empty{padding:35px 24px;color:#8a9681;font-size:13px}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.metrics>div{border-bottom:1px solid #edf0e9}}@media(max-width:500px){.metrics{grid-template-columns:1fr}.metrics>div{padding:15px 20px}.detail{gap:10px}}
.time-scroll{margin:0 24px 18px;padding:12px 16px 10px;background:#f6f8f4;border:1px solid #e6ece2;border-radius:9px}.time-scroll-heading{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;color:#71806b;font-size:11px}.time-scroll-heading strong{color:#3f5f49;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}.time-scroll-heading small{margin-left:auto;color:#86927e;font-size:10px}.time-scroll-range{display:block;width:100%;margin:12px 0 2px;accent-color:#4e8069;cursor:ew-resize}.time-scroll-range:disabled{opacity:.55;cursor:default}.time-scroll-ends{display:flex;justify-content:space-between;color:#91a08b;font-size:10px;font-variant-numeric:tabular-nums}@media(max-width:500px){.time-scroll{margin:0 14px 16px;padding:10px 12px}.time-scroll-heading small{margin-left:0}}
.legend .ma20,.legend .ma60,.legend .ma239{height:3px}.legend .ma20{background:#8060ac}.legend .ma60{background:#3b80bd}.legend .ma239{background:#b4832b}
.chart-frame{position:relative;margin:0 14px}.chart-axis{position:absolute;top:0;left:0;width:58px;height:675px;z-index:1;background:#fff;pointer-events:none}.chart-axis text{fill:#86917f;font-size:11px}.chart-scroll{overflow-x:scroll;overflow-y:hidden;padding:0;scrollbar-color:#8daf9a #e9f0e8;scrollbar-width:auto}.chart-scroll::-webkit-scrollbar{height:13px}.chart-scroll::-webkit-scrollbar-track{background:#e9f0e8;border-radius:8px}.chart-scroll::-webkit-scrollbar-thumb{background:#8daf9a;border:2px solid #e9f0e8;border-radius:8px}.chart-scroll svg{display:block;height:675px;max-width:none;min-width:0;touch-action:pan-y}.time-scroll{padding:10px 15px;margin-top:6px}.time-scroll-heading{align-items:center}.date-scrollbar{position:relative;height:16px;margin:11px 0 1px;border-radius:9px;background:#e4ebe1;cursor:pointer;touch-action:none}.date-scrollbar-thumb{position:absolute;top:2px;bottom:2px;border-radius:8px;background:#6e9c7c;box-shadow:inset 0 0 0 1px #4f7d60;cursor:grab}.date-scrollbar:active .date-scrollbar-thumb{cursor:grabbing;background:#4f8064}.date-scrollbar:focus-visible{outline:2px solid #3f7455;outline-offset:3px}
.legend .bar.daytrade{background:#8c79ad}.volume-toggle{display:flex;gap:3px;margin-left:auto;padding:3px;background:#f0f3ed;border-radius:7px}.volume-toggle button{border:0;border-radius:5px;padding:6px 9px;background:transparent;color:#71806b;font:inherit;cursor:pointer;white-space:nowrap}.volume-toggle button[aria-pressed=true]{background:#fff;color:#31593d;box-shadow:0 1px 4px #263d2420}.volume-toggle button:focus-visible{outline:2px solid #4f8064;outline-offset:2px}@media(max-width:700px){.volume-toggle{margin-left:0}}
.legend .financing-key{background:#4b9c8c}.legend .short-key{background:#aa80ad}.margin-status{margin:0 24px 17px;color:#8b7955;background:#faf7ee;padding:10px 13px;border-radius:6px;font-size:11px;line-height:1.6}
.chart-axis,.chart-scroll svg{height:825px}
.legend .ratio-key{background:#8c79ad}.legend .ratio-median-key{background:#40886b;height:3px}.legend .maintenance-key{background:#bc813c;height:3px}
.maintenance-guide{--guide-color:#728173;margin:0 24px 18px;padding:15px 18px;border:1px solid #e6ece2;border-left:4px solid var(--guide-color);border-radius:8px;background:#f7f9f5;font-size:12px;line-height:1.6}.maintenance-guide.level1{--guide-color:#b65e54;background:#fcf5f3}.maintenance-guide.level2{--guide-color:#bb8550;background:#fbf7f0}.maintenance-guide.level3{--guide-color:#6c9369;background:#f4f8f2}.maintenance-guide.level4{--guide-color:#a98754;background:#faf7ef}.maintenance-guide.level5{--guide-color:#737fa3;background:#f4f5fa}.maintenance-guide.transition{--guide-color:#889184}.maintenance-guide-heading{display:flex;align-items:baseline;gap:8px 18px;flex-wrap:wrap}.maintenance-guide-heading strong{color:#354b3b;font-size:15px}.maintenance-guide-heading span{color:#687966;font-variant-numeric:tabular-nums}.maintenance-guide p{margin:8px 0 5px;color:#435640}.maintenance-guide small{color:#7d8978;font-size:11px}@media(max-width:500px){.maintenance-guide{margin:0 14px 16px;padding:12px 14px}}
</style>
