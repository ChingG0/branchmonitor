export type SectorLeader={name:string;return_pct:number;quoted:number;members:number;coverage_pct:number}
export type SectorMode='monthly'|'ytd'
export type SectorRanking={leaders:SectorLeader[];status?:'ready'|'no_risers'|'missing'|'stale';date?:string;previous_date?:string|null;eligible_groups?:number;total_groups?:number}
export type MonthClose={sector_ytd?:SectorRanking;sector_leaders?:SectorLeader[];sector_status?:'ready'|'no_risers'|'missing'|'stale';sector_eligible_groups?:number;sector_total_groups?:number;month:string;date:string;close:number;source:string;previous_date?:string|null;previous_close?:number|null;return_pct:number|null;partial:boolean;in_progress?:boolean}
export type MonthYear={year:number;months:(MonthClose|null)[];return_pct:number|null;through:string|null;partial:boolean;months_included:number}
export type MonthMarket={metadata:{label:string;currency:string;sources:{id:string;label:string;url:string;note?:string;retrieved_at?:string}[];archive_check?:{overlap_days:number;mismatches:number}};rows:MonthClose[];years:MonthYear[];as_of:string;first_return_month:string;latest_month:string;latest_close:number;missing_months:string[]}
export type MonthReport={sector_metadata?:{ytd_first_month?:string|null;ytd_last_month?:string|null;dictionary_snapshot?:string;first_month?:string|null;last_month?:string|null;group_count?:number;note?:string};sector_saved_at?:string;version:string;saved_at?:string;markets:Record<string,MonthMarket>;source_errors?:Record<string,string>;job:{status:string;message:string};note?:string;history_note?:string;error?:string}
export function monthStats(rows:MonthClose[],startYear:number){return Array.from({length:12},(_,i)=>{
 const vals=rows.filter(r=>Number(r.month.slice(0,4))>=startYear&&Number(r.month.slice(5))===i+1&&!r.partial&&r.return_pct!=null).map(r=>r.return_pct!).sort((a,b)=>a-b)
 const n=vals.length,up=vals.filter(v=>v>1e-10).length,down=vals.filter(v=>v< -1e-10).length
 return {n,up,down,flat:n-up-down,up_rate:n?up/n*100:null,mean:n?vals.reduce((a,b)=>a+b,0)/n:null,median:n?(n%2?vals[n>>1]!:((vals[n/2-1]||0)+(vals[n/2]||0))/2):null}
})}
export function heatColor(value:number|null|undefined){
 if(value==null||!Number.isFinite(value))return {background:'#f6f8f4',color:'#a1aa9c'}
 if(Math.abs(value)<1e-10)return {background:'#eef2eb',color:'#75846d'}
 if(value<=-10)return {background:'#287b52',color:'#fff'}
 if(value<=-5)return {background:'#83bf99',color:'#164c33'}
 if(value<0)return {background:'#d7eada',color:'#397354'}
 if(value<5)return {background:'#fbe7e7',color:'#a25050'}
 if(value<10)return {background:'#f5bdc5',color:'#883d46'}
 if(value<20)return {background:'#e98696',color:'#642331'}
 if(value<30)return {background:'#cc4f69',color:'#fff'}
 return {background:'#9e203c',color:'#fff'}
}

export type MonthExtreme={value:number;months:string[]}
export type MonthRun={length:number;start:string|null;end:string|null}
export type WindowMetrics={n:number;up:number;down:number;flat:number;up_rate:number|null;down_rate:number|null;flat_rate:number|null;mean:number|null;maximum:MonthExtreme|null;minimum:MonthExtreme|null}
export type MonthWindow=WindowMetrics&{key:string;label:string;from:string|null;through:string|null;expected_months:number;longest_up:MonthRun;longest_down:MonthRun;current:MonthRun&{direction:'up'|'down'|'flat'|'none'}}
const windowDefinitions=[{key:'2',label:'近2年',months:24},{key:'5',label:'近5年',months:60},{key:'10',label:'近10年',months:120},{key:'20',label:'近20年',months:240},{key:'all',label:'全部歷史',months:null}] as const
const usableMonth=(r:MonthClose)=>!r.partial&&r.return_pct!=null&&Number.isFinite(r.return_pct)
function monthNumber(month:string){
 const match=/^(\d{4})-(0[1-9]|1[0-2])$/.exec(month)
 if(!match)throw new Error('月份格式無效')
 return Number(match[1])*12+Number(match[2])-1
}
function monthName(value:number){return `${Math.floor(value/12)}-${String(value%12+1).padStart(2,'0')}`}
function orderedMonths(rows:MonthClose[]){
 const sorted=[...rows].sort((a,b)=>monthNumber(a.month)-monthNumber(b.month))
 for(let i=1;i<sorted.length;i++)if(sorted[i]!.month===sorted[i-1]!.month)throw new Error('月份統計不能重複計入同一月份')
 return sorted
}
function metrics(rows:MonthClose[]):WindowMetrics{
 const valid=rows.filter(usableMonth),n=valid.length,up=valid.filter(r=>r.return_pct!>1e-10).length,down=valid.filter(r=>r.return_pct!< -1e-10).length,flat=n-up-down
 const extreme=(highest:boolean):MonthExtreme|null=>{
  if(!n)return null
  const value=(highest?Math.max:Math.min)(...valid.map(r=>r.return_pct!))
  return {value,months:valid.filter(r=>r.return_pct===value).map(r=>r.month)}
 }
 return {n,up,down,flat,up_rate:n?up/n*100:null,down_rate:n?down/n*100:null,flat_rate:n?flat/n*100:null,mean:n?valid.reduce((sum,r)=>sum+r.return_pct!,0)/n:null,maximum:extreme(true),minimum:extreme(false)}
}
export function latestCompleteMonth(rows:MonthClose[]){return orderedMonths(rows).filter(usableMonth).at(-1)?.month||null}
export function windowStatistics(rows:MonthClose[],through:string|null):MonthWindow[]{
 const ordered=orderedMonths(rows),end=through==null?null:monthNumber(through)
 return windowDefinitions.map(w=>{
  const first=end==null?undefined:ordered.find(r=>monthNumber(r.month)<=end&&usableMonth(r))
  const start=end==null?null:w.months==null?(first?monthNumber(first.month):null):end-w.months+1
  const selected=start==null||end==null?[]:ordered.filter(r=>monthNumber(r.month)>=start&&monthNumber(r.month)<=end)
  let previous:number|null=null,direction:'up'|'down'|'flat'|'none'='none',run:MonthRun={length:0,start:null,end:null}
  let longestUp:MonthRun={length:0,start:null,end:null},longestDown:MonthRun={length:0,start:null,end:null}
  for(const r of selected){
   const ordinal=monthNumber(r.month)
   if(!usableMonth(r)){direction='none';run={length:0,start:null,end:null};previous=ordinal;continue}
   const side=r.return_pct!>1e-10?'up':r.return_pct!< -1e-10?'down':'flat'
   if(side==='flat')run={length:1,start:r.month,end:r.month}
   else run={length:side===direction&&previous===ordinal-1?run.length+1:1,start:side===direction&&previous===ordinal-1?run.start:r.month,end:r.month}
   direction=side;previous=ordinal
   if(side==='up'&&run.length>longestUp.length)longestUp={...run}
   if(side==='down'&&run.length>longestDown.length)longestDown={...run}
  }
  const current=previous===end?{...run,direction}:{length:0,start:null,end:null,direction:'none' as const}
  return {...metrics(selected),key:w.key,label:w.label,from:start==null?null:monthName(start),through,expected_months:start==null||end==null?0:end-start+1,longest_up:longestUp,longest_down:longestDown,current}
 })
}
export function windowMonthlyStats(rows:MonthClose[],window:MonthWindow,category:ElectionYearCategory='all'){
 const selected=rowsInWindow(rows,window).filter(r=>matchesElectionYear(Number(r.month.slice(0,4)),category))
 return Array.from({length:12},(_,i)=>({month:i+1,...metrics(selected.filter(r=>Number(r.month.slice(5))===i+1))}))
}
export type ElectionYearCategory='all'|'taiwan-president'|'us-president'|'us-midterm'
export const electionYearOptions:{key:ElectionYearCategory;label:string;note:string;url?:string;source?:string}[]=[
 {key:'all',label:'全部年份',note:'使用所選年期內所有完整月份。'},
 {key:'taiwan-president',label:'台灣總統大選年',note:'自1996年總統直選起，每4年一屆；統計符合年份的1～12月。',url:'https://web.cec.gov.tw/central/article/19170',source:'中選會'},
 {key:'us-president',label:'美國總統大選年',note:'美國總統選舉每4年舉行；統計該曆年的1～12月，包含選前與選後月份。',url:'https://www.usa.gov/presidential-election-process',source:'USAGov'},
 {key:'us-midterm',label:'美國期中選舉年',note:'總統大選之間的期中選舉年；統計該曆年的1～12月，包含選前與選後月份。',url:'https://www.usa.gov/midterm-elections',source:'USAGov'},
]
export function matchesElectionYear(year:number,category:ElectionYearCategory){
 switch(category){
  case 'all':return true
  case 'taiwan-president':return year>=1996&&year%4===0
  case 'us-president':return year>=1792&&year%4===0
  case 'us-midterm':return year>=1790&&year%4===2
  default:throw new Error('選舉年份類型無效')
 }
}
function rowsInWindow(rows:MonthClose[],window:MonthWindow){
 const start=window.from==null?null:monthNumber(window.from),end=window.through==null?null:monthNumber(window.through)
 return start==null||end==null?[]:orderedMonths(rows).filter(r=>monthNumber(r.month)>=start&&monthNumber(r.month)<=end)
}
export function electionYearsInWindow(rows:MonthClose[],window:MonthWindow,category:ElectionYearCategory='all'){
 const years=new Map<number,number>()
 for(const r of rowsInWindow(rows,window)){
  const year=Number(r.month.slice(0,4))
  if(usableMonth(r)&&matchesElectionYear(year,category))years.set(year,(years.get(year)||0)+1)
 }
 return Array.from(years,([year,n])=>({year,n}))
}


export function sectorRanking(row:MonthClose,mode:SectorMode='monthly'):SectorRanking{
 return mode==='ytd'?(row.sector_ytd||{leaders:[],status:'missing'}):{leaders:row.sector_leaders||[],status:row.sector_status,date:row.date,previous_date:row.previous_date,eligible_groups:row.sector_eligible_groups,total_groups:row.sector_total_groups}
}
export function sectorCaption(row:MonthClose|null|undefined,mode:SectorMode='monthly'){
 if(!row||row.return_pct==null)return ''
 const ranking=sectorRanking(row,mode),names=ranking.leaders.map(s=>s.name)
 if(names.length)return `（${row.partial?'暫：':''}${names.join('、')}）`
 return ranking.status==='no_risers'?'（無上漲族群）':ranking.status==='stale'?'（族群資料待更新）':'（族群資料不足）'
}
export function sectorTooltip(row:MonthClose|null|undefined,mode:SectorMode='monthly'){
 if(!row||row.return_pct==null)return ''
 const ranking=sectorRanking(row,mode)
 const prefix=`自訂族群${mode==='ytd'?'年初至今（YTD）漲幅':'月漲幅'}；${ranking.previous_date||'—'}至${ranking.date||row.date}。依目前分類回看，非當年成員名單。`
 const groups=ranking.total_groups?` 可計算${ranking.eligible_groups||0}／${ranking.total_groups}族群。`:''
 const leaders=ranking.leaders.map((s,i)=>`${i+1}. ${s.name} ${s.return_pct>0?'+':''}${s.return_pct.toFixed(2)}%（${s.quoted}／${s.members}檔）`).join('；')
 return prefix+groups+(leaders?' '+leaders+(ranking.leaders.length<3?'；上漲族群不足3個。':''):ranking.status==='no_risers'?' 可計算族群皆未上漲。':' 這個期間的成員行情不足或截止日期不同，未推估名次。')
}
