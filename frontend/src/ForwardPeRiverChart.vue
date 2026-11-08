<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import type {RiverPoint,RiverWindow} from './forwardPeTypes'
const props=defineProps<{window:RiverWindow|null;annual:boolean;previewNames?:number}>()
const hover=ref<number|null>(null)
const points=computed(()=>props.window?.points||[])
const present=computed(()=>points.value.filter(p=>p.pe!==null))
const selected=computed(()=>hover.value===null?present.value.at(-1):points.value[hover.value])
const domain=computed(()=>{
 const values=points.value.flatMap(p=>[p.pe,...(p.cuts||[])]).filter((v):v is number=>v!==null&&Number.isFinite(v))
 if(!values.length)return [0,1]
 const lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*.12,.5)
 return [Math.max(0,lo-pad),hi+pad]
})
const x=(i:number)=>62+i*800/Math.max(1,points.value.length-1)
const y=(v:number)=>280-(Math.max(domain.value[0],Math.min(domain.value[1],v))-domain.value[0])/(domain.value[1]-domain.value[0])*245
function segments(bands=false){
 const result:{p:RiverPoint;i:number}[][]=[]
 points.value.forEach((p,i)=>{
  if(p.pe===null||(bands&&!p.cuts))return
  const last=result.at(-1),prior=last?.at(-1)
  if(!prior||i!==prior.i+1||(props.annual&&p.forecast_period!==prior.p.forecast_period))result.push([{p,i}])
  else last!.push({p,i})
 })
 return result
}
const paths=computed(()=>segments().map(s=>s.map(({p,i},j)=>`${j?'L':'M'}${x(i)},${y(p.pe!)}`).join(' ')))
const colors=['#4e9179','#6c9db6','#d3bc6b','#cf9860','#b76562']
const areas=computed(()=>colors.flatMap((color,k)=>segments(true).filter(s=>s.length>1).map(s=>({color,path:s.map(({p,i},j)=>`${j?'L':'M'}${x(i)},${y(k===0?domain.value[0]:p.cuts![k-1])}`).join(' ')+[...s].reverse().map(({p,i})=>`L${x(i)},${y(k===4?domain.value[1]:p.cuts![k])}`).join(' ')+'Z'}))))
const ticks=computed(()=>[0,1,2,3,4].map(i=>domain.value[0]+i*(domain.value[1]-domain.value[0])/4))
const dates=computed(()=>points.value.map((p,i)=>({p,i})).filter(({i})=>i%12===0||i===points.value.length-1))
const fmt=(v:number|null|undefined,d=2)=>v==null?'—':v.toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d})
function move(event:PointerEvent){const box=(event.currentTarget as SVGElement).getBoundingClientRect();hover.value=Math.max(0,Math.min(points.value.length-1,Math.round(((event.clientX-box.left)/box.width*920-62)/800*(points.value.length-1))))}
function key(event:KeyboardEvent){if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();hover.value=Math.max(0,Math.min(points.value.length-1,(hover.value??points.value.length-1)+(event.key==='ArrowLeft'?-1:1)))}}
watch(()=>props.window,()=>hover.value=null)
</script>
<template>
 <div v-if="!present.length" class="empty-chart" role="status">
  <div class="empty-icon" aria-hidden="true">▦</div><h3>尚無合格歷史曲線</h3><p>完整前50檔的預估EPS尚未齊備。<br><template v-if="previewNames">不以{{previewNames}}檔子集試算畫成大盤，也不以今日預估回填過去。</template><template v-else>不以年度EPS代替NTM，也不以今日預估回填過去。</template></p>
  <small>位階需之前連續 {{window?.reference.required_months||36}} 個合格保存月，目前 {{window?.reference.available_months||0}} 個。</small>
 </div>
 <template v-else>
 <div class="legend"><span><i class="line-key"/>完整前50檔預估本益比</span><span v-for="(label,k) in ['相對便宜','偏低','中間區間','偏高','相對昂貴']" :key="label"><i :style="{background:colors[k]}"/>{{label}}</span></div>
 <div class="chart-scroll" tabindex="0" aria-label="左右捲動本益比歷史"><svg viewBox="0 0 920 330" role="img" tabindex="0" aria-label="預估本益比月度河流圖，左右方向鍵查看保存月份" @pointermove="move" @pointerleave="hover=null" @keydown="key">
  <g v-for="t in ticks" :key="t"><line x1="62" x2="862" :y1="y(t)" :y2="y(t)" stroke="#e5eae2" stroke-dasharray="3 4"/><text x="50" :y="y(t)+4" text-anchor="end">{{fmt(t,1)}}</text></g>
  <path v-for="(a,i) in areas" :key="i" :d="a.path" :fill="a.color" fill-opacity=".28"/>
  <path v-for="(p,i) in paths" :key="'p'+i" :d="p" fill="none" stroke="#3f6754" stroke-width="2.6"/>
  <circle v-for="({p,i}) in points.map((p,i)=>({p,i})).filter(({p})=>p.pe!==null)" :key="p.month" :cx="x(i)" :cy="y(p.pe!)" r="3.2" fill="#3f6754" stroke="white"/>
  <text v-for="({p,i}) in dates" :key="p.month" :x="x(i)" y="312" text-anchor="middle">{{p.month}}</text>
  <g v-if="hover!==null"><line :x1="x(hover)" :x2="x(hover)" y1="30" y2="280" stroke="#84917b" stroke-dasharray="3 4"/></g>
 </svg></div>
 <div v-if="selected" class="selected" aria-live="polite"><b>{{selected.month}}</b><span>{{selected.pe===null?'當月缺合格估值':fmt(selected.pe)+' 倍'}}</span><small v-if="selected.forecast_period">預估期間 {{selected.forecast_period}}</small><small v-if="selected.price_date">股價 {{selected.price_date}}</small><small v-if="selected.information_cutoff">資訊 {{selected.information_cutoff.slice(0,16).replace('T',' ')}}</small></div>
 <p class="chart-note">缺月斷線<template v-if="annual">；年度切換也斷線</template>。五色帶只在每一點之前有完整 {{window!.reference.required_months}} 個保存月時顯示。</p>
 </template>
</template>
<style scoped>
.empty-chart{min-height:315px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border:1px dashed #dce5d7;border-radius:10px;background:linear-gradient(150deg,#f8faf5,#fff);padding:25px;color:#6f8266}.empty-icon{font-size:36px;color:#9aa991}.empty-chart h3{font-size:18px;margin:16px 0 7px;font-weight:500}.empty-chart p{font-size:12px;line-height:1.9;color:#84907b}.empty-chart small{font-size:11px;background:#eef2e9;padding:8px 12px;border-radius:6px;line-height:1.6}.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:10px;color:#829176;padding:10px 0}.legend span{display:flex;align-items:center;gap:5px}.legend i{width:11px;height:8px;border-radius:2px}.legend .line-key{height:3px;background:#3f6754}.chart-scroll{overflow-x:auto}.chart-scroll svg{width:100%;min-width:640px;display:block}.chart-scroll text{font-size:10px;fill:#8a9780}.selected{display:flex;flex-wrap:wrap;gap:13px;padding:13px;background:#f2f6ed;border-radius:7px;font-size:12px;color:#506b4e}.selected small{font-size:10px;color:#86937b}.chart-note{font-size:11px;color:#8b967f;line-height:1.8}
</style>
