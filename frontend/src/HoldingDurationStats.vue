<script setup lang="ts">
import type {HoldingDurationStats} from './holdingDurationTypes'
defineProps<{stats?:HoldingDurationStats,modelKey:string}>()
const days=(value:number|null|undefined)=>value==null?'—':value.toLocaleString('zh-TW',{minimumFractionDigits:1,maximumFractionDigits:1})
</script>

<template>
 <section class="holding-duration" :data-model="modelKey" aria-label="平均持股天數">
  <header><h3>平均持股天數</h3><span>已完成交易 · 截至 {{stats?.as_of||'—'}}</span></header>
  <div class="duration-groups">
   <article class="win"><small>獲利交易平均持有</small><strong>{{days(stats?.win.avg_days)}}<span v-if="stats?.win.avg_days!=null"> 交易日</span></strong><p>{{stats?.win.samples||0}} 筆獲利交易</p></article>
   <article class="loss"><small>虧損交易平均持有</small><strong>{{days(stats?.loss.avg_days)}}<span v-if="stats?.loss.avg_days!=null"> 交易日</span></strong><p>{{stats?.loss.samples||0}} 筆虧損交易</p></article>
  </div>
  <footer>依成本後損益分組，每筆等權；不含進場日，同日買賣為0日。持有中不計入。<template v-if="modelKey==='v9'">V9以本年同方向持倉為一筆，轉倉續抱不重算天數。</template><template v-if="modelKey==='v4'">V4借款利息計入帳戶，未分攤至個別交易。</template><span v-if="stats?.flat_count"> 損益兩平 {{stats.flat_count}} 筆另計。</span><span v-if="stats?.unavailable_count"> {{stats.unavailable_count}} 筆資料不足未納入。</span></footer>
 </section>
</template>

<style scoped>
.holding-duration{background:#fff;border:1px solid #dfe6dc;border-radius:15px;margin-bottom:18px;overflow:hidden;color:#485548}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid #edf0ea}h3{font-size:16px;margin:0}header span,small,p,footer{font-size:12px;color:#7b8878}.duration-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}article{padding:16px 20px;min-width:0}article+article{border-left:1px solid #edf0ea}strong{display:block;font:700 27px Georgia,serif;margin:8px 0}strong span{font:500 12px sans-serif;color:#7b8878}.win strong{color:#b75b50}.loss strong{color:#2d8060}p{margin:0}footer{padding:10px 20px;background:#fafbf8;border-top:1px solid #edf0ea;line-height:1.7}
@media(max-width:600px){header{align-items:flex-start;flex-direction:column}.duration-groups{grid-template-columns:1fr}article+article{border-left:0;border-top:1px solid #edf0ea}}
</style>
