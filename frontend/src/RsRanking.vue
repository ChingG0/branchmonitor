<script setup lang="ts">
import {localFetch,readJson} from './http'
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import Icon from './Icon.vue'
import FlowCircle from './FlowCircle.vue'
import TopBrokers from './TopBrokers.vue'
import type {MajorStock} from './types'
const props=defineProps<{etf?:boolean}>()
const endpoint=computed(()=>props.etf?'/api/etf-rs':'/api/rs')
type Row={product_type?:string;close?:number;symbol:string;name:string;market:string;rs:number;ytd_pct:number;return_pct:number;excess_pct:number;major_balance?:MajorStock['major_balance']}
type Report={available:boolean;mode:string;as_of:string;anchor:string;year_base:string;index_ytd:number;index_close:number;index_base_close:number;fetched_at:string;universe:number;hidden:number;excluded:Record<string,number>;left:Row[];right:Row[];equal:Row[];job:null|{status:string;message:string}}
const period=ref('20'),report=ref<Report|null>(null),error=ref(''),loading=ref(false),starting=ref(false),expanded=ref('')
const localBalances=ref<Record<string,MajorStock['major_balance']>>({})
let timer:ReturnType<typeof setTimeout>|undefined,epoch=0,alive=true
const running=computed(()=>starting.value||report.value?.job?.status==='running')
const label=computed(()=>period.value==='ytd'?'YTD':period.value+' 日')
const totalShown=computed(()=>(report.value?.left.length||0)+(report.value?.right.length||0)+(report.value?.equal.length||0))
const fmt=(n:number|undefined,d=2)=>n==null?'—':n.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(n:number|undefined)=>n==null?'—':(n>0?'+':'')+fmt(n)+'%'
const balance=(row:Row)=>localBalances.value[report.value?.as_of+':'+row.symbol]??row.major_balance
const flowPeriod=(row:Row)=>balance(row)?balance(row)!.start+' ～ '+balance(row)!.end:'截至 '+report.value?.as_of+' 的近 5 個交易日'
function remember(row:Row,_names:string[],value:MajorStock['major_balance']){localBalances.value[report.value?.as_of+':'+row.symbol]=value}
async function load(){
 const id=++epoch;loading.value=true;clearTimeout(timer)
 try{const response=await localFetch(endpoint.value+'?period='+period.value);const result=await readJson(response);if(!alive||id!==epoch)return;if(!response.ok)throw new Error(result.error||'無法讀取 RS 排行');if(report.value?.as_of!==result.as_of)expanded.value='';report.value=result;error.value='';if(result.job?.status==='running')timer=setTimeout(()=>{void load()},2500)}catch(e){if(alive&&id===epoch)error.value=(e as Error).message}finally{if(alive&&id===epoch)loading.value=false}
}
async function refresh(){starting.value=true;try{const response=await localFetch(endpoint.value+'/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await readJson(response);if(!response.ok)throw new Error(data.error||'無法啟動 RS 計算');await load()}catch(e){error.value=(e as Error).message}finally{starting.value=false}}
watch(period,()=>{report.value=null;expanded.value='';void load()})
onMounted(()=>{void load()})
onUnmounted(()=>{alive=false;epoch++;clearTimeout(timer)})
</script>
<template>
 <div class="rs-page">
  <header class="page-header"><div><div class="eyebrow">RELATIVE STRENGTH</div><h1>{{etf?'台股 ETF RS 排行':'RS 排行'}}</h1><p>{{etf?'台灣上市櫃 ETF／ETN，含槓桿、反向與海外標的':'上市＋上櫃普通股'}} · RS > 60 · 依 YTD 是否勝過加權指數分欄</p></div><button class="primary" :disabled="running" @click="refresh"><Icon name="refresh" :size="17" :class="{spinning:running}"/>{{running?'RS 計算中':'更新行情並重算 RS'}}</button></header>
  <div class="rs-toolbar"><div class="segmented" role="tablist" aria-label="RS 計算期間"><button v-for="item in [{id:'20',name:'20 日'},{id:'60',name:'60 日'},{id:'ytd',name:'今年以來 YTD'}]" :key="item.id" role="tab" :aria-selected="period===item.id" :class="{selected:period===item.id}" @click="period=item.id">{{item.name}}</button></div><span>切換只讀取已計算資料；手動更新一次會重算三種期間。</span></div>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="load">重新讀取</button></div>
  <div v-if="report?.job" class="sync-status" :class="{'has-error':['failed','interrupted'].includes(report.job.status)}" role="status"><Icon :name="running?'refresh':'info'" :class="{spinning:running}" :size="16"/>{{report.job.message}}</div>
  <p v-if="loading&&!report" class="watch-loading">正在讀取 RS 排行…</p>
  <div v-if="report&&!report.available" class="panel major-empty"><Icon name="chart" :size="28"/><h2>尚未計算 RS 排行</h2><p>按「更新行情並重算 RS」，取得所需日期的市場行情與加權指數。</p></div>
  <template v-if="report?.available">
   <div class="rs-benchmark panel"><div><span>加權指數 YTD</span><strong :class="report.index_ytd>0?'positive':'negative'">{{pct(report.index_ytd)}}</strong><small>{{report.year_base}} → {{report.as_of}}</small></div><div><span>加權指數收盤</span><strong>{{fmt(report.index_close)}}</strong><small>年底基準 {{fmt(report.index_base_close)}}</small></div><div><span>{{label}} RS 合格{{etf?'商品':'個股'}}</span><strong>{{totalShown}} <small>/ {{report.universe}}</small></strong><small>已隱藏 RS ≤ 60 的 {{report.hidden}} 檔</small></div></div>
   <div class="major-definition"><Icon name="info" :size="18"/><p><strong>{{label}} RS 是漲幅百分位，不是 RSI，也不是漲幅本身。</strong><span>RS 80% 代表在本次可評分{{etf?'ETF／ETN 商品':'股票'}}中，強度約位於第 80 百分位。同報酬給相同百分位。計算區間 {{report.anchor}} → {{report.as_of}}；左右欄一律比較 YTD。</span></p></div>
   <div class="ranking-grid rs-grid">
    <section v-for="group in [{id:'left',title:'YTD 勝過大盤',rows:report.left,tone:'positive'},{id:'right',title:'YTD 低於大盤',rows:report.right,tone:'negative'}]" :key="group.id" class="panel ranking-panel"><div class="ranking-heading"><div><h2 :class="group.tone">{{group.title}}</h2><p>RS > 60 · 依 RS 由高到低排序</p></div><span class="count-badge">{{group.rows.length}} 檔</span></div><div class="table-scroll"><table class="rs-table"><thead><tr><th>{{etf?'ETF／ETN':'股票'}}</th><th>{{label}} RS%</th><th>YTD 漲幅</th><th>較大盤 <small>百分點</small></th></tr></thead>
     <tbody v-for="row in group.rows" :key="row.symbol"><tr><td><button class="stock-button" :aria-expanded="expanded===row.symbol" @click="expanded=expanded===row.symbol?'':row.symbol"><FlowCircle v-if="!etf" :buy="balance(row)?.buy??null" :sell="balance(row)?.sell??null" :period="flowPeriod(row)" scope="近五日主力名單互抵"/><span><strong>{{row.name}}</strong><small>{{row.symbol}} · {{row.market==='TWSE'?'上市':'上櫃'}}{{row.product_type?' · '+row.product_type:''}}</small></span></button></td><td><span class="rs-score">{{fmt(row.rs)}}%</span></td><td :class="row.ytd_pct>0?'positive':row.ytd_pct<0?'negative':'muted'">{{pct(row.ytd_pct)}}</td><td :class="group.tone">{{row.excess_pct>0?'+':''}}{{fmt(row.excess_pct)}}</td></tr><tr v-if="expanded===row.symbol" class="contribution-row"><td colspan="4"><div v-if="etf" class="broker-method">{{label}}漲幅 {{pct(row.return_pct)}} · 收盤 {{fmt(row.close)}} 元 · {{row.product_type}}<br>RS 在 ETF／ETN 合併樣本內計算；比較實際收盤價漲幅，不套用槓桿倍數。</div><TopBrokers v-else :key="report.as_of+':'+row.symbol" :symbol="row.symbol" :date="report.as_of" @loaded="(names,value)=>remember(row,names,value)"/></td></tr></tbody>
     <tbody v-if="!group.rows.length"><tr><td colspan="4" class="empty-row">此期間沒有 RS > 60 且{{group.title}}的股票。</td></tr></tbody>
    </table></div></section>
   </div>
   <p v-if="report.equal.length" class="major-footnote">另有 {{report.equal.length}} 檔 RS > 60 且 YTD 與大盤持平：{{report.equal.map(r=>r.name+' '+r.symbol+'（RS '+fmt(r.rs)+'%）').join('、')}}。</p>
   <p class="major-footnote">收盤價漲幅不含股息。{{Object.entries(report.excluded).map(([reason,count])=>reason+' '+count+' 檔').join('；')}}，未納入百分位。{{etf?'本榜 ETF／ETN 不與普通股混合排名；期間跨越分割、反分割或其他公司事件且價格未還原者暫不評分。新掛牌缺少年底價格者也不列入。點商品可查看期間漲幅。':'公司事件包含除權、減資與面額變更，待價格還原後才能比較；ETF 不參與普通股排名。股票旁圓圈為近五日主力互抵，灰色「待」需點個股才查詢。'}}</p>
   <div class="major-sources"><span>更新 {{report.fetched_at.slice(0,16).replace('T',' ')}}</span><a href="https://www.twse.com.tw/zh/trading/historical/fmtqik.html" target="_blank" rel="noopener">證交所加權指數</a><a href="https://www.twse.com.tw/zh/trading/historical/mi-index.html" target="_blank" rel="noopener">上市行情</a><a href="https://www.tpex.org.tw/zh-tw/mainboard/trading/info/daily-pricing.html" target="_blank" rel="noopener">上櫃行情</a></div>
  </template>
 </div>
</template>
