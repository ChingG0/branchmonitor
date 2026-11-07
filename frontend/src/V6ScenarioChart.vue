<script setup lang="ts">
import {computed} from 'vue'
import type {V6ComparisonSnapshot,ThreeDayScenario} from './v6ComparisonTypes'
import {scenarioPlot} from './v6ScenarioPlot'
const props=defineProps<{snapshot:V6ComparisonSnapshot;scenario:ThreeDayScenario;label:string}>()
const plot=computed(()=>scenarioPlot(props.snapshot,props.scenario))
const fmt=(v:number,d=0)=>v.toLocaleString('zh-TW',{maximumFractionDigits:d})
</script>
<template>
 <figure class="scenario-chart">
  <svg viewBox="0 0 620 249" role="img" :aria-label="label+'：大盤實際收盤與後3交易日上漲、平盤、下跌歷史相似路徑'">
   <title>{{label}}｜後3交易日情境路徑</title>
   <rect :x="plot.anchorX" y="16" width="144" height="192" class="future"/>
   <rect x="52" :y="Math.min(...plot.bounds)" width="489" :height="Math.abs(plot.bounds[1]!-plot.bounds[0]!)" class="flat-band"/>
   <g v-for="v in plot.grid" :key="v"><line x1="52" x2="541" :y1="plot.y(v)" :y2="plot.y(v)" class="grid"/><text x="46" :y="plot.y(v)+4" text-anchor="end" class="tick">{{fmt(v)}}</text></g>
   <path :d="plot.history" class="actual"/>
   <line :x1="plot.anchorX" :x2="plot.anchorX" y1="16" y2="211" class="divider"/>
   <g v-for="c in plot.branches" :key="c.key" :class="c.key"><path v-if="c.d" :d="c.d" class="branch"/><circle v-if="c.path.length" :cx="plot.lastX" :cy="plot.y(c.path.at(-1)!.level)" r="3.5"/><text v-if="c.path.length" x="549" :y="plot.y(c.path.at(-1)!.level)+4" class="end-label">{{fmt(c.path.at(-1)!.level)}}</text></g>
   <circle :cx="plot.anchorX" :cy="plot.anchorY" r="4" class="anchor"/>
   <text x="54" y="238" class="tick">{{snapshot.history[0]?.date.slice(5)}}</text>
   <text :x="plot.anchorX-4" y="226" text-anchor="end" class="tick">{{snapshot.market_as_of.slice(5)}} 收盤</text>
   <g v-for="(date,i) in snapshot.forecast_dates" :key="date"><text :x="437+i*40" y="226" text-anchor="middle" class="tick">{{date.slice(5)}}</text><text :x="437+i*40" y="241" text-anchor="middle" class="tiny">第{{i+1}}日</text></g>
   <text x="411" y="12" class="tiny">歷史情境路徑</text>
  </svg>
  <figcaption>實線為已發生收盤；右側三線為各類相似樣本的中位路徑，並非預測每天一定如此。淡色帶為起點±0.5%。</figcaption>
 </figure>
</template>
<style scoped>
.scenario-chart{margin:14px 0 10px;min-width:0}.scenario-chart svg{width:100%;display:block;overflow:visible}.grid{stroke:#e9eee9;stroke-dasharray:3 4}.future{fill:#f7f8f2}.flat-band{fill:#edcf7b;opacity:.15}.actual{fill:none;stroke:#506b61;stroke-width:2.5}.divider{stroke:#a7b5aa;stroke-dasharray:4 4}.branch{fill:none;stroke-width:2.3;stroke-dasharray:5 3}.up{color:#c66657;fill:#c66657;stroke:#c66657}.flat{color:#b39131;fill:#b39131;stroke:#b39131}.down{color:#2e8879;fill:#2e8879;stroke:#2e8879}.end-label{stroke:none;font-size:10px;font-weight:700}.tick{fill:#788779;font-size:10px}.tiny{fill:#8c9688;font-size:9px}.anchor{fill:#fff;stroke:#506b61;stroke-width:2}.scenario-chart figcaption{font-size:11px;line-height:1.6;color:#81907d}
</style>
