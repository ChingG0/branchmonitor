export function normalizeOrder(saved:unknown,ids:string[]):string[]{
 const valid=Array.isArray(saved)?saved.filter((s):s is string=>typeof s==='string'&&ids.includes(s)):[]
 return [...new Set([...valid,...ids])]
}
export function normalizeMarketOrder(saved:unknown,ids:string[]):string[]{
 let next=normalizeOrder(saved,ids)
 // Place the new local indicator after the river once, preserving other cards'
 // relative order. A user's later saved position is never reset.
 if(ids.includes('ma20')&&Array.isArray(saved)&&!saved.includes('ma20')&&next.includes('pe_river')){
  next=next.filter(id=>id!=='ma20');next.splice(next.indexOf('pe_river')+1,0,'ma20')
 }
 return next
}
export function moveCard(order:string[],from:string,to:string):string[]{
 if(from===to||!order.includes(from)||!order.includes(to))return [...order]
 const next=order.filter(id=>id!==from);next.splice(order.indexOf(to),0,from);return next
}
