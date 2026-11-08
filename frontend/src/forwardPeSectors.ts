export type SectorRow={name:string;parent:string;members:number;quoted:number;return_coverage_pct:number|null;return_pct:number|null;advancers:number;decliners:number;unchanged:number;amount_twd:number|null;amount_names:number;amount_complete:boolean;amount_share_pct:number|null;previous_amount_share_pct:number|null;share_change_pp:number|null}
export type SectorReport={status:string;date:string|null;previous_date:string|null;dates:string[];rows:SectorRow[];classification:{groups:number;snapshot:string|null;sha256?:string};warnings:string[];notes:string[];scope:string;market_turnover_twd?:number|null;market_names?:number;version?:string;previous_market_turnover_twd?:number|null;sources?:Record<string,Record<string,{id?:string;sha256?:string;url?:string;retrieved_at?:string}>>}

export function selectSectors(rows:SectorRow[],options:{sort:string;quality:string;parent:string;search:string}){
 const list=rows.filter(r=>(options.quality==='all'||r.members>=3&&(r.return_coverage_pct??0)>=80)&&(!options.parent||r.parent===options.parent)&&(!options.search.trim()||`${r.name} ${r.parent}`.includes(options.search.trim())))
 const value=(r:SectorRow)=>options.sort==='amount'?r.amount_twd:r.return_pct
 return list.slice().sort((a,b)=>{const x=value(a),y=value(b);if(x==null&&y==null)return a.name.localeCompare(b.name);if(x==null)return 1;if(y==null)return -1;return (options.sort==='falling'?x-y:y-x)||a.name.localeCompare(b.name)})
}
