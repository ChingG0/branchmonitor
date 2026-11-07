<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {localFetch} from './http'
import V6ScenarioTimeline from './V6ScenarioTimeline.vue'
import V6NightConditions from './V6NightConditions.vue'
import V6ForecastAudit from './V6ForecastAudit.vue'
import {vRememberDisclosure} from './rememberDisclosure'
import {conditionalResearchUrl,conditionalResearchSha256} from './v6ConditionalResource'
import {validateResearch} from './v6ConditionalResearch'
import type {ResearchData} from './v6ConditionalResearch'
import {validateHistory,researchSeries,probabilityHistoryPlot,probabilityAccuracy,probabilityOutcome,historyPreferences,saveHistoryPreferences} from './v6ProbabilityHistory'
import type {History,ProbabilityDay} from './v6ProbabilityHistory'

const props=defineProps<{payload?:History;researchPayload?:ResearchData;initialMode?:'actual'|'research'|'scenario'}>()
const preferences=historyPreferences(),data=ref<History|null>(props.payload?validateHistory(props.payload):null),research=ref<ResearchData|null>(props.researchPayload||null)
const mode=ref<'actual'|'research'|'scenario'>(props.initialMode||preferences.mode||('actual'))
const horizon=ref([1,2,3].includes(preferences.horizon||0)?preferences.horizon!:1),range=ref([20,60,0].includes(preferences.range??20)?preferences.range??20:20)
const chartContainer=ref<HTMLElement|null>(null),chartScroll=ref<HTMLElement|null>(null),chartViewportWidth=ref(800)
let chartObserver:ResizeObserver|undefined
const selectedDate=ref(''),loading=ref(false),error=ref(''),researchError=ref(''),controller=new AbortController()
const allRows=computed(()=>mode.value==='research'?(research.value?researchSeries(research.value,horizon.value):[]):data.value?.series.find(s=>s.horizon===horizon.value)?.rows||[])
const rows=computed(()=>range.value?allRows.value.slice(-range.value):allRows.value)
const scenario=computed(()=>data.value?.upcoming?.scenario)
const scenarioOpen=ref(false),archiveOpen=ref(false)
function toggleScenario(e:Event){scenarioOpen.value=(e.target as HTMLDetailsElement).open}
function toggleArchive(e:Event){archiveOpen.value=(e.target as HTMLDetailsElement).open}
const saved=computed(()=>rows.value.filter(r=>r.status==='saved'))
const accuracy=computed(()=>probabilityAccuracy(rows.value))
const checkedRows=computed(()=>[...rows.value].reverse().map(row=>({row,check:probabilityOutcome(row)})))
const selected=computed(()=>rows.value.find(r=>r.date===selectedDate.value)||saved.value.at(-1)||rows.value.at(-1))
const selectedOutcome=computed(()=>selected.value?probabilityOutcome(selected.value):null)
const chartWidth=computed(()=>Math.max(chartViewportWidth.value-48,44+Math.max(0,rows.value.length-1)*28))
const plot=computed(()=>probabilityHistoryPlot(rows.value,320,chartWidth.value))
const scrollOffset=ref(0),scrollLimit=ref(0)
function syncChartScroll(){if(chartScroll.value){scrollOffset.value=chartScroll.value.scrollLeft;scrollLimit.value=Math.max(0,chartScroll.value.scrollWidth-chartScroll.value.clientWidth)}}
function scrollToLatest(){if(chartScroll.value){chartScroll.value.scrollLeft=chartScroll.value.scrollWidth;syncChartScroll()}}
function panChart(event:Event){if(chartScroll.value){chartScroll.value.scrollLeft=Number((event.target as HTMLInputElement).value);syncChartScroll()}}
watch([rows,chartWidth,chartViewportWidth],async()=>{await nextTick();scrollToLatest()},{flush:'post'})
const time=(value?:string|null)=>value?value.slice(0,19).replace('T',' '):'尚無紀錄'
const pct=(value?:number|null)=>value==null?'—':value.toFixed(1)+'%'
const points=(value?:number|null)=>value==null?'—':value.toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2})
const signedPct=(value?:number|null)=>value==null?'—':(value>0?'+':'')+value.toFixed(2)+'%'
const prediction=(c:ReturnType<typeof probabilityOutcome>)=>c.forecastDirection==='up'?'看漲':c.forecastDirection==='down'?'看跌':c.forecastDirection==='none'?'無方向':'—'
const verdict=(c:ReturnType<typeof probabilityOutcome>)=>c.status==='missing'?'無有效預報':c.status==='invalid'?'資料待核對':c.status==='pending'?'尚未核對':c.hit==null?'50/50不判方向':c.hit?'命中':'未命中'
const delta=(value?:number|null)=>value==null?'首筆有效預報':(value>0?'+':'')+value.toFixed(1)+' 個百分點'
const result=(r:ProbabilityDay)=>r.outcome?(r.outcome.up?'上漲（含收平）':'下跌'):'尚未保存到期核對'
function selectDay(date:string){selectedDate.value=date}
watch(rows,()=>{selectedDate.value=saved.value.at(-1)?.date||rows.value.at(-1)?.date||''},{immediate:true})
watch([mode,horizon,range],()=>saveHistoryPreferences({mode:mode.value,horizon:horizon.value,range:range.value}))
async function loadResearch(){
 if(research.value)return
 researchError.value='';loading.value=true
 try{
  const r=await localFetch(conditionalResearchUrl,{signal:controller.signal});if(!r.ok)throw Error('歷史研究資料讀取失敗。')
  const raw=await r.text()
  if(globalThis.crypto?.subtle){const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));const hex=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('');if(hex!==conditionalResearchSha256)throw Error('歷史研究封存版本不符。')}
  research.value=validateResearch(JSON.parse(raw))
 }catch(e){if((e as Error).name!=='AbortError')researchError.value=(e as Error).message}
 finally{loading.value=false}
}
async function loadActual(){
 if(props.payload)return
 error.value='';loading.value=true
 try{const r=await localFetch('/v6-research/daily-probability-history.json',{signal:controller.signal});if(!r.ok)throw Error('每日走勢尚未匯出；等待對話更新V6。');data.value=validateHistory(await r.json())}
 catch(e){if((e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{loading.value=false}
}
watch(mode,value=>{if(value==='research')loadResearch()})
onMounted(()=>{
 loadActual();if(mode.value==='research')loadResearch()
 if(chartContainer.value){chartObserver=new ResizeObserver(entries=>{chartViewportWidth.value=entries[0]!.contentRect.width});chartObserver.observe(chartContainer.value)}
})
onBeforeUnmount(()=>{controller.abort();chartObserver?.disconnect()})
</script>

<template>
 <section ref="chartContainer" class="probability-history" aria-label="每日漲跌機率走勢">
  <header><div><small>V6 · DAILY PROBABILITY HISTORY</small><h2>每日漲跌機率走勢</h2><p>主圖只列合格盤前紀錄；盤後假設另放研究區，不以歷史比例補機率。</p></div><span class="history-kind" :class="mode">{{mode==='scenario'?'對話保存 · 盤後情境':mode==='actual'?'實際保存 · 平行觀察':'歷史重建 · 非本次預報・不計入0/60'}}</span></header>
  <section v-if="data?.upcoming" class="upcoming-forecast" aria-label="最新收盤後三個交易日">
   <div class="upcoming-heading"><h3>接下來三個交易日</h3><p>T＝{{data.upcoming.anchorDate}} 加權指數收盤</p></div>
   <div class="upcoming-cards"><article v-for="target in data.upcoming.targets" :key="target.horizon" :data-horizon="target.horizon">
    <small>T+{{target.horizon}}</small><strong class="upcoming-date">{{target.targetDate||'交易日待確認'}}</strong>
    <div v-if="target.status==='saved'" class="upcoming-probabilities"><span class="rise">上漲 {{pct(target.upPct)}}</span><span class="fall">下跌 {{pct(target.downPct)}}</span><small>保存 {{time(target.probabilitySavedAt)}}</small></div>
    <p v-else class="upcoming-pending">{{target.status==='pending'?'機率待更新':'等待已公告交易日曆'}}<span>上漲 — ／ 下跌 —</span></p>
   </article></div>
   <p class="upcoming-note">{{data.upcoming.note}} 夜盤尚未收完時，不把盤中試算填入這三張卡片。</p>
  </section>
  <div class="history-controls"><div role="group" aria-label="走勢資料來源"><button :aria-pressed="mode==='actual'" @click="mode='actual'">實際保存</button><button :aria-pressed="mode==='research'" @click="mode='research'">歷史研究參考</button></div><div v-if="mode!=='scenario'" role="group" aria-label="每日走勢期限"><button v-for="h in [1,2,3]" :key="h" :aria-pressed="horizon===h" @click="horizon=h">T+{{h}}</button></div><label v-if="mode!=='scenario'">顯示<select v-model.number="range" aria-label="走勢顯示範圍"><option :value="20">最近20個日期</option><option :value="60">最近60個日期</option><option :value="0">全部紀錄</option></select></label></div>
  <p v-if="mode==='actual'" class="history-timestamps">最近市場研究：{{time(data?.lastMarketUpdate?.savedAt)}}<span>最近有效機率：{{time(data?.lastProbabilitySavedAt)}}</span></p>
  <p v-else-if="mode==='research'" class="history-notice research-notice">以下為 {{research?.calculatedOn||'2026-11-06'}} 重建的歷史機率，並非當年實際發佈，也不會增加前瞻觀察筆數。</p>
  <p v-if="loading" class="history-message" role="status">正在讀取已保存紀錄…</p>
  <p v-else-if="mode!=='research'&&error" class="history-notice">{{error}}</p>
  <p v-else-if="mode==='research'&&researchError" class="history-notice">{{researchError}}</p>
  <template v-else>
   <p v-if="mode==='scenario'" class="history-notice">盤後試算已移入下方收折研究區，不作為目前漲跌機率。</p>
   <template v-else>
   <p v-if="mode==='actual'&&data?.withheld" class="history-notice">{{data.note}}。本次沒有新機率；圖中若有舊點，僅代表該日期當時的預報。</p>
   <div v-if="saved.length" class="daily-chart">
    <p class="chart-date-definition">下圖橫軸是「當時盤前預報日期」，不是到期日；最新三個到期日請看上方。</p>
    <div class="daily-legend"><span class="rise">上漲機率</span><span class="fall">下跌機率</span><small>缺項日斷線，不補0或沿用前值。</small></div>
    <div class="daily-scroll-tools"><small>左右捲動查看日期</small><button @click="scrollToLatest">回到最新</button></div>
    <div class="daily-chart-frame">
    <svg class="daily-y-axis" viewBox="0 0 48 320" aria-hidden="true"><text v-for="p in [0,.25,.5,.75,1]" :key="p" x="43" :y="plot.y(p)+5" text-anchor="end">{{p*100}}%</text></svg>
    <div ref="chartScroll" class="daily-chart-scroll" @scroll="syncChartScroll" tabindex="0" role="region" aria-label="左右捲動每日機率日期">
    <svg class="daily-series-chart" :style="{width:plot.width+'px',height:plot.height+'px'}" :viewBox="`0 0 ${plot.width} ${plot.height}`" role="img" :aria-label="`${mode==='actual'?'實際保存':'歷史研究'}T+${horizon}每日漲跌機率階梯走勢`">
     <g v-for="p in [0,.25,.5,.75,1]" :key="p" class="daily-grid"><line :x1="plot.left" :x2="plot.right" :y1="plot.y(p)" :y2="plot.y(p)" :class="{mid:p===.5}"/></g>
     <g v-for="i in plot.ticks" :key="i" class="daily-date"><text :x="plot.x(i)" :y="plot.tickY" :text-anchor="i===0?'start':i===rows.length-1?'end':'middle'">{{rows[i]!.date}}</text></g>
     <g v-for="line in plot.lines" :key="line.key" :class="['daily-series',line.key]"><path v-for="(path,i) in line.segments" :key="i" :d="path" fill="none"/><circle v-for="p in line.points" :key="p.row.date" :cx="p.x" :cy="p.y" :r="p.row.date===selected?.date?6:3.5" role="button" tabindex="0" :aria-label="`${p.row.date} ${line.key==='up'?'上漲':'下跌'} ${pct(p.row[line.key==='up'?'upPct':'downPct'])}`" @click="selectDay(p.row.date)" @keydown.enter="selectDay(p.row.date)"><title>{{p.row.date}} · {{line.key==='up'?'上漲':'下跌'}} {{pct(p.row[line.key==='up'?'upPct':'downPct'])}} · 到期 {{p.row.targetDate}}</title></circle></g>
     <g v-for="r in rows.filter((r)=>r.status==='missing')" :key="r.date" class="missing-day"><title>{{r.date}}：{{r.note}}</title><text :x="plot.x(rows.indexOf(r))" :y="plot.missingY" text-anchor="middle">·</text></g>
    </svg>
    </div>
    </div>
    <input v-if="scrollLimit>0" class="daily-date-scrollbar" type="range" min="0" :max="scrollLimit" step="1" :value="scrollOffset" aria-label="日期捲軸" @input="panChart">
   </div>
   <div v-else class="history-empty"><b>{{mode==='actual'?'尚未累積實際保存的機率':'此範圍沒有已封存研究機率'}}</b><p>有合格來源與預報時才會新增點。沒有更新的日期留空，歷史研究不會自動補進實際紀錄。</p><button v-if="mode==='actual'" @click="mode='research'">查看歷史研究參考</button></div>
   <article v-if="selected" class="selected-day" :class="selected.status"><div><b>預報日 {{selected.date}} · T+{{horizon}}</b><span v-if="selected.status==='saved'">到期 {{selected.targetDate}}<small v-if="selected.targetDate!==selected.originalTargetDate">（原定 {{selected.originalTargetDate}}）</small></span><span v-else>沒有合格機率</span></div><template v-if="selected.status==='saved'"><div class="selected-probabilities"><strong class="rise">上漲 {{pct(selected.upPct)}}</strong><strong class="fall">下跌 {{pct(selected.downPct)}}</strong></div><p>上漲較前筆有效預報：{{delta(selected.deltaUpPp)}}<span v-if="selected.previousValidDate">（{{selected.previousValidDate}}）</span></p><p>{{mode==='actual'?'實際保存：'+time(selected.savedAt):'歷史假設資訊截點：'+time(selected.informationCutoff)}}<br>相對 {{selected.anchorDate}} 現貨收盤 · {{result(selected)}}<span v-if="selected.outcome">，核對資訊可取得 {{time(selected.outcome.availableAt)}}</span></p><p v-if="selectedOutcome?.status==='verified'" class="selected-close">到期實際收盤 <b>{{points(selectedOutcome.actualClose)}} 點</b> · 相對起點前收 <b :class="selectedOutcome.actualUp?'rise':'fall'">{{signedPct(selectedOutcome.actualReturnPct)}}</b> · {{verdict(selectedOutcome)}}</p></template><p v-else>{{selected.note}}</p></article>
   <section class="accuracy-panel" aria-label="預測與實際收盤對照">
    <header><div><h3>預測與實際收盤對照</h3><p>{{mode==='research'?'歷史研究重建':'實際保存紀錄'}} · T+{{horizon}} · {{rows.length?rows[0]!.date+'～'+rows.at(-1)!.date:'尚無紀錄'}}</p></div></header>
    <div class="accuracy-cards">
     <article><small>方向命中率</small><strong class="direction-accuracy">{{accuracy.accuracy==null?'—':pct(accuracy.accuracy*100)}}</strong><p>命中 {{accuracy.correct}}／{{accuracy.assessed}} 筆有方向且已核對預報</p></article>
     <article><small>已核對／尚未核對</small><strong>{{accuracy.verified}} <span>／ {{accuracy.pending}} 筆</span></strong><p>缺預報 {{accuracy.missing}} 筆<span v-if="accuracy.neutral"> · 50/50無方向 {{accuracy.neutral}} 筆</span><span v-if="accuracy.invalid"> · 資料待核對 {{accuracy.invalid}} 筆</span></p></article>
     <article><small>機率誤差 Brier · 越低越好</small><strong class="probability-error">{{accuracy.brier==null?'—':accuracy.brier.toFixed(4)}}</strong><p>使用未四捨五入機率，核對全部有效到期結果</p></article>
    </div>
    <p class="accuracy-note">統計跟隨上方期限與日期範圍；左右捲圖不改變樣本。{{mode==='research'?'這是歷史研究成績，不能當作實際前瞻準確率。':'只用對話更新時已保存的到期核對結果；未核對不算錯，也不算對。'}}</p>
    <div class="accuracy-table-scroll" tabindex="0" role="region" aria-label="實際收盤與方向命中明細"><table><thead><tr><th>盤前預報日</th><th>到期日</th><th>預測方向／機率</th><th>比較基準收盤</th><th>到期實際收盤</th><th>實際漲跌幅</th><th>方向核對</th></tr></thead><tbody>
     <tr v-for="{row:r,check:c} in checkedRows" :key="r.date" :class="{selected:r.date===selected?.date}"><td><button @click="selectDay(r.date)">{{r.date}}</button></td><td>{{r.targetDate||'—'}}</td><td>{{prediction(c)}} <span v-if="c.confidence!=null">{{pct(c.confidence)}}<small v-if="r.upPct===50&&r.up!==.5">（原始上漲 {{(r.up!*100).toFixed(4)}}% · 接近無方向）</small></span></td><td>{{points(r.anchorClose)}}</td><td>{{points(c.actualClose)}}</td><td :class="c.actualUp==null?'':c.actualUp?'rise':'fall'">{{signedPct(c.actualReturnPct)}}</td><td><span class="check-verdict" :class="c.status==='verified'&&c.hit!=null?(c.hit?'hit':'miss'):'waiting'">{{verdict(c)}}</span></td></tr>
     <tr v-if="!checkedRows.length"><td colspan="7">尚無已保存預報，方向命中率與機率誤差不補0。</td></tr>
    </tbody></table></div>
    <details v-remember-disclosure="'v6.daily-probability.accuracy-method.v1'" class="accuracy-method"><summary>對照方式與計算口徑</summary><p>預測方向取原始機率較高的一方；恰好50/50不列方向命中率，但仍列機率誤差。顯示四捨五入為50.0%但原值不等於50%時，另標示原值，避免誤認完全無方向。實際收盤為現貨加權指數，和該筆起點前一交易日的錨點收盤相比；沿用模型定義，收平併入上漲。</p><p>T+2、T+3是從該起點前收盤到到期日的累積漲跌，不是到期當天的單日報酬。方向命中率＝命中筆數÷已核對且有方向筆數；Brier＝平均（上漲機率−實際結果）²，實際上漲為1、下跌為0。這裡不提供預測收盤價，也不以命中率代表投資報酬。</p></details>
   </section>
   <p class="daily-definition">每個點是當時盤前保存或重建的新預報，T以該筆錨點收盤為準；T+{{horizon}}到期日會逐日改變，較前筆差值不是同一到期日的修訂。兩條線合計100%，也不是股價漲跌幅。</p>
   <details v-remember-disclosure="'v6.daily-probability.table.v1'" class="daily-table"><summary>逐日數字、來源時間與到期核對 · {{saved.length}}筆有效／{{rows.length}}個日期</summary><div class="daily-table-scroll" tabindex="0"><table><thead><tr><th>盤前預報日</th><th>{{mode==='actual'?'實際保存':'假設截點'}}</th><th>到期日</th><th>上漲</th><th>下跌</th><th>上漲較前筆有效</th><th>核對結果／缺項</th></tr></thead><tbody><tr v-for="r in [...rows].reverse()" :key="r.date" :class="{selected:r.date===selected?.date}" @click="selectDay(r.date)"><td><button @click.stop="selectDay(r.date)">{{r.date}}</button></td><td>{{time(mode==='actual'?r.savedAt:r.informationCutoff)}}</td><td>{{r.targetDate||'—'}}</td><td class="rise">{{pct(r.upPct)}}</td><td class="fall">{{pct(r.downPct)}}</td><td>{{r.status==='saved'?delta(r.deltaUpPp):'—'}}</td><td>{{r.status==='saved'?result(r):r.note}}</td></tr><tr v-if="!rows.length"><td colspan="7">尚無已保存紀錄。</td></tr></tbody></table></div></details>
   </template>
  </template>
  <V6ForecastAudit/>
  <details v-if="scenario||data?.scenarioHistory?.groups.length" v-remember-disclosure="'v6.night.conditions.research.v1'" class="night-research" @toggle="toggleScenario"><summary>夜盤條件情境研究 · 尚未驗證盤中準確率</summary>
   <template v-if="scenarioOpen"><V6NightConditions/>
    <details v-remember-disclosure="'v6.night.retired.archive.v1'" @toggle="toggleArchive"><summary>舊版單點試算、幅度區間與原值核對（已撤離目前預報）</summary><V6ScenarioTimeline v-if="archiveOpen" :value="data?.scenarioHistory"/></details>
   </template>
  </details>
  <p class="history-footnote">只在你於對話要求更新V6時累積。盤前模型採08:30固定資訊截點；盤後情境另存並標出假設，不修改08:30合格機率或正式準確率。重新整理只讀取紀錄。</p>
 </section>
</template>

<style scoped>
.night-research{margin-top:18px;border-top:1px solid #e0e7dc;padding-top:16px;color:#788475;font-size:12px}.night-research summary{cursor:pointer;font-weight:600}.night-research details{margin-top:18px}.night-research small{font-size:10px}

.history-kind.scenario{background:#fff5df;color:#947c3f}
.scenario-notice{padding:12px 14px;background:#fff5df;border:1px solid #eadcb5;border-radius:8px;color:#8f743d;font-size:12px;line-height:1.8;margin:13px 0}.scenario-notice b,.scenario-notice span{display:block}.scenario-values>b{font-size:10px;color:#9a824b;width:100%;font-weight:500}.scenario-values span{font-weight:600;font-size:16px}.scenario-basis{margin-top:12px;font-size:11px;line-height:1.9;color:#819179}.scenario-basis summary{cursor:pointer;color:#7f8f70}.scenario-basis p{margin:8px 0}

.upcoming-forecast{margin:18px 0 22px;padding:17px;border:1px solid #dce6dc;background:#f8fbf6;border-radius:11px;min-width:0}.upcoming-heading{display:flex;align-items:baseline;justify-content:space-between;gap:8px 18px;flex-wrap:wrap}.upcoming-heading h3{font-size:16px;margin:0}.upcoming-heading p,.upcoming-note,.chart-date-definition{font-size:11px;line-height:1.8;color:#7f9077}.upcoming-heading p{margin:0}.upcoming-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.upcoming-cards article{background:#fff;border:1px solid #e1e8dc;border-radius:9px;padding:15px;min-width:0}.upcoming-cards article>small{font-size:12px;color:#73886c}.upcoming-date{display:block;font-size:20px;margin:8px 0 13px;color:#3e674e}.upcoming-pending{font-size:12px;color:#9a834d;margin:0}.upcoming-pending span{display:block;font-size:11px;color:#8d9a84;margin-top:7px}.upcoming-probabilities{display:flex;gap:8px 16px;flex-wrap:wrap;font-size:13px}.upcoming-probabilities small{width:100%;font-size:10px;color:#8d9a84}.upcoming-note{margin:13px 0 0}.chart-date-definition{margin:0 0 12px}@container(max-width:650px){.upcoming-forecast{padding:13px}.upcoming-cards{gap:7px}.upcoming-cards article{padding:11px 8px}.upcoming-date{font-size:15px}.upcoming-pending{font-size:11px}}@container(max-width:370px){.upcoming-cards{grid-template-columns:1fr}.upcoming-date{font-size:18px}}

.probability-history{container-type:inline-size;background:#fff;border:1px solid #dce6dc;border-radius:15px;margin:20px 0;padding:22px;min-width:0;max-width:100%;color:#395746}.probability-history header{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;align-items:center}.probability-history header>div{min-width:0}.probability-history h2{font-size:21px;margin:7px 0}.probability-history header small{font-size:10px;letter-spacing:.1em;color:#8b997f}.probability-history header p,.history-footnote,.daily-definition{font-size:12px;line-height:1.8;color:#83927c}.history-kind{font-size:11px;padding:8px 11px;border-radius:20px;background:#edf4ed;color:#5b7e65}.history-kind.research{background:#fff5df;color:#9c7d31}.history-controls{display:flex;gap:12px 20px;flex-wrap:wrap;align-items:center;margin-top:15px}.history-controls>div{display:flex;gap:5px;flex-wrap:wrap}.history-controls button,.history-empty button{border:1px solid #dce5d6;border-radius:7px;padding:8px 12px;font-size:12px}.history-controls button[aria-pressed=true]{background:#4e745e;color:#fff;border-color:#4e745e}.history-controls label{font-size:11px;color:#87967d;display:flex;align-items:center;gap:8px}.history-controls select{min-width:0;max-width:100%;border:1px solid #dce4d6;background:#fff;padding:6px 9px;border-radius:7px;font:inherit;color:#597452}.history-timestamps{font-size:11px;color:#87937e;line-height:1.9;margin:13px 0}.history-timestamps span{margin-left:25px}.history-notice{background:#faf5e6;color:#94782d;border-radius:8px;padding:11px 14px;font-size:12px;line-height:1.8;margin:14px 0}.history-message{font-size:12px;color:#87977e;padding:14px}.daily-chart{min-width:0;margin-top:22px}.daily-legend{display:flex;gap:18px;align-items:center;flex-wrap:wrap;font-size:12px}.daily-legend>span:before{content:'';display:inline-block;width:19px;vertical-align:middle;border-top:3px solid;margin-right:6px}.daily-legend small{color:#929d86;font-size:11px}.rise{color:#bf6a5b}.fall{color:#538b74}.daily-chart-frame{display:flex;min-width:0;max-width:100%;margin-top:5px}.daily-y-axis{width:48px;height:320px;flex:0 0 48px;display:block}.daily-chart-scroll{min-width:0;flex:1;overflow-x:scroll;overflow-y:hidden;max-width:100%;padding-bottom:8px;scrollbar-color:#9bb29c #eef3eb;scrollbar-width:auto;overscroll-behavior-x:contain}.daily-chart-scroll::-webkit-scrollbar{height:11px}.daily-chart-scroll::-webkit-scrollbar-track{background:#eef3eb;border-radius:8px}.daily-chart-scroll::-webkit-scrollbar-thumb{background:#9bb29c;border-radius:8px}.daily-series-chart{display:block;max-width:none;flex:none}.daily-scroll-tools{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-top:10px}.daily-scroll-tools small{font-size:11px;color:#87977e}.daily-scroll-tools button{border:1px solid #dce5d6;border-radius:7px;padding:5px 9px;font-size:11px}.daily-date-scrollbar{display:block;appearance:none;-webkit-appearance:none;width:calc(100% - 48px);margin:10px 0 22px 48px;height:9px;min-height:9px;border:0;border-radius:9px;background:#e7eee4;cursor:ew-resize;padding:0;accent-color:#7e9d82}.daily-date-scrollbar::-webkit-slider-thumb{appearance:none;-webkit-appearance:none;width:46px;height:11px;border-radius:8px;background:#91aa92;cursor:grab}.daily-date-scrollbar::-moz-range-thumb{width:46px;height:11px;border:0;border-radius:8px;background:#91aa92;cursor:grab}.daily-date-scrollbar:focus-visible{outline:2px solid #64947e;outline-offset:4px}.daily-chart-scroll:focus-visible{outline:2px solid #64947e;outline-offset:2px}.daily-grid line{stroke:#e8eee4;stroke-width:1}.daily-grid .mid{stroke:#b8cdb9;stroke-dasharray:5 5}.daily-chart text{font-size:13px;fill:#8d9d83;font-family:inherit}.daily-series path{stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.daily-series circle{cursor:pointer;stroke:#fff;stroke-width:1.5}.daily-series.up path{stroke:#c57365}.daily-series.up circle{fill:#c57365}.daily-series.down path{stroke:#628f7b}.daily-series.down circle{fill:#628f7b}.missing-day text{fill:#b9bdae;font-size:19px}.history-empty{background:#f8faf5;border:1px dashed #dce5d4;border-radius:10px;padding:24px 20px;margin-top:20px;color:#78916e}.history-empty b{font-size:15px}.history-empty p{font-size:12px;line-height:1.9;margin:10px 0 15px}.selected-day{padding:16px 18px;border:1px solid #dfe8dc;border-radius:10px;background:#f8fbf6;margin-bottom:15px}.selected-day>div:first-child{display:flex;gap:10px 20px;align-items:center;flex-wrap:wrap}.selected-day b{font-size:14px}.selected-day span{font-size:11px;color:#8c9984}.selected-probabilities{display:flex;gap:25px;margin:14px 0 10px;flex-wrap:wrap}.selected-probabilities strong{font-size:22px;font-weight:550}.selected-day p{font-size:11px;line-height:1.9;color:#87957d;margin:6px 0}.selected-day.missing{background:#fffaf0}.daily-table{border-top:1px solid #e6ecdf;min-width:0}.daily-table summary{font-size:12px;color:#7f9277;padding:14px 0;cursor:pointer}.daily-table-scroll{overflow-x:auto;max-width:100%;min-width:0}.daily-table table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:11px}.daily-table th,.daily-table td{padding:11px 12px;border-bottom:1px solid #e7eddf}.daily-table th{font-size:10px;color:#8d9c84;text-align:left;background:#f7faf4}.daily-table button{font-size:11px;padding:0}.daily-table .selected{background:#eff5ea}.daily-table td:last-child{white-space:normal;min-width:160px}.history-footnote{font-size:11px;margin:16px 0 0}.probability-history button:focus-visible,.probability-history circle:focus-visible{outline:2px solid #64947e;outline-offset:3px}@container(max-width:650px){.history-timestamps span{display:block;margin-left:0}.history-controls{gap:10px}.probability-history header{flex-direction:column;align-items:start}.daily-legend small{width:100%}.selected-probabilities{gap:18px}.selected-probabilities strong{font-size:20px}}@container(max-width:430px){.history-controls label{width:100%}.selected-day{padding:13px}.history-empty{padding:18px 15px}.history-kind{font-size:10px}.probability-history h2{font-size:19px}}
.accuracy-panel{min-width:0;margin:20px 0;border:1px solid #dfe8da;border-radius:11px;padding:17px;background:#fff}.accuracy-panel h3{font-size:16px;margin:0 0 7px}.accuracy-panel header p{font-size:11px;margin-bottom:12px;line-height:1.7;color:#83927c}.accuracy-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.accuracy-cards article{min-width:0;padding:13px;border-radius:8px;background:#f5f8f1}.accuracy-cards small{font-size:11px;color:#85957c}.accuracy-cards strong{display:block;font-size:25px;font-weight:550;margin:9px 0;color:#4e735b}.accuracy-cards strong span{font-size:14px;color:#8d9c83}.accuracy-cards p,.accuracy-note,.accuracy-method p{font-size:11px;line-height:1.9;color:#85947c;margin:6px 0}.accuracy-note{margin:13px 0}.accuracy-table-scroll{max-height:330px;overflow:auto;min-width:0;border:1px solid #e6ecdf;border-radius:7px}.accuracy-table-scroll table{border-collapse:separate;border-spacing:0;width:100%;white-space:nowrap;font-size:11px}.accuracy-table-scroll th{position:sticky;top:0;z-index:1;background:#f6f9f3;text-align:left;font-size:10px;color:#85967a}.accuracy-table-scroll td,.accuracy-table-scroll th{padding:11px 12px;border-bottom:1px solid #e7eddf}.accuracy-table-scroll tr.selected td{background:#eef5e9}.accuracy-table-scroll button{padding:0;font:inherit;text-decoration:underline;text-underline-offset:3px}.check-verdict{display:inline-block;font-size:10px;padding:3px 7px;border-radius:12px}.check-verdict.hit{color:#47785a;background:#eaf3e9}.check-verdict.miss{color:#aa675c;background:#fbefea}.check-verdict.waiting{color:#959c8b;background:#f1f3ed}.accuracy-method summary{padding-top:13px;font-size:11px;cursor:pointer;color:#7b9172}.selected-close b{font-size:12px;font-weight:550}@container(max-width:650px){.accuracy-cards{grid-template-columns:1fr}.accuracy-cards article{padding:12px}.accuracy-cards strong{font-size:23px}.accuracy-panel{padding:13px}}
</style>
