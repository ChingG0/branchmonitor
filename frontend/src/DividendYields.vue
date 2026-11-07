<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import Icon from './Icon.vue'
import {localFetch,readJson} from './http'

type Schedule={last_buy_date:string|null;next_ex_date:string|null}
type Stock=Schedule&{symbol:string;name:string;market:string;close:number;yield_pct:number|null;dividend_year:string;source:string;cash_per_share?:number|null;last_ex_date?:string|null;yield_basis?:string;pe_ratio?:number|null;cash_dividend_year?:number|null;yield_method?:string;fill_probability_pct?:number|null;fill_days_avg?:number|null;fill_successes?:number;fill_sample?:number;fill_source?:string;fill_new_events?:number}
type Etf=Schedule&{symbol:string;name:string;market:string;close:number;yield_pct:number;cash_per_unit:number;distribution_count:number;last_ex_date:string|null;fill_probability_pct?:number|null;fill_days_avg?:number|null;fill_successes?:number;fill_sample?:number;fill_source?:string;fill_new_events?:number}
type FillRow=Stock|Etf
type Report={yield_version?:number;yield_audit?:Record<string,number>;as_of:string|null;etfs_as_of?:string|null;etfs_reused?:boolean;latest_market_day:string|null;stocks:Stock[];etfs:Etf[];fetched_at:string|null;window_start?:string;fill_window_start?:string;wantgoo_source_day?:string|null;wantgoo_fetched_at?:string|null;wantgoo_status?:string;sources:Record<string,string>;job:{status:string;message:string}}
const report=ref<Report|null>(null),error=ref(''),busy=ref(false),kind=ref<'stocks'|'etfs'>('stocks'),market=ref('all'),query=ref(''),page=ref(1),sortMode=ref<'buy_desc'|'buy_asc'|'yield_desc'|'yield_asc'>('buy_desc')
const pageSize=100
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
const pct=(value:number|null)=>value===null?'—':`${value.toFixed(2)}%`
const fillSample=(row:FillRow)=>row.fill_sample||0
const fillSuccesses=(row:FillRow)=>row.fill_successes||0
const fillSource=(row:FillRow)=>row.fill_source||'無歷史樣本'
const money=(value:number)=>value.toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2})
const sortDescription=computed(()=>sortMode.value==='buy_desc'?'已公告的最後買進日由近到遠，未公告者置後':sortMode.value==='buy_asc'?'已公告的最後買進日由遠到近，未公告者置後':sortMode.value==='yield_asc'?'殖利率由低到高':'殖利率由高到低')
const incompleteCount=computed(()=>report.value?.stocks.filter(row=>row.yield_method==='unavailable').length||0)
const fallbackCount=computed(()=>(report.value?.yield_audit?.previous_year||0)+(report.value?.yield_audit?.last_paid||0))
const toggleYieldSort=()=>{sortMode.value=sortMode.value==='yield_desc'?'yield_asc':'yield_desc'}
const toggleBuyDateSort=()=>{sortMode.value=sortMode.value==='buy_desc'?'buy_asc':'buy_desc'}
const visibleRows=computed(()=>{
 const text=query.value.trim().toLowerCase()
 const rows=(kind.value==='stocks'?report.value?.stocks||[]:report.value?.etfs||[]).filter(row=>(market.value==='all'||row.market===market.value)&&(!text||`${row.symbol} ${row.name}`.toLowerCase().includes(text)))
 return [...rows].sort((a,b)=>{
  if(sortMode.value==='buy_desc'||sortMode.value==='buy_asc'){
   if(!a.last_buy_date&&b.last_buy_date)return 1
   if(a.last_buy_date&&!b.last_buy_date)return -1
   if(a.last_buy_date&&b.last_buy_date&&a.last_buy_date!==b.last_buy_date)return sortMode.value==='buy_desc'?b.last_buy_date.localeCompare(a.last_buy_date):a.last_buy_date.localeCompare(b.last_buy_date)
  }
  if(a.yield_pct==null)return b.yield_pct==null?a.symbol.localeCompare(b.symbol):1
  if(b.yield_pct==null)return -1
  const difference=sortMode.value==='yield_asc'?a.yield_pct-b.yield_pct:b.yield_pct-a.yield_pct
  return difference||a.symbol.localeCompare(b.symbol)
 })
})
const pageCount=computed(()=>Math.max(1,Math.ceil(visibleRows.value.length/pageSize)))
const shown=computed(()=>visibleRows.value.slice((page.value-1)*pageSize,page.value*pageSize))
watch([kind,market,query,sortMode],()=>{page.value=1})

async function load(){
 clearTimeout(timer)
 try{
  const response=await localFetch('/api/dividend-yields')
  const data=await readJson<Report&{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法讀取殖利率排行')
  if(!alive)return
  report.value=data;busy.value=data.job.status==='running';error.value=''
  if(busy.value)timer=setTimeout(load,1200)
 }catch(e){if(alive){error.value=(e as Error).message;busy.value=false}}
}
async function refresh(){
 busy.value=true;error.value='';clearTimeout(timer)
 try{
  const response=await localFetch('/api/dividend-yields/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
  const data=await readJson<{error?:string}>(response)
  if(!response.ok)throw new Error(data.error||'無法更新殖利率排行')
  timer=setTimeout(load,400)
 }catch(e){error.value=(e as Error).message;busy.value=false}
}
onMounted(async()=>{
 await load()
 if(!alive||busy.value||!report.value||
    (report.value.as_of===report.value.latest_market_day&&report.value.yield_version===2))return
 const key=`branch-monitor.yield-annual-v2.${report.value.latest_market_day}`
 if(sessionStorage.getItem(key))return
 sessionStorage.setItem(key,'1')
 await refresh()
})
onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>

<template>
 <section class="yield-page">
  <header class="page-header"><div><div class="eyebrow">TWSE · TPEX</div><h1>殖利率排行</h1><p>上市、上櫃股票與 ETF 分開排名；資料截至 {{report?.as_of||'待更新'}} 收盤。</p></div><button class="primary" :disabled="busy" @click="refresh"><Icon name="refresh" :class="{spinning:busy}"/>{{busy?'官方資料更新中':'更新殖利率'}}</button></header>
  <div v-if="error" class="message warning" role="alert">{{error}}</div>
  <div v-if="report?.job.status==='running'" class="message">{{report.job.message}}</div>
  <div v-else-if="report?.job.status==='failed'" class="message warning">{{report.job.message}}</div>
  <div v-else-if="report?.job.status==='partial'" class="message warning">{{report.job.message}}</div>
  <div v-if="report?.etfs_as_of&&report.etfs_as_of!==report.as_of&&report.job.status!=='partial'" class="message warning">ETF 官方來源暫不可用，ETF 列表沿用 {{report.etfs_as_of}} 已保存資料；個股現金殖利率已重新估算。</div>
  <div v-if="report?.as_of&&!report.sources.twse_notice" class="message warning">最後買進日資料尚未更新，正在補查官方除息預告；目前列表仍顯示已保存的殖利率。</div>
  <div v-if="report?.as_of&&!report.sources.twse_ex_history" class="message warning">個股現金殖利率尚未重新估算，正在補查已除息紀錄；舊排行可能包含股票股利。</div>
  <div v-if="incompleteCount" class="message warning">{{incompleteCount}} 檔的股利年度、發放日或股數變更仍待核對，殖利率留空並排在最後；下次更新本區會重新核對。</div>
  <div v-if="fallbackCount" class="message">{{fallbackCount}} 檔採前年度或前次已發放現金估算，已逐檔標示；前次單筆金額不代表全年股利。</div>
  <div v-if="report?.as_of&&report.wantgoo_status==='cached'" class="message warning">玩股網暫時無法連線，股利年度、發放日對照與填息欄位沿用 {{report.wantgoo_source_day}} 取得的紀錄；新除息仍依本機行情追蹤。</div>
  <div v-if="report?.as_of&&report.wantgoo_status==='unavailable'" class="message warning">玩股網填息資料暫時無法取得；填息欄位暫用本機歷史，已標示資料來源。</div>
  <div v-if="report?.as_of&&report.latest_market_day&&report.as_of<report.latest_market_day" class="message warning">排行截至 {{report.as_of}}，本機行情已更新至 {{report.latest_market_day}}；請更新本區資料。</div>
  <div class="metrics"><div><span>上市櫃股票</span><strong>{{report?.stocks.length.toLocaleString('zh-TW')||'—'}}</strong><small>股利年度現金股利估算</small></div><div><span>上市櫃 ETF</span><strong>{{report?.etfs.length.toLocaleString('zh-TW')||'—'}}</strong><small>近 12 個月已除息現金配息率</small></div><div><span>排行日期</span><strong>{{report?.as_of||'—'}}</strong><small>以本機最新收盤價估算</small></div></div>
  <div class="controls panel"><div class="switch"><button :class="{active:kind==='stocks'}" @click="kind='stocks'">個股殖利率</button><button :class="{active:kind==='etfs'}" @click="kind='etfs'">ETF 配息率</button></div><label>排序<select v-model="sortMode"><option value="buy_desc">最後買進日近到遠</option><option value="buy_asc">最後買進日遠到近</option><option value="yield_desc">殖利率高到低</option><option value="yield_asc">殖利率低到高</option></select></label><label>市場<select v-model="market"><option value="all">上市＋上櫃</option><option value="上市">上市</option><option value="上櫃">上櫃</option></select></label><label>搜尋<input v-model="query" type="search" placeholder="股票或 ETF 代號、名稱"></label></div>
  <section class="panel table-panel"><header><div><h2>{{kind==='stocks'?'個股殖利率':'ETF 近 12 月現金配息率'}}</h2><p>{{visibleRows.length.toLocaleString('zh-TW')}} 檔 · {{sortDescription}} · 排行日 {{report?.as_of||'尚無資料'}}</p></div></header>
   <div class="table-wrap"><table><thead><tr><th>#</th><th>市場</th><th>代號／名稱</th><th>收盤價</th><th :aria-sort="sortMode==='yield_asc'?'ascending':sortMode==='yield_desc'?'descending':'none'"><button type="button" class="sort-button" :aria-label="`${kind==='stocks'?'現金殖利率':'近 12 月配息率'}排序，${sortMode==='yield_desc'?'目前由高到低，點擊改為由低到高':sortMode==='yield_asc'?'目前由低到高，點擊改為由高到低':'點擊由高到低排序'}`" @click="toggleYieldSort">{{kind==='stocks'?'現金股利殖利率':'近 12 月配息率'}} <span aria-hidden="true">{{sortMode==='yield_desc'?'↓':sortMode==='yield_asc'?'↑':'↕'}}</span></button></th><th :aria-sort="sortMode==='buy_asc'?'ascending':sortMode==='buy_desc'?'descending':'none'" title="參與下一次現金配息的最後可買交易日；依官方除息預告回推"><button type="button" class="sort-button" :aria-label="`最後買進日排序，${sortMode==='buy_desc'?'目前由近到遠，點擊改為由遠到近':sortMode==='buy_asc'?'目前由遠到近，點擊改為由近到遠':'點擊由近到遠排序'}`" @click="toggleBuyDateSort">最後買進日 <span aria-hidden="true">{{sortMode==='buy_desc'?'↓':sortMode==='buy_asc'?'↑':'↕'}}</span></button></th><th>下一次除息日</th><th title="優先採玩股網除息紀錄；若有尚未收錄的新除息，依本機行情追加追蹤">填息機率</th><th>平均填息天數</th><template v-if="kind==='stocks'"><th>本益比</th><th>計算現金／股</th><th>股利依據</th></template><template v-else><th>每單位累計配息</th><th>除息次數</th><th>最近除息</th></template></tr></thead><tbody>
    <tr v-for="(row,index) in shown" :key="row.symbol"><td>{{(page-1)*pageSize+index+1}}</td><td>{{row.market}}</td><td><b>{{row.name}}</b><small>{{row.symbol}}<template v-if="kind==='stocks'"> · <a :href="`https://www.wantgoo.com/stock/${row.symbol}/dividend-policy/ex-dividend`" target="_blank" rel="noopener noreferrer">玩股網歷史</a></template></small></td><td>{{money(row.close)}}</td><td class="yield"><strong>{{pct(row.yield_pct)}}</strong><small v-if="kind==='stocks'">{{(row as Stock).yield_basis||'待更新'}} · 最新收盤價</small></td><td class="buy-day"><b v-if="row.last_buy_date">{{row.last_buy_date}}</b><span v-else>{{report?.sources.twse_notice?'未公告':'待更新'}}</span></td><td>{{row.next_ex_date||'—'}}</td><td>{{pct(row.fill_probability_pct??null)}}<small v-if="fillSample(row)">{{fillSuccesses(row)}}／{{fillSample(row)}} 次 · {{fillSource(row)}}<template v-if="row.fill_new_events"> {{row.fill_new_events}} 筆新除息</template></small><small v-else>無歷史樣本</small></td><td>{{fillSuccesses(row)>0&&row.fill_days_avg!=null?`${row.fill_days_avg} 日`:'—'}}<small v-if="fillSuccesses(row)>0">僅統計已填息 · {{fillSource(row)}}</small></td><template v-if="kind==='stocks'"><td>{{(row as Stock).pe_ratio==null?'—':`${(row as Stock).pe_ratio?.toFixed(2)} 倍`}}</td><td>{{(row as Stock).cash_per_share==null?'—':(row as Stock).cash_per_share?.toFixed(4)}}</td><td>{{(row as Stock).yield_basis||'待更新'}}<small v-if="(row as Stock).last_ex_date">最近 {{(row as Stock).last_ex_date}}</small></td></template><template v-else><td>{{money((row as Etf).cash_per_unit)}}</td><td>{{(row as Etf).distribution_count}} 次</td><td>{{(row as Etf).last_ex_date||'近 12 月無除息'}}</td></template></tr>
    <tr v-if="!shown.length"><td colspan="12" class="empty">{{busy?'正在取得官方資料…':report?.as_of?'沒有符合條件的標的':'尚無排行，請按「更新殖利率」。'}}</td></tr>
   </tbody></table></div><footer v-if="pageCount>1" class="pager"><button :disabled="page===1" @click="page--">上一頁</button><span>{{page}}／{{pageCount}}</span><button :disabled="page===pageCount" @click="page++">下一頁</button></footer>
  </section>
  <p class="note">最後買進日＝已公告的下一次除息日前一交易日，依<a :href="report?.sources.holidays" target="_blank" rel="noopener noreferrer">官方開休市日曆</a>回推；從排行日 {{report?.as_of||'—'}} 起列示，尚未公告者留空。<a :href="report?.sources.twse_notice" target="_blank" rel="noopener noreferrer">上市預告</a>與<a href="https://www.tpex.org.tw/zh-tw/announce/market/ex/announce.html" target="_blank" rel="noopener noreferrer">上櫃預告</a>為更新時查得，可能晚於收盤價日期；排在當日的標的若已收盤就無法再買入參與該次配息。</p>
  <p class="note">個股現金殖利率＝所採股利年度的已發放每股現金股利 ÷ 排行日收盤價 × 100%。金額以<a :href="report?.sources.twse_ex_history" target="_blank" rel="noopener noreferrer">證交所</a>、<a :href="report?.sources.tpex_ex_history" target="_blank" rel="noopener noreferrer">櫃買中心</a>除息紀錄核對，股利年度與公告發放日依歷史配息明細對照；季配息依股利所屬年度合計，不混用近 365 日。當年度尚未全數發放時採前一個完整年度；不足時才用前次已發放金額並標示「非全年」。當年度已確認不配息則為 0%。股票股利不列為現金；配股與面額變更按股數換算，無法確認的減資等事件先留空。</p>
  <p class="note">ETF 維持近 365 日已除息現金配息合計 ÷ 排行日收盤價，已按期間內分割／反分割換算每單位配息。歷史殖利率並非預期報酬；股利年度與發放日對照仍使用玩股網歷史配息明細，但不採用其殖利率數值。</p>
  <p class="note">個股與 ETF 的填息機率、平均天數優先依<a href="https://www.wantgoo.com/stock/dividend-yield" target="_blank" rel="noopener noreferrer">玩股網最新除息紀錄</a>計算（資料取得日 {{report?.wantgoo_source_day||'—'}}）：有現金配息的除息次數為分母，填息日期在除息後 360 天內的次數為分子；天數為已填息紀錄的日曆天數平均。若官方出現來源尚未收錄的新除息，才用本機每日最高價追蹤至填息，並併入統計；來源收錄後即由其紀錄接手。資料來源與樣本數顯示於每檔下方；沒有樣本留空。這是歷史統計，不保證下一次填息。</p>
 </section>
</template>

<style scoped>
.yield-page{display:grid;gap:17px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.metrics>div{border:1px solid #e3e9df;background:#fff;border-radius:14px;padding:18px}.metrics span,.metrics small{display:block;color:#87967e;font-size:.7rem}.metrics strong{display:block;color:#405a3a;font-size:1.35rem;margin:8px 0}.message{padding:12px 15px;border-radius:9px;background:#edf3e9;color:#4d6842;font-size:.78rem}.warning{background:#fff6e4;color:#8b652c}.controls{display:flex;gap:14px;padding:15px;align-items:end;flex-wrap:wrap}.switch{display:flex;padding:3px;background:#edf1e8;border-radius:9px}.switch button{padding:8px 13px;border:0;border-radius:7px;background:transparent;color:#6f806a;cursor:pointer}.switch button.active{background:#fff;color:#405a3a;box-shadow:0 1px 4px #c8d2c2}.controls label{display:grid;gap:5px;color:#829078;font-size:.7rem}.controls select,.controls input{padding:8px 10px;min-height:37px;border:1px solid #d9e2d3;border-radius:8px;background:#fff;color:#42583b}.controls input{min-width:220px}.table-panel{overflow:hidden}.table-panel header{padding:17px 20px;border-bottom:1px solid #e6ece1}.table-panel h2{margin:0 0 5px;font-size:1rem}.table-panel p{margin:0;color:#8a9782;font-size:.72rem}.table-wrap{overflow:auto}.table-panel table{width:100%;border-collapse:collapse;font-size:.78rem}.table-panel th,.table-panel td{padding:13px 16px;border-bottom:1px solid #edf0e9;text-align:left;white-space:nowrap}.table-panel th{background:#f7f9f5;color:#829078;font-size:.69rem;font-weight:500}.sort-button{border:0;padding:0;background:transparent;color:inherit;font:inherit;cursor:pointer}.sort-button:hover,.sort-button:focus-visible{color:#405a3a;text-decoration:underline}.sort-button span{font-size:.8rem;font-weight:700}.table-panel td b,.table-panel td small{display:block}.table-panel td small{font-size:.67rem;color:#97a18f;margin-top:3px}.table-panel td.yield strong{color:#ba5c4d;font-size:.93rem}.buy-day b{color:#486d4d}.buy-day span{color:#9aa69a}.empty{text-align:center!important;padding:45px!important;color:#8d9a84}.pager{display:flex;justify-content:center;align-items:center;gap:15px;padding:14px;color:#70816a;font-size:.75rem}.pager button{padding:7px 12px;border:1px solid #d9e4d2;border-radius:7px}.pager button:disabled{opacity:.4}.note{margin:0 2px;color:#829079;font-size:.72rem;line-height:1.7}.note a{color:#557654}@media(max-width:800px){.metrics{grid-template-columns:1fr}.table-panel table{min-width:850px}.controls label,.controls input{width:100%}}
</style>
