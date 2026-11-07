<script setup lang="ts">
import {computed,onMounted,provide,ref} from 'vue'
import {localFetch,readJson} from './http'
import Icon from './Icon.vue'
import ModelCurve from './ModelCurve.vue'
import HoldingDurationStats from './HoldingDurationStats.vue'
import StockHistory from './StockHistory.vue'
import type {HoldingDurationStats as HoldingDurationSummary} from './holdingDurationTypes'
import V1Panel from './V1Panel.vue'
import V10Panel from './V10Panel.vue'
import V11Panel from './V11Panel.vue'
import type {V11Model} from './v11Types'
import V2Panel from './V2Panel.vue'
import V3Panel from './V3Panel.vue'
import V4Panel from './V4Panel.vue'
import type {V3Model} from './v3Types'
import V8Panel from './V8Panel.vue'
import type {V8Model,V1Model,V4Model,V10Model} from './v8Types'
import V9Panel from './V9Panel.vue'
import type {V9Model} from './v9Types'

type Horizon={status:string,exit_date?:string|null,net_return_pct?:number|null,hit?:boolean|null}
type HorizonKey='1'|'5'|'10'|'20'|'40'
type StrategyHistory={wins:number,losses:number,samples:number,win_rate:number|null,last_result:'win'|'loss'|null,last_return_pct:number|null,last_exit_date:string|null}
type BranchProof={name:string,broker?:string,branch?:string,net:number,direction:'buy'|'sell',win_rate:number,quality_score:number,effective_samples:number,cycle_win_rate:number|null,cycle_samples:number,dominant_style:string,forward_5:{win_rate:number|null,samples:number},forward_10:{win_rate:number|null,samples:number},average_cost?:number|null,estimated_position?:number|null,average_cost_status?:'available'|'no_position'|'missing_price',average_cost_as_of?:string}
type V1Outcome={status:string,entry_date?:string|null,entry_price?:number|null,exit_date?:string|null,exit_price?:number|null,exit_reason?:'take_profit'|'take_profit_gap'|'stop_loss'|'stop_loss_gap'|'day20'|null,gross_return_pct?:number|null,net_return_pct?:number|null,hit?:boolean|null,holding_sessions?:number|null}
type Signal={symbol:string,name:string,close?:number|null,sectors:string[],score:number,trust_score:number,net:number,net_value:number|null,buy_count:number,sell_count:number,rs20:number,rs_ytd:number,branches:BranchProof[],qualified_branches:BranchProof[],total_direction_branches:number,ignored_branch_count:number,strategy_history?:Partial<Record<'v1'|'v1_sell'|'v4b'|'v4_sell',StrategyHistory>>,backtest?:{status:string,entry_date?:string|null,entry_price?:number|null,horizons:Record<string,Horizon>},v1_strategy?:V1Outcome}
type V4Outcome={status:string,entry_kind?:string|null,entry_date?:string|null,entry_price?:number|null,target_price?:number|null,wait_sessions?:number|null,exit_date?:string|null,net_return_pct?:number|null,hit?:boolean|null}
type V4Signal=Signal&{base_score:number,sector_score:number,sector_name:string,strategies?:{hybrid20:V4Outcome}}
type V4Block={version:string,buy:V4Signal[],sell:V4Signal[],sector_weight_pct:number,hold_sessions:number,wait_sessions:number,pullback_pct:number,method:string}
type MarketFilter={date:string,available:boolean,blocked:boolean,taiex_close?:number,taiex_sma20?:number,tpex_close?:number,tpex_sma20?:number}
type Saved={date:string,created_at:string,mode:'forward'|'replay',coverage:{observed:number,total:number,missing:string[]},buy:Signal[],blocked_buy?:Signal[],sell:Signal[],v4?:V4Block,warnings:string[],round_trip_cost_pct:number,v1_market_filter?:MarketFilter}
type Stats={samples:number,wins:number,hit_rate:number|null,avg_return_pct:number|null,portfolio_return_pct:number|null,report_days:number}
type V4Stats={label:string,eligible_signals:number,entries:number,fill_rate:number|null,wins:number,hit_rate:number|null,avg_return_pct:number|null,expected_return_pct:number|null,portfolio_return_pct:number|null,report_days:number}
type ModelKey='v11'|'v10'|'v3'|'v2'|'v1'|'v4'|'v4b'|'v5'|'v8'|'v9'
type V5Trade={signal_date:string,entry_date:string,entry_price:number,entry_bias20:number,exit_signal_date:string,exit_date:string,exit_price:number,exit_bias20:number,holding_sessions:number,net_return_pct:number,hit:boolean}
type V5OpenPosition={signal_date:string,entry_date:string,entry_price:number,entry_bias20:number,mark_date:string,mark_price:number,holding_sessions:number,unrealized_return_pct:number}
type RankedPosition={symbol:string,name:string,signal_date:string,entry_date:string,entry_price:number,exit_date?:string,exit_price?:number|null,mark_date?:string,mark_price?:number|null,holding_sessions:number,return_pct:number}
type ModelStats={key:ModelKey,label:string,description:string,hold_sessions:number|null,report_days:number,eligible_signals:number,entries:number,fill_rate:number|null,wins:number,hit_rate:number|null,avg_return_pct:number|null,expected_return_pct:number|null,portfolio_return_pct:number|null,realized_return_pct?:number,unrealized_return_pct?:number,curve:{date:string,value:number,day_return_pct:number|null,eligible:number,entries:number,open_positions?:number,closed_positions?:number,pending_signals?:number,realized_value?:number,unrealized_value?:number}[],as_of?:string|null,trades?:unknown[],open_positions?:unknown[],events?:unknown[],open_position_count?:number,closed_position_count?:number,pending_signal_count?:number,stop_count?:number,current_bias?:number|null,entry_bias?:number,exit_bias?:number,monte_carlo?:{samples:number,signal_days:number,p5:number|null,median:number|null,p95:number|null,positive_rate:number|null},valuation?:string,return_method?:string,top_realized?:RankedPosition[],top_unrealized?:RankedPosition[],basis?:string}
type VolatilityPeriod={window:number,current_pct:number|null,as_of:string|null,average_pct:number|null,min_pct:number|null,max_pct:number|null,percentile:number|null,curve:{date:string,value:number}[]}
type MarketVolatility={daily:VolatilityPeriod,weekly:VolatilityPeriod,regime:string,method:string}
type ModelPerformance={models:Record<ModelKey,ModelStats>,comparison:Record<ModelKey,ModelStats>,holding_duration?:Partial<Record<ModelKey,HoldingDurationSummary>>,trading_calendar?:string[],common_dates:string[],mature_common_dates?:string[],comparison_note:string,market_volatility?:MarketVolatility}
type Report={v11?:V11Model,v10?:V10Model,v3?:V3Model,v1?:V1Model,v2?:V8Model,v4?:V4Model,v8?:V8Model,v9?:V9Model,available:boolean,selected:Saved|null,reports:Saved[],saved_dates:string[],eligible_dates:string[],summary:Record<string,Record<string,Stats>>,v4_summary?:Record<'hybrid20',V4Stats>,model_performance?:ModelPerformance,v1_method?:string,v4_method?:string,v5_method?:string,method:string,round_trip_cost_pct:number}

const data=ref<Report|null>(null),loading=ref(true),saving=ref(false),error=ref(''),selectedDate=ref('')
provide('stock-history',computed(()=>({models:data.value?.model_performance?.models||{},calendar:data.value?.model_performance?.trading_calendar||[]})))
const v9Refreshing=ref(false),v9Message=ref('')
const horizons:HorizonKey[]=['1','5','10','20','40']
const activeModel=ref<ModelKey>('v1')
const modelKeys:ModelKey[]=['v1','v10','v11','v2','v3','v4','v5','v8','v9']
const manualModel=computed(()=>['v3','v10','v11'].includes(activeModel.value))
const manualAsOf=computed(()=>activeModel.value==='v11'?data.value?.v11?.as_of:activeModel.value==='v10'?data.value?.v10?.as_of:data.value?.v3?.as_of)
const modelColors:Record<ModelKey,string>={v11:'#6974a8',v10:'#397f80',v3:'#895e9b',v2:'#307c7c',v1:'#2d6c4f',v4:'#ae6d3d',v4b:'#b9574b',v5:'#705a96',v8:'#9b683c',v9:'#536da3'}
const scoreSide=ref<'combined'|'buy'|'sell'>('combined')
const trialSide=ref<'buy'|'sell'>('buy')
const trialHorizon=ref<HorizonKey>('5')
const trialAmount=ref<number|string>(100000)
const scoreSideLabel=computed(()=>scoreSide.value==='buy'?'買方':scoreSide.value==='sell'?'賣方':'買賣合計')
const current=computed(()=>data.value?.selected||null)
const v4Current=computed(()=>current.value?.v4||null)
const activeModelStats=computed(()=>data.value?.model_performance?.models[activeModel.value]||null)
const modelTabs=computed(()=>modelKeys.map(key=>({key,label:data.value?.model_performance?.models[key]?.label||key.toUpperCase()})))
const isV4=computed(()=>activeModel.value==='v4b')
const activeV4Strategy:'hybrid20'='hybrid20'
const modelSignals=computed(()=>(activeModel.value==='v1'?current.value?.buy:v4Current.value?.buy)||[])
const selectedModelSeries=computed(()=>!activeModelStats.value?[]:[{key:activeModel.value,label:activeModelStats.value.label,color:modelColors[activeModel.value],curve:activeModelStats.value.curve}])
const v1Activity=computed(()=>{
 const reports=new Map((data.value?.reports||[]).map(report=>[report.date,report]))
 return (data.value?.model_performance?.models.v1?.curve||[]).map(point=>{const report=reports.get(point.date);return {
  date:point.date,buy:(report?.buy||[]).map(row=>row.symbol),sell:(report?.sell||[]).map(row=>row.symbol),
 }})
})
const v4Activity=computed(()=>{
 if(!isV4.value)return []
 const model='v4b'
 const reports=new Map((data.value?.reports||[]).map(report=>[report.date,report]))
 return (data.value?.model_performance?.models[model]?.curve||[]).map(point=>{const report=reports.get(point.date);return {
  date:point.date,buy:(report?.v4?.buy||[]).map(row=>row.symbol),sell:(report?.v4?.sell||[]).map(row=>row.symbol),
 }})
})
const isV5=computed(()=>activeModel.value==='v5')
const v5Trades=computed(()=>(activeModelStats.value?.trades||[]).slice().reverse() as V5Trade[])
const v5OpenPositions=computed(()=>(activeModelStats.value?.open_positions||[]).slice().reverse() as V5OpenPosition[])
const trialCapital=computed(()=>{const value=Number(trialAmount.value);return Number.isFinite(value)&&value>0?Math.floor(value):0})
const trialStats=computed(()=>Object.fromEntries(horizons.map(horizon=>{
 let reportDays=0,positions=0,invested=0,profit=0,winningDays=0
 for(const report of data.value?.reports||[]){
  const signals=report[trialSide.value]
  if(!signals.length||signals.some(row=>{const value=row.backtest?.horizons?.[horizon];return !row.backtest?.entry_price||value?.status!=='scored'||!Number.isFinite(value.net_return_pct)}))continue
  const budget=trialCapital.value/signals.length;let dayInvested=0,dayProfit=0
  for(const row of signals){const price=row.backtest!.entry_price!;const shares=Math.floor(budget/price);if(!shares)continue;const cost=shares*price;dayInvested+=cost;dayProfit+=cost*row.backtest!.horizons[horizon].net_return_pct!/100;positions++}
  if(!dayInvested)continue
  reportDays++;invested+=dayInvested;profit+=dayProfit;if(dayProfit>0)winningDays++
 }
 return [horizon,{report_days:reportDays,positions,invested,profit,return_pct:invested?profit/invested*100:null,winning_days:winningDays}]
})))
const trialRows=computed(()=>{const signals=current.value?.[trialSide.value]||[],budget=signals.length?trialCapital.value/signals.length:0;return signals.map(row=>{const price=row.backtest?.entry_price||null,shares=price?Math.floor(budget/price):null,invested=price&&shares!==null?price*shares:null,value=row.backtest?.horizons?.[trialHorizon.value];return {...row,allocation:budget,trial_price:price,trial_shares:shares,trial_invested:invested,trial_return:value?.status==='scored'?value.net_return_pct:null,trial_profit:value?.status==='scored'&&invested!==null&&value.net_return_pct!==null&&value.net_return_pct!==undefined?invested*value.net_return_pct/100:null,trial_status:value?.status||'pending'}})})
const v4TrialStats=computed(()=>{
 let reportDays=0,positions=0,allocated=0,invested=0,profit=0,unfilled=0
 if(!isV4.value)return {report_days:0,positions:0,allocated:0,invested:0,profit:0,unfilled:0,return_pct:null,use_rate:null}
 for(const report of data.value?.reports||[]){
  const signals=report.v4?.buy||[];if(!signals.length)continue
  const outcomes=signals.map(row=>row.strategies?.[activeV4Strategy])
  if(outcomes.some(outcome=>!outcome||!['scored','unfilled'].includes(outcome.status)))continue
  reportDays++;allocated+=trialCapital.value;const budget=trialCapital.value/signals.length
  outcomes.forEach(outcome=>{if(outcome!.status==='unfilled'){unfilled++;return}const price=outcome!.entry_price||0,shares=price?Math.floor(budget/price):0;if(!shares)return;const cost=price*shares;invested+=cost;positions++;profit+=cost*(outcome!.net_return_pct||0)/100})
 }
 return {report_days:reportDays,positions,allocated,invested,profit,unfilled,return_pct:allocated?profit/allocated*100:null,use_rate:allocated?invested/allocated*100:null}
})
const v4TrialRows=computed(()=>{const signals=v4Current.value?.buy||[],budget=signals.length?trialCapital.value/signals.length:0;return signals.map(row=>{const outcome=row.strategies?.[activeV4Strategy],actualPrice=outcome?.entry_price||null,estimatePrice=outcome?.target_price||(row.close?row.close*.95:null),price=actualPrice||estimatePrice,estimated=!actualPrice&&outcome?.status!=='unfilled',shares=price?Math.floor(budget/price):outcome?.status==='unfilled'?0:null,invested=price&&shares!==null?price*shares:outcome?.status==='unfilled'?0:null,trialReturn=outcome?.status==='scored'?outcome.net_return_pct:null,profit=trialReturn!==null&&trialReturn!==undefined&&invested!==null?invested*trialReturn/100:outcome?.status==='unfilled'?0:null;return {...row,allocation:budget,trial_price:price,trial_price_estimated:estimated,trial_shares:shares,trial_invested:invested,trial_return:trialReturn,trial_profit:profit,trial_status:outcome?.status||'pending'}})})
const fmt=(v:number|null|undefined,d=0)=>v===null||v===undefined?'—':v.toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d})
const signed=(v:number|null|undefined,d=2)=>v===null||v===undefined?'—':`${v>0?'+':''}${fmt(v,d)}%`
const money=(v:number|null|undefined)=>v===null||v===undefined?'—':`${v>0?'+':''}${Math.abs(v)>=1e8?fmt(v/1e8,2)+' 億':fmt(v/1e4,1)+' 萬'}`
const currency=(v:number|null|undefined,signedValue=false)=>v===null||v===undefined?'—':`${signedValue&&v>0?'+':''}NT$ ${Math.round(v).toLocaleString('zh-TW')}`
const tone=(v:number|null|undefined)=>v===null||v===undefined?'muted':v>0?'positive':v<0?'negative':'muted'
const statusText=(signal:Signal,horizon='5')=>{const h=signal.backtest?.horizons?.[horizon];return h?.status==='scored'?(h.hit?'命中':'未命中'):signal.backtest?.status==='gap'||h?.status==='gap'?'行情缺口':'待成熟'}
const statusClass=(signal:Signal,horizon='5')=>{const h=signal.backtest?.horizons?.[horizon];return {ready:h?.status==='scored'&&h.hit,miss:h?.status==='scored'&&!h.hit,pending:h?.status!=='scored'}}
const v4Status=(outcome:V4Outcome|undefined)=>outcome?.status==='scored'?`${outcome.hit?'命中':'未命中'} ${signed(outcome.net_return_pct)}`:outcome?.status==='unfilled'?'5 日未成交':outcome?.status==='tracking'?`已進場，等待20日`:outcome?.status==='gap'?'行情缺口':outcome?.target_price?`等待 ${fmt(outcome.target_price,2)}`:'等待下一交易日'
const selectedV4Status=(row:V4Signal)=>v4Status(row.strategies?.[activeV4Strategy])
const strategyHistory=(row:Signal,side:'buy'|'sell'='buy')=>{const key=side==='sell'?(isV4.value?'v4_sell':'v1_sell'):(isV4.value?'v4b':'v1');return row.strategy_history?.[key]}
const strategyHistoryTitle=(row:Signal,side:'buy'|'sell'='buy')=>{const value=strategyHistory(row,side),direction=side==='buy'?'跟買':'跟賣';if(!value?.samples)return `過去同模型尚無已完成${direction}交易`;const latest=value.last_exit_date?`；最近一次 ${value.last_exit_date} ${value.last_result==='win'?'成功':'失敗'} ${signed(value.last_return_pct)}`:'';return `同一股票過去已完成 ${value.samples} 筆${direction}：成功 ${value.wins}、失敗 ${value.losses}、勝率 ${fmt(value.win_rate,1)}%${latest}`}
const branchCostText=(branch:BranchProof)=>branch.average_cost_status==='available'&&branch.average_cost!==null&&branch.average_cost!==undefined
 ? `分點推估成本 ${fmt(branch.average_cost,2)} 元 · 尚有 ${fmt(branch.estimated_position,0)} 張`
 : branch.average_cost_status==='no_position'?'觀察期目前無推估部位':'分點成本資料不足'
const v4TargetSellPrice=(row:V4Signal)=>{const outcome=row.strategies?.[activeV4Strategy],entry=outcome?.entry_price||outcome?.target_price||(row.close?row.close*.95:null),rate=Math.max(0,activeModelStats.value?.avg_return_pct||0);return entry===null?null:entry*(1+rate/100)}

async function request(path:string,body?:unknown){
 const response=await localFetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const result=await readJson(response);if(!response.ok)throw new Error(result.error||'無法讀取每日跟單資料');return result
}
async function load(date=''){
 loading.value=true;try{data.value=await request('/api/daily-follow'+(date?'?date='+encodeURIComponent(date):''));selectedDate.value=data.value?.selected?.date||'';error.value=''}catch(e){error.value=(e as Error).message}finally{loading.value=false}
}
async function selectDate(){if(selectedDate.value)await load(selectedDate.value)}
async function generate(){
 saving.value=true;try{data.value=await request('/api/daily-follow/generate',{all:true});selectedDate.value=data.value?.selected?.date||'';error.value=''}catch(e){error.value=(e as Error).message}finally{saving.value=false}
}
async function refreshV9(){
 v9Refreshing.value=true;v9Message.value=''
 try{const result=await request('/api/v9/refresh',{});v9Message.value=result.message||'V9 行情已更新';await load(selectedDate.value)}
 catch(e){v9Message.value=(e as Error).message}
 finally{v9Refreshing.value=false}
}
onMounted(()=>load())
</script>

<template>
 <div class="follow-page">
  <header class="hero">
   <div><div class="eyebrow">DAILY FOLLOW SIGNALS</div><h1>每日跟單</h1><p>依各模型的條件、資金與退出規則追蹤；收盤更新後查看候選、持股與實際買賣紀錄。</p></div>
   <div v-if="!manualModel" class="actions"><label>名單日期<select v-model="selectedDate" :disabled="!data?.saved_dates.length" @change="selectDate"><option v-for="date in data?.saved_dates" :key="date" :value="date">{{date}}</option></select></label><button :disabled="saving" @click="generate"><Icon name="refresh" :class="{spin:saving}" :size="17"/>{{saving?'正在整理':'更新今日名單'}}</button></div><span v-else>{{activeModel.toUpperCase()}} 僅依對話指令更新 · 已保存 {{manualAsOf||'—'}}</span>
  </header>
  <div v-if="error" class="notice error"><Icon name="info" :size="17"/>{{error}}</div>
  <div v-if="loading" class="empty"><Icon name="refresh" class="spin"/><strong>正在讀取本機回測…</strong></div>
  <div v-else-if="!current&&!data?.v11?.available&&!data?.v10?.available&&!data?.v3?.available" class="empty"><Icon name="star"/><strong>尚未建立每日名單</strong><p v-if="data?.eligible_dates.length">已有 {{data.eligible_dates.length}} 個分點與 RS 同日快照，按「更新今日名單」即可建立。</p><p v-else>請先完成追蹤分點與個股 RS 更新。</p></div>
  <template v-else>
   <section class="model-nav">
    <div><div class="eyebrow">STRATEGY ENGINE</div><h2>策略模型</h2><p>V1 獲利保護；V10 手動 V1；V11 手動 α 趨勢（與 V3、V6、V10 同步）；V2 三檔強度重排與盤中停損；V3 十萬元手動研究；V4 百萬挑戰（十萬本金）；V5 00663L；V8 吸籌壓縮突破；V9 台指期。V6 股災氣象球已移至左側獨立頁。</p></div>
    <div class="model-tabs" aria-label="切換策略模型"><button v-for="model in modelTabs" :key="model.key" :class="[model.key,{active:activeModel===model.key}]" @click="activeModel=model.key">{{model.label}}</button></div>
   </section>
   <HoldingDurationStats :stats="data?.model_performance?.holding_duration?.[activeModel]" :model-key="activeModel"/>
   <p v-if="activeModel!=='v9'" class="stock-history-help">個股歷史列在股票下方：只計同策略截至查看日期收盤已完成的交易，勝率依扣成本後損益計算，持有天數為交易日。未平倉不計；— 表示尚無樣本。</p>
   <section v-if="isV5" class="v4-card v5-card">
    <header><div><div class="eyebrow">00663L · TSMC BIAS TIMING</div><h2>{{activeModelStats?.label}}</h2><p>{{activeModelStats?.description}}</p></div><span>槓桿 ETF 做多 · BIAS 6% 出場</span></header>
    <div class="model-metrics"><article><small>等額試算部位</small><strong>{{activeModelStats?.entries||0}}</strong><span>已實現 {{v5Trades.length}} 筆 · 持有中 {{v5OpenPositions.length}} 筆</span></article><article><small>已實現平均報酬</small><strong :class="tone(activeModelStats?.realized_return_pct)">{{signed(activeModelStats?.realized_return_pct)}}</strong><span>未實現 {{signed(activeModelStats?.unrealized_return_pct)}}</span></article><article><small>已完成命中率</small><strong>{{fmt(activeModelStats?.hit_rate,1)}}%</strong><span>{{activeModelStats?.wins||0}} 筆獲利</span></article><article><small>台積電目前 BIAS20</small><strong :class="tone(activeModelStats?.current_bias)">{{signed(activeModelStats?.current_bias)}}</strong><span>{{activeModelStats?.as_of}} · 低於 0% 買入／達 6% 賣出</span></article></div>
    <div class="model-curve-head"><div><h3>00663L 等額部位平均報酬</h3><p>每個負 BIAS20 日期建立一筆等額部位，持有中以每日收盤評價。</p></div><div class="curve-total"><strong :class="tone(activeModelStats?.portfolio_return_pct)">{{signed(activeModelStats?.portfolio_return_pct)}}</strong><span>含持有中評價 · 已實現 {{signed(activeModelStats?.realized_return_pct)}}</span></div></div>
    <ModelCurve :series="selectedModelSeries" unit="%"/>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>買入觸發</th><th>買入日／價格</th><th>賣出觸發</th><th>賣出／評價日</th><th>持有日</th><th>成本後報酬</th></tr></thead><tbody><tr v-for="(position,i) in v5OpenPositions" :key="'v5-open-'+position.signal_date" class="open-position"><td class="rank">{{i+1}}</td><td>{{position.signal_date}}<small>BIAS {{signed(position.entry_bias20)}}</small></td><td>{{position.entry_date}}<small>{{fmt(position.entry_price,2)}} 元</small></td><td>持有中<small>等待 BIAS≥6%</small><StockHistory model-key="v5" symbol="00663L" :as-of="activeModelStats?.as_of"/></td><td>{{position.mark_date}}<small>{{fmt(position.mark_price,2)}} 元</small></td><td>{{position.holding_sessions}} 日</td><td :class="tone(position.unrealized_return_pct)"><strong>{{signed(position.unrealized_return_pct)}}</strong><small>未實現</small></td></tr><tr v-for="(trade,i) in v5Trades" :key="'v5-closed-'+trade.signal_date"><td class="rank">{{i+v5OpenPositions.length+1}}</td><td>{{trade.signal_date}}<small>BIAS {{signed(trade.entry_bias20)}}</small></td><td>{{trade.entry_date}}<small>{{fmt(trade.entry_price,2)}} 元</small></td><td>{{trade.exit_signal_date}}<small>BIAS {{signed(trade.exit_bias20)}}</small></td><td>{{trade.exit_date}}<small>{{fmt(trade.exit_price,2)}} 元</small></td><td>{{trade.holding_sessions}} 日</td><td :class="tone(trade.net_return_pct)"><strong>{{signed(trade.net_return_pct)}}</strong><small>已實現</small></td></tr><tr v-if="!v5Trades.length&&!v5OpenPositions.length"><td colspan="7" class="v5-empty">尚未觸發 00663L 買入。</td></tr></tbody></table></div>
    <footer><Icon name="info" :size="15"/><span>{{data?.v5_method}}</span></footer>
   </section>
   <V1Panel v-else-if="activeModel==='v1'" :model="data?.v1" :selected-date="selectedDate"/>
   <V10Panel v-else-if="activeModel==='v10'" :model="data?.v10"/>
   <V11Panel v-else-if="activeModel==='v11'" :model="data?.v11"/>
   <V2Panel v-else-if="activeModel==='v2'" :model="data?.v2" :selected-date="selectedDate"/>
   <V3Panel v-else-if="activeModel==='v3'" :model="data?.v3"/>
   <V4Panel v-else-if="activeModel==='v4'" :model="data?.v4" :selected-date="selectedDate"/>
   <V8Panel v-else-if="activeModel==='v8'" :model="data?.v8" :selected-date="selectedDate"/>
   <V9Panel v-else-if="activeModel==='v9'" :model="data?.v9" :refreshing="v9Refreshing" :message="v9Message" @refresh="refreshV9"/>
  </template>
 </div>
</template>

<style scoped>
.stock-history-help{margin:0 0 16px;padding:0 4px;color:#7b8875;font-size:12px;line-height:1.8}

.follow-page{color:#273429}.hero{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;margin-bottom:22px}.eyebrow{font-size:11px;letter-spacing:.14em;color:#83907f;font-weight:800}.hero h1{font-family:Georgia,serif;font-size:34px;margin:6px 0}.hero p,.signal-panel header p,.backtest header p{color:#7b8778;margin:0}.actions{display:flex;gap:10px;align-items:end}.actions label{font-size:12px;color:#71806e}.actions select{display:block;margin-top:5px;min-width:145px}.actions select,.actions button{border:1px solid #dce3d8;border-radius:10px;background:#fff;padding:10px 13px}.actions button{background:#405d3f;color:#fff;font-weight:700;display:flex;gap:7px;align-items:center;cursor:pointer}.actions button:disabled{opacity:.55}.status-line{background:#fff;border:1px solid #e2e7df;border-radius:13px;padding:13px 16px;display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.status-line>div{display:flex;gap:10px;align-items:center}.status-line span,.status-line small{color:#7b8778}.mode{border-radius:99px;padding:4px 8px;font-size:11px;font-weight:800}.mode.forward{background:#e3f3e9;color:#31744e}.mode.replay{background:#f3efe1;color:#876c28}.notice{padding:11px 14px;border-radius:10px;background:#f5f3e8;color:#78682f;display:flex;align-items:center;gap:8px;margin-bottom:14px}.notice.error{background:#fff0ed;color:#a44b3d}.scorecards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}.scorecards article{background:#fff;border:1px solid #e2e7df;border-radius:14px;padding:17px}.scorecards small{color:#778575}.scorecards strong{display:block;font:700 27px Georgia,serif;margin:8px 0}.scorecards p{margin:0 0 7px;color:#596856}.scorecards span{font-size:12px;color:#7d897b}.signal-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.signal-panel,.backtest{background:#fff;border:1px solid #e0e6dc;border-radius:15px;overflow:hidden}.signal-panel>header,.backtest>header{padding:17px 18px;border-bottom:1px solid #edf0ea}.signal-panel>header>div{display:flex;align-items:center;gap:9px}.signal-panel h2,.backtest h2{margin:0 0 4px;font-size:18px}.side-dot{width:9px;height:9px;border-radius:50%;background:#b9574b}.buy .side-dot{background:#27835f}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;padding:11px 10px;background:#f7f8f5;color:#71806d;white-space:nowrap;font-weight:700}td{padding:12px 10px;border-top:1px solid #edf0ea;vertical-align:top}td strong{white-space:nowrap}td strong small{color:#879184;font-weight:500}.rank{font:700 17px Georgia,serif;color:#97a193}.chips{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}.chips span{font-size:10px;border-radius:99px;background:#edf2e9;color:#63735e;padding:3px 6px}.branch-list,td>small{display:block;color:#90998d;font-size:10px;margin-top:4px;max-width:170px}.proof{margin-top:7px;padding:6px 7px;border-radius:7px;background:#f5f7f2;min-width:170px}.proof b,.proof span{display:block;font-size:10px}.proof span{color:#788575;margin-top:2px}.proof .branch-cost{color:#426447;font-weight:800;margin:4px 0}.positive{color:#1d7e59}.negative{color:#bd5348}.muted{color:#778277}.result-pill{display:inline-block;padding:4px 7px;border-radius:99px;background:#f2efe3;color:#8a7134;font-size:11px;font-weight:800}.result-pill.ready{background:#e3f3e9;color:#26734c}.result-pill.miss{background:#f9e8e5;color:#a84b43}.backtest{margin-top:17px}.backtest>header{display:flex;justify-content:space-between;align-items:center}.backtest>header>span{background:#eef3eb;border-radius:99px;padding:6px 9px;font-size:12px;color:#60705d}.backtest td,.backtest th{text-align:right}.backtest td:first-child,.backtest th:first-child,.backtest td:nth-child(2),.backtest th:nth-child(2){text-align:left}.backtest footer{display:flex;gap:8px;padding:13px 17px;background:#fafbf8;color:#7a8677;font-size:12px;line-height:1.6}.empty{min-height:320px;background:#fff;border:1px dashed #dce4d8;border-radius:15px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#788675;gap:10px}.empty p{margin:0}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:1200px){.signal-grid{grid-template-columns:1fr}.scorecards{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.hero,.status-line,.backtest>header{align-items:flex-start;flex-direction:column}.actions{width:100%}.scorecards{grid-template-columns:1fr}.status-line>div{flex-wrap:wrap}}
.market-gate{display:flex;align-items:center;gap:16px;background:#edf5ec;border:1px solid #dbe8d8;border-radius:13px;padding:13px 16px;margin-bottom:14px}.market-gate>div{display:flex;flex-direction:column;gap:3px}.market-gate span,.market-gate small{color:#6f7f6d;font-size:12px}.market-gate>b{margin-left:auto;color:#39704c}.market-gate.blocked{background:#fff1ed;border-color:#f0d6ce}.market-gate.blocked>b{color:#aa4f42}.market-gate.blocked small{max-width:230px}@media(max-width:760px){.market-gate{align-items:flex-start;flex-direction:column}.market-gate>b{margin-left:0}}
.v4-card{background:#fff;border:1px solid #dce5d8;border-radius:15px;overflow:hidden;margin-bottom:18px}.v4-card>header{padding:17px 18px;display:flex;justify-content:space-between;gap:20px;align-items:center;border-bottom:1px solid #edf0ea}.v4-card h2{margin:4px 0;font-size:20px}.v4-card header p{margin:0;color:#778575}.v4-card>header>span{background:#e6f0df;color:#4d6f42;border-radius:99px;padding:7px 10px;font-size:12px;font-weight:800;white-space:nowrap}.v4-summary{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #edf0ea}.v4-summary article{padding:17px 18px}.v4-summary article+article{border-left:1px solid #edf0ea}.v4-summary small,.v4-summary span{color:#778575}.v4-summary strong{display:block;font:700 28px Georgia,serif;margin:8px 0}.v4-summary p{margin:0 0 5px}.v4-card footer{display:flex;gap:8px;padding:12px 17px;background:#fafbf8;color:#748171;font-size:12px;line-height:1.6}@media(max-width:760px){.v4-card>header{align-items:flex-start;flex-direction:column}.v4-summary{grid-template-columns:1fr}.v4-summary article+article{border-left:0;border-top:1px solid #edf0ea}}
.model-nav{background:#fff;border:1px solid #dfe6dc;border-radius:15px;padding:16px 18px;display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:18px}.model-nav h2{font-size:20px;margin:3px 0}.model-nav p{margin:0;color:#7b8778}.model-tabs{display:grid;grid-template-columns:repeat(4,max-content);gap:4px;background:#edf0ea;border-radius:12px;padding:4px}.model-tabs button{border:0;background:transparent;color:#6f7d6d;border-radius:9px;padding:9px 10px;font-size:12px;font-weight:800;white-space:nowrap;cursor:pointer}.model-tabs button.active{background:#fff;color:#314e35;box-shadow:0 2px 7px #344f3920}.model-tabs button.v9.active{color:#536da3}.model-tabs button.v4b.active{color:#a54f46}.model-performance,.model-compare{background:#fff;border:1px solid #e0e6dc;border-radius:15px;overflow:hidden;margin-bottom:18px}.model-performance>header,.model-compare>header{padding:16px 18px 8px;display:flex;justify-content:space-between;align-items:center;gap:18px}.model-performance h2,.model-compare h2{margin:3px 0;font-size:19px}.model-performance header p,.model-compare header p{margin:0;color:#7b8778}.curve-total{text-align:right;white-space:nowrap}.curve-total strong{display:block;font:700 25px Georgia,serif}.curve-total span{display:block;margin-top:4px;color:#778575;font-size:11px}.model-metrics{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid #edf0ea}.model-metrics article{padding:15px 18px;border-right:1px solid #edf0ea}.model-metrics article:last-child{border-right:0}.model-metrics small,.model-metrics span{display:block;color:#788576;font-size:11px}.model-metrics strong{display:block;font:700 26px Georgia,serif;margin:7px 0}.model-curve-head{padding:15px 18px 0;display:flex;justify-content:space-between;align-items:center;gap:18px}.model-curve-head h3{margin:0 0 3px;font-size:16px}.model-curve-head p{margin:0;color:#7b8778;font-size:12px}.model-compare>header>span{background:#eef2ea;border-radius:99px;padding:7px 10px;color:#647261;font-size:12px;font-weight:800;white-space:nowrap}.compare-cards{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid #edf0ea;border-bottom:1px solid #edf0ea}.compare-cards article{padding:16px 18px;border-right:1px solid #edf0ea}.compare-cards article:last-child{border-right:0}.compare-cards article>div{display:flex;align-items:center;gap:7px}.compare-cards i,.compare-legend i{display:inline-block;width:9px;height:9px;border-radius:50%}.compare-cards b{display:block;font:700 27px Georgia,serif;margin:8px 0}.compare-cards p{margin:0 0 4px;color:#596856}.compare-cards span{font-size:12px;color:#7d897b}.compare-cards small{display:block;margin-top:5px;color:#8a9588;font-size:10px}.compare-legend{display:flex;justify-content:center;gap:22px;padding:14px 18px 0;color:#647261;font-size:12px}.compare-legend span{display:flex;align-items:center;gap:6px}.model-compare footer{display:flex;gap:8px;padding:12px 17px;background:#fafbf8;color:#748171;font-size:12px;line-height:1.6;border-top:1px solid #edf0ea}@media(max-width:900px){.model-nav{align-items:flex-start;flex-direction:column}.model-metrics{grid-template-columns:repeat(2,1fr)}.compare-cards{grid-template-columns:1fr}.compare-cards article{border-right:0;border-bottom:1px solid #edf0ea}}@media(max-width:600px){.model-tabs{width:100%;grid-template-columns:repeat(2,1fr)}.model-metrics{grid-template-columns:1fr}.model-metrics article{border-right:0;border-bottom:1px solid #edf0ea}.model-curve-head,.model-performance>header{align-items:flex-start;flex-direction:column}.curve-total{text-align:left}.compare-legend{align-items:flex-start;flex-direction:column;gap:7px}}
</style>
<style scoped>
.hit-curve{background:#fff;border:1px solid #e0e6dc;border-radius:15px;overflow:hidden;margin-bottom:18px}.hit-curve>header{padding:17px 18px 10px;display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.hit-curve h2{margin:0 0 4px;font-size:18px}.hit-curve header p{margin:0;color:#7b8778}.curve-tabs{display:flex;background:#f1f3ee;border-radius:10px;padding:3px;white-space:nowrap}.curve-tabs button{border:0;background:transparent;color:#778474;border-radius:8px;padding:7px 12px;font-weight:700;cursor:pointer}.curve-tabs button.active{background:#fff;color:#36583b;box-shadow:0 1px 4px #344f3920}.curve-meta{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:5px 18px 0;color:#788576;font-size:12px}.curve-meta i{display:inline-block;width:18px;height:3px;border-radius:4px;margin-right:6px;vertical-align:middle}.buy-key i{background:#2d6c4f}.sell-key i{background:#b58a43}.curve-meta b{margin-left:auto;color:#556452}.hit-curve footer{display:flex;gap:8px;padding:12px 17px;background:#fafbf8;color:#7a8677;font-size:12px;line-height:1.6;border-top:1px solid #edf0ea}@media(max-width:760px){.hit-curve>header{flex-direction:column}.curve-meta b{margin-left:0;width:100%}}
.scorecard-heading{display:flex;align-items:center;justify-content:space-between;gap:15px;margin:0 0 10px}.scorecard-heading>div:first-child{display:flex;align-items:baseline;gap:10px}.scorecard-heading strong{font-size:16px}.scorecard-heading span{font-size:12px;color:#7b8778}.score-side-tabs{display:flex;background:#e9ede6;border-radius:10px;padding:3px}.score-side-tabs button{border:0;background:transparent;color:#71806e;border-radius:8px;padding:7px 13px;font-weight:700;cursor:pointer}.score-side-tabs button.active{background:#fff;color:#36583b;box-shadow:0 1px 4px #344f3920}@media(max-width:760px){.scorecard-heading{align-items:flex-start;flex-direction:column}.scorecard-heading>div:first-child{align-items:flex-start;flex-direction:column;gap:3px}}
.trial-card{background:#fff;border:1px solid #e0e6dc;border-radius:15px;overflow:hidden;margin-bottom:18px}.trial-card>header{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;padding:17px 18px;border-bottom:1px solid #edf0ea}.trial-card h2{margin:0 0 4px;font-size:18px}.trial-card header p{margin:0;color:#7b8778}.trial-controls{display:flex;gap:10px;align-items:flex-end}.trial-controls label{font-size:12px;color:#71806e}.trial-controls input{display:block;margin-top:5px;width:160px;border:1px solid #dce3d8;border-radius:9px;padding:8px 10px}.trial-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:0;border-bottom:1px solid #edf0ea}.trial-summary article{padding:15px 17px;border-right:1px solid #edf0ea}.trial-summary article:last-child{border-right:0}.trial-summary small,.trial-summary span{display:block;color:#7b8778;font-size:11px}.trial-summary strong{display:block;font:700 19px Georgia,serif;margin:7px 0}.trial-summary p{margin:0 0 5px;font-size:12px}.trial-detail-head{display:flex;justify-content:space-between;align-items:center;padding:13px 17px}.trial-detail-head>div:first-child{display:flex;gap:10px;align-items:baseline}.trial-detail-head span{color:#7b8778;font-size:12px}.trial-table td,.trial-table th{text-align:right}.trial-table td:first-child,.trial-table th:first-child{text-align:left}.trial-card footer{display:flex;gap:8px;padding:12px 17px;background:#fafbf8;color:#7a8677;font-size:12px;line-height:1.6}@media(max-width:1200px){.trial-summary{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.trial-card>header,.trial-controls,.trial-detail-head{align-items:flex-start;flex-direction:column}.trial-summary{grid-template-columns:1fr}.trial-summary article{border-right:0;border-bottom:1px solid #edf0ea}.trial-controls{width:100%}.trial-detail-head>div:first-child{align-items:flex-start;flex-direction:column;gap:3px}}
.v4-trial-summary{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid #edf0ea}.v4-trial-summary article{padding:16px 18px;border-right:1px solid #edf0ea}.v4-trial-summary article:last-child{border-right:0}.v4-trial-summary small,.v4-trial-summary span{display:block;color:#7b8778;font-size:11px}.v4-trial-summary strong{display:block;font:700 23px Georgia,serif;margin:7px 0}.v4-signals{margin-bottom:18px}@media(max-width:760px){.v4-trial-summary{grid-template-columns:1fr}.v4-trial-summary article{border-right:0;border-bottom:1px solid #edf0ea}}
</style>
<style scoped>
.model-tabs{grid-template-columns:repeat(9,max-content);max-width:100%;overflow:auto}.model-tabs button.v5.active{color:#705a96}.compare-cards{grid-template-columns:repeat(auto-fit,minmax(185px,1fr))}.compare-legend{flex-wrap:wrap}.v5-card td small{display:block;margin-top:4px}.v5-empty{text-align:center;color:#859183;padding:30px!important}.volatility-block{border-top:1px solid #e4e9e2;background:#fbfcfa}.volatility-block>header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:18px 18px 5px}.volatility-block h3{margin:0 0 4px}.volatility-block p{margin:0;color:#7b8778;font-size:12px}.volatility-head-values{display:flex;align-items:baseline;gap:12px;white-space:nowrap}.volatility-head-values strong{font:700 22px Georgia,serif}.volatility-head-values small{font:600 12px sans-serif;color:#768373}.volatility-metrics{display:flex;gap:18px;flex-wrap:wrap;padding:8px 18px;color:#70806d;font-size:12px}.volatility-metrics span:first-child{color:#2d6c4f}.volatility-metrics span:last-child{color:#9a7535}@media(max-width:900px){.compare-cards{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.model-tabs{grid-template-columns:repeat(2,1fr)}.compare-cards{grid-template-columns:1fr}.volatility-block>header{align-items:flex-start;flex-direction:column}.volatility-head-values{flex-wrap:wrap}}
.history-pill{display:inline-flex;width:auto;min-width:max-content;max-width:none;min-height:0;white-space:nowrap;align-items:center;margin-top:5px;padding:3px 7px;border-radius:99px;background:#e7f1e5;color:#416943;font-size:10px;line-height:1.35;font-weight:800;cursor:help}.history-pill.no-history{background:#f1f2ee;color:#899287;font-weight:600}
.compare-rankings{border-top:1px solid #e4e9e2}.compare-rankings>article{padding:20px 18px;border-bottom:1px solid #e4e9e2}.compare-rankings>article>header{display:flex;align-items:flex-start;gap:9px;margin-bottom:14px}.compare-rankings>article>header i{width:10px;height:10px;border-radius:50%;margin-top:5px;flex:0 0 auto}.compare-rankings h3,.compare-rankings h4{margin:0}.compare-rankings header p{font-size:11px;color:#7b8778;margin:4px 0 0}.ranking-pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}.ranking-pair>section{min-width:0}.ranking-pair h4{font-size:13px;margin-bottom:8px}.ranking-pair table{font-size:11px}.ranking-pair td small{display:block;margin-top:3px;color:#879284}.ranking-empty{text-align:center;color:#8a9588;padding:20px!important}@media(max-width:1000px){.ranking-pair{grid-template-columns:1fr}} 
</style>

