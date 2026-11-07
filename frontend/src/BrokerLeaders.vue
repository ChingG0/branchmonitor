<script lang="ts">
// Reuse reports when this page is unmounted and revisited in the same session.
type Style={key:string;label:string;observable:boolean;count:number|null;wins:number|null;win_rate:number|null;pnl:number|null;gross_profit:number|null;gross_loss:number|null;mean_days:number|null;share_pct:number|null}
type Forward={win_rate:number|null;avg_return:number|null;wins:number;samples:number;pending:number;missing:number;total:number}
type Row={broker:string;branch:string;name:string;first_date:string|null;status:string;verified_stocks:number;candidate_stocks:number;paired_rate:number|null;paired_wins:number;paired_samples:number;paired_unscored:number;cycle_rate:number|null;cycle_wins:number;cycle_samples:number;cycle_unscored:number;open_cycles:number;turnover:number|null;known_turnover:number|null;unpriced_flows:number;cycle_pnl:number|null;cycle_gross_profit:number|null;cycle_gross_loss:number|null;dominant_style:string|null;profit_style:string|null;styles:Style[];forward:Record<string,Forward>;forward_5_rate:number|null;forward_10_rate:number|null;forward_5_return:number|null;forward_10_return:number|null;forward_5_samples:number;forward_10_samples:number}
type Report={as_of:string|null;source_latest_date?:string|null;available_dates:string[];computed_at:string;rows:Row[];job:{status:string;message:string}|null;totals:{branches:number;ready:number;paired_samples:number;cycle_samples:number;unscored_cycles:number}}
const savedReports=new Map<string,Report>()
</script>
<script setup lang="ts">
import {localFetch,readJson} from './http'
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import Icon from './Icon.vue'
const props=defineProps<{scopeKey?:string}>()
type Metric='paired_rate'|'cycle_rate'|'turnover'|'cycle_pnl'|'forward_5_rate'|'forward_10_rate'|'forward_5_return'|'forward_10_return'
const report=ref<Report|null>(null),date=ref(''),dates=ref<string[]>([]),sort=ref<Metric>('paired_rate'),expanded=ref(''),loading=ref(false),error=ref('')
const options:[Metric,string][]=[['paired_rate','已配對賣出勝率'],['cycle_rate','完整清倉勝率'],['forward_5_rate','買入後 5 日報酬勝率'],['forward_10_rate','買入後 10 日報酬勝率'],['forward_5_return','買入後 5 日平均報酬'],['forward_10_return','買入後 10 日平均報酬'],['turnover','成交金額（估算）'],['cycle_pnl','可評分清倉損益']]
let epoch=0,alive=true
const cacheKey=()=>JSON.stringify([props.scopeKey||'',date.value])
const rows=computed(()=>[...(report.value?.rows||[])].sort((a,b)=>{
 const x=a[sort.value],y=b[sort.value]
 if(x==null&&y!=null)return 1;if(y==null&&x!=null)return -1
 const diff=(y??0)-(x??0);if(diff)return diff
 const sample=sort.value.startsWith('forward_5_')?'forward_5_samples':sort.value.startsWith('forward_10_')?'forward_10_samples':sort.value==='paired_rate'?'paired_samples':'cycle_samples'
 return b[sample]-a[sample]||a.name.localeCompare(b.name,'zh-TW')
}))
const fmt=(n:number|null|undefined,d=0)=>n==null?'—':n.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const pct=(n:number|null|undefined)=>n==null?'—':fmt(n,1)+'%'
const money=(n:number|null|undefined)=>n==null?'—':(n<0?'−':'')+(Math.abs(n)>=1e8?fmt(Math.abs(n)/1e8,2)+' 億':fmt(Math.abs(n)/1e4,1)+' 萬')
const tone=(n:number|null|undefined)=>n==null||n===0?'muted':n>0?'positive':'negative'
const key=(r:Row)=>r.broker+':'+r.branch
async function load(force=false){
 const id=++epoch,key=cacheKey();error.value=''
 const saved=savedReports.get(key)
 if(!force&&saved){report.value=saved;dates.value=saved.available_dates;loading.value=false;return}
 loading.value=true
 try{
  const response=await localFetch(force?'/api/leaders/refresh':'/api/leaders'+(date.value?'?date='+date.value:''),force?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date:date.value||null})}:undefined)
  const data=await readJson(response);if(!alive||id!==epoch)return
  if(!response.ok)throw new Error(data.error||'無法計算券商風雲榜')
  report.value=data;dates.value=data.available_dates;error.value=''
  if(data.as_of){
   for(const [entry,value] of savedReports)if(JSON.parse(entry)[0]===(props.scopeKey||'')&&value.as_of===data.as_of)savedReports.set(entry,data)
   savedReports.set(key,data);savedReports.set(JSON.stringify([props.scopeKey||'',data.as_of]),data)
  }
 }catch(e){if(alive&&id===epoch)error.value=(e as Error).message}finally{if(alive&&id===epoch)loading.value=false}
}
async function changeDate(){report.value=null;expanded.value='';await load()}
watch(()=>props.scopeKey,()=>{report.value=null;date.value='';expanded.value='';void load()})
onMounted(()=>{void load()})
onUnmounted(()=>{alive=false;epoch++})
</script>
<template>
 <div class="leaders-page">
  <header class="page-header"><div><div class="eyebrow">BROKER LEADERBOARD</div><h1>券商風雲榜</h1><p>已追蹤分點 · 買入後報酬、賣出勝率與交易風格</p></div><div class="header-actions"><label class="date-control"><Icon name="clock" :size="17"/><select v-model="date" @change="changeDate" aria-label="風雲榜統計日期"><option value="">已儲存統計{{report?.as_of?' · '+report.as_of:''}}</option><option v-for="day in dates" :key="day" :value="day">{{day}}</option></select></label><button class="primary" :disabled="loading" @click="load(true)"><Icon name="refresh" :class="{spinning:loading}" :size="17"/>{{loading?'整理排行中':'更新並儲存統計'}}</button></div></header>
  <div v-if="error" class="error-banner" role="alert">{{error}}<button @click="load(true)">重試</button></div>
  <p v-if="loading&&!report" class="watch-loading">正在彙整已追蹤分點的清倉紀錄…</p>
  <template v-if="report">
   <div class="watch-totals"><article><span>已追蹤分點</span><strong>{{report.totals.branches}}<small> 家</small></strong></article><article><span>歷史已補齊</span><strong>{{report.totals.ready}}<small> / {{report.totals.branches}} 家</small></strong></article><article><span>可評分賣出事件</span><strong>{{fmt(report.totals.paired_samples)}}<small> 次</small></strong></article><article><span>可評分完整清倉</span><strong>{{fmt(report.totals.cycle_samples)}}<small> 輪</small></strong></article></div>
   <div class="major-definition"><Icon name="info" :size="18"/><p><strong>用兩種勝率，分別看賣出事件與完整一輪。</strong><span>已配對賣出勝率：可完整配對、有價格的獲利賣出事件 ÷ 可評分賣出事件。完整清倉勝率：獲利清倉輪數 ÷ 可評分完整清倉輪數。分批賣出可有多次事件，但清倉只計一輪；損平列入分母。</span></p></div>
   <p class="major-footnote">5／10 日報酬：觀察期間每個「個股 × 淨買超日」計一筆，買入日為第 0 日；以第 5／10 個交易日收盤價計算漲跌幅。勝率＝正報酬筆數 ÷ 可評分筆數，損平列入分母；平均報酬採等權平均。未到期、缺價或交易日曆缺漏不列入。這是買入後價格表現，與是否實際賣出無關，也不是超越大盤的命中率。</p>
   <div v-if="report.totals.ready<report.totals.branches" class="main-watch-warning"><Icon name="info" :size="17"/>儲存時部分分點歷史尚未補齊，排名為暫計。缺價或不完整清倉不列入清倉勝率；目前 {{report.totals.unscored_cycles}} 輪待評分。</div>
   <div class="leaders-controls"><label>排名依據<select v-model="sort" aria-label="券商排名依據"><option v-for="[id,label] in options" :key="id" :value="id">{{label}} · 由高到低</option></select></label><span>同分時優先樣本較多者；沒有可評分資料的分點排在最後。點分點展開風格。</span></div>
   <section class="panel leaders-panel"><div class="table-scroll"><table class="leaders-table"><thead><tr><th>排名</th><th>券商分點</th><th :class="{selected:sort==='paired_rate'}">已配對賣出勝率</th><th :class="{selected:sort==='cycle_rate'}">完整清倉勝率</th><th v-for="h in [5,10]" :key="h" :class="{selected:sort.startsWith('forward_'+h+'_')}">買入後 {{h}} 日<small class="cell-note">報酬勝率 / 平均報酬</small></th><th :class="{selected:sort==='turnover'}">成交金額 <small>估算</small></th><th :class="{selected:sort==='cycle_pnl'}">清倉損益 <small>估算</small></th><th>清倉獲利風格</th></tr></thead>
    <tbody v-for="(row,i) in rows" :key="key(row)"><tr :class="{'ranking-expanded':expanded===key(row)}"><td><span class="leader-rank" :class="{leading:i<3}">{{String(i+1).padStart(2,'0')}}</span></td><td><button class="leader-name" :aria-expanded="expanded===key(row)" @click="expanded=expanded===key(row)?'':key(row)"><strong>{{row.name}} <Icon name="chevron" :size="13"/></strong><small>{{row.first_date||'尚無資料'}} 起 · {{row.status==='ready'?'歷史已補齊':row.status==='partial'?'回補中／資料待齊':'尚無紀錄'}}</small></button></td><td><strong>{{pct(row.paired_rate)}}</strong><small class="cell-note">{{fmt(row.paired_wins)}} 勝 / {{fmt(row.paired_samples)}} 次</small><small v-if="row.paired_unscored" class="cell-note">另 {{fmt(row.paired_unscored)}} 次未評分</small></td><td><strong>{{pct(row.cycle_rate)}}</strong><small class="cell-note">{{fmt(row.cycle_wins)}} 勝 / {{fmt(row.cycle_samples)}} 輪</small><small v-if="row.cycle_unscored" class="cell-note">另 {{fmt(row.cycle_unscored)}} 輪未評分</small></td><td v-for="h in [5,10]" :key="h"><strong>{{pct(row.forward[String(h)].win_rate)}}</strong><small class="cell-note" :class="tone(row.forward[String(h)].avg_return)">平均報酬 {{pct(row.forward[String(h)].avg_return)}}</small><small class="cell-note">{{fmt(row.forward[String(h)].wins)}} 勝 / {{fmt(row.forward[String(h)].samples)}} 次</small><small class="cell-note">未到期 {{fmt(row.forward[String(h)].pending)}} · 缺資料 {{fmt(row.forward[String(h)].missing)}}</small></td><td>{{money(row.turnover)}}<small v-if="row.unpriced_flows" class="cell-note">缺 {{row.unpriced_flows}} 筆價格；已知 {{money(row.known_turnover)}}</small><small v-else class="cell-note">買進＋賣出</small></td><td><strong :class="tone(row.cycle_pnl)">{{money(row.cycle_pnl)}}</strong><small class="cell-note">可評分 {{row.cycle_samples}} 輪合計</small></td><td><span class="leader-style">{{row.profit_style||(row.cycle_samples?'尚無獲利區間':'尚無清倉樣本')}}</span><small class="cell-note">常見持股：{{row.dominant_style||'待確認'}}</small></td></tr>
     <tr v-if="expanded===key(row)" class="contribution-row"><td colspan="9"><div class="contribution-heading"><strong>{{row.name}} · 清倉風格拆解</strong><small>持股天數採交易日，從首次建倉至部位歸零。常見持股＝清倉輪數最多的區間；獲利風格＝清倉淨損益最高且為正的區間。</small></div><table class="contribution-table"><thead><tr><th>持股風格</th><th>完整清倉</th><th>清倉占比</th><th>勝率</th><th>獲利合計</th><th>虧損合計</th><th>淨損益</th></tr></thead><tbody><tr v-for="style in row.styles.filter(s=>s.key!=='unknown'||s.count)" :key="style.key"><td>{{style.label}}</td><template v-if="style.observable"><td>{{fmt(style.count)}} 輪</td><td>{{pct(style.share_pct)}}</td><td>{{pct(style.win_rate)}}</td><td :class="tone(style.gross_profit)">{{money(style.gross_profit)}}</td><td :class="tone(style.gross_loss)">{{money(style.gross_loss)}}</td><td :class="tone(style.pnl)"><strong>{{money(style.pnl)}}</strong></td></template><td v-else colspan="6" class="daytrade-unavailable">無法判定：每日買賣彙總無法確認同日交易順序與實際當沖損益。</td></tr></tbody></table><p class="broker-method">已確認明細 {{row.verified_stocks}} / {{row.candidate_stocks}} 檔 · 尚持有 {{row.open_cycles}} 輪。未清倉部位不參與清倉風格；同一分點包含多位投資人，分類是此觀察期間的推估結果。</p></td></tr>
    </tbody><tbody v-if="!rows.length"><tr><td colspan="9" class="empty-row">目前沒有已追蹤分點。</td></tr></tbody>
   </table></div></section>
   <p class="major-footnote">成交金額＝觀察期間各日（買進＋賣出張數）× 1,000 × 當日收盤價，僅涵蓋已發現個股；不是券商全部實際成交額。清倉損益沿用每日淨買賣超的 FIFO 推估，未扣手續費、交易稅，未還原股利或公司事件。各分點觀察起日可能不同。</p>
   <p class="major-footnote">統計資料截至 {{report.as_of||'尚無資料'}} · 儲存時間 {{report.computed_at.slice(0,19).replace('T',' ')}} · 切換頁籤沿用已儲存統計，按「更新並儲存統計」才重新計算。首次開啟未儲存的日期會計算一次。<span v-if="report.source_latest_date&&report.as_of&&report.source_latest_date>report.as_of"> 本機已有 {{report.source_latest_date}} 資料，可切回「已儲存統計」後按更新。</span></p>
  </template>
 </div>
</template>
