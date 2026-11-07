import type {HistoricalIndicator} from './v6HistoryTypes'
import {numericReading} from './v6ObservationVisual'
import type {observationVisual} from './v6ObservationVisual'

type Visual=ReturnType<typeof observationVisual>
export type IndicatorTone='danger'|'warning'|'caution'|'normal'|'tailwind'|'unknown'
export const indicatorPalette:Record<IndicatorTone,{label:string;color:string;background:string}>={
 danger:{label:'明顯壓力',color:'#b34943',background:'#fff1ee'},
 warning:{label:'警示',color:'#b66924',background:'#fff3e6'},
 caution:{label:'輕微偏離',color:'#967311',background:'#fff9dc'},
 normal:{label:'中性合理',color:'#3e7859',background:'#edf7ef'},
 tailwind:{label:'相對順風',color:'#3b70a2',background:'#edf5ff'},
 unknown:{label:'待判讀',color:'#7f8987',background:'#f2f4f3'},
}
type State={tone:IndicatorTone;reason:string;basis:string}
const state=(tone:IndicatorTone,reason:string,basis:string):State=>({tone,reason,basis})
const median=(values:number[])=>{const a=[...values].sort((a,b)=>a-b);const i=Math.floor(a.length/2);return a.length%2?a[i]:(a[i-1]+a[i])/2}

export function indicatorState(item:HistoricalIndicator,visual:Visual,override?:number):State{
 const value=override??numericReading(item.value)
 if(value===null||!Number.isFinite(value))return state('unknown','數值缺漏，暫不分級','未將缺資料當成低風險。')
 if(item.label.includes('日乖離')){
  const basis='自訂乖離觀察：−1%～+1% 綠；+1%～+3% 藍；−3%～−1% 或 +3%～+6% 黃；−6%～−3% 或高於 +6% 橘；≤−6% 紅。不是經驗證的買賣門檻。'
  if(value<=-6)return state('danger','跌離均線較深',basis)
  if(value< -3)return state('warning','低於均線，價格承壓',basis)
  if(value< -1)return state('caution','略低於均線',basis)
  if(value<=1)return state('normal','接近均線',basis)
  if(value<=3)return state('tailwind','溫和高於均線',basis)
  if(value<=6)return state('caution','正乖離擴大',basis)
  return state('warning','正乖離過大，留意過熱',basis)
 }
 if(item.label==='融資維持率估值'){
  const basis='沿用本機維持率觀察區間：≤150% 紅、150%～158% 橘、158%～166% 黃、166%～183% 綠、183%～190% 藍、≥190% 紅（過熱）。這是市場估值，不是個別帳戶的斷頭線。'
  if(value<=150)return state('danger','維持率低，留意去槓桿壓力',basis)
  if(value<=158)return state('warning','維持率偏低',basis)
  if(value<166)return state('caution','維持率過渡區',basis)
  if(value<183)return state('normal','位於既有觀察中間區',basis)
  if(value<190)return state('tailwind','多頭續航觀察區',basis)
  return state('danger','維持率過熱，留意追價風險',basis)
 }
 if(item.label==='VIX'){
  const basis='VIX 衡量預期波動，不是跌勢機率。參考 S&P／Cboe 對低於12、12～20與高於20的示例，另自訂細分顏色：<12 藍、12～20 綠、20～25 黃、25～30 橘、≥30 紅；不是官方風險等級。'
  if(value<0)return state('unknown','超出指標有效範圍',basis)
  if(value<12)return state('tailwind','預期波動較低',basis)
  if(value<20)return state('normal','預期波動位於一般觀察區',basis)
  if(value<25)return state('caution','預期波動略偏高',basis)
  if(value<30)return state('warning','預期波動偏高',basis)
  return state('danger','預期波動明顯升高',basis)
 }
 if(item.label==='恐懼貪婪'){
  const basis='情緒觀察：≤25 紅（極度恐懼）、25～45 橘、45～55 綠、55～75 藍、>75 黃（過度樂觀）。恐懼不等於必跌，貪婪不等於必漲。'
  if(value<0||value>100)return state('unknown','超出指標有效範圍',basis)
  if(value<=25)return state('danger','市場情緒極度恐懼',basis)
  if(value<45)return state('warning','市場情緒偏恐懼',basis)
  if(value<=55)return state('normal','市場情緒中性',basis)
  if(value<=75)return state('tailwind','市場情緒偏樂觀',basis)
  return state('caution','市場情緒過度樂觀',basis)
 }
 const directions:Record<string,{direction:number;caption:string}>={
  '前一交易日加權收盤':{direction:-1,caption:'加權價格'},
  '標普 500':{direction:-1,caption:'美股價格'},
  '台積電 ADR':{direction:-1,caption:'ADR 價格'},
  '金融 ETF 0055':{direction:-1,caption:'金融 ETF 價格'},
  '電子 ETF 0052':{direction:-1,caption:'電子 ETF 價格'},
  '美元／台幣':{direction:1,caption:'台幣走弱壓力'},
  '外資台指期淨空單':{direction:1,caption:'外資淨空部位'},
 }
 const rule=directions[item.label]
 const unknownReasons:Record<string,string>={
  '融資餘額':'須合看價格與融資變化，餘額高低不單獨定好壞',
  '當沖金額占比':'交易結構指標，不單憑高低判斷多空',
  '布蘭特原油':'需辨別供給衝擊或需求變化',
  '美債殖利率代理':'須與通膨及利率預期合看',
  '小台非三大法人部位比':'非三大法人不等於逐戶散戶，需與價格合看',
  '美國 DIX 暗池指標':'需合看市場走勢與歷史分布',
  '美國 GEX 估值':'需合看正負值、規模與造市避險情境',
 }
 if(!rule)return state('unknown',unknownReasons[item.label]||'尚未建立此指標的方向判讀規則','只顯示數值走勢，不硬套風險顏色。')
 const history=visual.readings.filter(r=>r.date<item.as_of).map(r=>r.value)
 const basis='近期相對位置（非長期分布）：至少 10 筆較早來源日；z＝方向×(當值−歷史中位數)/(1.4826×MAD)。z≥2 紅、≥1 橘、≥0.5 黃、>−0.5 綠、其餘藍。自訂視覺分級，未校準為下跌機率。'
 if(history.length<10)return state('unknown',`來源日不足（${history.length}／10），暫不分級`,basis)
 const center=median(history),scale=1.4826*median(history.map(n=>Math.abs(n-center)))
 if(!Number.isFinite(scale)||scale<=Math.max(1,Math.abs(center))*1e-8)return state('unknown','歷史變化不足，無法穩定分級',basis)
 const z=rule.direction*(value-center)/scale
 const tone:IndicatorTone=z>=2?'danger':z>=1?'warning':z>=.5?'caution':z>-.5?'normal':'tailwind'
 const reason=tone==='danger'||tone==='warning'?`${rule.caption}相對承壓`:tone==='caution'?`${rule.caption}略偏不利`:tone==='tailwind'?`${rule.caption}相對有利`:`${rule.caption}接近近期中位`
 const scope=rule.direction===-1?'；僅比較價格，未評估估值或後續報酬':''
 return state(tone,reason,`${basis} 參考中位 ${center.toFixed(2)}，相對偏離 ${z.toFixed(2)}。${scope}`)
}
