export type NumericBound = string | number | null
export interface TableFilters {
 search?:string; minDays?:NumericBound; maxDays?:NumericBound; minPnl?:NumericBound; maxPnl?:NumericBound;
 pnlKey?:string; minRate?:NumericBound; minSamples?:NumericBound; status?:string;
 sortKey?:string; descending?:boolean; absolute?:boolean;
}
export function selectRows<T extends {symbol:string;name:string}>(rows:T[], f:TableFilters):T[]{
 const value=(row:T,key:string):unknown=>(row as unknown as Record<string,unknown>)[key]
 // Vue's number inputs emit numbers while populated, and an empty string when cleared.
 const bound=(value:NumericBound|undefined)=>{
  if(typeof value==='number')return Number.isFinite(value)?value:null
  if(typeof value!=='string'||value.trim()==='')return null
  const parsed=Number(value)
  return Number.isFinite(parsed)?parsed:null
 }
 const number=(row:T,key:string)=>typeof value(row,key)==='number'&&Number.isFinite(value(row,key))?value(row,key) as number:null
 const inRange=(v:number|null,min:NumericBound|undefined,max:NumericBound|undefined)=>{
  const lo=bound(min),hi=bound(max)
  return (lo===null&&hi===null)||(v!==null&&(lo===null||v>=lo)&&(hi===null||v<=hi))
 }
 const query=(f.search||'').trim().toLowerCase()
 return rows.filter(r=>(r.symbol+r.name).toLowerCase().includes(query)
  &&inRange(number(r,'holding_days'),f.minDays,f.maxDays)
  &&inRange(number(r,f.pnlKey||'pnl'),f.minPnl,f.maxPnl)
  &&inRange(number(r,'win_rate'),f.minRate,undefined)
  &&inRange(number(r,'closed_count'),f.minSamples,undefined)
  &&(!f.status||f.status==='all'||value(r,'status')===f.status)
 ).sort((a,b)=>{
  if(!f.sortKey)return 0
  const av=value(a,f.sortKey),bv=value(b,f.sortKey)
  if(av===null||av===undefined)return bv===null||bv===undefined?0:1
  if(bv===null||bv===undefined)return -1
  const delta=typeof av==='number'&&typeof bv==='number'?(f.absolute?Math.abs(av)-Math.abs(bv):av-bv):String(av).localeCompare(String(bv))
  return (f.descending===false?1:-1)*delta
 })
}
