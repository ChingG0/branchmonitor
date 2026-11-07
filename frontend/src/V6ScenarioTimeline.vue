<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {vRememberDisclosure} from './rememberDisclosure'
import {probabilityHistoryPlot,probabilityAccuracy,probabilityOutcome} from './v6ProbabilityHistory'
import type {ScenarioHistory,ProbabilityDay} from './v6ProbabilityHistory'
const props=defineProps<{value?:ScenarioHistory}>()
const groups=computed(()=>props.value?.groups||[]),rootId=ref(''),versionId=ref('')
const group=computed(()=>groups.value.find(g=>g.rootId===rootId.value)||groups.value.at(-1))
const current=computed(()=>group.value?.snapshots.find(v=>v.id===versionId.value)||group.value?.snapshots.at(-1))
const original=computed(()=>group.value?.snapshots[0])
const previous=computed(()=>{const g=group.value,v=current.value;if(!g||!v)return;return g.snapshots[g.snapshots.findIndex(s=>s.id===v.id)-1]})
watch(groups,()=>{if(!groups.value.some(g=>g.rootId===rootId.value))rootId.value=groups.value.at(-1)?.rootId||''},{immediate:true})
watch(rootId,()=>{versionId.value=''})
const time=(v?:string)=>v?v.slice(0,19).replace('T',' '):'—'
const pct=(v?:number|null)=>v==null?'—':v.toFixed(1)+'%'
const points=(v?:number|null)=>v==null?'—':v.toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2})
const level=(v?:number|null)=>v==null?'—':v.toLocaleString('zh-TW',{maximumFractionDigits:0})
const change=(v?:number|null)=>v==null?'—':(v>0?'+':'')+level(v)
const pointTarget=(h:number)=>current.value?.pointEstimate?.targets.find(t=>t.horizon===h)
const pp=(v?:number|null)=>v==null?'首次保存':(v>0?'+':'')+v.toFixed(1)+' 個百分點'
function rowsFor(first=false):ProbabilityDay[]{
 const g=group.value,v=first?original.value:current.value;if(!g||!v)return []
 return v.targets.map(t=>({date:t.targetDate,horizon:t.horizon,status:'saved',savedAt:v.savedAt,up:t.up,down:t.down,upPct:t.upPct,downPct:t.downPct,
  deltaUpPp:null,deltaDownPp:null,targetDate:t.targetDate,anchorDate:g.anchorDate,anchorClose:g.anchorClose,note:'對話情境；非合格盤前預報',outcome:g.outcomes[String(t.horizon)]||null}))
}
const latestRows=computed(()=>rowsFor()),firstRows=computed(()=>rowsFor(true)),stats=computed(()=>probabilityAccuracy(firstRows.value))
const comparisons=computed(()=>latestRows.value.map((row,i)=>({row,check:probabilityOutcome(row),initial:original.value!.targets[i]!,
 estimate:pointTarget(row.horizon),delta:previous.value?100*(row.up!-previous.value.targets[i]!.up):null,originalCheck:probabilityOutcome(firstRows.value[i]!)})))
const chartRows=computed(()=>{
 const g=group.value;if(!g)return []
 const origin:ProbabilityDay={date:g.anchorDate,horizon:0,status:'missing',savedAt:null,up:null,down:null,upPct:null,downPct:null,deltaUpPp:null,deltaDownPp:null,note:'收盤基準日，並非假設50%起點'}
 return [origin,...latestRows.value]
})
const plot=computed(()=>probabilityHistoryPlot(chartRows.value,300,820))
</script>
<template>
 <section class="scenario-timeline" aria-label="對話情境走勢與實際收盤對照">
  <p v-if="!group" class="scenario-empty">尚無實際保存的對話情境；未產生的機率不補值。</p>
  <template v-else-if="current&&original">
   <div class="scenario-controls"><label>收盤基準<select v-model="rootId" aria-label="情境收盤起點"><option v-for="g in [...groups].reverse()" :key="g.rootId" :value="g.rootId">{{g.anchorDate}} · {{points(g.anchorClose)}} 點</option></select></label><label>查看保存版本<select v-model="versionId" aria-label="情境保存版本"><option value="">最新 · {{time(group.snapshots.at(-1)?.savedAt)}}</option><option v-for="v in group.snapshots" :key="v.id" :value="v.id">{{time(v.savedAt)}}{{v.id===original.id?' · 最初值':''}}</option></select></label></div>
   <div class="scenario-current"><h3>每日漲跌機率走勢 · 對話保存</h3><p class="scenario-saved">實際保存 {{time(current.savedAt)}} · 基準 {{group.anchorDate}} 加權收盤 {{points(group.anchorClose)}} 點</p><p>橫軸為到期日，三個點是這次保存對後三日的情境值；切換保存版本可比較變化。T日只標基準，不補50%起點。</p></div>
   <p class="scenario-warning">{{group.assumption}} 每次對話更新才追加紀錄；不會因重新整理自動修改。</p>
   <div class="scenario-legend"><span class="rise">上漲情境值</span><span class="fall">下跌情境值</span><small>尚未到期也先列出；實際結果不接成0%／100%預報。</small></div>
   <div class="scenario-chart-scroll" tabindex="0" role="region" aria-label="後三個交易日情境走勢，可左右捲動"><svg class="scenario-chart" :viewBox="`-42 0 ${plot.width+42} ${plot.height}`" role="img" :aria-label="`${group.anchorDate}起點與後三日情境走勢`">
    <g v-for="p in [0,.25,.5,.75,1]" :key="p" class="grid"><line :x1="plot.left" :x2="plot.right" :y1="plot.y(p)" :y2="plot.y(p)"/><text x="8" :y="plot.y(p)+4" text-anchor="end">{{p*100}}%</text></g>
    <line :x1="plot.x(0)" :x2="plot.x(0)" :y1="plot.top" :y2="plot.bottom" class="origin"/>
    <g v-for="(r,i) in chartRows" :key="r.date"><text :x="plot.x(i)" :y="plot.tickY" :text-anchor="i===0?'start':i===chartRows.length-1?'end':'middle'">{{r.date}}</text><text :x="plot.x(i)" :y="plot.tickY+17" :text-anchor="i===0?'start':i===chartRows.length-1?'end':'middle'">{{i===0?'T 收盤基準':'T+'+r.horizon+' 到期'}}</text></g>
    <g v-for="line in plot.lines" :key="line.key" :class="['series',line.key]"><path v-for="(path,i) in line.segments" :key="i" :d="path" fill="none"/><g v-for="p in line.points" :key="p.row.targetDate"><circle :cx="p.x" :cy="p.y" r="5"><title>{{time(current.savedAt)}} 保存 → {{p.row.targetDate}} 到期：{{line.key==='up'?'上漲':'下跌'}} {{pct(p.row[line.key==='up'?'upPct':'downPct'])}}</title></circle><text :x="p.x" :y="p.y+(line.key==='up'?-12:23)" text-anchor="middle">{{pct(p.row[line.key==='up'?'upPct':'downPct'])}}</text></g></g>
   </svg></div>
   <section class="scenario-comparison" aria-label="盤後情境與實際收盤對照"><h3>舊版試算原值與實際收盤核對</h3><p>舊版單點試算與幅度區間已撤離目前預報；這裡只供查帳，不代表目前有效預測。保存日期是發布日；實際收盤須等各到期日完成，再由對話更新核對。以下不是08:30前瞻成績。</p>
    <p v-if="current.pointEstimate" class="point-estimate-note">幅度研究試算補算於 {{time(current.pointEstimate.generatedAt)}}，沿用 {{time(current.savedAt)}} 保存的夜盤條件；不是當時已發佈的點數預報。中位數與區間由獨立幅度模型估算，不由上漲機率換算。</p>
    <div class="scenario-scorecards"><article><small>最初情境方向核對</small><b>{{stats.accuracy==null?'—':pct(stats.accuracy*100)}}</b><span>命中 {{stats.correct}}／{{stats.assessed}} 筆</span></article><article><small>已核對／待到期或待核對</small><b>{{stats.verified}}／{{stats.pending}}</b><span>未到期不算對或錯</span></article><article><small>最初情境 Brier</small><b>{{stats.brier==null?'—':stats.brier.toFixed(4)}}</b><span>僅情境核對，非正式準確率</span></article></div>
    <p v-if="current.pointEstimate" class="point-basis">累積計算基準：{{group.anchorDate}} 加權收盤 {{points(group.anchorClose)}} 點。每一期預估收盤＝此基準＋該期累積點數；T+2、T+3不從前一期預估收盤再加一次。</p>
    <div class="scenario-table-scroll" tabindex="0" role="region" aria-label="後三日最初值、修訂與實際收盤"><table><thead><tr><th>實際保存</th><th>期限／到期日</th><th>最初上漲／下跌</th><th>本次上漲／下跌</th><th>上漲較前次修訂</th><th>預估收盤（中位）</th><th>對起點累積預估漲跌點數</th><th>舊名目80%區間（盤中未驗證）</th><th>到期實際收盤</th><th>對原基準漲跌幅</th><th>最初方向核對</th></tr></thead><tbody><tr v-for="r in comparisons" :key="r.row.horizon"><td>{{time(current.savedAt)}}</td><td><b>T+{{r.row.horizon}}</b><span>{{r.row.targetDate}}</span></td><td><b class="rise">{{pct(r.initial.upPct)}}</b><span class="fall">{{pct(r.initial.downPct)}}</span></td><td><b class="rise">{{pct(r.row.upPct)}}</b><span class="fall">{{pct(r.row.downPct)}}</span></td><td>{{pp(r.delta)}}</td><td class="estimated-close">{{r.estimate?level(r.estimate.medianClose)+' 點':'尚未估計'}}</td><td class="estimated-change" :class="r.estimate?(r.estimate.medianChangePoints>=0?'rise':'fall'):''"><b>{{r.estimate?change(r.estimate.medianChangePoints)+' 點':'—'}}</b><span v-if="r.estimate">{{r.estimate.medianReturnPct>0?'+':''}}{{r.estimate.medianReturnPct.toFixed(2)}}%</span></td><td class="estimated-band"><template v-if="r.estimate">{{level(r.estimate.low80Close)}}～{{level(r.estimate.high80Close)}} 點<span>漲跌 {{change(r.estimate.low80ChangePoints)}}～{{change(r.estimate.high80ChangePoints)}} 點</span></template><template v-else>—</template></td><td>{{r.check.actualClose==null?'待到期／待核對':points(r.check.actualClose)+' 點'}}</td><td>{{r.check.actualReturnPct==null?'—':(r.check.actualReturnPct>0?'+':'')+r.check.actualReturnPct.toFixed(2)+'%'}}</td><td>{{r.originalCheck.status!=='verified'?'待到期／待核對':r.originalCheck.hit==null?'50/50無方向':r.originalCheck.hit?'命中':'未命中'}}</td></tr></tbody></table></div>
    <p v-if="current.pointEstimate">所有預估點數均相對 {{group.anchorDate}} 加權收盤 {{points(group.anchorClose)}} 點，T+2／T+3是累積漲跌。區間是期末收盤參考，並非期間最高／最低點；原模型假設及盤後未驗證限制仍適用。</p>
    <details v-if="current.pointEstimate" v-remember-disclosure="'v6.points.validation.v1'" class="point-estimate-validation"><summary>幅度模型的歷史誤差與區間涵蓋</summary><p>使用2017～2023年訓練、2023～2024年校準、2024～2026年作歷史時間外檢查；該測試期曾供其他研究使用，不是全新未接觸樣本。盤中夜盤情境尚未另行驗證。</p><p v-for="t in current.pointEstimate.targets" :key="t.horizon">T+{{t.horizon}}：平均絕對漲跌幅誤差 {{t.historicalMaePct.toFixed(2)}} 個百分點；名目80%區間實際涵蓋 {{t.historicalCoverage80Pct.toFixed(1)}}%。不是保證未來涵蓋率，也不是股價目標達成率。</p></details>
    <p>原基準與三個到期日保持不變；較前次修訂只比較本組相同到期日。其他收盤基準另列一組，不把不同基準的機率當作同一筆修訂。三個期限互相重疊，不是三次獨立試驗。</p>
   </section>
   <details v-remember-disclosure="'v6.scenario.timeline.v1'" class="scenario-versions"><summary>每次實際保存與變化 · {{group.snapshots.length}} 個版本（含最初值）</summary><div class="scenario-table-scroll"><table><thead><tr><th>保存時間</th><th>報價時間／夜盤</th><th>到期日</th><th>上漲</th><th>下跌</th></tr></thead><tbody><template v-for="v in group.snapshots" :key="v.id"><tr v-for="t in v.targets" :key="v.id+t.horizon"><td>{{time(v.savedAt)}}<span>{{v.id===original.id?'最初值':'追加版本'}}</span></td><td>{{time(v.quoteAt)}}<span>{{points(v.nightPrice)}} 點</span></td><td>T+{{t.horizon}} · {{t.targetDate}}</td><td class="rise">{{pct(t.upPct)}}</td><td class="fall">{{pct(t.downPct)}}</td></tr></template></tbody></table></div></details>
  </template>
 </section>
</template>
<style scoped>
.point-basis{font-weight:600;color:#5a795c!important}.point-estimate-note{padding:11px 14px;border-radius:8px;background:#fbf5e6;color:#9a8148!important}.point-estimate-validation{font-size:11px;line-height:1.8;color:#829475;margin:14px 0}.point-estimate-validation summary{cursor:pointer}.estimated-close{font-weight:600;color:#4f7158}.estimated-band span{font-size:10px;color:#89997d}

.scenario-timeline{margin-top:20px;color:#3e604c;min-width:0}.scenario-controls{display:flex;gap:14px 20px;flex-wrap:wrap;padding:14px;background:#f7faf3;border:1px solid #e0e8d9;border-radius:9px}.scenario-controls label{display:flex;align-items:center;gap:8px;font-size:11px;color:#7b8e70;min-width:0}.scenario-controls select{min-width:0;max-width:100%;border:1px solid #d9e3d0;border-radius:6px;padding:7px;background:white;color:#58704c;font:inherit}.scenario-current h3,.scenario-comparison h3{font-size:17px;margin:22px 0 9px}.scenario-current p,.scenario-comparison>p{font-size:11px;line-height:1.9;color:#839278}.scenario-current .scenario-saved{color:#52745a;font-weight:600}.scenario-warning{background:#fff7e6;color:#9b814b;padding:12px 15px;border-radius:8px;font-size:11px;line-height:1.9}.scenario-legend{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:11px;margin:18px 0}.scenario-legend small{font-size:10px;color:#91a087}.rise{color:#bf6a5b}.fall{color:#538b74}.scenario-chart-scroll{overflow-x:auto;max-width:100%;scrollbar-color:#9bb29c #eef3eb}.scenario-chart{display:block;width:100%;min-width:710px;max-height:380px}.scenario-chart text{font-size:12px;fill:#899a7e}.grid line{stroke:#e6ede0}.origin{stroke:#c9d5c1;stroke-dasharray:4 5}.series path{stroke-width:2}.series circle{stroke:#fff;stroke-width:2}.series.up path{stroke:#c57365}.series.up circle{fill:#c57365}.series.down path{stroke:#628f7b}.series.down circle{fill:#628f7b}.series.up text{fill:#b66b5b}.series.down text{fill:#538b74}.scenario-scorecards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}.scenario-scorecards article{background:#f5f8f0;border-radius:8px;padding:14px;min-width:0}.scenario-scorecards small,.scenario-scorecards span{font-size:11px;color:#899a7d;display:block}.scenario-scorecards b{display:block;font-size:25px;margin:9px 0;font-weight:550}.scenario-table-scroll{overflow:auto;max-width:100%;border:1px solid #e5ebdf;border-radius:8px}.scenario-table-scroll table{border-collapse:collapse;width:100%;font-size:11px;white-space:nowrap}.scenario-table-scroll th{text-align:left;font-size:10px;color:#8a9b7d;background:#f5f8f0}.scenario-table-scroll td,.scenario-table-scroll th{padding:12px;border-bottom:1px solid #e7edde}.scenario-table-scroll td span{display:block;margin-top:5px}.scenario-versions{font-size:11px;margin-top:18px;color:#829475}.scenario-versions summary{cursor:pointer;margin-bottom:12px}.scenario-empty{font-size:12px;color:#8b967f;padding:20px;background:#f8faf5;border-radius:9px}@media(max-width:650px){.scenario-controls{flex-direction:column}.scenario-controls label{flex-wrap:wrap}.scenario-controls select{width:100%}.scenario-scorecards{grid-template-columns:1fr}.scenario-scorecards article{padding:12px}.scenario-scorecards b{font-size:22px}.scenario-legend small{width:100%}}
</style>
