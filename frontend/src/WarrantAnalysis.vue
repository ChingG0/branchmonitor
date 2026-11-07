<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import Icon from './Icon.vue'

type Job={status:string;message:string}
type Report={result:any;query:string;tracked:Array<{query:string;kind:string;name:string;updated_at:string}>;available_dates:string[];fetched_at:string|null;job:Job;note:string}

const report=ref<Report|null>(null),input=ref('081988'),error=ref(''),busy=ref(false)
type SortKey='score'|'leverage'|'iv'|'ivChange'|'days'|'spreadLeverage'|'flow'|'volume'
const kind=ref<'all'|'call'|'put'>('all'),sort=ref<SortKey>('score'),sortDirection=ref<'asc'|'desc'>('desc'),maxIv=ref<number|null>(null)
const hideSmallVolume=ref(false),enriching=ref(false)
const target=ref(0),holdDays=ref(10),scenarioIv=ref(0),entry=ref(0),amount=ref(100000)
const manualPrice=ref<number|null>(null)
const previousStock=ref<{report:Report;input:string;kind:'all'|'call'|'put';manualPrice:number|null;hideSmallVolume:boolean}|null>(null)

const result=computed(()=>report.value?.result)
const detail=computed(()=>result.value?.kind==='warrant'?result.value.detail:null)
const stock=computed(()=>result.value?.kind==='stock'?result.value.stock:null)
const fmt=(v:number|null|undefined,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-TW',{maximumFractionDigits:d})
const money=(v:number|null|undefined)=>v==null?'—':`$${Math.round(v).toLocaleString('zh-TW')}`
const pct=(v:number|null|undefined,d=2)=>v==null?'—':`${v>0?'+':''}${fmt(v,d)}%`
const moneyness=(v:number|null|undefined)=>v==null?'—':`${v>=0?'價內':'價外'} ${fmt(Math.abs(v),2)}%`
function rowMoneyness(row:any){
 const price=Number(manualPrice.value)>0?Number(manualPrice.value):stock.value?.price
 if(!price||!row.strike)return row.moneyness
 return row.kind==='put'?(row.strike-price)/row.strike*100:(price-row.strike)/row.strike*100
}

function normCdf(x:number){
 const t=1/(1+0.2316419*Math.abs(x)),d=0.3989423*Math.exp(-x*x/2)
 const p=1-d*t*(0.3193815+t*(-0.3565638+t*(1.781478+t*(-1.821256+t*1.330274))))
 return x>=0?p:1-p
}
function bsMetrics(s:number,k:number,days:number,iv:number,ratio:number,type:string){
 if(!s||!k||!days||!iv||!ratio)return {price:null,delta:null}
 const t=Math.max(days,1)/365,sigma=iv/100,root=Math.sqrt(t),r=0.017
 const d1=(Math.log(s/k)+(r+sigma*sigma/2)*t)/(sigma*root),d2=d1-sigma*root,disc=Math.exp(-r*t)
 const price=(type==='put'?k*disc*normCdf(-d2)-s*normCdf(-d1):s*normCdf(d1)-k*disc*normCdf(d2))*ratio
 const delta=(type==='put'?normCdf(d1)-1:normCdf(d1))*ratio
 return {price,delta}
}
const calculation=computed(()=>{
 const d=detail.value;if(!d)return null
 const remaining=Math.max(1,d.remaining_days-Math.max(0,holdDays.value))
 const model=bsMetrics(target.value,d.strike,remaining,scenarioIv.value,d.ratio,d.kind),theoretical=model.price
 const lots=entry.value>0?Math.floor(amount.value/(entry.value*1000)):0
 const cost=lots*entry.value*1000,value=theoretical==null?null:lots*theoretical*1000
 const returnPct=value==null||!cost?null:(value-cost)/cost*100
 const ivDrop=Math.max(0,(d.iv||0)-scenarioIv.value)
 const scenarioMoneyness=d.kind==='put'?(d.strike-target.value)/d.strike*100:(target.value-d.strike)/d.strike*100
 const effective=model.delta==null||!entry.value?null:Math.abs(model.delta*target.value/entry.value)
 let scenarioScore=Number(d.score.value)||0
 if(returnPct!=null)scenarioScore+=returnPct>=20?10:returnPct>=5?5:returnPct<-30?-25:returnPct<-15?-15:returnPct<0?-8:0
 scenarioScore-=Math.min(25,Math.floor(ivDrop/2))
 scenarioScore-=remaining<30?20:remaining<60?10:0
 scenarioScore-=scenarioMoneyness<-20?15:scenarioMoneyness<-10?8:0
 scenarioScore=Math.max(0,Math.min(100,Math.round(scenarioScore)))
 if(d.score.spread==null)scenarioScore=Math.min(49,scenarioScore)
 const scenarioLabel=scenarioScore>=80?'優選':scenarioScore>=65?'良好':scenarioScore>=50?'觀察':'不建議'
 return {remaining,theoretical,lots,cost,value,profit:value==null?null:value-cost,returnPct,
  breakEven:d.kind==='put'?d.strike-entry.value/d.ratio:d.strike+entry.value/d.ratio,
  ivDrop,scenarioMoneyness,effective,scenarioScore,scenarioLabel,scoreChange:scenarioScore-d.score.value}
})
const rows=computed(()=>{
 const usePrice=Number(manualPrice.value)>0
 const values=[...(stock.value?.rows||[])].filter((row:any)=>(kind.value==='all'||row.kind===kind.value)&&(maxIv.value==null||row.iv==null||row.iv<=maxIv.value)&&(!hideSmallVolume.value||(row.volume??0)>=50)&&(!usePrice||rowMoneyness(row)>0))
 const get=(row:any)=>sort.value==='leverage'?row.effective_leverage:sort.value==='iv'?row.iv:sort.value==='ivChange'?row.iv_change:sort.value==='days'?row.remaining_days:sort.value==='spreadLeverage'?row.spread_leverage:sort.value==='flow'?(row.latest_flow?.side==='sell'?-(row.latest_flow?.lots||0):(row.latest_flow?.lots||0)):sort.value==='volume'?row.volume:row.score.value
 return values.sort((a:any,b:any)=>{const av=get(a),bv=get(b);if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return-1;return sortDirection.value==='asc'?av-bv:bv-av})
})

function seedCalculator(){
 const d=detail.value;if(!d)return
 target.value=d.underlying.price||0;scenarioIv.value=d.iv||0;entry.value=d.entry_price||0;holdDays.value=Math.min(10,Math.max(1,d.remaining_days-1))
}
async function load(query?:string,date?:string){
 const qs=new URLSearchParams();if(query)qs.set('query',query);if(date)qs.set('date',date)
 const response=await localFetch('/api/warrants'+(qs.size?'?'+qs.toString():''));const data=await readJson<Report>(response)
 if(!response.ok)throw new Error((data as any).error||'無法讀取權證資料')
 report.value=data;if(data.query)input.value=data.query;seedCalculator()
}
async function search(value=input.value){
 const query=value.trim().toUpperCase();if(!query)return
 busy.value=true;error.value=''
 try{
  const response=await localFetch('/api/warrants/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})})
  const data=await readJson<Report>(response);if(!response.ok)throw new Error((data as any).error||'查詢失敗')
  report.value=data;input.value=query;seedCalculator();if(data.result?.kind==='stock')void enrichFlows()
 }catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function enrichFlows(){
 const query=report.value?.query;if(!query||report.value?.result?.kind!=='stock'||enriching.value)return
 enriching.value=true
 try{
  const response=await localFetch('/api/warrants/enrich',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,limit:20})})
  const data=await readJson<Report>(response);if(response.ok)report.value=data
 }catch(e){error.value=(e as Error).message}finally{enriching.value=false}
}
function setSort(key:SortKey,defaultDirection:'asc'|'desc'='desc'){
 if(sort.value===key)sortDirection.value=sortDirection.value==='asc'?'desc':'asc'
 else{sort.value=key;sortDirection.value=defaultDirection}
}
function sortMark(key:SortKey){return sort.value===key?(sortDirection.value==='asc'?' ↑':' ↓'):''}
function openWarrant(row:any){
 if(report.value)previousStock.value={report:report.value,input:input.value,kind:kind.value,manualPrice:manualPrice.value,hideSmallVolume:hideSmallVolume.value}
 void search(row.code)
}
function backToStock(){
 const saved=previousStock.value;if(!saved)return
 report.value=saved.report;input.value=saved.input;kind.value=saved.kind;manualPrice.value=saved.manualPrice;hideSmallVolume.value=saved.hideSmallVolume;previousStock.value=null
}
async function refresh(){
 if(!report.value?.query)return search()
 busy.value=true;error.value=''
 try{await search(report.value.query)}finally{busy.value=false}
}
function scoreClass(value:number){return value>=80?'great':value>=65?'good':value>=50?'watch':'avoid'}
onMounted(async()=>{try{await load()}catch(e){error.value=(e as Error).message}})
</script>

<template>
 <section class="warrant-page">
  <header class="page-header">
   <div><div class="eyebrow">WARRANT LAB</div><h1>權證分析</h1><p>查單一權證的槓桿、隱波與時間價值，或用股票代號比較剩餘 60 日以上的權證。</p></div>
   <button class="secondary" :disabled="busy||!report?.query" @click="refresh"><Icon name="refresh" :class="{spinning:busy}"/>更新目前查詢</button>
  </header>

  <section class="search-panel panel">
   <div><strong>輸入代號</strong><span>權證如 081988；股票如 6672</span></div>
   <form @submit.prevent="search()"><input v-model="input" autocomplete="off" inputmode="text" aria-label="權證或股票代號" placeholder="輸入權證或股票代號"><input v-model.number="manualPrice" class="price-input" type="number" min="0" step="0.1" aria-label="當下股價" placeholder="當下股價（選填）"><button class="primary" :disabled="busy"><Icon name="search"/>{{busy?'查詢中':'分析'}}</button></form>
   <div v-if="report?.tracked.length" class="recent"><span>最近查詢</span><button v-for="item in report.tracked" :key="item.query" @click="search(item.query)">{{item.query}} {{item.name}}</button></div>
  </section>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="search()">重試</button></div>

  <template v-if="detail">
   <section class="identity panel">
    <div><button v-if="previousStock" class="back-button" @click="backToStock">← 返回 {{previousStock.report.query}} 權證清單</button><span class="pill">{{detail.kind==='call'?'認購':'認售'}}</span><h2>{{detail.code}} {{detail.name}} <span v-if="detail.market_warnings?.length" class="warning-icon" :title="detail.market_warnings.map((item:any)=>item.title).join('、')">⚠</span></h2><p>標的 {{detail.underlying.name}} {{detail.underlying.code}} · {{fmt(detail.underlying.price)}} <b :class="detail.underlying.change_percent>=0?'up':'down'">{{pct(detail.underlying.change_percent)}}</b></p></div>
    <div class="quote"><span>成交價</span><strong>{{fmt(detail.last)}}</strong><small>買 {{fmt(detail.bid)}} / 賣 {{fmt(detail.ask)}}</small></div>
   </section>

   <div class="metric-grid">
    <article class="panel"><span>實質槓桿</span><strong>{{fmt(detail.effective_leverage)}} 倍</strong><small>以 Delta、標的價與買價估算</small></article>
    <article class="panel"><span>價內外程度</span><strong>{{moneyness(detail.moneyness)}}</strong><small>履約價 {{fmt(detail.strike)}}</small></article>
    <article class="panel"><span>Delta</span><strong>{{fmt(detail.greeks.delta,4)}}</strong><small>標的每變動 1 元的理論影響</small></article>
    <article class="panel"><span>Theta／日</span><strong>{{fmt(detail.greeks.theta,4)}}</strong><small>其他條件不變的每日時間耗損</small></article>
    <article class="panel"><span>隱含波動率</span><strong>{{fmt(detail.iv)}}%</strong><small>HV20 {{fmt(detail.historical_volatility?.hv20)}}% · HV60 {{fmt(detail.historical_volatility?.hv60)}}%</small></article>
    <article class="panel"><span>收盤成交量</span><strong>{{fmt(detail.volume,0)}} 張</strong><small>資料日 {{result.date}} · 最後成交價 {{fmt(detail.last)}}</small></article>
   </div>

   <section class="score-panel panel">
    <div class="score-ring" :class="scoreClass(detail.score.value)"><strong>{{detail.score.value}}</strong><span>/ 100</span></div>
    <div><div class="eyebrow">權證條件評分</div><h2>{{detail.score.label}}</h2><p>{{report?.note}}</p><div class="tags"><span v-for="reason in detail.score.reasons" :key="reason" class="ok">{{reason}}</span><span v-for="warning in detail.score.warnings" :key="warning" class="warn">{{warning}}</span></div></div>
    <dl><div><dt>到期日</dt><dd>{{detail.expiry}}</dd></div><div><dt>剩餘天數</dt><dd>{{detail.remaining_days}} 日</dd></div><div><dt>行使比例</dt><dd>{{fmt(detail.ratio,4)}}</dd></div><div><dt>價差</dt><dd>{{detail.score.spread==null?'無完整報價':fmt(detail.score.spread)+'%'}}</dd></div><div><dt>自營庫存</dt><dd>{{detail.inventory==null?'來源未揭露':fmt(detail.inventory,0)+' 張'}}</dd></div><div><dt>流通張數</dt><dd>{{detail.outstanding==null?'—':fmt(detail.outstanding,0)+' 張'}}</dd></div></dl>
   </section>

   <section v-if="detail.market_warnings?.length" class="market-warning panel">
    <header><span class="warning-icon">⚠</span><div><h2>造市風險警示</h2><p>以下為公開報價的客觀異常訊號，不代表已能認定券商有不當行為。</p></div></header>
    <div class="warning-list"><article v-for="item in detail.market_warnings" :key="item.code" :class="item.severity"><strong>{{item.title}}</strong><p>{{item.message}}</p></article></div>
   </section>

   <section class="calculator panel">
    <header><div><div class="eyebrow">SCENARIO</div><h2>權證情境試算</h2></div><p>調整標的目標價、持有天數與隱波，估算權證理論價格與損益。</p></header>
    <div class="calc-body"><div class="inputs">
     <label>標的目標價<input v-model.number="target" type="number" min="0" step="0.1"></label>
     <label>預計持有天數<input v-model.number="holdDays" type="number" min="0" :max="detail.remaining_days-1"></label>
     <label>假設隱波 %<input v-model.number="scenarioIv" type="number" min="1" step="0.1"></label>
     <label>預計買入權證價<input v-model.number="entry" type="number" min="0.01" step="0.01"></label>
     <label>投入金額<input v-model.number="amount" type="number" min="0" step="1000"></label>
    </div><div class="calc-results">
     <div class="scenario-score"><span>情境分數</span><strong :class="scoreClass(calculation?.scenarioScore||0)">{{calculation?.scenarioScore}} · {{calculation?.scenarioLabel}}</strong><small>目前 {{detail.score.value}} 分 · 變動 {{(calculation?.scoreChange||0)>0?'+':''}}{{calculation?.scoreChange}}</small></div>
     <div><span>理論權證價</span><strong>{{fmt(calculation?.theoretical,3)}}</strong></div><div><span>預估報酬</span><strong :class="(calculation?.returnPct||0)>=0?'up':'down'">{{pct(calculation?.returnPct)}}</strong></div>
     <div><span>可買整張</span><strong>{{fmt(calculation?.lots,0)}} 張</strong></div><div><span>預估損益</span><strong>{{money(calculation?.profit)}}</strong></div>
     <div><span>隱波變動</span><strong :class="(calculation?.ivDrop||0)>0?'down':''">{{(calculation?.ivDrop||0)>0?'-':''}}{{fmt(calculation?.ivDrop)}} 個百分點</strong></div><div><span>情境實質槓桿</span><strong>{{fmt(calculation?.effective)}} 倍</strong></div>
     <div><span>到期損益兩平</span><strong>{{fmt(calculation?.breakEven)}}</strong></div><div><span>試算成本</span><strong>{{money(calculation?.cost)}}</strong></div>
    </div></div><footer>情境分數會隨目標價、持有天數、假設隱波與買入價同步調整；假設隱波低於目前隱波時會扣除隱波驟降風險。本試算未計入造市價差、隱波曲面、交易成本與美式提前履約特性。</footer>
   </section>

   <section class="weekly panel">
    <header><div><div class="eyebrow">BROKER FLOW</div><h2>本週買賣超</h2></div><p>{{result.weekly.date||'尚無資料'}} · {{result.weekly.period}} · 金額依平均成本估算</p></header>
    <div v-if="result.weekly_error" class="inline-warning">{{result.weekly_error}}</div>
    <div class="flow-columns"><div><h3>買超券商 <b>合計 {{fmt(result.weekly.total_buy,0)}} 張</b></h3><table><thead><tr><th>券商分點</th><th>淨買超</th><th>估算金額</th></tr></thead><tbody><tr v-for="row in result.weekly.buys" :key="row.broker"><td>{{row.broker}}</td><td class="up">+{{fmt(row.net,0)}} 張</td><td>{{money(row.estimated_amount)}}</td></tr><tr v-if="!result.weekly.buys.length"><td colspan="3">尚無可辨識資料</td></tr></tbody></table></div>
     <div><h3>賣超券商 <b>合計 {{fmt(result.weekly.total_sell,0)}} 張</b></h3><table><thead><tr><th>券商分點</th><th>淨賣超</th><th>估算金額</th></tr></thead><tbody><tr v-for="row in result.weekly.sells" :key="row.broker"><td>{{row.broker}}</td><td class="down">-{{fmt(row.net,0)}} 張</td><td>{{money(row.estimated_amount)}}</td></tr><tr v-if="!result.weekly.sells.length"><td colspan="3">尚無可辨識資料</td></tr></tbody></table></div></div>
   </section>
  </template>

  <template v-else-if="stock">
   <section class="identity panel"><div><span class="pill">標的權證比較</span><h2>{{stock.code}} {{stock.name}}</h2><p>來源現價 {{fmt(stock.price)}} · <b :class="stock.change_percent>=0?'up':'down'">{{pct(stock.change_percent)}}</b><template v-if="Number(manualPrice)>0"> · 依輸入股價 {{fmt(manualPrice)}} 僅顯示價內權證</template></p></div><div class="quote"><span>目前顯示</span><strong>{{rows.length}}</strong><small>全部 {{stock.total}} 檔；到期至少 60 日</small></div></section>
   <div class="filter-bar">
    <div class="segments"><button :class="{active:kind==='all'}" @click="kind='all'">全部</button><button :class="{active:kind==='call'}" @click="kind='call'">認購</button><button :class="{active:kind==='put'}" @click="kind='put'">認售</button></div>
    <label>最高隱波 %<input v-model.number="maxIv" type="number" placeholder="不限" min="1"></label>
    <label class="check-filter"><input v-model="hideSmallVolume" type="checkbox">排除最新成交量小於 50 張</label>
    <span class="enrich-status">{{enriching?'正在分批載入最新日券商分點…':'點欄位標題可切換排序'}}</span>
   </div>
   <section class="stock-table panel"><table><thead><tr><th>權證</th><th>類型</th><th><button @click="setSort('days')">到期／剩餘{{sortMark('days')}}</button></th><th>履約價／比例</th><th>買／賣</th><th>價差</th><th>價內外</th><th><button @click="setSort('iv','asc')">隱波／升降{{sortMark('iv')}}</button></th><th>Delta</th><th><button @click="setSort('leverage')">實質槓桿{{sortMark('leverage')}}</button></th><th><button @click="setSort('spreadLeverage','asc')">差槓比{{sortMark('spreadLeverage')}}</button></th><th><button @click="setSort('flow')">最新日分點{{sortMark('flow')}}</button></th><th><button @click="setSort('volume')">最新成交量{{sortMark('volume')}}</button></th><th><button @click="setSort('score')">評分{{sortMark('score')}}</button></th><th></th></tr></thead><tbody>
    <tr v-for="row in rows" :key="row.code"><td><b>{{row.name}} <span v-if="row.market_warnings?.length" class="warning-icon small" :title="row.market_warnings.join('、')">⚠</span></b><small>{{row.code}}</small></td><td><span class="pill tiny">{{row.kind==='call'?'認購':'認售'}}</span></td><td>{{row.expiry}}<small>{{row.remaining_days}} 日</small></td><td>{{fmt(row.strike)}}<small>{{fmt(row.ratio,4)}}</small></td><td>{{fmt(row.bid)}} / {{fmt(row.ask)}}</td><td>{{row.spread==null?'—':fmt(row.spread)+'%'}}</td><td>{{moneyness(rowMoneyness(row))}}</td><td>{{row.iv==null?'缺資料':fmt(row.iv)+'%'}}<small v-if="row.iv_change!=null" :class="row.iv_change>0?'up':row.iv_change<0?'down':''">（{{row.iv_change>0?'調升':row.iv_change<0?'調降':'持平'}} {{row.iv_change>0?'+':''}}{{fmt(row.iv_change)}}）</small></td><td>{{fmt(row.delta,4)}}</td><td>{{fmt(row.effective_leverage)}} 倍</td><td>{{fmt(row.spread_leverage,3)}}</td><td><template v-if="row.latest_flow"><b :class="row.latest_flow.side==='buy'?'up':row.latest_flow.side==='sell'?'down':''">{{row.latest_flow.side==='buy'?'買超':row.latest_flow.side==='sell'?'賣超':'持平'}} {{fmt(row.latest_flow.lots,0)}} 張</b><small>{{row.latest_flow.broker}}</small></template><span v-else>{{enriching&&row.volume!=null?'載入中':'—'}}</span></td><td>{{row.volume==null?'—':fmt(row.volume,0)+' 張'}}</td><td><span class="score-chip" :class="scoreClass(row.score.value)">{{row.score.value}} {{row.score.label}}</span></td><td><button class="row-action" @click="openWarrant(row)">完整分析</button></td></tr>
    <tr v-if="!rows.length"><td colspan="15" class="empty">{{stock.no_warrants?'沒有相關權證':'目前沒有符合篩選條件的權證。'}}</td></tr></tbody></table></section>
   <p class="footnote">差槓比＝買賣價差比 ÷ 實質槓桿，數值越低通常越有利。成交量先由批次行情提供；最新日分點只對有成交量的候選分批讀取並保存，避免一次查詢全部權證。按「完整分析」可查看正式到期日、Greeks 與本週券商流向。</p>
  </template>

  <section v-else class="empty-state panel"><Icon name="search" :size="34"/><h2>輸入權證或股票代號</h2><p>查權證可看實質槓桿、價內外、Delta、Theta、隱波、評分與本週券商買賣超；查股票可比較其 60 日以上權證。</p></section>
 </section>
</template>

<style scoped>
.warrant-page{display:grid;gap:20px}.search-panel{padding:18px 20px;display:grid;grid-template-columns:auto minmax(320px,1fr);align-items:center;gap:14px 24px}.search-panel>div:first-child{display:grid}.search-panel strong{font-size:.85rem}.search-panel span{font-size:.66rem;color:#8b9983;margin-top:4px}.search-panel form{display:flex;gap:8px}.search-panel input{flex:1;border:1px solid #d9e2d3;border-radius:9px;padding:12px 14px;font-size:.88rem;color:#42553c;background:#fff}.recent{grid-column:1/-1;display:flex;gap:7px;align-items:center;flex-wrap:wrap}.recent button{font-size:.64rem;padding:6px 9px;border-radius:999px;background:#f0f4ec;color:#5f7455}.identity{display:flex;align-items:center;justify-content:space-between;padding:24px}.identity h2{font-size:1.45rem;margin:9px 0 6px}.identity p{font-size:.75rem;color:#7d8c76;margin:0}.pill{display:inline-block;border-radius:999px;padding:5px 9px;background:#eaf2e3;color:#5d774c;font-size:.62rem}.pill.tiny{padding:4px 7px}.quote{text-align:right;display:grid}.quote span,.quote small{font-size:.65rem;color:#87957f}.quote strong{font-size:2rem;margin:2px 0}.metric-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}.metric-grid article{padding:18px;display:grid;gap:8px}.metric-grid span{font-size:.66rem;color:#83917c}.metric-grid strong{font-size:1.28rem}.metric-grid small{font-size:.6rem;line-height:1.5;color:#97a28f}.up{color:#b65348!important}.down{color:#278668!important}.score-panel{padding:24px;display:grid;grid-template-columns:auto 1fr auto;gap:24px;align-items:center}.score-ring{width:102px;height:102px;border:9px solid #c8d6bf;border-radius:50%;display:grid;place-content:center;text-align:center}.score-ring strong{font-size:1.8rem}.score-ring span{font-size:.58rem;color:#879580}.great{color:#537b3d!important}.good{color:#748848!important}.watch{color:#9a7939!important}.avoid{color:#a45952!important}.score-panel h2{margin:4px 0}.score-panel p{font-size:.68rem;color:#879580;line-height:1.7}.tags{display:flex;gap:6px;flex-wrap:wrap}.tags span{font-size:.6rem;padding:5px 8px;border-radius:999px}.tags .ok{background:#edf5e8;color:#5e794e}.tags .warn{background:#f8efe3;color:#916c35}.score-panel dl{min-width:230px;margin:0}.score-panel dl div{display:flex;justify-content:space-between;gap:18px;padding:8px 0;border-bottom:1px solid #edf0e9;font-size:.7rem}.score-panel dt{color:#8a9783}.calculator>header,.weekly>header{padding:20px 22px;border-bottom:1px solid #e7ece3;display:flex;justify-content:space-between;align-items:end}.calculator h2,.weekly h2{font-size:1.05rem;margin:4px 0 0}.calculator header p,.weekly header p{font-size:.67rem;color:#899781;margin:0}.calc-body{display:grid;grid-template-columns:1fr 1fr}.inputs{padding:20px;display:grid;grid-template-columns:repeat(2,1fr);gap:12px;border-right:1px solid #e8ede4}.inputs label,.filter-bar label{display:grid;gap:6px;font-size:.66rem;color:#819079}.inputs input,.filter-bar input,.filter-bar select{border:1px solid #dae3d5;border-radius:7px;padding:10px;background:#fff;color:#45583e}.calc-results{display:grid;grid-template-columns:repeat(2,1fr);padding:20px}.calc-results div{padding:12px;border-bottom:1px solid #edf0e9}.calc-results span{display:block;font-size:.62rem;color:#8b9884;margin-bottom:5px}.calc-results strong{font-size:1rem}.calc-results small{display:block;font-size:.58rem;color:#8b9884;margin-top:4px}.scenario-score{background:#f7f9f4;border-radius:8px}.calculator footer{border-top:1px solid #e8ede4;padding:13px 20px;font-size:.62rem;color:#8c9985}.flow-columns{display:grid;grid-template-columns:1fr 1fr}.flow-columns>div{padding:20px}.flow-columns>div+div{border-left:1px solid #e7ece3}.flow-columns h3{font-size:.78rem;display:flex;justify-content:space-between}.flow-columns h3 b{font-size:.64rem;color:#879580}.weekly table{width:100%;border-collapse:collapse;font-size:.7rem}.weekly th,.weekly td{padding:10px 7px;text-align:right;border-bottom:1px solid #edf0e9}.weekly th:first-child,.weekly td:first-child{text-align:left}.weekly th{color:#899780;font-weight:450}.inline-warning{padding:10px 20px;background:#faf3e8;color:#8f6b35;font-size:.67rem}.filter-bar{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.segments{display:flex;background:#edf1e9;border-radius:8px;padding:3px}.segments button{padding:8px 12px;border-radius:6px;color:#7f8d78;font-size:.7rem}.segments button.active{background:#fff;color:#45603c;box-shadow:0 1px 4px #73836722}.stock-table{overflow:auto}.stock-table table{border-collapse:collapse;width:100%;min-width:1380px;font-size:.69rem}.stock-table th,.stock-table td{padding:13px 10px;text-align:right;border-bottom:1px solid #e8ede4;white-space:nowrap}.stock-table th{background:#f6f8f3;color:#829177;font-weight:450}.stock-table th:first-child,.stock-table td:first-child{text-align:left}.stock-table td>b,.stock-table td>small{display:block}.stock-table td>small{font-size:.6rem;color:#94a08e;margin-top:4px}.score-chip{font-size:.62rem;padding:5px 7px;border-radius:999px;background:#f0f3ec}.row-action{padding:7px 9px;border:1px solid #d6e0d1;border-radius:7px;color:#5b744f}.empty{text-align:center!important;padding:50px!important;color:#8b9883}.empty-state{text-align:center;padding:70px 20px;color:#7d8d75}.empty-state h2{font-size:1rem}.empty-state p{font-size:.72rem;max-width:620px;line-height:1.8;margin:0 auto}.footnote{font-size:.65rem;color:#899681;margin:0}.error-banner{padding:12px 16px;background:#faece8;border-radius:8px;color:#9b5149;font-size:.72rem}.error-banner button{float:right;text-decoration:underline}@media(max-width:1100px){.metric-grid{grid-template-columns:repeat(3,1fr)}.score-panel{grid-template-columns:auto 1fr}.score-panel dl{grid-column:1/-1}.calc-body,.flow-columns{grid-template-columns:1fr}.inputs{border-right:0}.flow-columns>div+div{border-left:0;border-top:1px solid #e7ece3}}@media(max-width:720px){.search-panel{grid-template-columns:1fr}.search-panel form{grid-column:1}.metric-grid{grid-template-columns:1fr 1fr}.score-panel{grid-template-columns:1fr}.score-ring{width:84px;height:84px}.inputs{grid-template-columns:1fr}.identity{align-items:flex-start}.quote strong{font-size:1.4rem}}
.check-filter{display:flex!important;align-items:center;gap:7px;padding:9px 11px;border:1px solid #dbe3d6;border-radius:8px;background:#fff}.check-filter input{width:15px;flex:none;padding:0}.enrich-status{font-size:.65rem;color:#879580;margin-left:auto}.stock-table table{min-width:1750px}.stock-table th button{color:inherit;font:inherit;font-weight:500;white-space:nowrap}.stock-table th button:hover{color:#4f6845;text-decoration:underline}.stock-table td>span:not(.pill):not(.score-chip):not(.warning-icon){font-size:.65rem;color:#94a08e}.price-input{max-width:175px}.back-button{display:block;margin:0 0 12px;color:#5d774f;font-size:.68rem;text-decoration:underline}.warning-icon{color:#c88824;font-size:1rem;vertical-align:middle}.warning-icon.small{font-size:.75rem;margin-left:3px}.market-warning{padding:20px}.market-warning>header{display:flex;gap:12px;align-items:flex-start}.market-warning h2{font-size:1rem;margin:0 0 4px}.market-warning header p{font-size:.66rem;color:#8b9784;margin:0}.warning-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:16px}.warning-list article{padding:13px;border-radius:8px;background:#faf5e9;border-left:3px solid #c79a49}.warning-list article.high{background:#fbefea;border-color:#bd675a}.warning-list strong{font-size:.72rem}.warning-list p{font-size:.63rem;line-height:1.6;color:#7e8978;margin:5px 0 0}@media(max-width:900px){.warning-list{grid-template-columns:1fr}.search-panel form{flex-wrap:wrap}.price-input{max-width:none}}
</style>
