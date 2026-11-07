<script setup lang="ts">
import {computed, inject} from 'vue'
import type {ComputedRef} from 'vue'
import {stockHistory} from './stockHistory'
import type {StockHistoryContext} from './stockHistory'
const props = defineProps<{modelKey: string, symbol: string, asOf?: string | null}>()
const context = inject<ComputedRef<StockHistoryContext>>('stock-history')
const history = computed(() => stockHistory(context?.value.models[props.modelKey],
 props.symbol, props.asOf, context?.value.calendar || [], props.modelKey))
const n = (value: number | null) => value === null ? '—' : value.toLocaleString('zh-TW',
 {minimumFractionDigits: 1, maximumFractionDigits: 1})
const explanation = computed(() => `${props.modelKey.toUpperCase()} 同策略、同股票，截至 ${history.value.asOf || '未載入'} 收盤的已完成交易。勝率＝成本後獲利筆數／有損益紀錄的完成筆數（損益兩平亦列入分母）。平均依交易日、不含進場日，同日買賣為0日；目前持股不列入。${props.modelKey === 'v4' ? '借款利息未分攤至個股。' : ''}歷史勝率不是下一筆上漲機率。`)
</script>
<template>
 <div class="stock-history" :data-model="modelKey" :data-symbol="symbol" :title="explanation">
  <template v-if="history.asOf && history.completed">
   <span class="history-rate">歷史勝率 {{n(history.winRate)}}% · {{history.wins}}／{{history.completed}} 筆</span>
   <span>平均持有 {{n(history.all.avgDays)}} 日</span>
   <span>獲利 {{n(history.win.avgDays)}} 日／虧損 {{n(history.loss.avgDays)}} 日</span>
   <span v-if="history.all.samples < history.completed" class="history-note">天數可用 {{history.all.samples}}／{{history.completed}} 筆</span>
  </template>
  <span v-else-if="!history.asOf">歷史資料未載入</span>
  <span v-else-if="history.missingResult">歷史損益資料不足</span>
  <span v-else>尚無完成交易</span>
 </div>
</template>
<style scoped>
.stock-history{display:flex;flex-direction:column;gap:3px;margin-top:9px;padding-top:7px;border-top:1px dashed #e1e7de;font-size:12px;line-height:1.5;font-weight:400;color:#778371;text-align:left;white-space:nowrap}.history-rate{color:#4f6655;font-weight:600}.history-note{color:#927c48}
</style>
