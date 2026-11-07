<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import Icon from './Icon.vue'
type Row={symbol:string;name:string;amount:number;shares:number;count:number;vwap:number;min_price:number;max_price:number;types:string[];average_amount?:number|null;amount_ratio?:number|null;history_days?:number}
type Snapshot={date:string|null;rows:Row[];total_amount:number;total_shares:number;trade_count:number;source:string;note:string}
type Report={snapshot:Snapshot;available_dates:string[];fetched_at:string|null;job:{status:string;message:string}}
const report=ref<Report|null>(null),error=ref(''),selected=ref(''),target=ref(''),search=ref(''),sort=ref('ratio'),busy=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
const fmt=(v:number|null|undefined,d=0)=>v==null?'—':v.toLocaleString('zh-TW',{maximumFractionDigits:d})
const money=(v:number|null|undefined)=>v==null?'—':fmt(v/100000000,2)+' 億'
const lots=(v:number|null|undefined)=>v==null?'—':fmt(v/1000,3)+' 張'
const rows=computed(()=>[...(report.value?.snapshot.rows||[])].filter(r=>!search.value||(r.symbol+r.name).includes(search.value)).sort((a,b)=>sort.value==='amount'?b.amount-a.amount:sort.value==='count'?b.count-a.count:(b.amount_ratio||0)-(a.amount_ratio||0)||b.amount-a.amount))
async function load(){clearTimeout(timer);try{const response=await localFetch('/api/block-trades'+(selected.value?'?date='+encodeURIComponent(selected.value):''));const data=await readJson<Report>(response);if(!response.ok)throw new Error((data as any).error||'無法讀取鉅額交易');if(!alive)return;report.value=data;busy.value=data.job.status==='running';error.value='';if(busy.value)timer=setTimeout(load,1500)}catch(e){if(alive)error.value=(e as Error).message}}
async function refresh(){busy.value=true;error.value='';try{const response=await localFetch('/api/block-trades/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(target.value?{date:target.value}:{})});const data=await readJson(response);if(!response.ok)throw new Error(data.error||'無法更新鉅額交易');selected.value='';await load()}catch(e){error.value=(e as Error).message;busy.value=false}}
onMounted(load);onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>

<template>
 <section class="block-page">
  <header class="page-header"><div><div class="eyebrow">TWSE · BLOCK TRADES</div><h1>每日鉅額交易追蹤</h1><p>保存證交所單一證券鉅額交易，觀察成交金額與相較歷史是否放大。</p></div><button class="primary" :disabled="busy" @click="refresh"><Icon name="refresh" :class="{spinning:busy}"/>{{busy?'鉅額交易更新中':'更新最新交易日'}}</button></header>
  <div v-if="error" class="error-banner">{{error}}</div>
  <div class="block-tools"><label>查看保存日期<select v-model="selected" @change="load"><option value="">最新保存日期</option><option v-for="day in report?.available_dates" :key="day" :value="day">{{day}}</option></select></label><label>指定交易日<input v-model="target" type="date"></label><button :disabled="busy||!target" @click="refresh">更新指定日期</button><input v-model="search" placeholder="搜尋代碼或名稱"><label>排序<select v-model="sort"><option value="ratio">歷史放大倍數</option><option value="amount">成交金額</option><option value="count">成交筆數</option></select></label></div>
  <div class="block-cards"><article class="panel"><span>資料日期</span><strong>{{report?.snapshot.date||'待更新'}}</strong><p>{{report?.job.message}}</p></article><article class="panel"><span>鉅額成交總額</span><strong>{{money(report?.snapshot.total_amount)}}</strong><p>{{fmt(report?.snapshot.rows.length)}} 檔證券</p></article><article class="panel"><span>成交總張數</span><strong>{{lots(report?.snapshot.total_shares)}}</strong><p>{{fmt(report?.snapshot.trade_count)}} 筆單一證券鉅額交易</p></article></div>
  <section class="panel block-table"><table><thead><tr><th>證券</th><th>成交金額</th><th>歷史放大</th><th>成交張數</th><th>筆數／交易別</th><th>加權均價</th><th>價格區間</th></tr></thead><tbody><tr v-for="row in rows" :key="row.symbol"><td><b>{{row.name}}</b><small>{{row.symbol}}</small></td><td><b>{{money(row.amount)}}</b></td><td><b :class="{hot:(row.amount_ratio||0)>=2}">{{row.amount_ratio==null?'首次記錄':fmt(row.amount_ratio,2)+'×'}}</b><small>{{row.history_days||0}} 個歷史交易日 · 均額 {{money(row.average_amount)}}</small></td><td>{{lots(row.shares)}}</td><td>{{fmt(row.count)}} 筆<small>{{row.types.join('、')}}</small></td><td>{{fmt(row.vwap,2)}}</td><td>{{fmt(row.min_price,2)}}～{{fmt(row.max_price,2)}}</td></tr><tr v-if="!rows.length"><td colspan="7" class="empty">尚無鉅額交易快照，請按更新。</td></tr></tbody></table></section>
  <p class="block-note">{{report?.snapshot.note}} <a :href="report?.snapshot.source" target="_blank" rel="noopener">證交所原始資料 ↗</a></p>
 </section>
</template>

<style scoped>
.block-tools{display:flex;align-items:end;gap:14px;flex-wrap:wrap;margin:22px 0}.block-tools label{display:grid;gap:7px;font-size:.72rem;color:#829176}.block-tools input,.block-tools select,.block-tools button{border:1px solid #d9e2d2;border-radius:6px;background:white;padding:10px 12px;color:#4b643f}.block-tools>input{min-width:190px}.block-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:20px}.block-cards article{padding:23px}.block-cards span{font-size:.75rem;color:#849477}.block-cards strong{display:block;font-size:1.7rem;font-weight:500;margin:16px 0}.block-cards p{font-size:.69rem;color:#8b9980}.block-table{overflow:auto}.block-table table{width:100%;border-collapse:collapse;font-size:.78rem}.block-table th,.block-table td{padding:15px 14px;text-align:left;white-space:nowrap;border-bottom:1px solid #e9eee4}.block-table th{background:#f6f8f3;color:#87977b;font-weight:400}.block-table b,.block-table small{display:block}.block-table small{font-size:.65rem;color:#8d9b82;margin-top:6px}.block-table .hot{color:#b15a4b}.empty{text-align:center!important;padding:55px!important;color:#89977e}.block-note{font-size:.72rem;color:#87957c;line-height:1.8}.block-note a{text-decoration:underline;color:#5d7949}@media(max-width:800px){.block-cards{grid-template-columns:1fr}.block-table table{min-width:900px}}
</style>
