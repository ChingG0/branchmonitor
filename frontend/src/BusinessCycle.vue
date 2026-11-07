<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import {vRememberDisclosure} from './rememberDisclosure'
import BusinessCycleChart from './BusinessCycleChart.vue'
import Icon from './Icon.vue'
type Point={month:string;value:number;amount_million_usd?:number}
type Series={key:string;label:string;region:string;unit:string;kind:string;source:string;url:string;reference_url?:string;description:string;points:Point[];checked_at?:string;released_at?:string;release_note?:string;next_release?:string;history_note?:string;manual?:boolean;error?:string;summary:{reading:string;color:string;change:number|null;previous_month?:string}}
type Report={checked_at:string|null;series:Series[];warnings:{key:string;message:string}[];coverage:{available:number;total:number};job:{status:string;message:string};note:string}
const props=defineProps<{initial?:Report}>()
const data=ref<Report|null>(props.initial||null),loading=ref(!props.initial),error=ref(''),busy=ref(false),period=ref('36')
let timer:number|undefined
const latest=(s:Series)=>s.points.at(-1)
const fmt=(v:number|undefined|null,d=2)=>v==null?'—':v.toLocaleString('zh-TW',{maximumFractionDigits:d})
const time=(v?:string|null)=>v?v.slice(0,19).replace('T',' '):'來源未提供'
const change=(s:Series)=>s.summary.change==null?'前月資料不足':`${s.summary.change>0?'+':''}${fmt(s.summary.change)} ${s.unit==='%'?'百分點':s.unit}`
const visible=(s:Series)=>{if(period.value==='all')return s.points;const end=s.points.at(-1)?.month;if(!end)return [];const d=new Date(end+'-01T00:00:00Z');d.setUTCMonth(d.getUTCMonth()-Number(period.value)+1);return s.points.filter(p=>p.month>=d.toISOString().slice(0,7))}
const twPmi=computed(()=>data.value?.series.find(s=>s.key==='tw_pmi'))
const currentMonths=computed(()=>[...new Set(data.value?.series.flatMap(s=>s.points.map(p=>p.month))||[])].sort().reverse().slice(0,36))
const valueAt=(s:Series,m:string)=>s.points.find(p=>p.month===m)?.value
async function load(){
 try{const r=await localFetch('/api/business-cycle');const j=await readJson(r);if(!r.ok)throw Error(j.error||'無法讀取景氣資料');data.value=j;error.value='';busy.value=j.job.status==='running';if(busy.value){timer=window.setTimeout(load,1500)}}catch(e){error.value=(e as Error).message;busy.value=false}finally{loading.value=false}
}
async function refresh(){if(busy.value)return;busy.value=true;try{const r=await localFetch('/api/business-cycle/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const j=await readJson(r);if(!r.ok)throw Error(j.error||'更新未完成');await load()}catch(e){error.value=(e as Error).message;busy.value=false}}
onMounted(()=>{if(!props.initial)load()});onUnmounted(()=>{if(timer)clearTimeout(timer)})
</script>
<template>
 <div class="business-cycle">
 <header class="page-heading"><div><small>BUSINESS CYCLE · TAIWAN & UNITED STATES</small><h1>景氣循環分析</h1><p>先看需求與景氣動能，再看物價壓力。台灣與美國、資料月份與發布時間分開閱讀。</p></div><button class="primary" :disabled="busy" @click="refresh"><Icon name="refresh" :size="16" :class="{spinning:busy}"/>{{busy?'更新中':'更新景氣資料'}}</button></header>
 <p v-if="loading" class="notice">讀取已保存的景氣資料…</p><p v-if="error" class="notice error">{{error}}</p>
 <template v-if="data">
 <div class="cycle-overview"><div><small>台灣製造業目前位置</small><h2>{{twPmi?.summary.reading||'尚待資料'}}</h2><p>PMI {{fmt(twPmi?latest(twPmi)?.value:null)}} · {{twPmi?latest(twPmi)?.month:''}}<span v-if="twPmi"> · 較前月 {{change(twPmi)}}</span></p></div><div><b>判讀依據</b><p>PMI 高於50且上升：擴張加快；高於50但下降：擴張放慢。低於50則分為收縮改善或收縮加深。這是當前月資料的描述，不是下一季或股市方向預測。</p><small>最近更新 {{time(data.checked_at)}} · 已保存 {{data.coverage.available}}／{{data.coverage.total}} 項</small></div></div>
 <div class="controls"><span>走勢期間</span><button v-for="p in [{value:'12',label:'近1年'},{value:'36',label:'近3年'},{value:'120',label:'近10年'},{value:'all',label:'全部'}]" :key="p.value" :aria-pressed="period===p.value" @click="period=p.value">{{p.label}}</button><small>各指標依實際可取得的月份顯示；缺月斷線，不補值。</small></div>
 <div class="indicator-grid"><article v-for="s in data.series" :key="s.key" class="indicator-card" :style="{'--series-color':s.summary.color}">
 <div class="card-heading"><span>{{s.region}}</span><small v-if="s.manual">官方查核保存</small><a :href="s.url" target="_blank" rel="noopener noreferrer">來源 ↗</a></div><h2>{{s.label}}</h2>
 <div class="current"><strong>{{fmt(latest(s)?.value)}}<small>{{s.unit}}</small></strong><span>{{s.summary.reading}}</span></div>
 <div class="comparison"><b>資料 {{latest(s)?.month||'尚無資料'}}</b><span>較前月 {{change(s)}}</span></div>
 <p v-if="s.key==='tw_orders'&&latest(s)?.amount_million_usd" class="amount">外銷訂單 {{fmt(latest(s)!.amount_million_usd!/100)}} 億美元</p>
 <BusinessCycleChart :points="visible(s)" :kind="s.kind" :label="s.label" :unit="s.unit" :color="s.summary.color"/>
 <div v-if="s.kind==='signal'" class="lamp-legend"><span style="color:#4d83bc">9–16 藍燈</span><span>17–22 黃藍</span><span style="color:#4e8b71">23–31 綠燈</span><span style="color:#ca9951">32–37 黃紅</span><span style="color:#c76e62">38–45 紅燈</span></div>
 <p class="description">{{s.description}}</p><p v-if="s.history_note" class="method">{{s.history_note}}</p>
 <p v-if="s.error" class="notice source-warning">{{s.error}}</p>
 <footer><span>{{s.source}} <a v-if="s.reference_url" :href="s.reference_url" target="_blank" rel="noopener noreferrer">日曆對照 ↗</a></span><span>發布 {{time(s.released_at)}}<template v-if="s.next_release"> · 下次預定 {{s.next_release}}</template></span><span>取得／查核 {{time(s.checked_at)}}</span></footer>
 <details v-remember-disclosure="'business-cycle.method.'+s.key" class="method"><summary>資料口徑與發布時間</summary><p>{{s.release_note||'原始發布時間未提供，不以取得時間代替。'}}</p><p v-if="s.points.length">保存範圍 {{s.points[0].month}} ～ {{latest(s)?.month}}，共 {{s.points.length}} 個月份。年增率的前月差異以百分點計算。</p></details>
 </article></div>
 <details v-remember-disclosure="'business-cycle.monthly-table'" class="panel"><summary>近36個月份數值對照</summary><div class="table-scroll"><table><thead><tr><th>月份</th><th v-for="s in data.series" :key="s.key">{{s.region}} {{s.label}}<small>{{s.unit}}</small></th></tr></thead><tbody><tr v-for="m in currentMonths" :key="m"><th>{{m}}</th><td v-for="s in data.series" :key="s.key">{{fmt(valueAt(s,m))}}</td></tr></tbody></table></div></details>
 <div class="notice">{{data.note}}<br>首頁「更新全部資料」及每日18:00的一次更新會包含此頁；來源未齊時保留舊值並提示手動補齊，不會反覆請求。</div>
 </template>
 </div>
</template>
<style scoped>
.business-cycle{color:#354b3c;min-width:0}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:22px}.page-heading small{letter-spacing:.13em;font-size:10px;color:#8b9d80}h1{margin:8px 0;font-size:31px}.page-heading p{font-size:13px;color:#7a8c72;line-height:1.8;margin:0}.page-heading button{display:flex;align-items:center;gap:8px;white-space:nowrap}.cycle-overview{display:grid;grid-template-columns:1fr 1.7fr;gap:25px;border:1px solid #dfe8d9;border-radius:14px;padding:24px;background:#f5f8f0;margin-bottom:20px}.cycle-overview small{font-size:11px;color:#87967b}.cycle-overview h2{margin:10px 0;color:#506f50;font-size:27px}.cycle-overview p{font-size:12px;line-height:1.9;color:#748569}.cycle-overview b{font-size:13px}.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:18px 0;font-size:12px;color:#7b8c70}.controls button{border:1px solid #dfe7d8;border-radius:7px;background:#fff;padding:7px 12px;color:#69815d;font:inherit;cursor:pointer}.controls button[aria-pressed=true]{background:#557859;color:white}.controls small{font-size:11px;margin-left:auto}.indicator-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.indicator-card{min-width:0;background:white;border:1px solid #e1e8db;border-radius:15px;padding:23px;overflow:hidden}.card-heading{display:flex;align-items:center;gap:10px;font-size:11px;color:#8b9b7d}.card-heading>span{border-radius:20px;background:#eff4e9;padding:5px 10px}.card-heading small{color:#a08b55;font-size:10px}.card-heading a{margin-left:auto;color:#73906a;text-decoration:none}.indicator-card h2{font-size:18px;margin:14px 0 19px}.current{display:flex;justify-content:space-between;align-items:center;gap:12px}.current strong{font-size:36px;color:var(--series-color)}.current strong small{font-size:15px;margin-left:5px}.current>span{font-size:12px;color:var(--series-color);background:#f7f8f3;padding:7px 10px;border-radius:20px}.comparison{display:flex;justify-content:space-between;gap:8px;margin-top:12px;font-size:11px;color:#829177}.comparison b{font-weight:500}.amount{font-size:12px;color:#6f8764}.description{font-size:12px;line-height:1.8;color:#728367}.method{font-size:10px;line-height:1.8;color:#95a08a}.method summary{cursor:pointer;color:#7f9370}.lamp-legend{display:flex;flex-wrap:wrap;gap:10px;font-size:10px;color:#8d9a86;margin:7px 0 14px}.indicator-card footer a{color:#6f8866;margin-left:10px;text-decoration:none}.indicator-card footer{display:flex;flex-direction:column;gap:5px;border-top:1px solid #eaf0e4;padding-top:14px;margin-top:15px;color:#98a18c;font-size:10px;line-height:1.6}.notice{padding:14px 17px;border-radius:9px;background:#f6f6ee;color:#8c8d6b;line-height:1.9;font-size:11px;margin:17px 0}.notice.error,.source-warning{background:#fff5e9;color:#9b804f}.panel{margin:22px 0;background:white;border:1px solid #e1e8db;border-radius:13px;padding:20px}.panel>summary{cursor:pointer;font-size:14px;color:#607c56}.table-scroll{overflow-x:auto;margin-top:15px;max-height:490px}table{border-collapse:collapse;width:100%;min-width:900px;font-size:12px}th,td{padding:12px;text-align:right;border-bottom:1px solid #edf1e8}th:first-child{text-align:left}thead th{position:sticky;top:0;background:#f5f8f0;color:#708563;font-size:11px}thead small{display:block;font-weight:400;margin-top:5px}@media(max-width:1000px){.indicator-grid{grid-template-columns:1fr}}@media(max-width:650px){.page-heading{align-items:start;flex-direction:column}.cycle-overview{grid-template-columns:1fr;gap:8px;padding:18px}.indicator-card{padding:17px}.controls small{width:100%;margin-left:0}.current strong{font-size:31px}.comparison{flex-direction:column}h1{font-size:26px}}
</style>
