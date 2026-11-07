<script setup lang="ts">
import {localFetch,readJson} from './http'
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import Icon from './Icon.vue'
import HomeDashboard from './HomeDashboard.vue'
import MainWatch from './MainWatch.vue'
import RsRanking from './RsRanking.vue'
import AlphaBeta from './AlphaBeta.vue'
import SectorFlow from './SectorFlow.vue'
import StrategyResearch from './StrategyResearch.vue'
import BlockTrades from './BlockTrades.vue'
import ActiveEtf from './ActiveEtf.vue'
import InsiderChanges from './InsiderChanges.vue'
import DividendYields from './DividendYields.vue'
import WarrantAnalysis from './WarrantAnalysis.vue'
import BrokerFocus from './BrokerFocus.vue'
import DailyFollow from './DailyFollow.vue'
import V6Weather from './V6Weather.vue'
import MarketMonitor from './MarketMonitor.vue'
import MonthlyStatistics from './MonthlyStatistics.vue'
import MarginStatistics from './MarginStatistics.vue'
import BrokerLeaders from './BrokerLeaders.vue'
import HistoryRepair from './HistoryRepair.vue'
import FlowCircle from './FlowCircle.vue'
import TopBrokers from './TopBrokers.vue'
import type {MajorStock} from './types'
import {selectRows} from './table'
import type {TableFilters} from './table'
import TrendChart from './TrendChart.vue'
import CandleChart from './CandleChart.vue'
import type {Dashboard,Job,Branch,Detail,Settings,Flow,Position,Closed,Signal,Cycle,StockStats,BuyPattern} from './types'

const cloudRuntime=ref(false)
onMounted(()=>{localFetch('/api/health').then(readJson).then(info=>{cloudRuntime.value=info.storage?.mode==='google_drive_stream'}).catch(()=>{})})
const d=ref<Dashboard|null>(null),job=ref<Job|null>(null),error=ref(''),toast=ref('')
const loading=ref(true),selectedBranch=ref('9300:9339'),selectedDate=ref(''),view=ref('home')
type NavItem={id:string;label:string;icon:string}
const defaultNavItems:NavItem[]=[
 {id:'home',label:'首頁',icon:'grid'},
 {id:'overview',label:'分點總覽',icon:'grid'},
 {id:'main-watch',label:'主力關注',icon:'pulse'},
 {id:'broker-focus',label:'券商關注',icon:'wallet'},
 {id:'daily-follow',label:'每日跟單',icon:'star'},
 {id:'v6-weather',label:'V6 股災氣象球',icon:'pulse'},
 {id:'sector-flow',label:'族群流向',icon:'chart'},
 {id:'block-trades',label:'每日鉅額交易追蹤',icon:'chart'},
 {id:'warrants',label:'權證分析',icon:'chart'},
 {id:'active-etf',label:'主動 ETF 動向',icon:'pulse'},
 {id:'insider-changes',label:'內部人持股異動',icon:'document'},
 {id:'dividend-yields',label:'殖利率排行',icon:'chart'},
 {id:'etf-plan',label:'ETF 存股規劃',icon:'wallet'},
 {id:'market',label:'市場觀測',icon:'globe'},
 {id:'margin-statistics',label:'融資維持率統計',icon:'chart'},
 {id:'monthly-statistics',label:'月份漲跌統計',icon:'chart'},
 {id:'leaders',label:'券商風雲榜',icon:'star'},
 {id:'rs',label:'RS 排行',icon:'chart'},
 {id:'alpha-beta',label:'Alpha／Beta 排行',icon:'chart'},
 {id:'etf-rs',label:'台股 ETF RS',icon:'chart'},
 {id:'performance',label:'績效分析',icon:'chart'},
 {id:'settings',label:'資料與設定',icon:'settings'},
]
const navStorageKey='branch-monitor.sidebar-order.v1'
const navOrder=ref(defaultNavItems.map(item=>item.id))
const navItems=computed(()=>navOrder.value.map(id=>defaultNavItems.find(item=>item.id===id)).filter((item):item is NavItem=>Boolean(item)))
const editingNav=ref(false),draggedNav=ref(''),dropNav=ref('')
function restoreNavOrder(){
 try{
  const saved:unknown=JSON.parse(localStorage.getItem(navStorageKey)||'null')
  if(!Array.isArray(saved))return
  const valid=new Set(defaultNavItems.map(item=>item.id))
  navOrder.value=[...new Set(saved.filter((id):id is string=>typeof id==='string'&&valid.has(id))),...defaultNavItems.map(item=>item.id)].filter((id,index,all)=>all.indexOf(id)===index)
 }catch{/* Ignore unavailable or invalid browser storage. */}
}
function moveNav(id:string,target:number){
 const order=[...navOrder.value],from=order.indexOf(id)
 if(from<0||target<0||target>=order.length||from===target)return
 order.splice(from,1);order.splice(target,0,id);navOrder.value=order
 try{localStorage.setItem(navStorageKey,JSON.stringify(order))}catch{/* Keep the current session's order. */}
}
function startNavDrag(event:DragEvent,id:string){
 if(!editingNav.value){event.preventDefault();return}
 draggedNav.value=id
 if(event.dataTransfer){event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',id)}
}
function overNav(event:DragEvent,id:string){
 if(!editingNav.value||!draggedNav.value||draggedNav.value===id)return
 event.preventDefault();dropNav.value=id
 if(event.dataTransfer)event.dataTransfer.dropEffect='move'
}
function dropOnNav(event:DragEvent,id:string){
 if(!editingNav.value||!draggedNav.value)return
 event.preventDefault();moveNav(draggedNav.value,navOrder.value.indexOf(id));endNavDrag()
}
function endNavDrag(){draggedNav.value='';dropNav.value=''}
function chooseNav(id:string){if(id==='sector-flow')showSector('');else nav(id)}
const sectorGroup=ref('')
function showSector(group:string){sectorGroup.value=group;nav('sector-flow')}
const tab=ref('daily'),search=ref(''),side=ref('all'),page=ref(1),metric=ref<'flow'|'pnl'>('flow'),sort=ref('value')
const minDays=ref<string|number>(''),maxDays=ref<string|number>(''),minPnl=ref<string|number>(''),maxPnl=ref<string|number>(''),minRate=ref<string|number>(''),minSamples=ref<string|number>(''),cycleStatus=ref('all')
const methodDialog=ref<HTMLDialogElement|null>(null),stockDialog=ref<HTMLDialogElement|null>(null),branchDialog=ref<HTMLDialogElement|null>(null)
const detail=ref<Detail|null>(null),detailError=ref(''),detailLoading=ref(false)
const detailDate=ref(''),localMajorBalances=ref<Record<string,MajorStock['major_balance']>>({})
const stockBalance=(symbol:string)=>localMajorBalances.value[d.value?.as_of+':'+symbol]??d.value?.major_balances?.[symbol]
const stockPeriod=(symbol:string)=>stockBalance(symbol)?stockBalance(symbol)!.start+' ～ '+stockBalance(symbol)!.end:'截至 '+d.value?.as_of+' 的近 5 個交易日'
function rememberBalance(symbol:string,_names:string[],balance:MajorStock['major_balance']){localMajorBalances.value[detailDate.value+':'+symbol]=balance}
const catalog=ref<Branch[]>([]),branchSearch=ref(''),adding=ref(false),catalogError=ref('')
const config=ref<Settings>({schedule_enabled:true,schedule_time:'18:30',lookback_days:45})
let poll:number|undefined,toastTimer:number|undefined,loadEpoch=0,polls=0
const running=computed(()=>job.value?.status==='running')
const fmt=(v:number|null|undefined,digits=0)=>v===null||v===undefined?'—':v.toLocaleString('zh-TW',{minimumFractionDigits:digits,maximumFractionDigits:digits})
const syncPercent=computed(()=>{
 const progress=job.value?.progress
 if(!running.value)return 100
 if(!progress)return Math.min(100,(job.value?.done||0)/Math.max(1,job.value?.total||0)*100)
 if(progress?.planned)return Math.min(100,(job.value?.done||0)/Math.max(1,job.value?.total||0)*100)
 const stage=progress?.stages?.[progress.phase]
 return stage?.total?Math.min(100,stage.done/stage.total*100):0
})
const duration=(seconds:number|null|undefined)=>{
 if(seconds===null||seconds===undefined||!Number.isFinite(seconds))return ''
 const minutes=Math.max(1,Math.ceil(seconds/60));if(minutes<60)return `約 ${minutes} 分鐘`
 const hours=Math.floor(minutes/60),rest=minutes%60;return `約 ${hours} 小時${rest?` ${rest} 分鐘`:''}`
}
const syncSummary=computed(()=>{
 const progress=job.value?.progress
 if(!running.value)return ''
 if(!progress)return `舊版更新執行中 · ${fmt(job.value?.done)} / ${fmt(job.value?.total)}；完整工作量會在下次啟動後顯示`
 const base=progress?.planned?`完整計畫 · ${fmt(job.value?.done)} / ${fmt(job.value?.total)}`:`正在建立完整工作量 · 已處理 ${fmt(job.value?.done)}`
 const rate=progress?.rate_per_min?` · ${fmt(progress.rate_per_min,1)} 項/分`:''
 const eta=progress?.planned&&progress.eta_seconds!==null?` · ${duration(progress.eta_seconds)}`:''
 return base+rate+eta
})
const syncStages=computed(()=>Object.values(job.value?.progress?.stages||{}).map(stage=>`${stage.label} ${fmt(stage.done)} / ${stage.total===null?'盤點中':fmt(stage.total)}`).join(' · '))
const syncCurrent=computed(()=>{
 const current=job.value?.progress?.current;if(!current)return ''
 if(current.kind==='history')return `${current.branch||''} · ${current.name||''} ${current.symbol||''} · ${current.start||''}～${current.end||''} · ${fmt(current.index)} / ${fmt(current.branch_total)}`
 if(current.kind==='ranking')return `${current.branch||''} · ${current.date||''} · ${current.name||''}`
 if(current.kind==='market')return `${current.date||''} · ${current.name||''}`
 return [current.branch,current.date,current.name].filter(Boolean).join(' · ')
})
const signed=(v:number|null|undefined,digits=1)=>v===null||v===undefined?'—':(v>0?'+':'')+fmt(v,digits)
const pct=(v:number|null|undefined)=>v===null||v===undefined?'—':signed(v,2)+'%'
const rate=(v:number|null|undefined)=>v===null||v===undefined?'—':fmt(v,1)+'%'
const money=(v:number|null|undefined)=>v===null||v===undefined?'—':Math.abs(v)>=1e8?signed(v/1e8,2)+' 億':signed(v/1e4,1)+' 萬'
const tone=(v:number|null|undefined)=>v===null||v===undefined?'muted':v>0?'positive':v<0?'negative':'muted'
function notify(message:string){toast.value=message;window.clearTimeout(toastTimer);toastTimer=window.setTimeout(()=>toast.value='',5000)}
async function api<T>(path:string,body?:unknown):Promise<T>{
 const response=await localFetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const result=await readJson(response);if(!response.ok)throw new Error(result.error||'無法連接本機服務');return result
}
const params=()=>{const [broker,branch]=selectedBranch.value.split(':');return new URLSearchParams({broker,branch,...(selectedDate.value?{date:selectedDate.value}:{})})}
async function load(){const epoch=++loadEpoch;try{const result=await api<Dashboard>('/api/dashboard?'+params());if(epoch!==loadEpoch)return;d.value=result;job.value=result.job;error.value=''}catch(e){error.value=(e as Error).message}finally{loading.value=false}}
async function startSync(){try{job.value=await api<Job>('/api/sync',{});notify('開始每日快速更新：只抓共用行情與各分點當日榜單。')}catch(e){notify((e as Error).message)}}
async function saveSettings(){try{config.value=await api<Settings>('/api/settings',config.value);if(d.value)d.value.settings=config.value;notify('已儲存本機更新設定')}catch(e){notify((e as Error).message)}}
async function openBranches(){branchDialog.value?.showModal();if(catalog.value.length)return;try{catalog.value=await api<Branch[]>('/api/catalog')}catch(e){catalogError.value=(e as Error).message}}
const matches=computed(()=>catalog.value.filter(b=>(b.name+b.broker+b.branch).includes(branchSearch.value)).slice(0,80))
async function addBranch(b:Branch){adding.value=true;try{await api('/api/branches',b);selectedBranch.value=b.broker+':'+b.branch;selectedDate.value='';branchDialog.value?.close();await load();job.value=await api<Job>('/api/sync',{targets:[{broker:b.broker,branch:b.branch}]});notify(`只更新 ${b.name} 的最新張數與金額榜單。`)}catch(e){notify((e as Error).message)}finally{adding.value=false}}
async function openStock(symbol:string){detail.value=null;detailDate.value=d.value?.as_of||'';detailError.value='';detailLoading.value=true;stockDialog.value?.showModal();try{detail.value=await api<Detail>('/api/stock?'+params()+'&symbol='+encodeURIComponent(symbol))}catch(e){detailError.value=(e as Error).message}finally{detailLoading.value=false}}
const netValue=computed(()=>{const rows=d.value?.daily||[];return rows.length&&rows.every(r=>r.net_value!==null)?rows.reduce((s,r)=>s+(r.net_value||0),0):null})
const buyCount=computed(()=>d.value?.daily.filter(r=>r.net>0).length||0)
const sellCount=computed(()=>d.value?.daily.filter(r=>r.net<0).length||0)
const chartPoints=computed(()=>d.value?.series.map(p=>({date:p.date,value:metric.value==='flow'?p.cumulative_net:p.realized_pnl,secondary:metric.value==='flow'?undefined:p.unrealized_pnl}))||[])
const chartLast=computed(()=>chartPoints.value.at(-1))
const pnlKey=computed(()=>tab.value==='stock_stats'?'unrealized_pnl':tab.value==='cycles'?'unrealized_pnl':'pnl')
const filterable=computed(()=>['positions','cycles','stock_stats'].includes(tab.value))
const sortOptions=computed(()=>tab.value==='positions'?[['value','部位市值'],['holding_days','持有天數'],['pnl','未實現損益'],['return_pct','未實現報酬'],['win_rate','個股勝率']]:tab.value==='cycles'?[['start_date','建倉日期'],['holding_days','週期天數'],['pnl','整輪損益'],['unrealized_pnl','未實現損益'],['return_pct','整輪報酬']]:[['win_rate','個股勝率'],['closed_count','清倉樣本數'],['closed_pnl','清倉損益'],['holding_days','持有天數'],['unrealized_pnl','未實現損益'],['hit_rate','10 日命中率']])
const direction=ref('desc')
const patternTab=computed(()=>['streak_buys','fresh_buys'].includes(tab.value))
const minBuy=ref<string|number>(100)
const patternMin=computed(()=>minBuy.value===''?0:Math.max(0,Number(minBuy.value)||0))
function clearFilters(){minDays.value='';maxDays.value='';minPnl.value='';maxPnl.value='';minRate.value='';minSamples.value='';cycleStatus.value='all'}
const rows=computed(()=>{
 if(!d.value)return []
 const group:(Flow|Position|Closed|Signal|Cycle|StockStats|BuyPattern)[]=tab.value==='streak_buys'?(d.value.streak_buys||[]):tab.value==='fresh_buys'?(d.value.fresh_buys||[]):tab.value==='daily'?d.value.daily:tab.value==='positions'?d.value.positions:tab.value==='closed'?d.value.closed:tab.value==='cycles'?d.value.cycles:tab.value==='stock_stats'?d.value.stock_stats:d.value.signals
 const options:TableFilters={search:search.value}
 if(patternTab.value){options.sortKey=sort.value;options.descending=direction.value==='desc'}
 if(tab.value==='daily'){options.sortKey=sort.value==='lots'?'net':'net_value';options.absolute=true}
 if(filterable.value)Object.assign(options,{minDays:minDays.value,maxDays:maxDays.value,minPnl:minPnl.value,maxPnl:maxPnl.value,pnlKey:pnlKey.value,sortKey:sort.value,descending:direction.value==='desc'})
 if(tab.value==='cycles')options.status=cycleStatus.value
 if(tab.value==='stock_stats')Object.assign(options,{minRate:minRate.value,minSamples:minSamples.value})
 return selectRows(group,options).filter(r=>tab.value!=='fresh_buys'||('buy'in r&&r.buy>=patternMin.value)).filter(r=>tab.value!=='daily'||side.value==='all'||(side.value==='buy'?('net'in r&&r.net>0):('net'in r&&r.net<0)))
})
const winFilterSummary=computed(()=>{
 const parts:string[]=[]
 if(minRate.value!==''&&Number.isFinite(Number(minRate.value)))parts.push(`勝率 ≥ ${fmt(Number(minRate.value),1)}%`)
 if(minSamples.value!==''&&Number.isFinite(Number(minSamples.value)))parts.push(`可評分清倉 ≥ ${fmt(Number(minSamples.value))} 次`)
 return parts.join(' · ')||'未限制最低勝率與清倉次數'
})
const visibleRows=computed(()=>rows.value.slice((page.value-1)*15,page.value*15) as any[])
const pages=computed(()=>Math.max(1,Math.ceil(rows.value.length/15)))
const csvUrl=computed(()=>'/api/export?'+params()+'&table='+tab.value)
const sourceUrl=computed(()=>{const [a,b]=selectedBranch.value.split(':');return 'https://concords.moneydj.com/z/zg/zgb/zgb0.djhtm?'+new URLSearchParams({a,b,c:'E',...(d.value?.as_of?{e:d.value.as_of,f:d.value.as_of}:{d:'1'})})})
watch([tab,search,side,sort,direction,minBuy,minDays,maxDays,minPnl,maxPnl,minRate,minSamples,cycleStatus],()=>page.value=1)
watch(tab,()=>{clearFilters();sort.value=tab.value==='streak_buys'?'streak':tab.value==='fresh_buys'?'buy_value':tab.value==='cycles'?'start_date':tab.value==='stock_stats'?'win_rate':'value';direction.value='desc'})
watch(selectedBranch,()=>{selectedDate.value='';page.value=1;void load()})
watch(selectedDate,()=>{page.value=1;void load()})
watch(rows,()=>{page.value=Math.min(page.value,pages.value)})
function nav(next:string){view.value=next;if(next==='performance')tab.value='positions';else if(next==='overview')tab.value='daily'}
const lifecycle=new AbortController()
onMounted(async()=>{
 restoreNavOrder()
 await load();if(d.value)config.value={...d.value.settings}
 poll=window.setInterval(async()=>{try{const previous=job.value?.status;job.value=await api<Job|null>('/api/status');polls++;if((previous==='running'&&job.value?.status!=='running')||(running.value&&polls%4===0)){await load()}}catch{/* Main view preserves last successful snapshot. */}},2500)
 const context=(document as any).modelContext
 if(context?.registerTool){try{await context.registerTool({name:'read_branch_monitor',description:'Read the currently displayed local branch monitoring statistics.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({branch:d.value?.branch,date:d.value?.as_of,stats:d.value?.stats,coverage:d.value?.quality})},{signal:lifecycle.signal});await context.registerTool({name:'select_monitor_date',description:'Switch the visible monitor to an already downloaded date.',inputSchema:{type:'object',properties:{date:{type:'string'}},required:['date'],additionalProperties:false},annotations:{readOnlyHint:false},execute:async(input:{date:string})=>{if(!d.value?.available_dates.includes(input.date))throw new Error('Date is not available');selectedDate.value=input.date;await load();return {date:d.value?.as_of}}},{signal:lifecycle.signal})}catch{/* Optional browser standard. */}}
})
onUnmounted(()=>{window.clearInterval(poll);window.clearTimeout(toastTimer);lifecycle.abort()})
</script>

<template>
 <div class="app-shell">
  <aside class="sidebar">
   <a class="brand" href="/" aria-label="分點觀測站首頁" @click.prevent="nav('home')"><div class="brand-mark"><i></i><i></i><i></i></div><div>分點觀測站<small>{{ cloudRuntime ? '雲端資料 · localhost:3001' : 'BRANCH MONITOR' }}</small></div></a>
   <div class="workspace-label"><span class="workspace-name">WORKSPACE</span><button class="nav-edit-button" :aria-pressed="editingNav" @click="editingNav=!editingNav;endNavDrag()">{{editingNav?'完成':'排序'}}</button></div>
   <nav aria-label="主選單" :class="{'nav-editing':editingNav}">
    <div v-for="(item,index) in navItems" :key="item.id" class="nav-row" :class="{dragging:draggedNav===item.id,'drop-target':dropNav===item.id}" @dragover="overNav($event,item.id)" @drop="dropOnNav($event,item.id)">
     <button class="nav-link" :class="{active:view===item.id}" :title="item.label" @click="chooseNav(item.id)"><Icon :name="item.icon"/>{{item.label}}<span v-if="item.id==='home'" class="nav-indicator"></span></button>
     <div v-if="editingNav" class="nav-sort-controls">
      <button class="nav-move" :disabled="index===0" :aria-label="`${item.label}上移`" @click="moveNav(item.id,index-1)">↑</button>
      <button class="nav-move" :disabled="index===navItems.length-1" :aria-label="`${item.label}下移`" @click="moveNav(item.id,index+1)">↓</button>
      <span class="nav-drag-handle" draggable="true" :aria-label="`拖曳排列${item.label}`" @dragstart="startNavDrag($event,item.id)" @dragend="endNavDrag">⋮⋮</span>
     </div>
    </div>
   </nav>
   <div class="sidebar-label">追蹤分點 · {{d?.branches.length||0}} 個 <button @click="openBranches" aria-label="新增分點"><Icon name="plus" :size="17"/></button></div>
   <div class="tracked-branch-list" aria-label="指定追蹤分點"><button v-for="b in d?.branches||[{broker:'9300',branch:'9339',name:'華南永昌－竹北'}]" :key="b.broker+b.branch" class="branch-nav" :class="{selected:selectedBranch===b.broker+':'+b.branch}" @click="selectedBranch=b.broker+':'+b.branch;nav('overview')"><span class="branch-dot"></span><span>{{b.name}}<small>{{b.broker}} / {{b.branch}}</small></span></button></div>
   <div class="sidebar-bottom"><div class="local-state"><span></span>資料儲存在這台電腦</div><p>盤後追蹤，累積每一天的線索。</p><button @click="methodDialog?.showModal()"><Icon name="info" :size="16"/>資料與計算方式<Icon name="external" :size="13"/></button><div class="version">LOCAL EDITION <span>v1.0</span></div></div>
  </aside>
  <main>
   <div class="topbar"><div>監控中心 <span>/</span> <strong>{{view==='margin-statistics'?'融資維持率統計':view==='monthly-statistics'?'月份漲跌統計':view==='alpha-beta'?'Alpha／Beta 排行':view==='home'?'首頁':view==='v6-weather'?'V6 股災氣象球':view==='block-trades'?'每日鉅額交易追蹤':view==='warrants'?'權證分析':view==='active-etf'?'主動 ETF 動向':view==='insider-changes'?'內部人持股異動':view==='dividend-yields'?'殖利率排行':view==='etf-plan'?'ETF 存股規劃':view==='daily-follow'?'每日跟單':view==='sector-flow'?'族群流向':view==='broker-focus'?'券商關注':view==='market'?'市場觀測':view==='settings'?'資料與設定':view==='performance'?'績效分析':view==='main-watch'?'主力關注':view==='etf-rs'?'台股 ETF RS':view==='rs'?'RS 排行':view==='leaders'?'券商風雲榜':'分點總覽'}}</strong></div><div class="top-status"><span class="status-dot"></span>本機運行中 <span class="timezone">Asia / Taipei</span></div></div>
   <div class="main-content">
    <header v-if="!['margin-statistics','monthly-statistics','alpha-beta','home','main-watch','rs','etf-rs','leaders','market','broker-focus','daily-follow','v6-weather','sector-flow','block-trades','warrants','active-etf','insider-changes','dividend-yields','etf-plan'].includes(view)" class="page-header"><div><div class="eyebrow">{{view==='settings'?'DATA MANAGEMENT':'BROKER INTELLIGENCE'}}</div><h1>{{view==='settings'?'資料與設定':d?.branch.name||'華南永昌－竹北'}} <span v-if="view!=='settings'" class="id-badge">{{d?.branch.branch||'9339'}}</span></h1><p>{{view==='settings'?'管理本機資料來源與每日更新時間。':d?.first_date?`觀察期間 ${d.first_date} — ${d.as_of} · ${d.dates.length} 個交易日`:'從每日進出，觀察分點的資金動向。'}}</p></div><div class="header-actions"><label v-if="view!=='settings'" class="date-control"><Icon name="clock" :size="17"/><select v-model="selectedDate" aria-label="統計日期"><option value="">最新盤後{{d?.as_of?' · '+d.as_of.slice(5):''}}</option><option v-for="day in d?.available_dates" :key="day" :value="day">{{day}}</option></select></label><button class="primary" :disabled="running" @click="startSync"><Icon name="refresh" :class="{spinning:running}" :size="18"/>{{running?'資料同步中':'更新資料'}}</button></div></header>
    <div v-if="!['margin-statistics','monthly-statistics','alpha-beta','home','main-watch','rs','etf-rs','leaders','market','broker-focus','daily-follow','v6-weather','sector-flow','block-trades','warrants','active-etf','insider-changes','dividend-yields','etf-plan'].includes(view)" class="scope-control"><label>追蹤分點<select v-model="selectedBranch" aria-label="切換指定追蹤分點"><option v-for="b in d?.branches" :key="b.broker+b.branch" :value="b.broker+':'+b.branch">{{b.name}}</option></select></label><span>僅同步名單中的 {{d?.branches.length||0}} 個分點；其他分點待新增後再抓取。</span></div>
    <div v-if="error" class="error-banner" role="alert"><Icon name="info"/>{{error}}<button @click="load">重新連線</button></div>
    <div v-if="job && view!=='home'" class="sync-status" :class="{'has-error':['failed','partial'].includes(job.status)}"><Icon :name="running?'refresh':job.status==='complete'?'check':'info'" :size="16" :class="{spinning:running}"/><div class="sync-copy"><span>{{job.message}}</span><small v-if="running&&syncCurrent">目前：{{syncCurrent}}</small><small v-if="running&&syncStages" class="sync-stages">{{syncStages}}</small></div><small v-if="running" class="sync-summary">{{syncSummary}}</small><small v-else>目前未執行 · 結束於 {{job.finished_at?.slice(0,16).replace('T',' ')}}</small><div v-if="running" class="sync-track"><i :style="{width:syncPercent+'%'}"></i></div></div>
    <MarginStatistics v-if="view==='margin-statistics'"/><MonthlyStatistics v-else-if="view==='monthly-statistics'"/><AlphaBeta v-else-if="view==='alpha-beta'"/><HomeDashboard v-else-if="view==='home'"/><V6Weather v-else-if="view==='v6-weather'"/><BlockTrades v-else-if="view==='block-trades'"/><WarrantAnalysis v-else-if="view==='warrants'"/><ActiveEtf v-else-if="view==='active-etf'"/><InsiderChanges v-else-if="view==='insider-changes'"/><DividendYields v-else-if="view==='dividend-yields'"/><StrategyResearch v-else-if="view==='etf-plan'" key="etf-plan"/><DailyFollow v-else-if="view==='daily-follow'"/><SectorFlow v-else-if="view==='sector-flow'" :group="sectorGroup"/><BrokerFocus v-else-if="view==='broker-focus'" @rs="nav('rs')" @sector="showSector"/><MarketMonitor v-else-if="view==='market'"/><BrokerLeaders v-else-if="view==='leaders'" :scope-key="JSON.stringify(d?.branches.map(b=>[b.broker,b.branch,b.name]))"/><RsRanking v-else-if="view==='rs'" key="stock-rs"/><RsRanking v-else-if="view==='etf-rs'" key="etf-rs" :etf="true"/><MainWatch v-else-if="view==='main-watch'" :branch-sync-running="running" @sync="startSync"/>
    <template v-else-if="view!=='settings'">
     <div class="model-notice"><span class="model-tag">觀測模型</span><span>每日張數榜直接保存買賣，完整個股歷史由獨立修復流程補齊；推估部位與報酬不代表分點實際持股。</span><button @click="methodDialog?.showModal()">了解口徑 <Icon name="arrow" :size="15"/></button></div>
     <section class="metrics" aria-label="主要統計">
      <article class="metric-card"><div class="metric-label">當日淨買超估值<Icon name="pulse" :size="18"/></div><div class="metric-value" :class="tone(netValue)">{{money(netValue)}}</div><div class="metric-foot"><span class="buy-dot"></span>買超 {{buyCount}} 檔 <span class="divider">/</span><span class="sell-dot"></span>賣超 {{sellCount}} 檔</div><small>已觀察股票 · 收盤價估算</small></article>
      <article class="metric-card"><div class="metric-label">模擬投入成本報酬<Icon name="wallet" :size="18"/></div><div class="metric-value" :class="tone(d?.stats.return_pct)">{{pct(d?.stats.return_pct)}}</div><div class="metric-foot">{{d?.stats.pnl===null?'已知部分損益':'估計損益'}} <strong :class="tone(d?.stats.pnl??d?.stats.known_pnl)">{{money(d?.stats.pnl??d?.stats.known_pnl)}}</strong></div><small>{{d?.stats.unpriced_positions?`${d.stats.unpriced_positions} 檔缺價，總報酬未評分`:'累計淨買入成本為分母 · 未扣費稅'}}</small></article>
      <article class="metric-card"><div class="metric-label">已配對賣出勝率<Icon name="chart" :size="18"/></div><div class="metric-value">{{rate(d?.stats.win_rate)}}</div><div class="metric-foot"><strong>{{fmt(d?.stats.wins)}}</strong> 次獲利 <span class="divider">/</span>{{fmt(d?.stats.closed_count)}} 次完整事件</div><small>同一股票同日賣出，計為一次</small></article>
      <article class="metric-card"><div class="metric-label">完整清倉勝率<Icon name="chart" :size="18"/></div><div class="metric-value">{{rate(d?.cycle_stats.win_rate)}}</div><div class="metric-foot"><strong>{{fmt(d?.cycle_stats.wins)}}</strong> 次獲利 / {{fmt(d?.cycle_stats.closed_count)}} 次可評分清倉</div><small>{{fmt(d?.cycle_stats.open_count)}} 輪持有中 · {{fmt(d?.cycle_stats.unscored_count)}} 輪資料不足</small></article>
      <article class="metric-card accent-card"><div class="metric-label">10 日超額報酬命中率<Icon name="star" :size="18"/></div><div class="metric-value">{{rate(d?.hit_stats.rate)}}</div><div class="metric-foot">{{fmt(d?.hit_stats.scored)}} 個已評分訊號 <span class="divider">·</span>{{fmt(d?.hit_stats.pending)}} 個待成熟</div><small>近 30 交易日買超訊號 · 相對加權指數</small></article>
     </section>
     <div class="overview-grid">
      <section class="panel flow-panel"><div class="panel-heading"><div><h2>{{metric==='flow'?'累計淨流量':'已實現與未實現損益'}}</h2><p>{{metric==='flow'?'觀察期間買賣超張數累計':'已知交易以 FIFO 配對，金額未扣費稅與股息'}}</p></div><div class="segmented"><button :class="{selected:metric==='flow'}" @click="metric='flow'">淨流量</button><button :class="{selected:metric==='pnl'}" @click="metric='pnl'">損益</button></div></div><div class="chart-summary" :class="{'pnl-summary':metric==='pnl'}"><template v-if="metric==='flow'"><strong>{{signed(chartLast?.value,0)}}</strong><span>張</span><div class="chart-legend"><i></i>{{d?.branch.name||'華南永昌－竹北'}}</div></template><template v-else><div><small><i class="realized-line"></i>累計已實現</small><strong :class="tone(chartLast?.value)">{{money(chartLast?.value)}}</strong></div><div><small><i class="unrealized-line"></i>當日未實現</small><strong :class="tone(chartLast?.secondary)">{{money(chartLast?.secondary)}}</strong></div></template></div><TrendChart :points="chartPoints" :unit="metric==='flow'?'張':'元'" :primary-label="metric==='flow'?(d?.branch.name||'分點'):'累計已實現'" :secondary-label="metric==='pnl'?'當日未實現':undefined"/><div class="chart-foot"><Icon name="info" :size="14"/>{{metric==='flow'?'預設顯示最近 40 個交易日；拖動下方日期軸可查看完整期間。累計淨流量不等於持股張數。':'曲線加總成本及行情已知的部分；期初無法配對的賣出不計損益，後續完整買賣仍納入已實現損益。'}}</div></section>
      <section class="panel watch-panel"><div class="panel-heading"><div><h2><Icon name="star" :size="18"/>值得觀察</h2><p>當日買超 · 近 5 日淨流入估值排序</p></div><span class="count-badge">{{d?.watchlist.length||0}}</span></div><div v-if="!d?.watchlist.length" class="watch-empty"><Icon name="search" :size="27"/><strong>{{running?'正在整理個股線索':'等待符合條件的個股'}}</strong><p>個股明細回補完成後，顯示買超且近五日累計淨流入的股票。</p></div><button v-for="(stock,i) in d?.watchlist.slice(0,4)" :key="stock.symbol" class="watch-stock" @click="openStock(stock.symbol)"><span class="rank">{{String(i+1).padStart(2,'0')}}</span><div class="watch-info"><strong>{{stock.name}} <small>{{stock.symbol}}</small></strong><p>{{stock.streak>=2?`連續 ${stock.streak} 日買超`:'當日買超'}} <span>·</span>{{stock.history_verified?'歷史已回補':'明細待補'}}</p></div><div class="watch-value"><strong class="positive">+{{fmt(stock.recent_net)}} <small>張</small></strong><span>近 5 日</span></div></button><div class="watch-footer">觀察線索，非買進建議 <Icon name="arrow" :size="15"/></div></section>
     </div>
     <section class="panel table-panel" id="data-table">
      <div class="table-top"><div class="tabs" role="tablist" aria-label="分點資料檢視"><button v-for="item in [{id:'daily',name:'每日進出',count:d?.daily.length},{id:'streak_buys',name:'連續買超',count:d?.streak_buys?.length},{id:'fresh_buys',name:'20 日未買後買入',count:d?.fresh_buys?.length},{id:'positions',name:'推估部位',count:d?.positions.length},{id:'cycles',name:'建倉到清倉',count:d?.cycles.length},{id:'stock_stats',name:'個股勝率',count:d?.stock_stats.length},{id:'closed',name:'賣出紀錄',count:d?.closed.length},{id:'signals',name:'10 日明細',count:d?.signals.length}]" :key="item.id" role="tab" :aria-selected="tab===item.id" :class="{active:tab===item.id}" @click="tab=item.id">{{item.name}}<span>{{fmt(tab===item.id?rows.length:item.count)}}</span></button></div><a class="export" :href="csvUrl" :download="`分點-${tab}-${d?.as_of||'data'}.csv`" title="匯出此分頁全部資料"><Icon name="download" :size="16"/>匯出全部 CSV</a></div>
      <div class="table-controls">
       <div class="search-field"><Icon name="search" :size="17"/><input v-model="search" aria-label="搜尋個股" placeholder="搜尋股票代碼或名稱"/><button v-if="search" @click="search=''" aria-label="清除搜尋"><Icon name="close" :size="15"/></button></div>
       <div v-if="tab==='daily'" class="filter-group"><select v-model="side" aria-label="買賣方向"><option value="all">全部方向</option><option value="buy">只看買超</option><option value="sell">只看賣超</option></select><select v-model="sort" aria-label="每日進出排序"><option value="value">依淨額估值排序</option><option value="lots">依淨張數排序</option></select></div>
       <div v-if="patternTab" class="filter-group pattern-controls"><label v-if="tab==='fresh_buys'">當日買進至少 <input v-model="minBuy" type="number" min="0" step="1" aria-label="爆買最低買進張數"/> 張</label><select v-model="sort" aria-label="買入訊號排序"><option v-if="tab==='streak_buys'" value="streak">連續買超天數</option><option value="buy_value">當日買入金額估算</option><option value="buy">當日買進張數</option><option value="net">當日淨買超</option><option v-if="tab==='streak_buys'" value="streak_net">連買期間累計買超</option></select><select v-model="direction" aria-label="買入訊號排序方向"><option value="desc">由大到小</option><option value="asc">由小到大</option></select></div>
       <div v-if="filterable" class="filter-group"><select v-if="tab==='cycles'" v-model="cycleStatus" aria-label="清倉狀態"><option value="all">全部週期</option><option value="closed">已清倉</option><option value="open">持有中</option></select><label class="inline-label">排序<select v-model="sort" aria-label="排序欄位"><option v-for="option in sortOptions" :key="option[0]" :value="option[0]">{{option[1]}}</option></select></label><select v-model="direction" aria-label="排序方向"><option value="desc">由大到小／由新到舊</option><option value="asc">由小到大／由舊到新</option></select></div>
       <p class="table-description full-description">{{tab==='streak_buys'?'截至所選日期，連續至少 2 個交易日淨買超；假日不打斷，零買超或淨賣超即中斷。':tab==='fresh_buys'?'前 20 個交易日買進張數皆為 0，當日轉淨買超；爆買預設買進至少 100 張，可調整門檻。買入金額以收盤價估算。':tab==='positions'?'持有天數採剩餘部位加權交易日；個股勝率採完整清倉輪數。':tab==='cycles'?'推估部位由 0 → 正 → 0 為一輪，加碼、分批賣出合併結算；週期天數從首次建倉日起算。':tab==='stock_stats'?'本分點對每檔股票的勝率＝獲利清倉輪數 ÷ 可評分清倉輪數。持有中及資料不足不計入，損平計入分母。':tab==='closed'?'每個賣出日為一次事件，可能只賣出部分部位；完整一輪請查看「建倉到清倉」。':tab==='signals'?'訊號日收盤 → 10 交易日後收盤；超額報酬大於 0 為命中。':'個股勝率顯示本分點對此股的歷史清倉結果。'}}</p>
       <div v-if="filterable" class="range-filters">
        <fieldset><legend>{{tab==='cycles'?'週期天數':'持有天數'}}（交易日）</legend><input v-model="minDays" type="number" min="0" step="any" aria-label="最少持有天數" placeholder="最少"/><span>至</span><input v-model="maxDays" type="number" min="0" step="any" aria-label="最多持有天數" placeholder="最多"/></fieldset>
        <fieldset><legend>未實現損益（元）</legend><input v-model="minPnl" type="number" step="any" aria-label="最低未實現損益" placeholder="最低，可負值"/><span>至</span><input v-model="maxPnl" type="number" step="any" aria-label="最高未實現損益" placeholder="最高，可負值"/></fieldset>
        <fieldset v-if="tab==='stock_stats'" class="win-filter-fields"><legend>勝率與樣本 · 輸入即套用</legend><label><span>最低勝率（%）</span><input v-model="minRate" type="number" min="0" max="100" step="any" aria-label="最低個股勝率" placeholder="例如 60"/></label><label><span>至少可評分清倉（次）</span><input v-model="minSamples" type="number" min="0" step="1" aria-label="最少清倉樣本數" placeholder="例如 3"/></label></fieldset>
        <button class="clear-filters" @click="clearFilters">清除篩選</button><small>有填篩選值時，缺值資料不列入。</small>
       </div>
      </div>
      <div v-if="patternTab" class="pattern-status" role="status">截至 {{d?.as_of}} · 符合 {{rows.length}} 檔<span v-if="!d?.buy_pattern_status?.calendar_ready"> · 交易日曆不足，暫不判定。</span><template v-else-if="tab==='fresh_buys'"><span v-if="(d?.buy_pattern_status?.prior_sessions||0)<20"> · 前期僅有 {{d?.buy_pattern_status?.prior_sessions}} 個交易日，需滿 20 日。</span><span v-else> · 已確認前 20 日明細；{{d?.buy_pattern_status?.unverified_candidates||0}} 檔當日買超股因歷史待補未判定。 · 目前門檻 {{patternMin}} 張</span></template></div>
      <div v-if="tab==='stock_stats'" class="filter-result" role="status" aria-live="polite" aria-atomic="true"><div><span class="filter-result-label">篩選結果</span><strong>{{fmt(rows.length)}} <small>/ {{fmt(d?.stock_stats.length)}} 檔</small></strong></div><div><p>{{winFilterSummary}}</p><small>{{rows.length?'下方顯示符合條件的股票；可翻頁查看全部結果。':'沒有符合全部條件的股票，請降低門檻或清除篩選。'}}</small></div></div>
      <div class="table-scroll"><table><thead><tr><th class="stock-cell">股票</th>
       <template v-if="patternTab"><th>{{tab==='streak_buys'?'連買天數／起日':'未買入區間'}}</th><th>當日買進 <small>張</small></th><th>當日賣出 <small>張</small></th><th>當日淨買超</th><th>收盤價</th><th>買入金額估算</th><th>{{tab==='streak_buys'?'累計淨買超／估值':'確認天數'}}</th></template>
       <template v-if="tab==='daily'"><th>買進 <small>張</small></th><th>賣出 <small>張</small></th><th>買賣超 <small>張</small></th><th>收盤價</th><th>淨流量估值</th><th>占成交量</th><th>個股勝率</th></template>
       <template v-if="tab==='positions'"><th>推估部位 <small>張</small></th><th>平均成本</th><th>收盤價</th><th>持有天數</th><th>未實現損益</th><th>報酬</th><th>個股勝率</th></template>
       <template v-if="tab==='cycles'"><th>建倉 → 清倉</th><th>累計買入 / 剩餘 <small>張</small></th><th>週期天數</th><th>已實現損益</th><th>未實現損益</th><th>整輪損益</th><th>整輪報酬</th><th>狀態</th></template>
       <template v-if="tab==='stock_stats'"><th>個股勝率</th><th>獲利 / 可評分清倉</th><th>虧損 / 損平</th><th>資料不足 <small>輪</small></th><th>完整清倉損益</th><th>持有天數</th><th>未實現損益</th><th>10 日超額命中率</th></template>
       <template v-if="tab==='closed'"><th>賣出日</th><th>賣超 <small>張</small></th><th>已配對 <small>張</small></th><th>持有天數</th><th>估計損益</th><th>狀態</th></template>
       <template v-if="tab==='signals'"><th>買超日</th><th>買超 <small>張</small></th><th>10 日到期日</th><th>價格報酬</th><th>超額報酬</th><th>結果</th></template>
      </tr></thead><tbody>
       <tr v-for="(row,i) in visibleRows" :key="row.symbol+(row.start_date||row.date||'')"><td class="stock-cell"><button class="stock-button" @click="openStock(row.symbol)"><FlowCircle :buy="stockBalance(row.symbol)?.buy??null" :sell="stockBalance(row.symbol)?.sell??null" :period="stockPeriod(row.symbol)" scope="近五日主力名單互抵"/><span><strong>{{row.name}}</strong><small>{{row.symbol}}</small></span></button></td>
        <template v-if="patternTab"><td><template v-if="tab==='streak_buys'"><strong>{{row.streak_at_least?'至少 ':''}}{{row.streak}} 日</strong><small class="cell-note">{{row.start_date}} 起</small></template><template v-else>{{row.quiet_start}}<small class="cell-note">～ {{row.quiet_end}}</small></template></td><td>{{fmt(row.buy)}}</td><td>{{fmt(row.sell)}}</td><td class="positive">{{signed(row.net,0)}}</td><td>{{fmt(row.close,2)}}</td><td>{{money(row.buy_value)}}</td><td><template v-if="tab==='streak_buys'">{{signed(row.streak_net,0)}} 張<small class="cell-note">{{money(row.streak_value)}}</small></template><template v-else>{{row.quiet_days}} 交易日無買進</template></td></template>
        <template v-if="tab==='daily'"><td>{{fmt(row.buy)}}</td><td>{{fmt(row.sell)}}</td><td><span class="net-pill" :class="tone(row.net)">{{signed(row.net,0)}}</span></td><td>{{fmt(row.close,2)}}</td><td :class="tone(row.net_value)">{{money(row.net_value)}}</td><td>{{rate(row.volume_share)}}</td><td>{{rate(row.win_rate)}}<small class="cell-note">{{row.wins}} / {{row.closed_count}} 次</small></td></template>
        <template v-if="tab==='positions'"><td>{{fmt(row.quantity)}}</td><td>{{fmt(row.avg_cost,2)}}</td><td>{{fmt(row.close,2)}}</td><td>{{fmt(row.holding_days,1)}} 日</td><td :class="tone(row.pnl)">{{money(row.pnl)}}</td><td :class="tone(row.return_pct)">{{pct(row.return_pct)}}</td><td>{{rate(row.win_rate)}}<small class="cell-note">{{row.wins}} / {{row.closed_count}} 次</small></td></template>
        <template v-if="tab==='cycles'"><td>{{row.start_date}}<small class="cell-note">→ {{row.end_date||'持有中'}}</small></td><td>{{fmt(row.bought_lots)}} / {{fmt(row.quantity)}}</td><td>{{fmt(row.holding_days)}} 日</td><td :class="tone(row.realized_pnl)">{{money(row.realized_pnl)}}</td><td :class="tone(row.unrealized_pnl)">{{money(row.unrealized_pnl)}}</td><td :class="tone(row.pnl)">{{money(row.pnl)}}</td><td :class="tone(row.return_pct)">{{pct(row.return_pct)}}</td><td><span class="state-pill" :class="{pending:!!row.reason||row.status==='open'}">{{row.reason?'資料不足':row.status==='open'?'持有中':'已清倉'}}</span><small v-if="row.reason" class="cell-note">{{row.reason}}</small></td></template>
        <template v-if="tab==='stock_stats'"><td><strong>{{rate(row.win_rate)}}</strong></td><td>{{row.wins}} / {{row.closed_count}}</td><td>{{row.losses}} / {{row.breakeven}}</td><td>{{row.unscored_count}}</td><td :class="tone(row.closed_pnl)">{{money(row.closed_pnl)}}</td><td>{{fmt(row.holding_days,1)}}<small v-if="row.quantity===0" class="cell-note">目前無推估部位</small></td><td :class="tone(row.unrealized_pnl)">{{money(row.unrealized_pnl)}}</td><td>{{rate(row.hit_rate)}}<small class="cell-note">{{row.hit_samples}} 個訊號</small></td></template>
        <template v-if="tab==='closed'"><td>{{row.date}}</td><td>{{fmt(row.quantity)}}</td><td>{{fmt(row.matched)}}</td><td>{{fmt(row.holding_days,1)}} 日</td><td :class="tone(row.pnl)">{{money(row.pnl)}}</td><td><span class="state-pill" :class="{pending:!row.complete}">{{row.complete?'可評分':'資料不足'}}</span></td></template>
        <template v-if="tab==='signals'"><td>{{row.date}}</td><td>{{fmt(row.net)}}</td><td>{{row.maturity||'—'}}</td><td :class="tone(row.return_pct)">{{pct(row.return_pct)}}</td><td :class="tone(row.excess)">{{pct(row.excess)}}</td><td><span class="state-pill" :class="{pending:row.status!=='scored',miss:row.status==='scored'&&!row.hit}">{{row.status==='pending'?'待成熟':row.status==='missing'?'資料不足':row.hit?'命中':'未命中'}}</span></td></template>
       </tr>
       <tr v-if="!visibleRows.length"><td :colspan="['cycles','stock_stats'].includes(tab)?9:['daily','positions','streak_buys','fresh_buys'].includes(tab)?8:7" class="empty-row"><Icon name="search" :size="25"/><strong>{{loading?'正在讀取本機資料…':'沒有符合條件的紀錄'}}</strong><p>{{filterable?'可調整篩選條件，或切換其他分點與日期。':running?'資料回補中，完成項目會陸續顯示。':'可搜尋其他股票，或切換分點與日期。'}}</p></td></tr>
      </tbody></table></div>
      <div class="table-bottom"><span>共 {{fmt(rows.length)}} 筆 <span v-if="rows.length">· 顯示 {{(page-1)*15+1}}–{{Math.min(page*15,rows.length)}}</span></span><div class="pagination"><button :disabled="page<=1" @click="page--" aria-label="上一頁"><Icon name="arrow" class="flip" :size="14"/></button><span>{{page}} / {{pages}}</span><button :disabled="page>=pages" @click="page++" aria-label="下一頁"><Icon name="arrow" :size="14"/></button></div></div>
     </section>
     <footer class="page-foot"><span><Icon name="database" :size="14"/>{{fmt(d?.quality.history_count)}} / {{fmt(d?.quality.candidate_count)}} 檔已回補明細 <span>·</span>來源張數經取整，買減賣可能不等於公布淨額。</span><a :href="sourceUrl" target="_blank" rel="noopener noreferrer">查看 MoneyDJ 原始資料 <Icon name="external" :size="14"/></a></footer>
    </template>
    <template v-else>
     <div class="settings-grid"><section class="panel settings-panel"><h2>每日自動更新</h2><p>程式開啟、電腦保持喚醒時，依台北時間執行首頁全部更新，包含融資維持率與月份統計。V3／V6／V10／V11仍在對話中手動更新。</p><label class="setting-row"><span>啟用每日更新<small>週一至週五，每天自動執行一次；來源未齊請手動更新。</small></span><input type="checkbox" v-model="config.schedule_enabled" class="switch"/></label><label class="setting-row"><span>更新時間<small>預設18:00；官方來源未發布時保留舊值並提示手動更新，不自動重試。</small></span><input type="time" v-model="config.schedule_time" min="14:00" max="23:59"/></label><label class="setting-row"><span>歷史修復批次範圍<small>保留給獨立的歷史修復流程，不影響每日更新。</small></span><select v-model.number="config.lookback_days" :disabled="!!config.history_start_date"><option :value="14">近 14 日</option><option :value="45">近 45 日</option><option :value="90">近 90 日</option><option :value="180">近 180 日</option></select></label><label class="setting-row"><span>歷史修復起日<small>保留既有設定；之後由歷史修復頁面使用。</small></span><input type="date" v-model="config.history_start_date" min="2000-01-01" aria-label="歷史修復起日"/></label><button class="primary" @click="saveSettings">儲存設定</button></section>
     <section class="panel settings-panel"><h2>資料涵蓋</h2><div class="quality-stat"><strong>{{fmt(d?.quality.history_count)}}<span> / {{fmt(d?.quality.candidate_count)}}</span></strong><span>檔股票已回補歷史</span></div><p>每日金額、張數兩份榜單的聯集用來發現股票；張數榜直接保存當日買賣，完整個股明細由歷史修復另行補齊。未曾進榜的交易仍可能遺漏。</p><dl class="quality-list"><div><dt>本機交易紀錄</dt><dd>{{fmt(d?.quality.flow_count)}} 筆</dd></div><div><dt>行情抓取失敗</dt><dd>{{fmt(d?.quality.failed_market_days)}} 日</dd></div><div><dt>分點榜單失敗</dt><dd>{{fmt(d?.quality.failed_snapshot_days)}} 日</dd></div><div><dt>期初未知流出</dt><dd>{{fmt(d?.stats.unmatched_lots)}} 張</dd></div><div><dt>無法估價部位</dt><dd>{{fmt(d?.stats.unpriced_positions)}} 檔</dd></div><div><dt>未評分賣出事件</dt><dd>{{fmt(d?.stats.unscored_events)}} 次</dd></div></dl><button class="secondary" @click="methodDialog?.showModal()">閱讀計算方式 <Icon name="arrow" :size="16"/></button></section></div>
     <HistoryRepair/>
     <section class="panel settings-panel log-panel"><h2>最近同步紀錄</h2><p>{{job?.message||'尚未同步'}}</p><div v-if="job?.errors.length" class="error-list"><p v-for="(message,i) in job.errors" :key="i">{{message}}</p></div><div v-else class="success-line"><Icon name="check" :size="17"/>{{running?'目前已處理的項目沒有錯誤。':'沒有待處理錯誤。'}}</div><div class="snapshot-list"><span v-for="snapshot in d?.quality.snapshots" :key="snapshot.date" :class="{failed:snapshot.status==='error'}" :title="snapshot.message||'榜單已取得，股票發現範圍有限'">{{snapshot.date.slice(5)}} <Icon :name="snapshot.status==='error'?'info':'check'" :size="13"/></span></div></section>
    </template>
   </div>
  </main>
  <div v-if="toast" class="toast" role="status"><Icon name="info" :size="18"/>{{toast}}<button @click="toast=''" aria-label="關閉通知"><Icon name="close" :size="16"/></button></div>
 </div>
 <dialog ref="methodDialog" class="modal method-modal"><div class="modal-heading"><div><div class="eyebrow">METHODOLOGY</div><h2>看懂數字的邊界</h2></div><button @click="methodDialog?.close()" aria-label="關閉計算方式"><Icon name="close"/></button></div><div class="method-content"><section><h3>01 / 每日進出與資料範圍</h3><p>來源為 MoneyDJ 分點榜單，每日張數、金額各列買超前 50 與賣超前 50 名。每日快速更新直接保存張數榜的買進、賣出與淨張數；金額榜用於補充股票發現與金額排行。完整個股歷史由獨立修復流程處理。未進榜個股不在本工具的當日觀察範圍，因此合計不等於全分點總額。</p><p>榜單的買、賣、淨額分別取整；一張以 1,000 股估算，使用來源公布淨額。只有個股明細已驗證的期間，省略日期才可視為無紀錄流量；尚未修復的日期會標示資料不足。</p></section><section><h3>02 / 推估部位與持有天數</h3><p>觀察起點庫存設為零；每日淨買超新增批次，淨賣超依先進先出（FIFO）扣除。超過可配對庫存的流出列為「期初未知」，不視為放空。持有天數採剩餘數量加權的市場交易日，買入日為第 0 日。</p><p>分點彙總多位客戶，無法辨識同一投資人的持倉、轉倉或當沖。本頁呈現的是觀察模型。</p></section><section><h3>03 / 模擬投資組合價格報酬</h3><p>以官方當日收盤價代替實際成交價。已實現損益為完整配對批次的價差；未實現損益以評價日收盤估價。投入成本報酬＝（已知已實現＋未實現損益）÷ 累計淨買入成本。</p><p>未扣手續費與交易稅，未含股息、除權息與分割調整；不是時間加權總報酬。期初未知部分不計價，已知批次缺價時總報酬留空。盤後才看見訊號，不能用同日收盤作為實際跟單成交價。</p></section><section><h3>04 / 清倉週期與勝率</h3><p>完整清倉週期：推估部位由 0 轉正開始，加碼與分批賣出仍算同一輪，歸零才結束；之後再買入另開一輪。整輪損益為這一輪所有已實現加未實現損益，除以這一輪累計淨買入成本即為整輪報酬。週期天數為首次建倉至清倉（或評價日）的交易日差。</p><p>個股勝率：本分點對該股票「獲利的完整清倉輪數 ÷ 可評分清倉輪數」。整體完整清倉勝率合併各股的輪數計算，並非各股勝率平均。損平計入分母；尚在持有、歷史缺口、買賣缺價、清倉日含期初未知流出均不評分。每一輪都獨立判定，1 次獲利／1 次清倉也會顯示樣本數。</p><p>賣出勝率：同一股票同一賣出日為一事件，完整配對且毛損益大於 0 為贏；零損益算入分母。含未知期初成本的事件不評分。</p><p>10 日命中率：最近 30 個交易日的每個買超日是一個訊號；股票 t 至 t+10 收盤價格報酬，減去同期加權指數價格報酬，大於 0 為命中。未成熟與缺價分開列示，不能當作輸。此統計為訊號後續表現，並非可成交回測。</p></section><section><h3>05 / 值得觀察的股票</h3><p>限一般股票、個股歷史回補完成、當日買超且最近 5 個交易日淨額為正，依近 5 日淨張數 × 評價日收盤估值排序。連買日數只往前讀取已知日期；此清單用來研究籌碼變化，不預測未來獲利。</p></section><div class="source-links"><a :href="sourceUrl" target="_blank" rel="noopener">MoneyDJ 分點資料 <Icon name="external" :size="14"/></a><a href="https://www.twse.com.tw/zh/trading/historical/mi-index.html" target="_blank" rel="noopener">證交所盤後行情 <Icon name="external" :size="14"/></a><a href="https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html" target="_blank" rel="noopener">櫃買中心 <Icon name="external" :size="14"/></a></div></div></dialog>
 <dialog ref="branchDialog" class="modal branch-modal"><div class="modal-heading"><div><div class="eyebrow">FOLLOW A BRANCH</div><h2>新增追蹤分點</h2></div><button @click="branchDialog?.close()" aria-label="關閉新增分點"><Icon name="close"/></button></div><div class="search-field"><Icon name="search" :size="18"/><input v-model="branchSearch" placeholder="輸入券商、地區或代碼" aria-label="搜尋分點"/></div><p class="muted">新增後只更新這一家的最新榜單；原有分點不會重抓，完整歷史可由修復流程另行補齊。</p><p v-if="catalogError" role="alert">{{catalogError}}</p><p v-else-if="!catalog.length">正在讀取券商分點清單…</p><div class="branch-list"><button v-for="b in matches" :key="b.broker+b.branch" @click="addBranch(b)" :disabled="adding"><span>{{b.name}}<small>{{b.broker}} / {{b.branch}}</small></span><Icon name="plus" :size="17"/></button><p v-if="catalog.length&&!matches.length">沒有符合的分點。</p></div></dialog>
 <dialog ref="stockDialog" class="modal stock-modal"><div class="modal-heading"><div><div class="eyebrow">STOCK OBSERVATION</div><h2>{{detail?.name||'個股觀察'}} <span class="muted">{{detail?.symbol}}</span></h2></div><button @click="stockDialog?.close()" aria-label="關閉個股明細"><Icon name="close"/></button></div><p v-if="detailLoading">正在讀取個股資料…</p><p v-if="detailError" role="alert">{{detailError}}</p><template v-if="detail"><div class="detail-subline">{{d?.branch.name}} <span>·</span>{{d?.as_of}} <a :href="detail.source" target="_blank" rel="noopener">原始明細 <Icon name="external" :size="14"/></a></div><div class="detail-stats"><div>推估部位<strong>{{fmt(detail.position?.quantity||0)}}<small> 張</small></strong></div><div>平均成本<strong>{{fmt(detail.position?.avg_cost,2)}}</strong></div><div>持有天數<strong>{{fmt(detail.position?.holding_days,1)}}<small> 日</small></strong></div><div>未實現報酬<strong :class="tone(detail.position?.return_pct)">{{pct(detail.position?.return_pct)}}</strong></div></div><div class="detail-stats cycle-detail-stats"><div>未實現損益<strong :class="tone(detail.position?.pnl)">{{money(detail.position?.pnl)}}</strong></div><div>本分點對此股勝率<strong>{{rate(detail.stock_stats?.win_rate)}}</strong><small>{{fmt(detail.stock_stats?.wins)}} 勝 / {{fmt(detail.stock_stats?.closed_count)}} 次可評分清倉</small></div><div>10 日超額命中率<strong>{{rate(detail.stock_stats?.hit_rate)}}</strong><small>{{fmt(detail.stock_stats?.hit_samples)}} 個已評分訊號</small></div></div>
<TopBrokers v-if="detailDate" :key="detailDate+':'+detail.symbol" :symbol="detail.symbol" :date="detailDate" @loaded="(names,value)=>rememberBalance(detail!.symbol,names,value)"/><div class="detail-cycle-heading"><h3>建倉到清倉</h3><p>從首次淨買超開始，部位歸零結算一輪；加碼及部分賣出合併計算。</p></div>
<div class="table-scroll detail-cycle-table"><table><thead><tr><th>建倉 → 清倉</th><th>累計買入 / 剩餘張數</th><th>週期天數</th><th>已實現損益</th><th>未實現損益</th><th>整輪損益 / 報酬</th><th>狀態</th></tr></thead><tbody><tr v-for="cycle in detail.cycles" :key="cycle.start_date"><td>{{cycle.start_date}}<small class="cell-note">→ {{cycle.end_date||'持有中'}}</small></td><td>{{fmt(cycle.bought_lots)}} / {{fmt(cycle.quantity)}}</td><td>{{fmt(cycle.holding_days)}} 日</td><td :class="tone(cycle.realized_pnl)">{{money(cycle.realized_pnl)}}</td><td :class="tone(cycle.unrealized_pnl)">{{money(cycle.unrealized_pnl)}}</td><td :class="tone(cycle.pnl)">{{money(cycle.pnl)}}<small class="cell-note" :class="tone(cycle.return_pct)">{{pct(cycle.return_pct)}}</small></td><td><span class="state-pill" :class="{pending:!!cycle.reason||cycle.status==='open'}">{{cycle.reason?'資料不足':cycle.status==='open'?'持有中':'已清倉'}}</span><small v-if="cycle.reason" class="cell-note">{{cycle.reason}}</small></td></tr><tr v-if="!detail.cycles.length"><td colspan="7" class="empty-row">觀察期間尚未建立推估部位。</td></tr></tbody></table></div>
<div class="detail-chart-heading"><strong>股價與分點進出</strong><span><i class="buy-dot"></i>紅漲／買超 <i class="sell-dot"></i>綠跌／賣超</span></div><CandleChart :quotes="detail.prices" :flows="detail.flows"/><div class="table-scroll detail-table"><table><thead><tr><th>交易日</th><th>買進張數</th><th>賣出張數</th><th>淨買超</th><th>收盤</th></tr></thead><tbody><tr v-for="r in [...detail.flows].reverse()" :key="r.date"><td>{{r.date}}</td><td>{{fmt(r.buy)}}</td><td>{{fmt(r.sell)}}</td><td :class="tone(r.net)">{{signed(r.net,0)}}</td><td>{{fmt(r.close,2)}}</td></tr></tbody></table></div></template></dialog>
</template>
