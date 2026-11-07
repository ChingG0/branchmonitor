<script setup lang="ts">
import {onMounted,ref,computed} from 'vue'
import {localFetch,readJson} from './http'
import {vRememberDisclosure} from './rememberDisclosure'
import V6LiveAssessment from './V6LiveAssessment.vue'
import type {LiveContext} from './v6LiveTypes'
import type {ComparisonReport,V6ComparisonStrategy} from './v6ComparisonTypes'
const props=defineProps<{report?:ComparisonReport|null;standalone?:boolean;liveAssessment?:LiveContext|null;researchNotice?:string}>()
const loaded=ref<ComparisonReport|null>(null),error=ref('')
const data=computed(()=>props.standalone?loaded.value:props.report),snap=computed(()=>data.value?.snapshot)
const live=computed(()=>props.liveAssessment??snap.value?.live_assessment)
const fmt=(n:number|null|undefined,d=1)=>n==null?'—':n.toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d})
const sourceNames:Record<string,string>={foreign:'外資台指期部位',retail:'小台非三大法人部位',vix:'VIX',feargreed:'恐懼貪婪',twd:'美元／台幣',oil:'原油',spx:'標普500',financial:'金融ETF',electronics:'電子ETF',tsm:'台積電ADR','rates-proxy':'美債利率',sox:'費半',spy:'美股ETF',tlt:'長債ETF',gold:'黃金ETF',dollar:'美元指數'}
const sourceName=(key:string)=>sourceNames[key]||key
const waiting=(s:V6ComparisonStrategy)=>s.sources.filter(r=>!r.fresh&&r.state==='pending')
const missing=(s:V6ComparisonStrategy)=>s.missing.filter(k=>!waiting(s).some(r=>r.key===k))
const verified=(s:V6ComparisonStrategy)=>s.sources.filter(r=>r.availability_basis==='verified_fetch')
const signed=(n:number|null|undefined,d=1)=>n==null?'—':(n>0?'+':'')+fmt(n,d)
const expired=computed(()=>!!snap.value&&!!snap.value.forecast_dates.length&&snap.value.forecast_dates.at(-1)!<new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Taipei'}))
onMounted(async()=>{if(!props.standalone)return;try{const r=await localFetch('/api/v6-comparison');const json=await readJson(r);if(!r.ok)throw Error(json.error||'讀取失敗');loaded.value=json}catch(e){error.value=(e as Error).message}})
</script>
<template>
 <section class="dual-v6" aria-label="V6雙策略風險監控">
  <header><div><small>V6 · TWO RISK STRATEGIES</small><h2>雙策略風險監控</h2><p>風險分數、事件目標與是否開倉分開閱讀；漲跌觀察請看「3日模型預測」分頁的最新保存；未校準觀察版與策略分數分開閱讀。</p></div><span class="manual">對話手動更新</span></header>
  <p v-if="error" class="notice">{{error}}</p>
  <details v-if="live" v-remember-disclosure="'v6.strategy-source.archive.v1'"><summary>策略更新時保存的夜盤與海外來源（原值；最新觀察取價見「3日模型預測」）</summary><V6LiveAssessment :value="live"/></details><p v-if="props.researchNotice" class="notice">{{props.researchNotice}}</p>
  <p v-if="!snap" class="notice">這個保存時間尚無雙策略紀錄。頁面及市場資料更新不會自行產生預報。</p>
  <template v-else>
   <div class="basis"><b>策略資料 {{snap.market_as_of}} · 加權收盤 {{fmt(snap.anchor,2)}}</b><span>實際保存 {{snap.prepared_at.slice(0,16).replace('T',' ')}}</span></div>
   <div v-if="snap.market_view" class="market-view"><b>本次市場觀點 · 保存資料</b><p>{{snap.market_view.summary}}</p><p>{{snap.market_view.evidence.join('；')}}</p><small>{{snap.market_view.note}}</small></div>
   <p v-if="expired" class="notice">這份紀錄的觀察期間已結束，保留原值供核對；請在對話中要求更新 V6。</p>
   <div v-if="data?.audit?.status==='complete'" class="audit">第3日已完成核對：{{data.audit.target_date}} 收盤 {{fmt(data.audit.close,2)}}，累計 {{signed(data.audit.return_pct,2)}}%，實際為{{data.audit.actual_class==='up'?'上漲':data.audit.actual_class==='flat'?'平盤':'下跌'}}。</div>
   <p v-else-if="data?.audit?.status==='pending'" class="sample">待 {{data.audit.target_date}} 收盤後核對，保存值不改寫。</p>
   <p v-if="snap.trade_policy" class="policy-note">{{snap.trade_policy.note}}</p>
   <div class="strategy-grid"><article v-for="s in snap.strategies" :key="s.key" :data-strategy="s.key">
    <div class="strategy-heading"><div><span>{{s.key==='legacy'?'V6 · 原版':'V6-2 · 跨資產'}}</span><h3>{{s.label}}</h3></div><div class="score"><b>{{fmt(s.score,1)}}</b><small>分／100 · 觀察線65</small><em v-if="s.score_change!=null">較前日 {{signed(s.score_change)}} 分</em></div></div>
    <p class="policy-setting">交易冷卻 {{s.cooldown?.total_sessions??5}} 日 · 出場當日不重複開倉</p>
    <div class="action" :class="{alert:s.alert}">{{s.action}}<small>65分是策略門檻，不是65%下跌機率。</small></div>
    <div class="event-target"><small>這張策略卡的事件目標</small><h4>{{s.key==='legacy'?'最多3日規則空單扣成本後獲利':'3日內最低價回落至少2%'}}</h4>
     <p>{{s.key==='legacy'?'原V6尚無合格的事件機率校準，保留分數與模擬紀錄，不顯示獲利機率。':'V6-2分數不是已驗證的事件機率；不顯示無條件歷史百分比。'}}</p>
     <p>{{s.score_basis}}</p></div>
    <p v-if="s.cooldown?.remaining" class="cooldown-note">冷卻＝暫停新增空單，預警照常。最近出場 {{s.cooldown.last_exit}}；暫停 {{s.cooldown.blocked_dates.join('、')||'待核對日曆'}}。<span v-if="s.cooldown.earliest_review">最快 {{s.cooldown.earliest_review}} 重檢，並非必定進場。</span></p>
    <div class="performance"><span>歷史完成 <b>{{s.backtest.completed}}筆</b></span><span>勝率 <b>{{fmt(s.backtest.win_rate_pct)}}%</b></span><span>淨點數 <b>{{signed(s.backtest.net_points,0)}}</b></span></div>
    <p v-if="waiting(s).length" class="notice">等待來源達可用時間：{{waiting(s).map(r=>sourceName(r.key)).join('、')}}。</p>
    <p v-if="missing(s).length" class="notice">缺少或過期來源：{{missing(s).map(sourceName).join('、')}}；不以缺值當安全。</p>
    <details v-remember-disclosure="'v6.dual.'+s.key"><summary>策略依據與來源</summary><p>{{s.assessment}}</p><ul><li v-for="factor in s.factors" :key="factor">{{factor}}</li></ul><p>來源日：{{s.sources.map(r=>sourceName(r.key)+' '+(r.as_of||'待補')).join('／')}}</p><p v-if="verified(s).length">實際取得並核對：{{verified(s).map(r=>sourceName(r.key)+' '+r.available_at?.slice(0,16).replace('T',' ')).join('／')}}。</p><p v-if="s.key==='legacy'">原V6訓練有3筆交易日窗口錯位，尚未獲新分布校準資格；既有分數與模擬保存值仍保留。</p></details>
   </article></div>
   <footer><p>無條件歷史比例區塊已撤下；原始保存檔保留，後續不再計算該區塊。</p><p>{{snap.execution_note}}</p></footer>
  </template>
 </section>
</template>
<style scoped>
.original-three-day{margin:15px 24px}.original-three-day>summary{font-size:12px;cursor:pointer;color:#6d8467;font-weight:700}

.policy-setting{font-size:12px;color:#647c65;margin:10px 0}.policy-note{font-size:12px;line-height:1.8;color:#82754e;background:#faf6e9;padding:12px 15px;margin:0 26px 18px;border-radius:8px}

.dual-v6{margin:20px 0;background:#fff;border:1px solid #dfe7dc;border-radius:17px;overflow:hidden;color:#354d3c;min-width:0}.dual-v6>header{display:flex;justify-content:space-between;gap:16px;padding:24px 26px;border-bottom:1px solid #e8eee5}.dual-v6 header small{font-size:10px;letter-spacing:.13em;color:#8a9b80}.dual-v6 h2{font-size:23px;margin:7px 0 9px}.dual-v6 header p{font-size:13px;color:#7a8973;line-height:1.8;margin:0}.manual{align-self:start;white-space:nowrap;background:#eef3e9;border-radius:20px;font-size:11px;padding:8px 12px}.basis{padding:16px 26px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:13px}.basis span{font-size:11px;color:#82917c}.market-view{margin:0 26px 18px;padding:14px 17px;background:#f4f7f1;border-radius:10px;color:#62785d;font-size:12px;line-height:1.8}.market-view p{margin:7px 0}.market-view small{font-size:10px;color:#8e9b82}.strategy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border-top:1px solid #e7ece3}.strategy-grid>article{padding:22px;min-width:0}.strategy-grid>article+article{border-left:1px solid #e7ece3}.strategy-heading{display:flex;justify-content:space-between;gap:12px;align-items:center}.strategy-heading>div>span{font-size:11px;color:#96a18d}.strategy-heading h3{font-size:17px;margin:6px 0}.score{text-align:right;flex-shrink:0}.score b{font-size:32px;color:#4e6e54}.score small,.score em{display:block;font-size:10px;color:#87947e;font-style:normal;line-height:1.8}.action{padding:12px;background:#f2f6ef;border-radius:9px;margin:15px 0;font-size:13px;font-weight:700}.action.alert{background:#fff5e9;color:#9d7739}.action small{display:block;font-weight:400;font-size:10px;margin-top:6px;color:#928977}.event-target{border:1px solid #e4eadf;border-radius:10px;padding:14px;margin-bottom:17px}.event-target small{font-size:10px;color:#96a18d}.event-target h4{font-size:14px;margin:8px 0}.event-target strong{display:block;font-size:20px;color:#4b7967;margin:12px 0}.event-target p{font-size:11px;color:#84917b;line-height:1.8}.performance{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#89937f}.performance b{color:#49604a;font-size:13px}.notice,.audit{padding:12px 15px;border-radius:8px;background:#fff8e9;color:#947a4b;font-size:12px;line-height:1.8;margin:14px 24px}.strategy-grid .notice{margin:13px 0}.audit{background:#f1f7f3;color:#527758}.sample{font-size:11px;color:#8c9783;margin:12px 26px}.cooldown-note{padding:11px;background:#f7f8f1;font-size:11px;color:#819174;line-height:1.9;border-radius:8px}details{margin-top:15px;border-top:1px solid #edf0e7;padding-top:12px;color:#829077;font-size:11px;line-height:1.9}summary{cursor:pointer;font-size:12px;color:#6a8066;font-weight:700}.legacy-record{margin:16px 26px 22px}.legacy-record>summary{padding:10px;background:#f7f8f1;border-radius:8px}footer{background:#f8faf5;padding:14px 26px;color:#87917d;font-size:11px;line-height:1.8;border-top:1px solid #e7ece3}footer p{margin:5px 0}@media(max-width:1000px){.strategy-grid{grid-template-columns:1fr}.strategy-grid>article+article{border-left:0;border-top:1px solid #e7ece3}}@media(max-width:600px){.dual-v6>header{padding:18px;flex-direction:column}.basis{padding:15px 18px}.strategy-grid>article{padding:16px}.strategy-heading h3{font-size:15px}.market-view{margin:0 16px 15px}.legacy-record{margin:15px}.dual-v6 h2{font-size:21px}}
</style>
