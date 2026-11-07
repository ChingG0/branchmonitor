import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const require=createRequire(import.meta.url),cache=new Map()
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')
async function component(file){
 if(cache.has(file))return cache.get(file)
 const text=await readFile(new URL('../frontend/src/'+file,import.meta.url),'utf8')
 let code=compileScript(parse(text).descriptor,{id:file,inlineTemplate:true}).content
 code=code.replace(/from ["']vue["']/g,`from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`)
 const matches=[...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]
 for(const m of matches){const name=m[1];let url
  if(name.endsWith('.vue'))url=await component(name)
  else {let raw=await readFile(new URL('../frontend/src/'+name+'.ts',import.meta.url),'utf8');raw=raw.replace(/import ['"].*\.css['"]/g,'');url=moduleUrl(raw)}
  code=code.replace(m[0],'from '+JSON.stringify(url))
 }
 const url=moduleUrl(code);cache.set(file,url);return url
}
async function html(file,props){return renderToString(createSSRApp((await import(await component(file))).default,props))}
const row={month:'2026-11',date:'2026-11-04',close:100,previous_date:'2026-10-30',previous_close:110,return_pct:-9.090909,partial:true,in_progress:true,source:'test'}
const market={metadata:{label:'台股加權指數',currency:'TWD',sources:[]},rows:[row],years:[{year:2026,months:Array.from({length:12},(_,i)=>i===10?row:null),return_pct:null,through:'2026-11-04',partial:true,months_included:11}],as_of:'2026-11-04',first_return_month:'1967-02',latest_month:'2026-11',latest_close:100,missing_months:[]}
const initial={version:'test',saved_at:'2026-11-04T22:00:00+08:00',markets:{taiex:market,sp500:{...market,metadata:{...market.metadata,label:'S&P 500'}}},job:{status:'idle',message:''}}
test('Taiwan-first month matrix marks incomplete month, missing months and optional comparison',async()=>{
 const s=await html('MonthlyStatistics.vue',{initial})
 for(const text of ['月份漲跌統計','1967-02','2026-11-04','本月迄今','-9.1%','上漲比例','平均漲跌','中位數漲跌','上漲／下跌／持平','只計目前期間已結束','不把缺值當作0%','對照 S&amp;P 500'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/class="sp-row"|NaN|Infinity|checked/)
 assert.equal((s.match(/data-month=/g)||[]).length,12)
})
test('read failure can render without fabricated table',async()=>{const s=await html('MonthlyStatistics.vue',{});assert.ok(s.includes('正在讀取'));assert.doesNotMatch(s,/class="year-matrix"|NaN|Infinity/)})

const math=await import(moduleUrl(await readFile(new URL('../frontend/src/monthlyStatistics.ts',import.meta.url),'utf8')))
const make=(month,value,partial=false)=>({month,date:month+'-28',close:100,source:'test',return_pct:value,partial})
const series=(first,count,fn)=>{const [y,m]=first.split('-').map(Number),start=y*12+m-1;return Array.from({length:count},(_,i)=>{const ordinal=start+i,month=`${Math.floor(ordinal/12)}-${String(ordinal%12+1).padStart(2,'0')}`;return make(month,fn(i,month))})}
test('rolling windows use 24/60/120/240 calendar months ending on last completed Taiwan month',()=>{
 const rs=series('2024-10',25,()=>1);rs.push(make('2026-11',999,true))
 assert.equal(math.latestCompleteMonth(rs),'2026-10')
 const w=math.windowStatistics(rs,'2026-10');assert.deepEqual(w.map(x=>x.key),['2','5','10','20','all'])
 assert.deepEqual(w.slice(0,4).map(x=>x.expected_months),[24,60,120,240])
 assert.equal(w[0].from,'2024-11');assert.equal(w[0].n,24);assert.equal(w[0].mean,1);assert.equal(w[4].n,25)
 assert.equal(w[0].longest_up.length,24);assert.equal(w[0].longest_up.start,'2024-11');assert.equal(w[4].longest_up.length,25)
})
test('missing month does not move window start or get replaced with an older observation',()=>{
 const rs=series('2024-10',25,()=>1).filter(r=>r.month!=='2025-05');rs[0].return_pct=1000
 const w=math.windowStatistics(rs,'2026-10')[0];assert.equal(w.n,23);assert.equal(w.expected_months,24);assert.equal(w.from,'2024-11');assert.equal(w.mean,1);assert.equal(w.maximum.value,1)
})
test('up down flat share the same denominator and no empty probability is fabricated',()=>{
 const w=math.windowStatistics([make('2026-08',10),make('2026-09',-10),make('2026-10',0)],'2026-10')[0]
 assert.equal(w.n,3);assert.equal(w.up,1);assert.equal(w.down,1);assert.equal(w.flat,1);assert.equal(w.mean,0);assert.ok(Math.abs(w.up_rate+w.down_rate+w.flat_rate-100)<1e-10)
 const e=math.windowStatistics([make('2026-10',null),make('2026-11',5,true)],null)[0]
 assert.equal(e.n,0);assert.equal(e.up_rate,null);assert.equal(e.maximum,null);assert.equal(e.current.direction,'none')
})
test('streaks cross year boundaries, stop at zero or missing months, and retain earliest tied maximum',()=>{
 const rs=[make('2026-07',-1),make('2025-11',1),make('2025-12',2),make('2026-01',3),make('2026-02',0),make('2026-03',-1),make('2026-04',-2),make('2026-06',-3)]
 const w=math.windowStatistics(rs,'2026-07')[0]
 assert.deepEqual(w.longest_up,{length:3,start:'2025-11',end:'2026-01'});assert.deepEqual(w.longest_down,{length:2,start:'2026-03',end:'2026-04'})
 assert.deepEqual(w.current,{length:2,start:'2026-06',end:'2026-07',direction:'down'})
})
test('incomplete or invalid-return month breaks streak; absent endpoint is not replaced by an earlier streak',()=>{
 for(const missing of [make('2026-09',-100,true),make('2026-09',null)]){
  const w=math.windowStatistics([make('2026-08',-1),missing,make('2026-10',-1)],'2026-10')[0];assert.equal(w.longest_down.length,1);assert.equal(w.current.length,1)
 }
 const w=math.windowStatistics([make('2026-08',1),make('2026-09',1)],'2026-10')[0];assert.equal(w.current.direction,'none');assert.equal(w.through,'2026-10')
})
test('extremes retain occurrence months and do not force positive or negative signs',()=>{
 const w=math.windowStatistics([make('2025-10',5),make('2026-01',1),make('2026-10',5)],'2026-10')[0]
 assert.deepEqual(w.maximum,{value:5,months:['2025-10','2026-10']});assert.equal(w.minimum.value,1)
 const negative=math.windowStatistics([make('2026-09',-5),make('2026-10',-1)],'2026-10')[0];assert.equal(negative.maximum.value,-1)
})
test('same-month detail uses only that calendar month within selected window',()=>{
 const rs=series('2024-10',25,(i,m)=>m.endsWith('-03')?-3:1);rs[0].return_pct=999
 const w=math.windowStatistics(rs,'2026-10')[0],detail=math.windowMonthlyStats(rs,w)
 assert.equal(detail.length,12);assert.ok(detail.every(x=>x.n===2));assert.equal(detail[2].down_rate,100);assert.equal(detail[2].mean,-3);assert.equal(detail[9].maximum.value,1)
})
test('future outcomes past the specified endpoint do not change historical windows',()=>{
 const rs=series('2024-11',24,()=>1),before=math.windowStatistics(rs,'2026-10')
 assert.deepEqual(math.windowStatistics([...rs,make('2026-11',-99),make('2026-12',999)],'2026-10'),before)
})
test('duplicate and malformed months cannot inflate the frequency sample',()=>{
 assert.throws(()=>math.windowStatistics([make('2026-10',1),make('2026-10',1)],'2026-10'),/重複/)
 assert.throws(()=>math.windowStatistics([make('2026-13',1)],'2026-10'),/月份格式/)
})
test('new block renders five periods and twelve same-month rows without fabricating empty percentages',async()=>{
 const s=await html('MonthlyStatistics.vue',{initial})
 for(const text of ['不同年期的漲跌機率與幅度','近2年','近5年','近10年','近20年','全部歷史','最大單月漲幅','最低單月漲幅','最長連漲','最長連跌','選年期查看1～12月'])assert.ok(s.includes(text),text)
 assert.equal((s.match(/data-lookback="taiex-/g)||[]).length,5);assert.equal((s.match(/data-window-month="taiex-/g)||[]).length,12)
 assert.doesNotMatch(s,/NaN|Infinity/)
})

test('presidential and midterm categories follow verified cycles and Taiwan starts with 1996 direct elections',()=>{
 for(const year of [1990,1992])assert.equal(math.matchesElectionYear(year,'taiwan-president'),false)
 for(const year of [1996,2000,2004,2008,2012,2016,2020,2024])assert.equal(math.matchesElectionYear(year,'taiwan-president'),true)
 assert.equal(math.matchesElectionYear(2026,'taiwan-president'),false)
 for(const year of [1968,1972,2020,2024])assert.equal(math.matchesElectionYear(year,'us-president'),true)
 for(const year of [1970,1986,2022,2026])assert.equal(math.matchesElectionYear(year,'us-midterm'),true)
 assert.equal(math.matchesElectionYear(2024,'us-midterm'),false);assert.equal(math.matchesElectionYear(2025,'us-midterm'),false)
 assert.throws(()=>math.matchesElectionYear(2024,'invalid'),/類型無效/)
})
test('all-history election groups report actual sample years and only complete months',()=>{
 const rows=series('1967-01',718,()=>1);rows.push(make('2026-11',999,true));const window=math.windowStatistics(rows,'2026-10').at(-1)
 const tw=math.electionYearsInWindow(rows,window,'taiwan-president'),us=math.electionYearsInWindow(rows,window,'us-president'),mid=math.electionYearsInWindow(rows,window,'us-midterm')
 assert.deepEqual(tw.map(x=>x.year),[1996,2000,2004,2008,2012,2016,2020,2024]);assert.equal(tw.reduce((s,x)=>s+x.n,0),96)
 assert.equal(us[0].year,1968);assert.equal(us.at(-1).year,2024);assert.equal(us.reduce((s,x)=>s+x.n,0),180)
 assert.equal(mid[0].year,1970);assert.deepEqual(mid.at(-1),{year:2026,n:10});assert.equal(mid.reduce((s,x)=>s+x.n,0),178)
 assert.equal(math.windowMonthlyStats(rows,window,'us-midterm')[10].n,14)
})
test('election filter intersects fixed two-year window rather than extending back to January of election year',()=>{
 const rows=series('2024-01',34,()=>1),window=math.windowStatistics(rows,'2026-10')[0]
 const tw=math.windowMonthlyStats(rows,window,'taiwan-president');assert.deepEqual(math.electionYearsInWindow(rows,window,'taiwan-president'),[{year:2024,n:2}])
 assert.equal(tw[0].n,0);assert.equal(tw[0].up_rate,null);assert.equal(tw[0].maximum,null);assert.equal(tw[10].n,1);assert.equal(tw[11].n,1)
 assert.deepEqual(tw,math.windowMonthlyStats(rows,window,'us-president'))
 const mid=math.windowMonthlyStats(rows,window,'us-midterm');assert.equal(mid[0].n,1);assert.equal(mid[10].n,0);assert.deepEqual(math.electionYearsInWindow(rows,window,'us-midterm'),[{year:2026,n:10}])
})
test('year category controls every monthly metric, including extrema and probability denominator',()=>{
 const rows=[make('2020-07',-5),make('2024-07',5),make('2024-09',999,true),make('2026-07',999)],window=math.windowStatistics(rows,'2026-10').at(-1)
 const july=math.windowMonthlyStats(rows,window,'taiwan-president')[6]
 assert.equal(july.n,2);assert.equal(july.mean,0);assert.equal(july.up_rate,50);assert.equal(july.down_rate,50);assert.deepEqual(july.maximum,{value:5,months:['2024-07']});assert.deepEqual(july.minimum,{value:-5,months:['2020-07']})
 assert.deepEqual(math.electionYearsInWindow(rows,window,'taiwan-president'),[{year:2020,n:1},{year:2024,n:1}])
})
test('no matching election year produces missing probabilities, and default all-years remains identical',()=>{
 const rows=series('2025-01',12,()=>1),window=math.windowStatistics(rows,'2025-12')[0]
 const none=math.windowMonthlyStats(rows,window,'us-president');assert.ok(none.every(m=>m.n===0&&m.up_rate===null&&m.mean===null&&m.maximum===null))
 assert.deepEqual(math.electionYearsInWindow(rows,window,'us-president'),[]);assert.deepEqual(math.windowMonthlyStats(rows,window),math.windowMonthlyStats(rows,window,'all'))
})
test('the requested section exposes three election categories and makes intersection scope explicit',async()=>{
 const s=await html('MonthlyStatistics.vue',{initial})
 for(const text of ['選舉年份篩選','台灣總統大選年','美國總統大選年','美國期中選舉年','與所選年期取交集','選舉年可能只納入範圍內的部分月份'])assert.ok(s.includes(text),text)
 assert.equal((s.match(/data-lookback="taiex-/g)||[]).length,5);assert.equal((s.match(/data-window-month="taiex-/g)||[]).length,12)
 assert.doesNotMatch(s,/NaN|Infinity/)
})

test('own-sector names sit below monthly Taiwan percent with current-month and data-gap labels',async()=>{
 const own={...row,sector_status:'ready',sector_eligible_groups:600,sector_total_groups:670,sector_leaders:[{name:'AI伺服器',return_pct:12,quoted:4,members:5,coverage_pct:80},{name:'ABF載板',return_pct:10,quoted:3,members:3,coverage_pct:100},{name:'ASIC',return_pct:8,quoted:2,members:2,coverage_pct:100}]}
 const ownMarket={...market,rows:[own],years:[{...market.years[0],months:market.years[0].months.map(r=>r?own:null)}]}
 const s=await html('MonthlyStatistics.vue',{initial:{...initial,markets:{...initial.markets,taiex:ownMarket}}})
 for(const text of ['（暫：AI伺服器、ABF載板、ASIC）','data-sector-month="2026-11"','我們自訂族群','非當年分類與成員','12.00%','4／5檔'])assert.ok(s.includes(text),text)
 assert.doesNotMatch(s,/NaN|Infinity/)
 assert.equal(math.sectorCaption({...own,partial:false}),'（AI伺服器、ABF載板、ASIC）')
 assert.equal(math.sectorCaption({...own,sector_status:'no_risers',sector_leaders:[]}),'（無上漲族群）')
 assert.equal(math.sectorCaption({...own,sector_status:'stale',sector_leaders:[]}),'（族群資料待更新）')
 assert.equal(math.sectorCaption({...own,sector_status:'missing',sector_leaders:[]}),'（族群資料不足）')
 assert.equal(math.sectorCaption({...own,return_pct:null}),'')
})

test('YTD sector captions and dates use prior year-end while monthly view stays intact',()=>{
 const own={...row,sector_leaders:[{name:'當月冠軍',return_pct:3,quoted:2,members:2,coverage_pct:100}],sector_status:'ready',sector_ytd:{leaders:[{name:'年度冠軍',return_pct:80,quoted:4,members:5,coverage_pct:80}],status:'ready',previous_date:'2025-12-31',date:'2026-11-04',eligible_groups:600,total_groups:670}}
 assert.equal(math.sectorCaption(own),'（暫：當月冠軍）')
 assert.equal(math.sectorCaption(own,'ytd'),'（暫：年度冠軍）')
 const tip=math.sectorTooltip(own,'ytd');for(const t of ['YTD','2025-12-31','2026-11-04','+80.00%','4／5檔','600／670'])assert.ok(tip.includes(t),t)
 assert.equal(math.sectorCaption({...own,sector_ytd:undefined},'ytd'),'（族群資料不足）')
 assert.equal(math.sectorCaption({...own,sector_ytd:{leaders:[],status:'no_risers'}},'ytd'),'（無上漲族群）')
})
test('sector mode control identifies YTD without relabeling index monthly returns',async()=>{
 const s=await html('MonthlyStatistics.vue',{initial})
 for(const text of ['族群排名方式','當月漲幅','年初至今（YTD）','格內百分比仍為台股當月漲跌'])assert.ok(s.includes(text),text)
 assert.ok(s.includes('aria-pressed="true"'))
})
