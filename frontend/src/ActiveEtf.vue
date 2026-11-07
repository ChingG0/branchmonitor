<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import Icon from './Icon.vue'
import {localFetch,readJson} from './http'

type Holding={symbol:string;name:string;shares:number;weight:number}
type Change=Holding&{kind:'new'|'increase'|'decrease'|'removed';previous_shares:number;change_shares:number;previous_weight:number;change_weight:number}
type PeriodReturn={return_pct:number|null;base_date:string;base_close:number;limited:boolean}
type Performance={close:number;price_date:string;month:PeriodReturn;half_year:PeriodReturn;ytd:PeriodReturn}
type Fund={fund:string;name:string;issuer:string;source:string;date:string|null;previous_date:string|null;holdings_as_of?:string|null;source_pcf_date?:string|null;holdings:Holding[];changes:Change[];counts:Record<string,number>;total_holdings:number;performance:Performance|null}
type Common={symbol:string;name:string;direction:'buy'|'sell';fund_count:number;funds:string[];change_shares:number}
type Report={available:boolean;date:string|null;available_dates:string[];funds:Fund[];common:Common[];note:string;job:{status:string;message:string}}

const report=ref<Report|null>(null),error=ref(''),selectedDate=ref(''),selectedFund=ref('00981A')
const mode=ref<'changes'|'common'|'holdings'>('changes'),kind=ref('all'),busy=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
const labels:Record<string,string>={new:'新增',increase:'加碼',decrease:'減碼',removed:'清倉'}
const current=computed(()=>report.value?.funds.find(row=>row.fund===selectedFund.value)||report.value?.funds[0])
const changes=computed(()=>current.value?.changes.filter(row=>kind.value==='all'||row.kind===kind.value)||[])
const holdings=computed(()=>[...(current.value?.holdings||[])].sort((a,b)=>b.weight-a.weight))
const fmt=(value:number|null|undefined,d=0)=>value==null?'—':value.toLocaleString('zh-TW',{maximumFractionDigits:d})
const signed=(value:number,d=0)=>`${value>0?'+':''}${fmt(value,d)}`
const lots=(value:number|null|undefined,signedValue=false)=>value==null?'—':`${signedValue&&value>0?'+':''}${fmt(value/1000,3)} 張`
const pct=(value:number|null|undefined)=>value==null?'—':`${value>0?'+':''}${fmt(value,2)}%`
const returnTitle=(value:PeriodReturn|null|undefined)=>value?`${value.limited?'掛牌後起算 · ':''}${value.base_date} 收盤 ${fmt(value.base_close,2)} → 最新`:'尚無足夠行情'
async function load(){
 clearTimeout(timer)
 try{
  const path='/api/active-etf'+(selectedDate.value?'?date='+encodeURIComponent(selectedDate.value):'')
  const response=await localFetch(path),data=await readJson<Report&{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法讀取主動 ETF 動向')
  if(!alive)return
  report.value=data;busy.value=data.job.status==='running';error.value=''
  if(data.funds.length&&!data.funds.some(row=>row.fund===selectedFund.value))selectedFund.value=data.funds[0].fund
  if(busy.value)timer=setTimeout(load,1200)
 }catch(e){if(alive){error.value=(e as Error).message;busy.value=false}}
}
async function refresh(){
 busy.value=true;error.value='';clearTimeout(timer)
 try{
  const response=await localFetch('/api/active-etf/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(selectedDate.value?{date:selectedDate.value}:{})})
  const data=await readJson<{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法更新主動 ETF')
  timer=setTimeout(load,500)
 }catch(e){error.value=(e as Error).message;busy.value=false}
}
onMounted(load);onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>

<template>
 <section class="active-page">
  <header class="page-header"><div><div class="eyebrow">ACTIVE ETF · DAILY HOLDINGS</div><h1>主動 ETF 動向</h1><p>比較四檔主動 ETF 每日官方持股股數與權重，追蹤新增、加碼、減碼及清倉。</p></div><button class="primary" :disabled="busy" @click="refresh"><Icon name="refresh" :class="{spinning:busy}"/>{{busy?'官方清單更新中':'更新官方清單'}}</button></header>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="load">重新讀取</button></div>
  <div class="toolbar"><label>持股日期<select v-model="selectedDate" @change="load"><option value="">最新持股</option><option v-for="day in report?.available_dates" :key="day" :value="day">{{day}}</option></select></label><div class="modes"><button :class="{active:mode==='changes'}" @click="mode='changes'">每日異動</button><button :class="{active:mode==='common'}" @click="mode='common'">共同操作</button><button :class="{active:mode==='holdings'}" @click="mode='holdings'">完整持股</button></div></div>
  <div class="fund-grid"><button v-for="fund in report?.funds||[]" :key="fund.fund" class="fund-card panel" :class="{active:selectedFund===fund.fund}" @click="selectedFund=fund.fund"><span>{{fund.issuer}}</span><strong>{{fund.fund}} <small>{{fund.name}}</small></strong><p v-if="fund.date">{{fund.date}} · {{fmt(fund.total_holdings)}} 檔 · 收盤 {{fmt(fund.performance?.close,2)}}</p><p v-else>本日資料待更新</p><div class="returns"><span :title="returnTitle(fund.performance?.month)"><small>本月</small><b :class="(fund.performance?.month.return_pct||0)>=0?'positive':'negative'">{{pct(fund.performance?.month.return_pct)}}</b></span><span :title="returnTitle(fund.performance?.half_year)"><small>半年{{fund.performance?.half_year.limited?'*':''}}</small><b :class="(fund.performance?.half_year.return_pct||0)>=0?'positive':'negative'">{{pct(fund.performance?.half_year.return_pct)}}</b></span><span :title="returnTitle(fund.performance?.ytd)"><small>YTD{{fund.performance?.ytd.limited?'*':''}}</small><b :class="(fund.performance?.ytd.return_pct||0)>=0?'positive':'negative'">{{pct(fund.performance?.ytd.return_pct)}}</b></span></div><div class="change-counts"><i class="new">新增 {{fund.counts.new||0}}</i><i class="up">加碼 {{fund.counts.increase||0}}</i><i class="down">減碼 {{fund.counts.decrease||0}}</i><i class="removed">清倉 {{fund.counts.removed||0}}</i></div></button></div>

  <section v-if="mode==='changes'" class="panel data-panel">
   <header><div><h2>{{current?.fund}} 每日持股異動</h2><p v-if="current?.previous_date">{{current.previous_date}} → {{current.date}}</p><p v-else>目前是第一份基準快照；下一個交易日後開始顯示異動。</p><p v-if="current?.source_pcf_date" class="date-basis">實際持股 {{current.holdings_as_of}} · PCF 清單適用 {{current.source_pcf_date}}</p></div><div class="kind-filter"><button :class="{active:kind==='all'}" @click="kind='all'">全部</button><button v-for="key in ['new','increase','decrease','removed']" :key="key" :class="[key,{active:kind===key}]" @click="kind=key">{{labels[key]}} {{current?.counts[key]||0}}</button></div></header>
   <div class="table-wrap"><table><thead><tr><th>股票</th><th>異動</th><th>前日張數</th><th>本日張數</th><th>增減張數</th><th>持股權重</th><th>權重變化</th></tr></thead><tbody><tr v-for="row in changes" :key="row.symbol"><td><b>{{row.name}}</b><small>{{row.symbol}}</small></td><td><span class="kind" :class="row.kind">{{labels[row.kind]}}</span></td><td>{{lots(row.previous_shares)}}</td><td>{{lots(row.shares)}}</td><td :class="row.change_shares>0?'positive':'negative'">{{lots(row.change_shares,true)}}</td><td>{{fmt(row.weight,2)}}%</td><td :class="row.change_weight>0?'positive':row.change_weight<0?'negative':''">{{signed(row.change_weight,2)}}pp</td></tr><tr v-if="!changes.length"><td colspan="7" class="empty">{{current?.previous_date?'本日沒有符合此分類的持股異動。':'已建立基準快照，需有下一個交易日資料才能比較。'}}</td></tr></tbody></table></div>
  </section>

  <section v-else-if="mode==='common'" class="panel data-panel"><header><div><h2>四檔共同操作</h2><p>同一交易日有至少兩檔 ETF 同向增加或減少持股。</p></div></header><div class="table-wrap"><table><thead><tr><th>股票</th><th>方向</th><th>同步檔數</th><th>ETF</th><th>合計張數變化</th></tr></thead><tbody><tr v-for="row in report?.common||[]" :key="row.symbol+row.direction"><td><b>{{row.name}}</b><small>{{row.symbol}}</small></td><td><span class="kind" :class="row.direction==='buy'?'increase':'decrease'">{{row.direction==='buy'?'共同加碼':'共同減碼'}}</span></td><td>{{row.fund_count}} 檔</td><td><span class="fund-chip" v-for="fund in row.funds" :key="fund">{{fund}}</span></td><td :class="row.change_shares>0?'positive':'negative'">{{lots(row.change_shares,true)}}</td></tr><tr v-if="!report?.common.length"><td colspan="5" class="empty">本日尚無兩檔以上同向操作的股票，或仍在建立基準快照。</td></tr></tbody></table></div></section>

  <section v-else class="panel data-panel"><header><div><h2>{{current?.fund}} 完整持股</h2><p>{{current?.date||'待更新'}} · 依持股權重排序</p><p v-if="current?.source_pcf_date" class="date-basis">PCF 清單適用 {{current.source_pcf_date}}；持股以 {{current.holdings_as_of}} 為準</p></div><a v-if="current?.source" :href="current.source" target="_blank" rel="noopener">投信原始資料 ↗</a></header><div class="table-wrap"><table><thead><tr><th>#</th><th>股票代號</th><th>股票名稱</th><th>張數</th><th>持股權重</th></tr></thead><tbody><tr v-for="(row,index) in holdings" :key="row.symbol"><td>{{index+1}}</td><td><b>{{row.symbol}}</b></td><td>{{row.name}}</td><td>{{lots(row.shares)}}</td><td>{{fmt(row.weight,2)}}%</td></tr><tr v-if="!holdings.length"><td colspan="5" class="empty">尚無此日持股清單。</td></tr></tbody></table></div></section>
  <p class="source-note"><Icon name="info" :size="15"/>畫面以 1,000 股＝1 張換算，最多保留三位小數。{{report?.note}}</p>
 </section>
</template>

<style scoped>
.active-page{display:grid;gap:20px}.toolbar{display:flex;justify-content:space-between;align-items:end;gap:16px;flex-wrap:wrap}.toolbar label{display:grid;gap:7px;font-size:.72rem;color:#829176}.toolbar select{border:1px solid #d9e2d2;border-radius:8px;background:#fff;padding:10px 13px;color:#4b643f}.modes,.kind-filter{display:flex;background:#eef1ea;border-radius:9px;padding:3px;gap:2px}.modes button,.kind-filter button{padding:9px 13px;border-radius:7px;color:#7b8973;font-size:.72rem}.modes button.active,.kind-filter button.active{background:#fff;color:#49643d;box-shadow:0 1px 5px #50654118}.fund-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.fund-card{text-align:left;padding:18px;border:1px solid transparent;transition:.15s}.fund-card.active{border-color:#91a87f;background:#fbfdf9}.fund-card>span{font-size:.64rem;color:#8c9a82}.fund-card strong{display:block;margin:8px 0;font-size:1rem;color:#40583a}.fund-card strong small{display:block;margin-top:4px;font-size:.68rem;font-weight:400;color:#7d8c74}.fund-card p{font-size:.66rem;color:#8a9780;margin:0 0 12px}.returns{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:0 0 12px;padding:9px 0;border-top:1px solid #edf0e9;border-bottom:1px solid #edf0e9}.returns span{text-align:center;border-right:1px solid #edf0e9}.returns span:last-child{border-right:0}.returns small,.returns b{display:block}.returns small{font-size:.58rem;color:#8a9880;margin-bottom:4px}.returns b{font-size:.75rem;font-weight:600}.change-counts{display:flex;gap:5px;flex-wrap:wrap}.fund-card i{font-style:normal;font-size:.6rem;padding:4px 6px;border-radius:12px;background:#f0f3ed;color:#73816c}.fund-card i.new,.fund-card i.up{background:#edf5e8;color:#5e814d}.fund-card i.down,.fund-card i.removed{background:#f9eeeb;color:#a35b50}.data-panel{overflow:hidden}.data-panel>header{padding:20px 22px;display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid #e6ece1}.data-panel h2{font-size:.98rem;margin:0 0 6px}.data-panel header p{font-size:.68rem;color:#89977e;margin:0}.data-panel header p.date-basis{margin-top:7px;color:#607b4d}.data-panel header a{font-size:.7rem;color:#607b4d}.table-wrap{overflow:auto}.data-panel table{width:100%;border-collapse:collapse;font-size:.76rem}.data-panel th,.data-panel td{padding:14px 16px;text-align:left;white-space:nowrap;border-bottom:1px solid #e8ede4}.data-panel th{font-size:.67rem;font-weight:450;color:#849378;background:#f7f9f5}.data-panel td b,.data-panel td small{display:block}.data-panel td small{font-size:.64rem;color:#94a08c;margin-top:4px}.kind{display:inline-block;padding:5px 8px;border-radius:13px;font-size:.64rem}.kind.new,.kind.increase{background:#eaf4e5;color:#527944}.kind.decrease,.kind.removed{background:#f8ebe8;color:#a55449}.positive{color:#b3594c}.negative{color:#218363}.fund-chip{display:inline-block;margin:2px 4px 2px 0;padding:4px 7px;border-radius:12px;background:#eef2ea;color:#607653;font-size:.62rem}.empty{text-align:center!important;padding:50px!important;color:#8c9983}.source-note{display:flex;gap:8px;align-items:flex-start;color:#87957d;font-size:.69rem;line-height:1.7;margin:0 3px}@media(max-width:1050px){.fund-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.fund-grid{grid-template-columns:1fr}.data-panel>header{align-items:flex-start;flex-direction:column}.kind-filter{max-width:100%;overflow:auto}.data-panel table{min-width:760px}}
</style>
