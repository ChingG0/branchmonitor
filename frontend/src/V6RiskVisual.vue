<script setup lang="ts">
import {vRememberDisclosure} from './rememberDisclosure'
import {computed,ref} from 'vue'
import {compareReports,priorReports,sampleStats,stageStyle} from './v6WeatherVisual'
import type {WeatherRecord} from './v6WeatherVisual'
import type {V6RiskHorizon} from './v6Types'
const props=defineProps<{
 current:WeatherRecord; history:WeatherRecord[]; horizons:V6RiskHorizon[];
 categories:{key:string;label:string}[]; indicators:{category:string;as_of:string}[]
}>()
const emit=defineEmits<{select:[id:string]}>()
const threshold=ref<65|70>(65)
const earlier=computed(()=>priorReports(props.current,props.history))
const previous=computed(()=>earlier.value.at(-1))
const comparison=computed(()=>compareReports(props.current,previous.value))
const style=computed(()=>stageStyle(props.current.stage))
const trail=computed(()=>[...earlier.value,props.current].slice(-8))
const coverage=computed(()=>props.categories.map(category=>{
 const rows=props.indicators.filter(row=>row.category===category.key)
 const dates=[...new Set(rows.map(row=>row.as_of).filter(Boolean))].sort()
 return {...category,count:rows.length,dates:dates.length>1?`${dates[0]} ～ ${dates.at(-1)}`:dates[0]||'尚未核對'}
}))
const samples=computed(()=>props.horizons.map(row=>{
 const sample=threshold.value===65?row.at_least65:row.at_least70
 return {...row,sample,stats:sampleStats(sample),base:sampleStats(row.baseline)}
}))
const time=(value:string)=>value.slice(0,16).replace('T',' ')
</script>

<template>
 <details v-remember-disclosure="'v6.risk-visual'" open class="risk-visual" aria-label="V6 風險視覺總覽">
  <summary class="section-summary"><div><span class="eyebrow">RISK OUTLOOK</span><h2>風險在膨脹，還是減弱？</h2></div><span class="saved">行情 {{current.market_as_of}} · 人工預報</span></summary>
  <div class="overview">
   <div class="orb-panel">
    <svg viewBox="0 0 300 260" role="img" :aria-label="`警戒階段：${current.stage}。${comparison.label}。泡泡大小為分級示意，不是下跌機率。`">
     <circle v-for="r in [48,72,96]" :key="r" cx="150" cy="123" :r="r" fill="none" stroke="#dce4df" stroke-dasharray="3 5"/>
     <circle cx="150" cy="123" :r="style.radius" :fill="style.color" fill-opacity=".14" :stroke="style.color" stroke-width="2"/>
     <circle v-if="previous && stageStyle(previous.stage).level!==null && style.level!==null" cx="150" cy="123" :r="stageStyle(previous.stage).radius" fill="none" stroke="#53665e" stroke-width="2" stroke-dasharray="5 5"/>
     <text x="150" y="113" text-anchor="middle" class="orb-caption">這份預報</text>
     <text x="150" y="143" text-anchor="middle" :fill="style.color" class="orb-label">{{current.stage}}</text>
     <text x="150" y="248" text-anchor="middle" class="orb-caption">小 → 大：平穩 ／ 形成 ／ 高風險</text>
    </svg>
    <p>實線：本次　虛線：前次（可比較時）</p>
   </div>
   <div class="change-panel">
    <span class="section-label">與上一份比較</span><h3 :style="{color:style.color}">{{comparison.label}}</h3>
    <div class="comparison"><span>{{previous?.stage||'無前次紀錄'}}</span><b>→</b><strong>{{current.stage}}</strong></div>
    <p>{{comparison.note}}</p>
    <small v-if="previous">前次 {{time(previous.prepared_at)}} · 行情 {{previous.market_as_of}}</small>
    <div class="scale-note">泡泡大小是警戒分級示意，沒有風險倍數含義。「資料不足」顯示灰色；只有增強／減弱方向時，不推定絕對等級。</div>
   </div>
  </div>
  <div class="trail"><div class="section-label">最近保存的預報 <span>點選可回看 · 最多 8 份</span></div><div class="trail-items">
   <button v-for="row in trail" :key="row.id" type="button" :aria-pressed="row.id===current.id" :class="{active:row.id===current.id}" @click="emit('select',row.id)">
    <span class="mini-orb-space"><span class="mini-orb" :style="{width:stageStyle(row.stage).radius/2+'px',height:stageStyle(row.stage).radius/2+'px',background:stageStyle(row.stage).color+'26',borderColor:stageStyle(row.stage).color}"></span></span>
    <b>{{row.stage}}</b><small>{{time(row.prepared_at)}}</small><small>行情 {{row.market_as_of}}</small>
   </button>
  </div></div>
  <div class="stats-panel">
   <div class="stats-head"><div><h3>從下一日到下週的歷史收跌比例</h3><p>用已保存研究的相似日樣本比較；不是這次股災的發生機率。</p></div><div class="toggle" aria-label="歷史樣本門檻"><button v-for="n in ([65,70] as const)" :key="n" type="button" :aria-pressed="threshold===n" :class="{active:threshold===n}" @click="threshold=n">{{n}} 分以上</button></div></div>
   <template v-if="samples.length">
    <div class="chart-legend"><span class="dot"></span>相似日收跌比例 <span class="line"></span>95% 統計區間 <span class="tick"></span>整體基準</div>
    <div class="rate-axis"><span>0%</span><span>50%</span><span>100%</span></div>
    <article v-for="row in samples" :key="row.key" class="rate-row">
     <div class="rate-title"><b>{{row.label}}</b><small>{{row.definition}}</small></div>
     <div class="rate-track" role="img" :aria-label="row.stats?`${row.label}：收跌 ${row.stats.rate.toFixed(1)}%，${row.sample.declines} 次／${row.sample.count} 個樣本，95% 區間 ${row.stats.low.toFixed(1)}% 至 ${row.stats.high.toFixed(1)}%`:`${row.label}：尚無樣本`">
      <template v-if="row.stats"><span class="interval" :style="{left:row.stats.low+'%',width:row.stats.high-row.stats.low+'%'}"></span><span class="point" :style="{left:row.stats.rate+'%'}"></span></template>
      <span v-if="row.base" class="baseline" :style="{left:row.base.rate+'%'}" :title="`整體基準 ${row.base.rate.toFixed(1)}%（${row.baseline.declines}／${row.baseline.count}）`"></span>
     </div>
     <div class="rate-value"><strong>{{row.stats?row.stats.rate.toFixed(1)+'%':'—'}}</strong><small>收跌 {{row.sample.declines}}／{{row.sample.count}} 次</small><small v-if="row.stats">區間 {{row.stats.low.toFixed(1)}}～{{row.stats.high.toFixed(1)}}%</small><small v-if="row.base">基準 {{row.base.rate.toFixed(1)}}% · {{row.baseline.count}} 次</small></div>
    </article>
    <p class="method">區間採 <a href="https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm" target="_blank" rel="noopener noreferrer">Wilson 95% 方法</a>，樣本少時通常較寬。此方法假設樣本獨立；交易日與持有期間可能重疊，區間僅供描述，不是未來預測範圍，也不能據此宣稱模型已校準。</p>
   </template><p v-else class="method">這份預報沒有已保存的歷史樣本，暫不繪製比例。</p>
  </div>
  <div class="coverage"><h3>總經與籌碼觀測範圍 <small>{{coverage.filter(row=>row.count).length}}／{{coverage.length}} 類有觀測資料</small></h3><p>顏色只表示本份預報有無資料，不表示利空程度、資料完整度或新鮮度。</p><div class="coverage-grid"><div v-for="row in coverage" :key="row.key" :class="{available:row.count}"><b>{{row.label}}</b><span>{{row.count?row.count+' 項觀測':'待補資料'}}</span><small>{{row.dates}}</small></div></div></div>
 </details>
</template>

<style scoped>
.risk-visual{background:#fff;border:1px solid #e1e7df;border-radius:16px;overflow:hidden;margin:18px 0;color:#30443b}.risk-visual>.section-summary{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:22px 26px;border-bottom:1px solid #edf0e9}.eyebrow{letter-spacing:.15em;font-size:10px;color:#86988c;font-weight:800}h2{font-size:23px;margin:6px 0 0}h3{font-size:17px;margin:0}.saved{font-size:12px;color:#798a7e}.overview{display:grid;grid-template-columns:minmax(240px,340px) 1fr;align-items:center;background:#fafcf9;padding:12px 26px 22px;gap:30px}.orb-panel svg{width:100%;max-height:270px;display:block}.orb-caption{font-size:11px;fill:#77897e}.orb-label{font-size:25px;font-weight:800}.orb-panel p{text-align:center;font-size:11px;color:#77897e;margin:0}.section-label{font-size:12px;color:#788a7f;font-weight:700}.change-panel h3{font-size:27px;margin:12px 0}.comparison{display:flex;align-items:center;gap:18px;font-size:16px}.comparison b{color:#9aa69e}.change-panel p{font-size:14px;line-height:1.8;max-width:650px}.change-panel small{color:#7b8b80}.scale-note{font-size:11px;color:#839087;line-height:1.7;border-top:1px solid #e5ebe4;margin-top:16px;padding-top:12px;max-width:650px}.trail{padding:18px 26px;border-top:1px solid #edf0e9}.section-label span{font-weight:400;margin-left:8px}.trail-items{display:flex;gap:10px;overflow-x:auto;margin-top:10px;padding-bottom:6px}.trail button{flex:0 0 146px;border:1px solid transparent;background:#fafbf9;border-radius:12px;padding:10px;cursor:pointer;color:#50645a}.trail button.active{border-color:#a0b7a7;background:#f0f5ef}.mini-orb-space{height:53px;display:flex;align-items:center;justify-content:center}.mini-orb{display:block;border:1px solid;border-radius:50%}.trail b,.trail small{display:block}.trail b{font-size:12px;margin:4px}.trail small{font-size:10px;line-height:1.7;color:#7e8c82}.stats-panel,.coverage{padding:22px 26px;border-top:1px solid #edf0e9}.stats-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.stats-head p,.coverage p{font-size:12px;color:#7a8a80;line-height:1.6;margin:7px 0 14px}.toggle{display:flex;background:#f0f3ed;padding:4px;border-radius:10px;flex-shrink:0}.toggle button{border:0;background:transparent;padding:8px 12px;color:#758675;cursor:pointer;border-radius:7px;font-weight:700}.toggle button.active{background:#fff;color:#355e4d;box-shadow:0 1px 4px #28392d15}.chart-legend{display:flex;align-items:center;gap:8px;font-size:11px;color:#829087;flex-wrap:wrap;margin:12px 0 20px}.dot{width:9px;height:9px;border-radius:50%;background:#ac625e}.line{height:6px;width:24px;background:#eaccc6;border-radius:8px;margin-left:12px}.tick{height:14px;width:2px;background:#548674;margin-left:12px}.rate-axis{margin-left:180px;margin-right:155px;display:flex;justify-content:space-between;font-size:10px;color:#99a69d}.rate-row{display:grid;grid-template-columns:160px minmax(90px,1fr) 135px;gap:20px;align-items:center;padding:16px 0;border-bottom:1px solid #edf0e9}.rate-title b,.rate-title small{display:block}.rate-title b{font-size:13px}.rate-title small{font-size:10px;color:#92a096;line-height:1.6;margin-top:4px}.rate-track{height:36px;position:relative;background:linear-gradient(#dfe5df,#dfe5df) center/100% 1px no-repeat;border-left:1px solid #e7ebe7;border-right:1px solid #e7ebe7}.rate-track:before{content:'';position:absolute;left:50%;height:100%;border-left:1px dashed #e1e7e1}.interval{position:absolute;height:8px;top:14px;background:#eaccc6;border-radius:8px}.point{position:absolute;width:12px;height:12px;top:12px;background:#ac625e;border:2px solid #fff;box-sizing:border-box;border-radius:50%;transform:translateX(-50%)}.baseline{position:absolute;height:26px;top:5px;width:2px;background:#548674;transform:translateX(-50%)}.rate-value{text-align:right}.rate-value strong{font-size:20px;color:#a45f56}.rate-value small{display:block;color:#839086;font-size:10px;line-height:1.7}.method{color:#8b958d;font-size:11px;line-height:1.8;margin-bottom:0}.method a{color:#577b6a}.coverage h3 small{font-size:11px;font-weight:400;color:#87958b;margin-left:10px}.coverage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.coverage-grid>div{background:#f6f6f3;border:1px dashed #dce1d9;border-radius:9px;padding:12px;min-width:0}.coverage-grid>div.available{background:#f0f6f1;border:1px solid #dbe8dd}.coverage b,.coverage span,.coverage small{display:block}.coverage b{font-size:12px}.coverage span{font-size:11px;margin-top:5px;color:#859589}.coverage-grid small{font-size:10px;margin-top:6px;color:#8a988d;overflow-wrap:anywhere}button:focus-visible{outline:2px solid #668b78;outline-offset:2px}@media(max-width:800px){.overview{grid-template-columns:1fr;gap:8px}.orb-panel{max-width:300px;margin:auto;width:100%}.risk-visual>.section-summary,.stats-head{align-items:flex-start;flex-direction:column}.coverage-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.rate-row{grid-template-columns:90px minmax(60px,1fr) 110px;gap:10px}.rate-axis{margin-left:100px;margin-right:120px}.rate-title small{display:none}.stats-panel,.coverage,.trail,.risk-visual>.section-summary,.overview{padding-left:16px;padding-right:16px}}@media(max-width:420px){.rate-row{grid-template-columns:1fr 110px}.rate-title{grid-column:1/-1}.rate-axis{margin-left:0}.coverage-grid{grid-template-columns:1fr 1fr}h2{font-size:20px}}
</style>
