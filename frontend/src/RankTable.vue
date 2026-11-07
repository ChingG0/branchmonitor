<script setup lang="ts">
import {ref,watch} from 'vue'
import Icon from './Icon.vue'
import TopBrokers from './TopBrokers.vue'
import FlowCircle from './FlowCircle.vue'
import type {MajorStock} from './types'
const props=defineProps<{rows:MajorStock[];direction:'buy'|'sell';watched?:boolean;date:string;metric?:'lots'|'value'}>()
const expanded=ref('')
const brokerNames=ref<Record<string,string[]>>({})
const names=(stock:MajorStock)=>brokerNames.value[stock.symbol]||stock.top_broker_names||[]
const balances=ref<Record<string,MajorStock['major_balance']>>({})
const balance=(stock:MajorStock)=>balances.value[stock.symbol]??stock.major_balance
function remember(symbol:string,names:string[],value:MajorStock['major_balance']){brokerNames.value[symbol]=names;balances.value[symbol]=value}
const period=(stock:MajorStock)=>balance(stock)?balance(stock)!.start+' ～ '+balance(stock)!.end+'（5 個交易日）':'截至 '+props.date+' 的近 5 個交易日'
watch(()=>props.rows,rows=>{if(!rows.some(row=>row.symbol===expanded.value))expanded.value=''})
const fmt=(n:number|null|undefined,digits=0)=>n==null?'—':n.toLocaleString('zh-TW',{maximumFractionDigits:digits,minimumFractionDigits:digits})
const signed=(n:number|null|undefined)=>n==null?'—':(n>0?'+':'')+fmt(n)
const signedMoney=(n:number|null|undefined)=>n==null?'—':(n>0?'+':'')+fmt(n/1e8,2)
const branchMoney=(net:number,close:number|null)=>close==null?null:net*1000*close
const tone=(n:number|null|undefined)=>n==null?'muted':n>0?'positive':n<0?'negative':'muted'
</script>
<template>
 <section class="panel ranking-panel">
  <div class="ranking-heading"><div><span class="ranking-direction" :class="direction==='buy'?'positive':'negative'"><Icon name="chart" :size="18"/>{{direction==='buy'?'買超':'賣超'}}前 30 名</span><p>依當日淨{{direction==='buy'?'買超':'賣超'}}{{metric==='value'?'估算金額':'張數'}}排序</p></div><span class="count-badge">{{rows.length}} 檔</span></div>
  <p class="flow-legend">個股圓圈：近 5 日主力名單互抵 · <span class="positive">買方較多</span> / <span class="negative">賣方較多</span> / 平手 · 灰色「待」為尚未載入</p>
  <div class="table-scroll"><table class="ranking-table"><thead><tr><th>排名</th><th class="stock-cell">股票</th><th>買進 <small>張</small></th><th>賣出 <small>張</small></th><th>買賣超 <small>{{metric==='value'?'億元':'張'}}</small></th><th>收盤價</th><th>近 5 日主力</th></tr></thead>
   <tbody v-for="(stock,i) in rows" :key="stock.symbol"><tr :class="{'ranking-expanded':expanded===stock.symbol}"><td class="ranking-number">{{String(i+1).padStart(2,'0')}}</td><td class="stock-cell"><button class="ranking-stock" :aria-expanded="expanded===stock.symbol" @click="expanded=expanded===stock.symbol?'':stock.symbol"><strong><FlowCircle :buy="balance(stock)?.buy??null" :sell="balance(stock)?.sell??null" :period="period(stock)" scope="買超、賣超各前 15 名主力券商互抵"/>{{stock.name}} <Icon name="chevron" :size="13" :class="{rotated:expanded===stock.symbol}"/></strong><small>{{stock.symbol}} · {{watched?stock.buy_branches+' 買超 / '+stock.sell_branches+' 賣超分點':stock.market}}</small></button></td><td>{{fmt(stock.buy)}}</td><td>{{fmt(stock.sell)}}</td><td :class="tone(stock.net)"><strong>{{metric==='value'?signedMoney(stock.net_value):signed(stock.net)}}</strong></td><td>{{fmt(stock.close,2)}}</td><td class="broker-preview"><button class="secondary" :aria-expanded="expanded===stock.symbol" @click="expanded=expanded===stock.symbol?'':stock.symbol">{{expanded===stock.symbol?'收合明細':'交易額前 3 名'}} <small>估算</small></button><small v-if="names(stock).length">{{names(stock).join(' · ')}}</small></td></tr>
    <tr v-if="expanded===stock.symbol" class="contribution-row"><td colspan="7"><TopBrokers :key="date+':'+stock.symbol" :symbol="stock.symbol" :date="date" @loaded="(names,value)=>remember(stock.symbol,names,value)"/><template v-if="watched"><div class="contribution-heading"><strong>關注分點的當日貢獻</strong><small>按淨張數絕對值排序；買賣方向互相抵銷後才計算上方合計。</small></div><table class="contribution-table"><thead><tr><th>分點</th><th>買進張數</th><th>賣出張數</th><th>買賣超{{metric==='value'?'（億元）':'（張）'}}</th><th>個股明細</th></tr></thead><tbody><tr v-for="branch in stock.contributors" :key="branch.broker+':'+branch.branch"><td>{{branch.name}}</td><td>{{fmt(branch.buy)}}</td><td>{{fmt(branch.sell)}}</td><td :class="tone(branch.net)">{{metric==='value'?signedMoney(branchMoney(branch.net,stock.close)):signed(branch.net)}}</td><td>{{branch.history_verified?'已驗證':'待補齊'}}</td></tr></tbody></table></template></td></tr>
   </tbody><tbody v-if="!rows.length"><tr><td colspan="7" class="empty-row"><Icon name="database" :size="24"/><strong>此日期沒有可列出的{{direction==='buy'?'買超':'賣超'}}排行</strong><p>請確認資料日期與更新狀態。</p></td></tr></tbody>
  </table></div>
 </section>
</template>
