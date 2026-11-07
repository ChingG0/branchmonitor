<script setup lang="ts">
import {vRememberDisclosure} from './rememberDisclosure'
import {computed,onMounted,ref} from 'vue'
import {localFetch,readJson} from './http'
import V6Panel from './V6Panel.vue'
import V6Comparison from './V6Comparison.vue'
import type {ComparisonReport} from './v6ComparisonTypes'
import type {LiveContext} from './v6LiveTypes'
import V6Forecast from './V6Forecast.vue'
import type {EventForecast,ForecastAudit,ForecastResearch,ForecastScorecard} from './v6ForecastTypes'
import V6RiskVisual from './V6RiskVisual.vue'
import V6History from './V6History.vue'
import V6CrashScale from './V6CrashScale.vue'
import {formatObservation,formatRiskText} from './v6Format'
import type {V6Historical} from './v6HistoryTypes'
import type {CrashScale} from './v6CrashTypes'
import type {V6Model} from './v6Types'

type Indicator={category:string,label:string,value:number|string,unit:string,as_of:string,source:string,url:string,reading:string}
type News={title:string,published_at:string,source:string,url:string,impact:string,direction:string}
type Snapshot={live_assessment?:LiveContext|null,comparison_error?:string,forecast?:EventForecast|null,id:string,prepared_at:string,market_as_of:string,stage:string,summary:string,drivers:string[],watch_next:string[],weakening:string[],indicators:Indicator[],local_indicators:Indicator[],news:News[],probability_note:string,model?:V6Model|null}
type History={id:string,prepared_at:string,market_as_of:string,stage:string}
type Category={key:string,label:string,description:string}
type Report={short_simulation?:V6Model|null,comparison?:ComparisonReport|null,forecast_scorecard?:ForecastScorecard|null,forecast_research?:ForecastResearch|null,forecast_audit?:ForecastAudit|null,crash_scale?:CrashScale|null,historical?:V6Historical|null,snapshot:Snapshot|null,history:History[],categories:Category[]}
const emit=defineEmits<{forecast:[]}>()
const data=ref<Report|null>(null),loading=ref(true),error=ref(''),selected=ref('')
const primaryModel=computed(()=>data.value?.short_simulation||data.value?.snapshot?.model||null)
const isPrimaryCross=computed(()=>primaryModel.value?.strategy_key==='v6-2')
const validUrl=(url:string)=>/^https?:\/\//i.test(url)?url:'#'
const categoryRows=(key:string)=>[...(data.value?.snapshot?.local_indicators||[]),...(data.value?.snapshot?.indicators||[])].filter(row=>row.category===key)
async function load(id=''){
 loading.value=true
 try{
  const response=await localFetch('/api/v6-weather'+(id?'?id='+encodeURIComponent(id):''))
  const result=await readJson(response)
  if(!response.ok)throw Error(result.error||'無法讀取 V6 預警紀錄')
  data.value=result as Report;selected.value=data.value.snapshot?.id||'';error.value=''
 }catch(cause){error.value=(cause as Error).message}
 finally{loading.value=false}
}
onMounted(()=>load())
</script>

<template>
 <div class="weather-page">
  <header class="hero"><div><span class="eyebrow">V6 · MARKET STORM WATCH</span><h1>股災氣象球</h1><p>追蹤風險形成、增強與減弱。只有你在對話中要求「更新 V6」時，才會重新查核市場數據與新聞並保存一份預報。</p></div><div class="hero-state"><small>最近一次人工研究</small><strong>{{data?.history[0]?.prepared_at?.slice(0,16).replace('T',' ')||'尚未建立'}}</strong></div></header>
  <div class="forecast-location"><span>T+1／T+2／T+3預報已移到獨立分頁。</span><button @click="emit('forecast')">前往3日模型預測</button></div>
  <p v-if="error" class="notice error">{{error}}</p>
  <p v-if="loading" class="notice">正在讀取已保存的 V6 預報…</p>
  <template v-if="data&&!loading">
   <V6Comparison :report="data.comparison||null" :live-assessment="data.snapshot?.live_assessment" :research-notice="data.snapshot?.comparison_error"/>
   <div class="history" v-if="data.history.length"><label for="v6-history">預報紀錄</label><select id="v6-history" v-model="selected" @change="load(selected)"><option v-for="row in data.history" :key="row.id" :value="row.id">{{row.prepared_at.slice(0,16).replace('T',' ')}} · {{row.stage}} · 行情 {{row.market_as_of}}</option></select></div>
   <V6CrashScale v-if="data.crash_scale" :data="data.crash_scale"/>
   <V6Forecast :forecast="data.snapshot?.forecast" :audit="data.forecast_audit" :research="data.forecast_research" :scorecard="data.forecast_scorecard"/>
   <V6RiskVisual v-if="data.snapshot" :current="data.snapshot" :history="data.history" :horizons="data.snapshot.model?.outlook?.horizons||[]" :categories="data.categories" :indicators="[...data.snapshot.local_indicators,...data.snapshot.indicators]" @select="load"/>
   <V6History v-if="data.comparison?.retrospective?.strategy_key==='v6-2'&&data.comparison.retrospective.points.length" :history="data.comparison.retrospective"/>
   <p v-else class="notice">V6-2 風險分數回溯尚無可用的保存資料，請在對話中要求更新 V6；原 V6 放空交易回測仍保留於下方。</p>
   <details v-if="data.snapshot" v-remember-disclosure="'v6.manual-review'" open class="forecast">
    <summary class="section-summary"><h2>本次人工研究</h2></summary>
    <div class="forecast-head"><div><span class="stage" :class="data.snapshot.stage">{{data.snapshot.stage}}</span><small>市場資料至 {{data.snapshot.market_as_of}} · 研究保存 {{data.snapshot.prepared_at.slice(0,16).replace('T',' ')}}</small></div><h2>{{formatRiskText(data.snapshot.summary)}}</h2><p>{{data.snapshot.probability_note}}</p></div>
    <div class="path-grid"><article><h3>風險形成／增強</h3><p v-for="item in data.snapshot.drivers" :key="item">{{formatRiskText(item)}}</p><p v-if="!data.snapshot.drivers.length">尚無已核對的增強因素。</p></article><article><h3>接下來觀察</h3><p v-for="item in data.snapshot.watch_next" :key="item">{{formatRiskText(item)}}</p><p v-if="!data.snapshot.watch_next.length">待下一次人工研究補充。</p></article><article><h3>減弱條件</h3><p v-for="item in data.snapshot.weakening" :key="item">{{formatRiskText(item)}}</p><p v-if="!data.snapshot.weakening.length">待下一次人工研究補充。</p></article></div>
   </details>
   <div v-else class="notice">尚未由對話建立第一份 V6 預報。下方列出監控範圍；沒有已核對資料時不顯示機率或新聞結論。</div>
   <details v-remember-disclosure="'v6.monitor'" class="monitor"><summary class="section-summary"><h2>風險監控</h2><p>各項資料有自己的公布頻率；空白表示這份預報尚未核對，不會拿舊資料當今天的新訊號。</p></summary><div class="category-grid"><article v-for="category in data.categories" :key="category.key"><div class="category-head"><h3>{{category.label}}</h3><span>{{categoryRows(category.key).length?categoryRows(category.key).length+' 項已核對':'待核對'}}</span></div><p class="description">{{category.description}} <a v-if="category.key==='darkpool'" href="https://squeezemetrics.com/monitor/dix" target="_blank" rel="noopener noreferrer">原始圖表 ↗</a></p><div v-for="item in categoryRows(category.key)" :key="item.label+item.as_of" class="indicator"><div><b>{{item.label}}</b><strong>{{formatObservation(item.value,item.unit,item.label)}}</strong></div><p>{{formatRiskText(item.reading)}}</p><small>{{item.as_of}} · <a :href="validUrl(item.url)" target="_blank" rel="noopener noreferrer">{{item.source}}</a></small></div></article></div></details>
   <details v-remember-disclosure="'v6.news'" open class="news"><summary class="section-summary"><h2>事件與新聞</h2><p>只保存查核過的來源、發生或發布時間，以及它對台股風險的可能傳導路徑。</p></summary><div v-if="data.snapshot?.news.length" class="news-grid"><article v-for="item in data.snapshot.news" :key="item.url"><span>{{item.direction}}</span><h3><a :href="validUrl(item.url)" target="_blank" rel="noopener noreferrer">{{item.title}}</a></h3><p>{{item.impact}}</p><small>{{item.source}} · {{item.published_at}}</small></article></div><p v-else class="empty">尚無本次人工查核的新聞；頁面不會自行抓取或替換。</p></details>
   <details v-if="primaryModel" v-remember-disclosure="'v6.simulation'" open class="model"><summary class="section-summary"><h2>{{isPrimaryCross?'V6-2 台指期純空模擬 · 主策略':'原 V6 台指期純空模擬 · 舊紀錄'}}</h2><p>{{isPrimaryCross?'以V6-2重新回放，保留8%跌深限制與2日冷卻。曲線與交易表為歷史模擬，非當時已發報或已成交。':'以下為該份原V6保存紀錄，並非V6-2績效。'}} 重新整理只讀已保存結果。</p></summary><V6Panel :model="primaryModel"/></details>
   <details v-if="isPrimaryCross&&data.snapshot?.model" v-remember-disclosure="'v6.legacy-simulation'" class="model"><summary class="section-summary"><h2>原V6歷史模擬 · 保留查閱</h2><p>原模型的舊資料獨立保存，不混入V6-2的交易與勝率。</p></summary><V6Panel :model="data.snapshot.model"/></details>
  </template>
 </div>
</template>

<style scoped>
.forecast-location{display:flex;align-items:center;justify-content:space-between;gap:15px;margin:16px 0;padding:12px 16px;background:#eff4eb;border-radius:9px;color:#6d8364;font-size:12px}.forecast-location button{border:1px solid #d5e1cd;border-radius:7px;background:white;color:#4d714a;padding:8px 12px;cursor:pointer;white-space:nowrap}

.weather-page{color:#2b3930}.hero{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:20px}.eyebrow{font-size:11px;letter-spacing:.14em;color:#9f6974;font-weight:800}.hero h1{font:700 34px Georgia,serif;margin:7px 0}.hero p{color:#71806f;line-height:1.7;max-width:780px;margin:0}.hero-state{background:#fff;border:1px solid #e1e7df;border-radius:12px;padding:13px 17px;white-space:nowrap}.hero-state small,.hero-state strong{display:block}.hero-state small{color:#859183;font-size:11px}.hero-state strong{margin-top:5px}.notice{background:#fff8eb;border:1px solid #eadfc9;border-radius:12px;padding:14px 18px;color:#816c43;margin:15px 0}.notice.error{background:#fff1ef;color:#a3433f}.history{display:flex;align-items:center;gap:10px;margin:16px 0}.history label{font-size:12px;color:#758474}.history select{border:1px solid #dce3d8;border-radius:9px;background:#fff;padding:9px 12px;max-width:100%}.forecast,.monitor,.news{background:#fff;border:1px solid #e1e7df;border-radius:16px;margin:18px 0;overflow:hidden}.forecast-head{padding:24px;background:#fbf7f8}.forecast-head>div{display:flex;align-items:center;gap:12px}.forecast-head small{color:#829080}.forecast-head h2{font-size:21px;line-height:1.5;margin:15px 0 8px}.forecast-head p{font-size:12px;color:#8a777b;margin:0}.stage{background:#ede7f2;color:#774f87;border-radius:99px;padding:6px 12px;font-weight:800}.stage.增強,.stage.高風險{background:#fee8df;color:#ad4b3c}.stage.減弱,.stage.平穩{background:#e5f3e8;color:#38805d}.path-grid{display:grid;grid-template-columns:repeat(3,1fr)}.path-grid article{padding:18px 22px;border-right:1px solid #edf0e9}.path-grid article:last-child{border-right:0}.path-grid h3,.category-grid h3{margin:0 0 11px;font-size:15px}.path-grid p{font-size:13px;color:#617160;line-height:1.65;margin:8px 0}.monitor>.section-summary,.news>.section-summary,.forecast>.section-summary,.model>.section-summary{padding:18px 22px;border-bottom:1px solid #edf0e9}.monitor h2,.news h2,.model h2{margin:0 0 5px}.monitor>.section-summary p,.news>.section-summary p,.model>.section-summary p{color:#7d897a;font-size:12px;margin:0}.category-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.category-grid>article{padding:18px 22px;border-bottom:1px solid #edf0e9}.category-grid>article:nth-child(odd){border-right:1px solid #edf0e9}.category-head,.indicator>div{display:flex;justify-content:space-between;gap:12px;align-items:center}.category-head h3{margin:0}.category-head span{color:#9a6e70;font-size:11px}.description{color:#849182;font-size:12px;margin:7px 0 12px}.indicator{border-top:1px solid #eff2ed;padding:10px 0}.indicator b{font-size:12px}.indicator strong{color:#444f42}.indicator p{color:#687567;font-size:12px;margin:6px 0}.indicator small,.news small{color:#929e90}.indicator a,.news a{color:#356e61;text-decoration:none}.news-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.news article{padding:18px 22px;border-bottom:1px solid #edf0e9}.news article span{color:#a75652;font-size:11px;font-weight:800}.news h3{font-size:15px;line-height:1.5;margin:5px 0}.news article p{font-size:12px;color:#667365;line-height:1.6}.empty{padding:20px 22px;color:#8a9687}.model{margin-top:24px}.forecast>.section-summary h2{font-size:20px;margin:0}.model{background:#fff;border:1px solid #e1e7df;border-radius:16px;overflow:hidden}.model>.section-summary p{margin-top:7px}@media(max-width:850px){.hero{align-items:start;flex-direction:column}.path-grid,.category-grid,.news-grid{grid-template-columns:1fr}.path-grid article,.category-grid>article:nth-child(odd){border-right:0}.path-grid article{border-bottom:1px solid #edf0e9}}
</style>
<style scoped>
@media(max-width:600px){.history{min-width:0}.history label{flex-shrink:0}.history select{min-width:0;flex:1;width:0}}
</style>
