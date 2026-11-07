<script setup lang="ts">
import {computed} from 'vue'
import {vRememberDisclosure} from './rememberDisclosure'
import type {DailyReport,DailyForecast,DailyRow,DailyTarget} from './v6DailyTypes'
const props=defineProps<{value?:DailyReport|null;current?:DailyForecast|null;archived?:boolean;liveContext?:boolean}>()
const binary=computed(()=>props.current?.version==='v6-daily-direction-2'||props.value?.method_version==='v6-daily-direction-baseline-2.0.0')
const fmt=(n:number|null|undefined,d=1)=>n==null?'—':n.toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d})
const sign=(n:number)=>`${n>0?'+':''}${fmt(n)}`
const keys=computed(()=>binary.value?['up','down']:['up','flat','down'])
const names:Record<string,string>={up:'上漲',flat:'平盤',down:'下跌'}
const outcomeName=(r:DailyRow)=>binary.value&&r.actual_return_pct===0?'收平（歸上漲側）':names[r.actual_class||'']
const probability=(r:DailyRow,k:string)=>r.classes.find(c=>c.key===k)?.probability_pct
const displayed=(t:DailyTarget)=>t.latest.status==='reference'?t.latest:t.latest.status==='complete'?t.last_reference:null
const stage=(r:DailyRow)=>r.status==='complete'?'到期核對':r.status==='unavailable'?'資料不足':r.stage==='initial'?'最初預報':r.stage==='calendar_revision'?'日曆修訂':`剩餘${r.remaining_sessions}日`
const sourceUrl=(u?:string)=>{try{const p=new URL(u||'');return ['https:','http:'].includes(p.protocol)?p.href:undefined}catch{return undefined}}
</script>
<template>
 <section class="daily-forecast" aria-label="逐日到期預報與同到期日修訂">
  <header><div><span class="eyebrow">SHARED DAILY DEADLINES</span><h3>{{archived?'舊版三分類紀錄':'逐日到期預報'}} · T+1／T+2／T+3</h3><p>每個到期日共用一份歷史分布，V6與V6-2的風險分數各自保留。</p></div><b>歷史基準</b></header>
  <p v-if="liveContext" class="limitation">下表為長期歷史基準比例，未納入上方夜盤與海外即時行情；不是本次行情的條件上漲／下跌機率。即時方向與波動請先看上方評估。</p><p v-if="binary" class="definition">上漲／下跌一律對原起點收盤：小漲歸上漲、小跌歸下跌；恰好收平併入上漲側（非下跌）。不是各日對前日的漲跌，也不能拼成三天的路徑機率。</p><p v-else class="definition">舊版保存口徑：漲／平／跌一律對原起點收盤：上漲超過0.5%、平盤±0.5%、下跌超過0.5%。不是各日對前日的漲跌，也不能拼成三天的路徑機率。</p>
  <p v-if="binary" class="limitation">按同一批已成熟歷史報酬重新計數，不是刪除平盤後把比例放大。只改分類，未新增方向訊號或證明更準；新舊版本成績分開。這些比例不作買賣訊號。</p><p v-else class="limitation">10月離線回放顯示初始方向判斷偏弱；晚期修訂已知道更多行情，誤差下降不代表提前預警能力提升。這些比例不作買賣訊號。</p>
  <p v-if="!archived&&current?.status!=='reference'" class="missing" role="status">{{current?.note||'等待下一次對話更新保存逐日期限預報；舊紀錄不回填。'}}</p>
  <p v-if="!value?.groups.length" class="empty">尚無逐日期限保存紀錄。功能不會由重整頁面、前台更新或排程產生預報。</p>
  <article v-for="g in value?.groups" :key="g.root_id" class="daily-group">
   <div class="group-heading"><div><h4>{{g.origin}} 起點 · {{fmt(g.anchor,2)}}點</h4><small>實際保存 {{g.issued_at.slice(0,16).replace('T',' ')}}</small></div><span :class="{early:!g.timing_qualified}">{{g.timing_qualified?'按08:30資訊截點保存':'提前參考・不列08:30前瞻成績'}}</span></div>
   <p v-if="binary" class="bounds">原起點漲跌分界 {{fmt(g.anchor,2)}}點，後續修訂保持不變；上漲側含恰好收平。</p><p v-else class="bounds">原起點平盤界線 {{fmt(g.flat_bounds?.[0],2)}}～{{fmt(g.flat_bounds?.[1],2)}}點，後續修訂保持不變。</p>
   <p class="mobile-hint">左右滑動表格，查看{{binary?'漲跌':'漲平跌'}}機率與區間 →</p><div class="table-scroll" tabindex="0" aria-label="各到期日最新比例，可左右捲動"><table class="deadline-table"><thead><tr><th>期限／到期日</th><th>最新資料／狀態</th><th v-for="k in keys" :key="k">{{names[k]}}<small>較前次有效預報</small></th><th>期末中位／中央80%</th></tr></thead><tbody>
    <tr v-for="t in g.targets" :key="t.horizon" :data-horizon="t.horizon" :class="{unavailable:t.latest.status==='unavailable'}">
     <td><b>T+{{t.horizon}}</b><strong>{{t.latest.target_date}}</strong><small v-if="t.latest.target_date!==t.initial.original_target_date">原定 {{t.initial.original_target_date}} · 日曆已更正</small></td>
     <td><b>{{t.latest.market_as_of}}</b><small>{{stage(t.latest)}}</small><small v-if="t.latest.status==='complete'">比例為到期前最後保存值</small></td>
     <td v-for="k in keys" :key="k" :class="k"><strong>{{displayed(t)?fmt(probability(displayed(t)!,k))+'%':'—'}}</strong><small v-if="displayed(t)?.change_previous_pp">{{sign(displayed(t)!.change_previous_pp![k])}}百分點</small><small v-else-if="displayed(t)">初次保存</small><small v-if="displayed(t)">最初{{fmt(probability(t.initial,k))}}%</small></td>
     <td><b>{{displayed(t)?fmt(displayed(t)!.median_level,0):'—'}}<span v-if="displayed(t)"> 點</span></b><small v-if="displayed(t)?.intervals?.find(b=>b.central_pct===80)">{{fmt(displayed(t)!.intervals!.find(b=>b.central_pct===80)!.low_level,0)}}～{{fmt(displayed(t)!.intervals!.find(b=>b.central_pct===80)!.high_level,0)}}</small></td>
    </tr>
   </tbody></table></div>
   <div v-for="t in g.targets" :key="'state'+t.horizon">
    <p v-if="t.latest.status==='unavailable'" class="missing">T+{{t.horizon}}：{{t.latest.note}}；保留原紀錄，本次不顯示新比例。</p>
    <p v-else-if="t.latest.status==='complete'" class="outcome"><b>T+{{t.horizon}} · {{t.latest.target_date}}實際{{outcomeName(t.latest)}} {{sign(t.latest.actual_return_pct||0)}}%</b><span> 收盤{{fmt(t.latest.actual_close,2)}}；{{t.latest.verified_on}}保存核對。最初機率RPS {{fmt(t.latest.original_rps,4)}}。</span></p>
   </div>
   <details v-remember-disclosure="'v6.daily.timeline.'+g.root_id"><summary>查看各期限的原值、每次修訂與來源日期</summary>
    <section v-for="t in g.targets" :key="t.horizon" class="target-history"><h5>T+{{t.horizon}} · 原定{{t.initial.original_target_date}}<span v-if="t.latest.target_date!==t.initial.original_target_date"> → {{t.latest.target_date}}</span></h5>
     <div class="table-scroll" tabindex="0" aria-label="同到期日的修訂紀錄，可左右捲動"><table><thead><tr><th>保存時間／資料日</th><th>階段</th><th v-for="k in keys" :key="k">{{names[k]}}</th><th>原到期結果</th></tr></thead><tbody><tr v-for="(r,i) in [t.initial,...t.updates]" :key="r.issued_at+'-'+i"><td>{{r.issued_at.slice(0,16).replace('T',' ')}}<small>{{r.market_as_of}}</small></td><td>{{stage(r)}}</td><template v-if="r.status==='reference'"><td v-for="k in keys" :key="k">{{fmt(probability(r,k))}}%<small v-if="r.change_previous_pp">{{sign(r.change_previous_pp[k])}}百分點</small></td></template><td v-else :colspan="keys.length">{{r.status==='complete'?'實際結果核對，不產生新機率':r.note}}</td><td>{{r.status==='complete'?outcomeName(r)+' '+sign(r.actual_return_pct||0)+'%':'待到期'}}</td></tr></tbody></table></div>
     <p v-if="t.latest.previous_issued_at">較前次有效預報：{{t.latest.previous_issued_at.slice(0,16).replace('T',' ')}}；資訊日{{t.latest.previous_market_as_of}}。</p>
     <p v-if="displayed(t)?.sample_n">成熟歷史{{displayed(t)!.sample_n?.toLocaleString()}}筆，起點{{displayed(t)!.sample_first}}～{{displayed(t)!.sample_last}}，結果已知至{{displayed(t)!.last_outcome}}。</p>
     <p v-for="band in displayed(t)?.intervals" :key="band.central_pct">歷史中央{{band.central_pct}}%：{{fmt(band.low_level,0)}}～{{fmt(band.high_level,0)}}點，並非保證涵蓋率。</p>
     <p v-for="(c,i) in t.latest.schedule_changes" :key="i">日曆更正：{{c.from_date}} → {{c.to_date}}；涉及{{c.calendar_changed_dates.join('、')}}。日曆取得{{c.calendar_saved_at||'未保存'}}、本次查核{{c.observed_at}}。<a v-if="sourceUrl(c.source)" :href="sourceUrl(c.source)" target="_blank" rel="noopener noreferrer">來源↗</a> {{c.note}}</p>
    </section>
    <p>{{g.timing_note}}<span v-if="g.alternative_saved_versions"> 同起點另保存{{g.alternative_saved_versions}}版；此處優先顯示首筆符合08:30資訊截點條件的版本，否則顯示首筆參考，不重複灌入筆數。</span></p>
   </details>
  </article>
  <details v-remember-disclosure="'v6.daily.scorecard'" class="scorecard"><summary>到期核對與真正保存紀錄</summary><p>已核對{{value?.summary.completed_origins||0}}個起點、{{value?.summary.completed_targets||0}}個期限；其中{{value?.summary.prospective_origins||0}}個起點符合原預報08:30資訊截點條件。</p><div v-for="s in value?.summary.by_horizon" :key="s.horizon"><p>T+{{s.horizon}}：核對{{s.completed}}筆，符合前瞻條件{{s.prospective_qualified}}筆；最初平均RPS {{fmt(s.original_mean_rps,4)}}。</p><p v-for="r in s.revision_scores" :key="r.remaining_sessions">剩餘{{r.remaining_sessions}}日修訂：{{r.n}}筆，平均RPS {{fmt(r.mean_rps,4)}}。</p></div><p v-if="binary">本版二分類RPS為下跌機率的平方誤差；與舊版三分類分開，不跨版本比較成績。</p><p>RPS越低越好，不是策略勝率。不同到期日有重疊，晚期修訂又知道更多行情，不能把組數當獨立試驗或誤差下降當新增方向能力。</p><p>僅對話要求更新時保存；未更新的日期不補造。原值、修訂與核對都不可覆寫。{{value?.method_version}}</p></details>
 </section>
</template>
<style scoped>
.mobile-hint{display:none}.daily-forecast{margin:20px 24px;padding:22px;border:1px solid #dce6de;border-radius:13px;color:#385345;min-width:0}.daily-forecast header,.group-heading{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;align-items:start}.eyebrow{font-size:10px;letter-spacing:.1em;color:#91a28b}h3{font-size:22px;margin:7px 0 9px}header p,.definition,.bounds{font-size:12px;line-height:1.9;color:#7c8f76}header>b,.group-heading>span{font-size:11px;padding:7px 11px;border-radius:20px;background:#edf4ef;white-space:nowrap}.definition{margin-top:18px}.limitation{padding:12px 15px;background:#faf6e9;color:#8c7848;border-radius:9px;font-size:12px;line-height:1.9}.daily-group{border-top:1px solid #e5ece1;padding-top:20px;margin-top:22px}h4{font-size:16px;margin:0 0 8px}.group-heading small{font-size:11px;color:#8d9c85}.group-heading .early{background:#f8f3e6;color:#9a834a}.table-scroll{overflow-x:auto;max-width:100%}table{width:100%;min-width:780px;border-collapse:collapse;font-size:12px;text-align:right}th,td{padding:13px 10px;border-bottom:1px solid #e8eee4;vertical-align:top}th{background:#f5f8f1;color:#7a8d73;font-weight:600}th:first-child,td:first-child{text-align:left}td strong,td b{display:block}td strong{font-size:19px;margin-bottom:5px}td:first-child strong{font-size:13px}td:first-child b{font-size:12px;margin-bottom:7px}td small,th small{display:block;font-size:10px;line-height:1.8;font-weight:400;color:#8c9c82}td.up strong{color:#bd7466}td.flat strong{color:#a18b42}td.down strong{color:#578f78}.unavailable{background:#fffcf5}.empty,.missing{font-size:12px;color:#9c8045;line-height:1.9;padding:12px 14px;background:#fff8e8;border-radius:8px}.outcome{padding:12px;background:#edf5f0;color:#557c63;font-size:12px;line-height:1.9;border-radius:8px}.outcome span{font-size:11px}details{font-size:11px;line-height:1.9;color:#85947b;margin-top:16px;border-top:1px solid #e8eee2;padding-top:12px}summary{cursor:pointer;font-size:12px;font-weight:700;color:#647f61}.target-history{margin-top:16px;padding-top:10px;border-top:1px solid #edf1e8}.target-history h5{font-size:13px;margin:0 0 12px;color:#5b775d}.target-history p{font-size:11px;line-height:1.9}.target-history td{font-size:11px}.target-history a{color:#557c63}.scorecard p{font-size:11px;line-height:1.9}@media(max-width:650px){.mobile-hint{display:block;font-size:11px;color:#74896f;margin:12px 0}.daily-forecast{margin:15px 12px;padding:16px}h3{font-size:19px}.group-heading>span{white-space:normal;font-size:10px}.daily-group{padding-top:16px}.definition,.limitation{font-size:11px}table{min-width:720px}}
</style>
