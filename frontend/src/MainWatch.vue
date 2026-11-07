<script setup lang="ts">
import {localFetch,readJson} from './http'
import {ref,computed,onMounted,onUnmounted} from 'vue'
import Icon from './Icon.vue'
import RankTable from './RankTable.vue'
import type {MainWatchReport} from './types'
const props=defineProps<{branchSyncRunning:boolean}>()
const emit=defineEmits<{sync:[]}>()
const availableDates=ref<string[]>([])
const report=ref<MainWatchReport|null>(null),scope=ref('market'),rankMetric=ref<'lots'|'value'>('lots'),date=ref(''),error=ref(''),loading=ref(true),refreshing=ref(false)
let poll:number|undefined,epoch=0
const fmt=(n:number|null|undefined,d=0)=>n==null?'—':n.toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d})
const signed=(n:number|null|undefined)=>n==null?'—':(n>0?'+':'')+fmt(n)
const tone=(n:number|null|undefined)=>n==null?'muted':n>0?'positive':n<0?'negative':'muted'
const marketRunning=computed(()=>refreshing.value||report.value?.market.job?.status==='running')
const totals=computed(()=>report.value?.watched.totals)
const marketBuyRows=computed(()=>rankMetric.value==='value'?(report.value?.market.buy_value_top||[]):(report.value?.market.buy_top||[]))
const marketSellRows=computed(()=>rankMetric.value==='value'?(report.value?.market.sell_value_top||[]):(report.value?.market.sell_top||[]))
const watchedBuyRows=computed(()=>rankMetric.value==='value'?(report.value?.watched.buy_value_top||[]):(report.value?.watched.buy_top||[]))
const watchedSellRows=computed(()=>rankMetric.value==='value'?(report.value?.watched.sell_value_top||[]):(report.value?.watched.sell_top||[]))
async function load(){
 const id=++epoch
 try{const response=await localFetch('/api/main-watch'+(date.value?'?date='+encodeURIComponent(date.value):''));const data=await readJson(response);if(id!==epoch)return;if(!response.ok)throw new Error(data.error||'無法讀取主力統計');report.value=data;availableDates.value=data.available_dates;error.value=''}catch(e){if(id===epoch)error.value=(e as Error).message}finally{if(id===epoch)loading.value=false}
}
async function selectDate(){report.value=null;loading.value=true;await load()}
async function refreshMarket(){
 refreshing.value=true
 try{const response=await localFetch('/api/main-watch/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const result=await readJson(response);if(!response.ok)throw new Error(result.error||'無法更新全市場排行');await load()}catch(e){error.value=(e as Error).message}finally{refreshing.value=false}
}
onMounted(()=>{void load();poll=window.setInterval(()=>{void load()},10000)})
onUnmounted(()=>{epoch++;window.clearInterval(poll)})
</script>
<template>
 <div class="main-watch">
  <header class="page-header"><div><div class="eyebrow">DAILY FLOW WATCH</div><h1>主力關注</h1><p>全市場主力與我的關注券商 · 每日買超、賣超前 30 名</p></div><div class="header-actions"><label class="date-control"><Icon name="clock" :size="17"/><select v-model="date" @change="selectDate" aria-label="主力關注日期"><option value="">最新已存資料{{report?.as_of?' · '+report.as_of:''}}</option><option v-for="day in availableDates" :key="day" :value="day">{{day}}</option></select></label><button v-if="scope==='market'" class="primary" :disabled="marketRunning" @click="refreshMarket"><Icon name="refresh" :class="{spinning:marketRunning}" :size="17"/>{{marketRunning?'主力排行更新中':'更新全市場主力'}}</button><button v-else class="primary" :disabled="branchSyncRunning" @click="emit('sync')"><Icon name="refresh" :class="{spinning:branchSyncRunning}" :size="17"/>{{branchSyncRunning?'分點同步中':'更新關注券商'}}</button></div></header>
  <div class="main-watch-tabs" role="tablist" aria-label="主力關注範圍"><button role="tab" :aria-selected="scope==='market'" :class="{active:scope==='market'}" @click="scope='market'"><Icon name="pulse" :size="18"/>全市場主力<span>上市＋上櫃</span></button><button role="tab" :aria-selected="scope==='watched'" :class="{active:scope==='watched'}" @click="scope='watched'"><Icon name="star" :size="18"/>我的關注券商<span>{{totals?.total_branches||0}} 個分點</span></button></div>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="load">重新讀取</button></div>
  <div v-if="loading&&!report" class="watch-loading" role="status">正在整理當日排行…</div>
  <template v-if="report">
   <template v-if="scope==='market'">
    <div class="major-definition"><Icon name="info" :size="18"/><p><strong>{{report.as_of||'尚無日期'}} · MoneyDJ 主力口徑</strong><span>取個股當日買賣超前 15 名券商，合併上市、上櫃排行，包含來源列出的 ETF。與下方「我的關注券商」使用不同範圍。</span></p><a :href="report.market.definition_url" target="_blank" rel="noopener">查看定義 <Icon name="external" :size="13"/></a></div>
    <div v-if="report.market.job" class="sync-status" :class="{'has-error':['failed','interrupted'].includes(report.market.job.status)}"><Icon :name="marketRunning?'refresh':report.market.job.status==='complete'?'check':'info'" :size="16" :class="{spinning:marketRunning}"/><span>{{report.market.job.message}}</span></div>
    <div v-if="!report.market.available" class="major-empty panel"><Icon name="database" :size="28"/><h2>此日期尚未保存全市場主力排行</h2><p>來源提供最新單日榜單，開始追蹤後每日保存。{{report.market.latest_date?'目前最新已保存：'+report.market.latest_date+'。':'按「更新全市場主力」取得最新排行。'}}</p><button v-if="report.market.latest_date" class="secondary" @click="date=report.market.latest_date;selectDate()">查看 {{report.market.latest_date}}</button><p class="muted">更新只讀取主力排行榜，不會將全市場券商加入追蹤名單。</p></div>
    <template v-else><div class="ranking-context"><div><strong>當日主力買賣超</strong><span>{{report.as_of}} · {{rankMetric==='lots'?'張數':'估算金額'}}排行 · 買賣兩榜各 30 檔</span></div><div class="segmented" role="tablist" aria-label="主力買賣超排行口徑"><button role="tab" :aria-selected="rankMetric==='lots'" :class="{selected:rankMetric==='lots'}" @click="rankMetric='lots'">張數</button><button role="tab" :aria-selected="rankMetric==='value'" :class="{selected:rankMetric==='value'}" @click="rankMetric='value'">金額</button></div></div><div class="ranking-grid"><RankTable :key="report.as_of+'market-buy-'+rankMetric" :date="report.as_of||''" :rows="marketBuyRows" direction="buy" :metric="rankMetric"/><RankTable :key="report.as_of+'market-sell-'+rankMetric" :date="report.as_of||''" :rows="marketSellRows" direction="sell" :metric="rankMetric"/></div><div class="major-sources"><span>取得時間 {{report.market.fetched_at?.slice(0,16).replace('T',' ')}}</span><a v-for="source in report.market.sources" :key="source.url" :href="source.url" target="_blank" rel="noopener">{{source.label}}原始排行 <Icon name="external" :size="12"/></a></div></template>
   </template>
   <template v-else>
    <div class="major-definition"><Icon name="info" :size="18"/><p><strong>{{report.as_of||'尚無日期'}} · 目前追蹤名單合計</strong><span>同一股票先加總各分點的來源淨張數，再分別排買超與賣超前 30 名。點股票名稱可展開分點貢獻。</span></p></div>
    <div class="watch-totals"><article><span>當日買進</span><strong>{{fmt(totals?.buy)}}<small> 張</small></strong></article><article><span>當日賣出</span><strong>{{fmt(totals?.sell)}}<small> 張</small></strong></article><article><span>當日淨買賣超</span><strong :class="tone(totals?.net)">{{signed(totals?.net)}}<small> 張</small></strong></article><article><span>已取得當日榜單</span><strong>{{totals?.observed_branches}}<small> / {{totals?.total_branches}} 分點</small></strong></article></div>
    <div v-if="totals&&totals.ready_branches<totals.total_branches" class="main-watch-warning" role="status"><Icon name="info" :size="17"/>目前為暫計結果：{{totals.ready_branches}} / {{totals.total_branches}} 個分點的已發現個股明細齊全。缺少的分點不會視為零交易；回補後排名可能改變。</div>
    <div class="ranking-context"><div><strong>關注券商買賣超合計</strong><span>{{totals?.buy_stocks}} 檔淨買超 · {{totals?.sell_stocks}} 檔淨賣超 · 已觀察股票 {{totals?.stocks}} 檔</span></div><div class="segmented" role="tablist" aria-label="關注券商排行口徑"><button role="tab" :aria-selected="rankMetric==='lots'" :class="{selected:rankMetric==='lots'}" @click="rankMetric='lots'">張數</button><button role="tab" :aria-selected="rankMetric==='value'" :class="{selected:rankMetric==='value'}" @click="rankMetric='value'">金額</button></div></div>
    <div class="ranking-grid"><RankTable :key="report.as_of+'watched-buy-'+rankMetric" :date="report.as_of||''" :rows="watchedBuyRows" direction="buy" watched :metric="rankMetric"/><RankTable :key="report.as_of+'watched-sell-'+rankMetric" :date="report.as_of||''" :rows="watchedSellRows" direction="sell" watched :metric="rankMetric"/></div>
    <section class="panel watched-branches"><div class="ranking-heading"><div><h2>目前關注券商</h2><p>統計日期 {{report.as_of||'—'}} · {{totals?.total_branches}} 個明確分點</p></div><span class="count-badge">本機追蹤名單</span></div><div class="table-scroll"><table><thead><tr><th>券商分點</th><th>買進張數</th><th>賣出張數</th><th>買賣超張數</th><th>已觀察股票</th><th>資料狀態</th></tr></thead><tbody><tr v-for="branch in report.watched.branches" :key="branch.broker+':'+branch.branch"><td>{{branch.name}}</td><td>{{fmt(branch.buy)}}</td><td>{{fmt(branch.sell)}}</td><td :class="tone(branch.net)">{{signed(branch.net)}}</td><td>{{branch.observed?branch.stocks:'—'}}</td><td><span class="state-pill" :class="{pending:branch.status!=='ready'}">{{branch.status==='ready'?'已發現個股明細齊全':branch.status==='partial'?'個股明細待補':'當日榜單未取得'}}</span></td></tr></tbody></table></div></section>
    <p class="major-footnote">榜單用於發現股票，未曾進榜的股票仍可能遺漏；合計不等於券商實際全部交易。金額以淨張數 × 1,000 股 × 當日收盤價估算，不含交易成本；缺少收盤價的股票不列入金額排行。使用來源公布的淨張數，各欄取整後買進減賣出可能與淨額不同。未同步日期不沿用前一天資料。</p>
   </template>
  </template>
 </div>
</template>
