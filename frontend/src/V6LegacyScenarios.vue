<script setup lang="ts">
import V6ScenarioChart from './V6ScenarioChart.vue'
import type {V6ComparisonSnapshot} from './v6ComparisonTypes'
defineProps<{snapshot:V6ComparisonSnapshot}>()
const fmt=(n:number|null|undefined,d=1)=>n==null?'—':n.toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d})
</script>
<template><div class="legacy-scenarios"><p>以下為這份舊快照當時保存的「相似80日」結果，不是新T1方法，也沒有事後重算或改寫。</p><article v-for="s in snapshot.strategies" :key="s.key"><h4>{{s.label}}｜舊版保存值</h4><template v-if="s.scenario.status==='reference'"><V6ScenarioChart :snapshot="snapshot" :scenario="s.scenario" :label="s.label+'（舊版紀錄）'"/><div class="old-probabilities"><span v-for="c in s.scenario.classes" :key="c.key">{{c.label}} {{fmt(c.probability_pct)}}%</span></div><p>{{s.scenario.note}}</p><p v-if="s.scenario_validation">當時回溯Brier {{fmt(s.scenario_validation.all.brier,3)}}；此數字與新研究日期、量尺不同，不直接比較。</p></template><p v-else>{{s.scenario.note}}</p></article></div></template>
<style scoped>.legacy-scenarios{color:#85937b;font-size:12px;line-height:1.8}.legacy-scenarios article{border-top:1px solid #e8ede3;margin-top:16px;padding:14px 0}.legacy-scenarios h4{color:#5e795b}.old-probabilities{display:flex;flex-wrap:wrap;gap:20px;font-size:15px;font-weight:700}</style>
