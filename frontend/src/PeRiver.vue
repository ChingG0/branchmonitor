<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import PeValuation from './PeValuation.vue'
import LiquidityRiver from './LiquidityRiver.vue'
type River={available:boolean;as_of:string|null;liquidity?:{available:boolean;as_of:string|null;points:any[];missing_dates:string[];warning:string;note:string;source_url:string;day_trade_source_url:string};[key:string]:any}
const props=defineProps<{data?:River;embedded?:boolean}>()
const saved=ref<River|null>(null),error=ref(''),busy=ref(false),mode=ref<'liquidity'|'valuation'>('liquidity')
let alive=true
const model=computed(()=>props.data||saved.value)
async function load(){try{const response=await localFetch('/api/market-pe'),data=await readJson(response);if(!response.ok)throw new Error(data.error||'讀取失敗');if(alive)saved.value=data}catch(e){if(alive)error.value=String(e)}}
async function refresh(){
 busy.value=true;error.value=''
 try{
  const response=await localFetch('/api/market-monitor/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card:'pe_river'})});const result=await readJson(response);if(!response.ok)throw new Error(result.error||'更新未啟動')
  for(let i=0;i<180&&alive;i++){await new Promise(resolve=>setTimeout(resolve,1000));if(!alive)return;const r=await localFetch('/api/market-monitor'),data=await readJson(r);if(!r.ok)throw new Error(data.error||'讀取更新狀態失敗');if(!data.job.running){await load();error.value=data.cards.find((c:any)=>c.id==='pe_river')?.error||'';return}}
  if(alive)error.value='資料仍在背景更新，稍後重新開啟此頁即可查看。'
 }catch(e){if(alive)error.value=String(e)}finally{if(alive)busy.value=false}
}
onMounted(()=>{if(!props.data)load()});onUnmounted(()=>{alive=false})
</script>
<template>
 <section class="market-river" aria-label="大盤價量與估值河流">
  <header class="head"><div><small v-if="!embedded">TAIEX · PRICE & TURNOVER</small><h3 v-if="!embedded">大盤價量河流</h3><p>{{mode==='liquidity'?'收盤價與扣當沖後成交額，觀察一季或一年的相對區間。':'官方月末本益比與每日指數推估值。'}}</p></div><div class="controls"><button :aria-pressed="mode==='liquidity'" @click="mode='liquidity'">價量河流</button><button :aria-pressed="mode==='valuation'" @click="mode='valuation'">本益比</button><button v-if="!embedded" class="refresh" :disabled="busy" @click="refresh">{{busy?'更新中…':'更新河流圖'}}</button></div></header>
  <p v-if="error" class="error" role="status">{{error}}</p>
  <LiquidityRiver v-if="mode==='liquidity'" :data="model?.liquidity"/>
  <PeValuation v-else :data="model as any" embedded/>
 </section>
</template>
<style scoped>
.market-river{background:#fff;border:1px solid #e1e7de;border-radius:14px;margin:20px 0;overflow:hidden;color:#425440}.head{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:22px 24px;flex-wrap:wrap}.head small{letter-spacing:.14em;color:#8b9a83;font-size:10px}.head h3{font-size:20px;margin:6px 0}.head p{font-size:12px;color:#7d8d77;margin:5px 0}.controls{display:flex;gap:4px;align-items:center;flex-wrap:wrap;background:#f0f3ed;padding:4px;border-radius:9px}.controls button{font:inherit;font-size:13px;padding:9px 14px;border:0;border-radius:6px;color:#64755d;background:transparent;cursor:pointer}.controls button[aria-pressed=true]{background:white;color:#31593d;box-shadow:0 1px 4px #263d2420}.controls .refresh{margin-left:7px;border:1px solid #dce4d7;background:white}.controls .refresh:disabled{opacity:.6;cursor:wait}.error{margin:8px 24px;color:#92723f;background:#faf4e7;padding:10px;font-size:12px}@media(max-width:650px){.head{padding:18px}.controls{width:100%}}
</style>
