<script setup lang="ts">
import {localFetch,readJson} from './http'
import {ref,onMounted,onUnmounted} from 'vue'
import Icon from './Icon.vue'
import type {MajorStock} from './types'
const props=defineProps<{symbol:string;date:string}>()
const emit=defineEmits<{loaded:[names:string[],balance:MajorStock['major_balance']]}>()
type Broker={broker:string;branch:string;name:string;buy:number;sell:number;net:number;buy_amount:number;sell_amount:number;turnover:number}
type Result={status:string;message:string;payload:null|{start:string;end:string;days:string[];candidate_count:number;top:Broker[];scope:string;formula:string;source:string;pool_balance?:{buy:number;sell:number;net:number;branches:number}}}
const result=ref<Result|null>(null),error=ref(''),loading=ref(true)
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
const fmt=(n:number,d=0)=>n.toLocaleString('zh-TW',{maximumFractionDigits:d})
const amount=(n:number)=>n>=1e8?fmt(n/1e8,2)+' 億':fmt(n/1e4,1)+' 萬'
async function load(start=false){
 loading.value=true;error.value=''
 try{
  const response=await localFetch('/api/main-watch/brokers'+(start?'':'?'+new URLSearchParams({symbol:props.symbol,date:props.date})),start?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:props.symbol,date:props.date})}:undefined)
  const data=await readJson(response);if(!alive)return
  if(!response.ok)throw new Error(data.error||'無法讀取五日主力')
  result.value=data
  if(data.status==='idle'){await load(true);return}
  if(data.status==='complete'&&data.payload)emit('loaded',data.payload.top.map((r:Broker)=>r.name),data.payload.pool_balance?{...data.payload.pool_balance,start:data.payload.start,end:data.payload.end}:null)
  if(['queued','running'].includes(data.status))timer=setTimeout(()=>{void load()},2500)
 }catch(e){if(alive)error.value=(e as Error).message}finally{if(alive)loading.value=false}
}
onMounted(()=>{void load()})
onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>
<template>
 <section class="top-brokers" aria-label="近五日主力交易額前三名">
  <div class="contribution-heading"><strong>近 5 日主力交易額前 3 名 <span class="estimate-label">估算</span></strong><small>以買進金額＋賣出金額排名，兩個方向都計入交易額。</small></div>
  <div v-if="error" class="error-banner" role="alert">{{error}} <button :disabled="loading" @click="load()">重新讀取</button></div>
  <template v-if="result?.payload&&result.status==='complete'">
   <p class="broker-window">{{result.payload.start}} ～ {{result.payload.end}} · {{result.payload.days.length}} 個交易日 · 主力名單 {{result.payload.candidate_count}} 家</p>
   <p v-if="result.payload.pool_balance" class="broker-window">全份主力名單互抵：買進 {{fmt(result.payload.pool_balance.buy)}} 張 − 賣出 {{fmt(result.payload.pool_balance.sell)}} 張 = <strong :class="result.payload.pool_balance.net>0?'positive':result.payload.pool_balance.net<0?'negative':'muted'">{{result.payload.pool_balance.net>0?'+':''}}{{fmt(result.payload.pool_balance.net)}} 張</strong>（股票旁圓圈，涵蓋 {{result.payload.pool_balance.branches}} 家）</p>
   <table class="contribution-table"><thead><tr><th>名次／券商分點</th><th>交易額 <small>估算</small></th><th>買進金額 <small>估算</small></th><th>賣出金額 <small>估算</small></th><th>五日買賣超 <small>張</small></th></tr></thead><tbody><tr v-for="(branch,i) in result.payload.top" :key="branch.broker+':'+branch.branch"><td><span class="broker-rank">{{i+1}}</span><strong>{{branch.name}}</strong></td><td><strong>{{amount(branch.turnover)}}</strong></td><td>{{amount(branch.buy_amount)}}</td><td>{{amount(branch.sell_amount)}}</td><td :class="branch.net>0?'positive':branch.net<0?'negative':'muted'">{{branch.net>0?'+':''}}{{fmt(branch.net)}}</td></tr></tbody></table>
   <p class="broker-method">{{result.payload.scope}}<br>{{result.payload.formula}}<br>金額單位為新臺幣；來源張數取整與收盤價估算可能影響名次。<a :href="result.payload.source" target="_blank" rel="noopener">查看五日主力來源 <Icon name="external" :size="12"/></a></p>
  </template>
  <div v-else-if="result" class="broker-progress" role="status"><Icon :name="['queued','running'].includes(result.status)?'refresh':'info'" :class="{spinning:['queued','running'].includes(result.status)}" :size="16"/>{{result.message}}<button v-if="['failed','interrupted'].includes(result.status)" class="secondary" :disabled="loading" @click="load(true)">重新載入此個股</button></div>
  <p v-else-if="loading" class="broker-progress" role="status">正在讀取此個股的五日主力…</p>
 </section>
</template>
