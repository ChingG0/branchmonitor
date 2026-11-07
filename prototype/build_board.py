"""把 sectors.report() 的結果輸出成單檔看板。原型用，不接 HTTP 服務。"""
import json
import os
import tempfile
import sys
from pathlib import Path

import sectors

OUT = Path(__file__).resolve().parent / 'board.html'

TEMPLATE = '''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>族群廣度 · 分點觀測站</title>
<style>
:root{--forest:#17392c;--ink:#20342a;--muted:#829087;--line:#e5eae6;--paper:#f4f6f4;
 --card:#fff;--red:#bc594e;--green:#278462;--lime:#d0ee99;--sunk:#eef1ee;
 --redW:#f7ebe9;--greenW:#e6f1ec;--tight:#17392c;--tightW:#e8efe9;--loose:#9a7b3f;--looseW:#f5efe2;
 font-family:Inter,-apple-system,BlinkMacSystemFont,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
 color:var(--ink);background:var(--paper);font-size:16px;font-synthesis:none}
*{box-sizing:border-box}
body{margin:0}
button,input,select{font:inherit}
button{cursor:pointer;border:0;background:none;color:inherit}
input,select{min-height:39px;border:1px solid var(--line);border-radius:7px;padding:0 11px;background:var(--card);color:inherit}
input:focus-visible,select:focus-visible,th:focus-visible{outline:3px solid #96bba2;outline-offset:2px}
.mono{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.wrap{max-width:1180px;margin:0 auto;padding:40px 28px 90px;display:flex;flex-direction:column;gap:30px}
header h1{font-size:1.6rem;font-weight:650;letter-spacing:.01em;margin:0 0 9px}
header p{margin:0;color:var(--muted);font-size:.875rem;max-width:74ch;line-height:1.7}
header p b{color:var(--ink);font-weight:600}
.meta{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}
.meta span{font-size:.75rem;letter-spacing:.03em;color:var(--muted);
 border:1px solid var(--line);border-radius:5px;padding:4px 9px;background:var(--card)}
.updbar{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-top:14px;
 background:var(--card);border:1px solid var(--line);border-radius:9px;padding:10px 14px}
.updbar .st{font-size:.8rem;color:var(--muted)}
.updbar .st b{color:var(--ink);font-weight:600}
.updbar button{background:var(--forest);color:#f6f9f5;border-radius:7px;padding:7px 15px;
 font-size:.82rem;font-weight:650}
.updbar button:hover{background:#1f4a39}
.updbar button:disabled{opacity:.5;cursor:not-allowed}
.updbar .spin{width:13px;height:13px;border:2px solid var(--line);border-top-color:var(--forest);
 border-radius:50%;animation:sp .8s linear infinite;display:inline-block;vertical-align:-2px}
@keyframes sp{to{transform:rotate(360deg)}}
.warn{background:var(--looseW);color:var(--loose);border-radius:6px;padding:6px 11px;
 font-size:.76rem;line-height:1.55}
.updbar .err{color:var(--green);font-size:.76rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px}
.find{padding:20px 22px;display:flex;flex-direction:column;gap:15px}
.findbar{display:flex;gap:11px;align-items:center;flex-wrap:wrap}
.findbar input{flex:1;min-width:230px}
.hint{color:var(--muted);font-size:.8rem}
.verdict{display:none;flex-direction:column;gap:13px}
.verdict.on{display:flex}
.vhead{display:flex;align-items:baseline;gap:11px;flex-wrap:wrap}
.vhead .nm{font-size:1.2rem;font-weight:650}
.vhead .cd{color:var(--muted);font-size:.9rem}
.tag{font-size:.75rem;font-weight:650;padding:4px 11px;border-radius:5px;letter-spacing:.03em}
.tag.grp{background:var(--redW);color:var(--red)}
.tag.solo{background:var(--sunk);color:var(--muted)}
.tag.mix{background:var(--looseW);color:var(--loose)}
.vrow{display:flex;gap:20px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}
.vrow b{color:var(--ink);font-weight:600}
.sec{display:flex;flex-direction:column;gap:13px}
.sechead{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;
 border-top:1.5px solid var(--forest);padding-top:14px}
.sechead h2{font-size:1.05rem;font-weight:650;margin:0}
.sechead .n{color:var(--muted);font-size:.8rem}
.ctl{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:.82rem;color:var(--muted)}
.ctl label{display:flex;align-items:center;gap:7px}
.ctl select,.ctl input{min-height:34px;font-size:.82rem}
.scroll{overflow:auto;max-height:660px;border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{text-align:left;padding:9px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
thead th{position:sticky;top:0;z-index:2;background:var(--sunk);color:var(--muted);
 font-size:.7rem;font-weight:650;letter-spacing:.09em;cursor:pointer;user-select:none}
thead th:hover{color:var(--ink)}
thead th.on{color:var(--forest)}
tbody tr:last-child td{border-bottom:0}
tbody tr.g{cursor:pointer}
tbody tr.g:hover{background:var(--sunk)}
td.num{text-align:right}
.up{color:var(--red)}.dn{color:var(--green)}
.chip{display:inline-block;font-size:.7rem;font-weight:650;padding:2px 8px;border-radius:4px}
.chip.t{background:var(--tightW);color:var(--tight)}
.chip.m{background:var(--greenW);color:var(--green)}
.chip.l{background:var(--looseW);color:var(--loose)}
.chip.x{background:var(--sunk);color:var(--muted)}
.bar{display:inline-flex;align-items:center;gap:8px;justify-content:flex-end}
.bar .t{width:56px;height:6px;background:var(--sunk);border-radius:3px;overflow:hidden}
.bar .f{display:block;height:100%;background:var(--red);border-radius:3px}
.bar .f.lo{background:var(--muted)}
.mem td{padding:0;background:var(--paper)}
.memin{padding:11px 15px 15px;display:flex;flex-wrap:wrap;gap:7px}
.m{display:inline-flex;align-items:center;gap:7px;background:var(--card);
 border:1px solid var(--line);border-radius:6px;padding:4px 10px;font-size:.8rem}
.m .c{color:var(--muted);font-size:.72rem}
.m .r{font-weight:600}
.empty{color:var(--muted);font-size:.85rem;padding:16px}
footer{border-top:1px solid var(--line);padding-top:18px;color:var(--muted);
 font-size:.76rem;display:flex;flex-direction:column;gap:5px;line-height:1.6}
dialog{border:0;border-radius:14px;padding:0;max-width:940px;width:calc(100% - 36px);
 max-height:88vh;background:var(--card);color:var(--ink);box-shadow:0 18px 60px rgba(20,52,42,.22)}
dialog::backdrop{background:rgba(20,52,42,.4)}
.dhead{display:flex;align-items:flex-start;gap:12px;padding:20px 24px 14px;
 border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card);z-index:3;flex-wrap:wrap}
.dhead .nm{font-size:1.25rem;font-weight:650}
.dhead .cd{color:var(--muted);font-size:.92rem}
.dhead .x{margin-left:auto;color:var(--muted);font-size:1.3rem;line-height:1;padding:2px 6px}
.dhead .x:hover{color:var(--ink)}
.dbody{padding:18px 24px 26px;overflow:auto;max-height:calc(88vh - 76px);display:flex;flex-direction:column;gap:20px}
.dbody>*{flex-shrink:0}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:1px;
 background:var(--line);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.fact{background:var(--card);padding:11px 13px}
.fact dt{color:var(--muted);font-size:.72rem;letter-spacing:.04em;margin-bottom:3px}
.fact dd{margin:0;font-size:.95rem;font-weight:600}
.fact dd small{display:block;font-weight:400;font-size:.7rem;color:var(--muted);margin-top:2px}
.dsec{display:flex;flex-direction:column;gap:9px}
.dsec h3{margin:0;font-size:.88rem;font-weight:650}
.dsec h3 span{color:var(--muted);font-weight:400;font-size:.78rem;margin-left:7px}
.chain{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.chain .cn{padding:10px 14px;background:var(--sunk);font-weight:650;font-size:.85rem}
.rail{display:grid;grid-template-columns:52px 1fr;gap:10px;padding:9px 14px;align-items:start}
.rail+.rail{border-top:1px solid var(--line)}
.rail .tier{font-size:.72rem;font-weight:650;color:var(--forest);padding-top:5px;letter-spacing:.06em}
.segs{display:flex;flex-wrap:wrap;gap:5px}
.seg{font-size:.76rem;padding:3px 9px;border-radius:5px;background:var(--sunk);color:var(--muted)}
.seg.me{background:var(--forest);color:#f6f9f5;font-weight:650}
.seg b{font-weight:400;opacity:.65;margin-left:4px}
.shares{display:flex;gap:16px;flex-wrap:wrap;padding:10px 14px;border-top:1px solid var(--line);
 background:var(--paper);font-size:.79rem;color:var(--muted)}
.shares b{color:var(--ink);font-weight:650}
.peers{padding:10px 14px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:6px}
.m.click{cursor:pointer}
.m.click:hover{border-color:var(--forest);background:var(--tightW)}
.note{color:var(--muted);font-size:.74rem;line-height:1.6}
.flowgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.flowcol h3{margin:0 0 9px;font-size:.82rem;font-weight:650;letter-spacing:.04em;
 display:flex;align-items:center;gap:7px}
.flowcol h3 i{width:9px;height:9px;border-radius:2px;font-style:normal}
.flowcol.in h3 i{background:var(--red)} .flowcol.out h3 i{background:var(--green)}
.lead{background:var(--card);border:1px solid var(--line);border-radius:9px;
 padding:11px 13px;cursor:pointer;position:relative;overflow:hidden}
.lead+.lead{margin-top:7px}
.lead:hover{border-color:#a9c0b2}
.lead .track{position:absolute;inset:0;z-index:0}
.lead .track i{position:absolute;top:0;bottom:0;left:0;display:block;opacity:.09}
.lead.in .track i{background:var(--red)} .lead.out .track i{background:var(--green)}
.lead>*{position:relative;z-index:1}
.lead .top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.lead .gname{font-weight:650;font-size:.92rem}
.lead .gpar{color:var(--muted);font-size:.74rem}
.lead .amt{margin-left:auto;font-weight:650;font-size:1rem}
.lead .sub{display:flex;gap:12px;flex-wrap:wrap;margin-top:5px;font-size:.74rem;color:var(--muted)}
.lead .sub b{color:var(--ink);font-weight:600}
.lead .win{margin-top:6px;font-size:.74rem;color:var(--muted);display:flex;gap:9px;
 flex-wrap:wrap;align-items:center}
.lead .win b{font-weight:650}
.lead .con{margin-top:8px;padding-top:8px;border-top:1px solid var(--line);
 display:flex;flex-wrap:wrap;gap:6px}
.mark{font-size:.7rem;font-weight:650;padding:2px 7px;border-radius:4px;letter-spacing:.02em}
.mark.keep{background:var(--redW);color:var(--red)}
.mark.bounce{background:var(--looseW);color:var(--loose)}
.mark.trim{background:var(--tightW);color:var(--tight)}
.mark.drop{background:var(--greenW);color:var(--green)}
.seg-toggle{display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.seg-toggle button{padding:5px 13px;font-size:.78rem;background:var(--card);color:var(--muted)}
.seg-toggle button.on{background:var(--forest);color:#f6f9f5;font-weight:650}
.qgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:8px}
.q{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
 cursor:pointer;text-align:left;width:100%;font:inherit;color:inherit}
.q:hover{border-color:var(--forest);background:var(--tightW)}
.q .qlinks{margin-top:5px;font-size:.7rem;color:var(--muted)}
.q .qn{font-weight:650;font-size:.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.q .qs{color:var(--muted);font-size:.7rem;margin-top:1px}
.q .qp{margin-top:6px;display:flex;align-items:baseline;gap:7px}
.q .qp b{font-size:1rem;font-weight:650}
.q .qp span{font-size:.76rem;font-weight:600}
.pill{display:inline-block;font-size:.72rem;font-weight:650;padding:2px 7px;border-radius:4px}
.pill.up{background:var(--redW);color:var(--red)} .pill.dn{background:var(--greenW);color:var(--green)}
.rel{display:grid;grid-template-columns:96px 1fr;gap:12px;padding:8px 0;align-items:baseline}
.rel+.rel{border-top:1px solid var(--line)}
.rel .rc{font-weight:600;font-size:.8rem}
.rel .rc small{display:block;color:var(--muted);font-weight:400;font-size:.7rem}
.ext{display:inline-flex;align-items:center;background:var(--sunk);border-radius:5px;
 padding:3px 9px;font-size:.78rem;color:var(--ink2,#475463)}
@media(max-width:760px){.flowgrid{grid-template-columns:1fr}}
@media(max-width:640px){.wrap{padding:26px 16px 70px}
 .rail{grid-template-columns:1fr;gap:4px}}
</style></head><body>
<div class="wrap">
<header>
  <h1>族群廣度</h1>
  <p>族群分類是靜態字典，<b>共動、廣度、報酬全部用本機 <code>prices</code> 表重算</b>，不連外部來源。
     共動 ≥ 0.35 的族群才拿來判讀廣度——共動低代表成員本來就不一起動，廣度數字沒有意義。</p>
  <div class="meta" id="meta"></div>
  <div class="updbar" id="bar" hidden>
    <button id="up">更新</button>
    <span class="st" id="st"></span>
  </div>
  <p class="warn" id="behind" hidden></p>
</header>

<div class="card find">
  <div class="findbar">
    <input id="q" placeholder="輸入股票代號或名稱，例如 2330、台積電" autocomplete="off" spellcheck="false">
    <span class="hint" id="qhint">輸入後按 Enter，或點畫面上任何股票</span>
  </div>
</div>

<section class="sec">
  <div class="sechead"><h2>主流族群</h2><span class="n" id="ln"></span>
    <span style="margin-left:auto" class="seg-toggle">
      <button id="mday" class="on">當日資金</button><button id="m5d">5 日累計</button></span></div>
  <div class="flowgrid">
    <div class="flowcol in"><h3><i></i>資金流入</h3><div id="lin"></div></div>
    <div class="flowcol out"><h3><i></i>資金流出</h3><div id="lout"></div></div>
  </div>
  <p class="note" id="lnote"></p>
</section>

<section class="sec">
  <div class="sechead"><h2>國際大廠報價</h2><span class="n" id="qn"></span></div>
  <div class="qgrid" id="qgrid"></div>
</section>

<section class="sec">
  <div class="sechead"><h2>族群排行</h2><span class="n" id="gn"></span></div>
  <div class="ctl">
    <label>共動門檻 <select id="fc">
      <option value="-9">全部</option><option value="0.15">≥ 0.15</option>
      <option value="0.25">≥ 0.25</option><option value="0.35" selected>≥ 0.35（建議）</option>
      <option value="0.5">≥ 0.50</option></select></label>
    <label>成員 ≥ <input type="number" id="fn" value="3" min="2" max="60" style="width:66px"></label>
    <label>成員 ≤ <input type="number" id="fx" value="30" min="2" max="200" style="width:66px"></label>
    <span class="hint">點任一列展開成員</span>
  </div>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th data-k="name">族群</th><th data-k="parent">產業別</th>
      <th data-k="members" class="num">成員</th><th data-k="comovement" class="num">共動</th>
      <th data-k="r1" class="num">今日%</th><th data-k="r5" class="num">5日%</th>
      <th data-k="r20" class="num">20日%</th><th data-k="breadth" class="num">廣度</th></tr></thead>
    <tbody id="gtb"></tbody></table></div></div>
</section>

<footer>
  <span id="ft"></span>
  <span>族群分類與供應鏈：MoneyDJ ｜ 價量：本機 SQLite（證交所 + 櫃買每日收盤）</span>
  <span>共動 = 成員日報酬的平均兩兩相關係數，逐對取雙方都有報酬的交易日；只在成員 3～30 檔時計算。
        廣度 = 當日上漲成員數 ÷ 當日有報價成員數。報酬為未還原收盤價格報酬，未計股息與公司事件。</span>
</footer>
</div>
<dialog id="pd"><div class="dhead">
  <span class="nm" id="pnm"></span><span class="cd mono" id="pcd"></span>
  <button class="x" id="px" aria-label="關閉">&times;</button></div>
  <div class="dbody" id="pbody"></div>
</dialog>
<dialog id="sd"><div class="dhead">
  <span class="nm" id="dnm"></span><span class="cd mono" id="dcd"></span><span class="tag" id="dtag"></span>
  <button class="x" id="dx" aria-label="關閉">&times;</button></div>
  <div class="dbody" id="dbody"></div>
</dialog>
<script>
const D=__DATA__;
const FOREIGN=Object.fromEntries((D.foreign||[]).map(f=>[f.partner,f]));
const $=i=>document.getElementById(i);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pct=v=>v==null?"—":(v>0?"+":"")+v.toFixed(2);
const cls=v=>v==null?"":v>0?"up":v<0?"dn":"";
function cchip(c){
  if(c==null)return '<span class="chip x">無</span>';
  const k=c>=.35?"t":c>=.15?"m":"l", lab=c>=.5?"很緊":c>=.35?"緊":c>=.15?"普通":"鬆";
  return `<span class="chip ${k}">${c.toFixed(2)} ${lab}</span>`;
}
const bar=b=>b==null?'<span class="hint">—</span>':
  `<span class="bar"><span class="t"><span class="f${b<.5?" lo":""}" style="width:${Math.round(b*100)}%"></span></span><span class="mono">${Math.round(b*100)}%</span></span>`;

$("meta").innerHTML=[`資料日 ${D.date}`,`視窗 ${D.window.window_start} ～ ${D.date}`,
  `${D.window.sessions} 個交易日`,`族群 ${D.groups.length} 個`,`個股 ${Object.keys(D.stocks).length} 檔`,
  `字典 ${D.meta.dictionary_snapshot} 版`].map(t=>`<span>${esc(t)}</span>`).join("");
$("ft").textContent=`本機重算於 ${D.window.at}｜字典來源：${D.meta.dictionary_source}`;

/* 更新狀態 */
let polling=null,lastFinish=null;
function renderStatus(s){
  $("bar").hidden=false;
  const running=s.status==="running";
  $("up").disabled=running;
  $("up").textContent=running?"更新中":"更新";
  const stamp=v=>v?v.replace("T"," ").slice(5,16):"—";
  const bits=[`最後更新 <b>${stamp(s.last_update)}</b>`,
              `資料日 <b>${s.computed||"—"}</b>`,
              `法人 <b>${s.institutional||"—"}</b>`];
  if(running)bits.unshift('<span class="spin"></span>');
  if(s.status==="partial"||s.status==="failed")
    bits.push(`<span class="err">${esc(s.message||"")}</span>`);
  $("st").innerHTML=bits.join(" · ");
  if(s.behind){
    $("behind").hidden=false;
    $("behind").innerHTML=`正式系統的行情最新只到 <b>${s.available}</b>，本看板的統計不會超過這個日期。
      要更新到更新的交易日，請先在分點觀測站（127.0.0.1:8765）執行同步，再回來按「更新」。`;
  }else $("behind").hidden=true;
  if(!running&&s.finished_at&&lastFinish&&s.finished_at!==lastFinish){location.reload();return;}
  if(s.finished_at)lastFinish=s.finished_at;
  if(running&&!polling)polling=setInterval(poll,1500);
  if(!running&&polling){clearInterval(polling);polling=null;}
}
async function poll(){
  try{renderStatus(await (await fetch("/api/status")).json());}
  catch(e){clearInterval(polling);polling=null;}
}
$("up").onclick=async()=>{
  $("up").disabled=true;
  try{
    const r=await fetch("/api/refresh",{method:"POST",
      headers:{"Content-Type":"application/json"},body:"{}"});
    renderStatus(await r.json());
    if(!polling)polling=setInterval(poll,1500);
  }catch(e){$("up").disabled=false;$("st").innerHTML='<span class="err">無法連上本機伺服器</span>';}
};
// 直接開檔（沒有伺服器）時整條更新列不顯示。
fetch("/api/status").then(r=>r.json()).then(renderStatus).catch(()=>{$("bar").hidden=true;});

/* 當日主流族群 */
const oku=v=>v==null?"—":(v>0?"+":"")+(v/1e8).toFixed(1);
const L=D.leaders, cov=D.leader_coverage;
let mode="day";
const MARK={keep:["連續流入","keep"],bounce:["單日反彈","bounce"],
            trim:["短線調節","trim"],drop:["連續流出","drop"]};
function markOf(g){
  const a=g.inst_value, b=g.inst_value_5d;
  if(a==null||b==null)return null;
  return a>0?(b>0?"keep":"bounce"):(b>0?"trim":"drop");
}
function renderLeaders(){
  const key=mode==="day"?"inst_value":"inst_value_5d";
  const other=mode==="day"?"inst_value_5d":"inst_value";
  const scored=L.filter(g=>g[key]!=null);
  const inflow=scored.filter(g=>g[key]>0).sort((a,b)=>b[key]-a[key]).slice(0,8);
  const outflow=scored.filter(g=>g[key]<0).sort((a,b)=>a[key]-b[key]).slice(0,8);
  const peak=Math.max(1,...scored.map(g=>Math.abs(g[key])));
  const card=(g,dir)=>{
    const mk=markOf(g), lab=mk?MARK[mk]:null;
    const fg=mode==="day"?g.foreign_value:g.foreign_value_5d;
    const tr=mode==="day"?g.trust_value:g.trust_value_5d;
    return `<div class="lead ${dir}" data-g="${esc(g.name)}">
    <span class="track"><i style="width:${Math.round(Math.abs(g[key])/peak*100)}%"></i></span>
    <div class="top"><span class="gname">${esc(g.name)}</span><span class="gpar">${esc(g.parent)}</span>
      ${lab?`<span class="mark ${lab[1]}">${lab[0]}</span>`:""}
      <span class="amt ${g[key]>0?"up":"dn"}">${oku(g[key])} 億</span></div>
    <div class="sub">
      <span>外資 <b class="${cls(fg)}">${oku(fg)}</b></span>
      <span>投信 <b class="${cls(tr)}">${oku(tr)}</b></span>
      <span>今日 <b class="${cls(g.r1)}">${pct(g.r1)}%</b></span>
      <span>廣度 <b>${Math.round(g.breadth*100)}%</b></span>
      <span>成交重心 <b>${cov.turnover?g.turnover_hits:"—"}</b></span>
      <span>券商同步 <b>${g.consensus_stocks==null?"—":g.consensus_stocks+" 檔"}</b></span>
      ${g.inst_missing?`<span class="hint">${g.inst_missing} 檔缺法人資料</span>`:""}
    </div>
    <div class="win"><span>${mode==="day"?`5 日累計`:`當日`}
      <b class="${cls(g[other])}">${oku(g[other])} 億</b></span>
      ${mode==="day"?`<span class="hint">外資 ${oku(g.foreign_value_5d)}／投信 ${oku(g.trust_value_5d)}</span>`
                   :`<span class="hint">外資 ${oku(g.foreign_value)}／投信 ${oku(g.trust_value)}</span>`}</div>
    <div class="con">${g.contributors.map(c=>
      `<span class="m click" data-s="${c.symbol}"><span>${esc((D.stocks[c.symbol]||{}).n||c.symbol)}</span>
        <span class="c mono">${c.symbol}</span>
        <span class="r mono ${cls(mode==="day"?c.inst_value:c.inst_value_5d)}">${oku(mode==="day"?c.inst_value:c.inst_value_5d)}億</span></span>`).join("")}</div></div>`;};
  $("lin").innerHTML=inflow.map(g=>card(g,"in")).join("")||`<p class="note">沒有淨流入的合格族群。</p>`;
  $("lout").innerHTML=outflow.map(g=>card(g,"out")).join("")||`<p class="note">沒有淨流出的合格族群。</p>`;
  $("ln").textContent=`${scored.length} 個共動夠緊的族群 · `+
    (mode==="day"?D.date:`${cov.window[0]} ～ ${D.date}（${cov.window_days} 個交易日）`);
  $("lnote").innerHTML=`資金＝<b>外陸資（不含外資自營商）＋投信</b>買賣超股數 × 當日收盤價，
    來源為證交所 T86 與櫃買三大法人日報；5 日累計是每日各自換算後相加，不是用最後一天的價格回推。
    只納入共動 ≥ 0.35、成員 3～30 檔的族群；缺法人資料的個股不計為零。
    ${cov.turnover?`成交重心＝當日成交金額前 ${cov.turnover_size} 名的入榜檔數；`:""}
    ${cov.has_branches?`券商同步＝有 2 家以上追蹤分點同日淨買超的檔數（目前追蹤 ${cov.branches} 家）。`:""}
    ${(!cov.turnover||!cov.has_branches)?`<b>${D.date} 的${!cov.has_branches?"分點榜單":""}${(!cov.turnover&&!cov.has_branches)?"與":""}${!cov.turnover?"成交重心":""}尚未由分點觀測站同步，該欄顯示「—」而不是 0。</b>`:""}
    標籤比較當日與 5 日方向：
    <b>連續流入</b>兩者皆正、<b>單日反彈</b>當日正但 5 日仍負、<b>短線調節</b>當日負但 5 日仍正、
    <b>連續流出</b>兩者皆負。${cov.institutional?"":"<b>當日尚未取得法人資料。</b>"}`;
}
$("mday").onclick=()=>{mode="day";$("mday").classList.add("on");$("m5d").classList.remove("on");renderLeaders();};
$("m5d").onclick=()=>{mode="5d";$("m5d").classList.add("on");$("mday").classList.remove("on");renderLeaders();};
renderLeaders();
$("lin").onclick=$("lout").onclick=e=>{
  if(e.target.closest("[data-s]"))return;
  const el=e.target.closest(".lead"); if(!el)return;
  $("fc").value="-9";
  open.add(el.dataset.g); renderGroups();
  const row=[...document.querySelectorAll("#gtb tr.g")].find(t=>t.dataset.g===el.dataset.g);
  if(row)row.scrollIntoView({behavior:"smooth",block:"center"});
};

/* 國際大廠報價 */
function renderQuotes(){
  const rows=Object.entries(D.partner_quotes)
    .sort((a,b)=>(b[1].change_pct??-99)-(a[1].change_pct??-99));
  $("qn").textContent=`${rows.length} 家 · 供應鏈對手方 · 紅漲綠跌`;
  $("qgrid").innerHTML=rows.map(([name,q])=>{
    const f=FOREIGN[name];
    const n=f?Object.values(f.links).reduce((a,c)=>a+Object.values(c).reduce((x,v)=>x+v.length,0),0):0;
    return `<button class="q" data-p="${esc(name)}">
    <div class="qn">${esc(name)}</div>
    <div class="qs mono">${esc(q.yahoo)} · ${esc(q.exchange||"")}</div>
    <div class="qp"><b class="${cls(q.change_pct)}">${q.price.toFixed(2)}</b>
      <span class="${cls(q.change_pct)}">${(q.change>0?"+":"")+q.change.toFixed(2)}</span>
      <span class="pill ${q.change_pct>0?"up":q.change_pct<0?"dn":""}">${pct(q.change_pct)}%</span></div>
    <div class="qs">${esc(q.currency)} · ${esc((q.as_of||"").replace("T"," ").slice(0,16))}</div>
    ${n?`<div class="qlinks">${n} 個台廠連結 · 點看成分</div>`:`<div class="qlinks">沒有供應鏈資料</div>`}
    </button>`;}).join("");
  $("qgrid").querySelectorAll(".q").forEach(el=>el.onclick=()=>openPartner(el.dataset.p));
}
renderQuotes();

/* 大廠供應鏈明細 */
function openPartner(name){
  const q=D.partner_quotes[name], f=FOREIGN[name];
  if(!q)return;
  $("pnm").textContent=name;
  $("pcd").textContent=`${q.yahoo} · ${q.exchange||""}`;
  const dirs=f?Object.entries(f.links):[];
  const total=dirs.reduce((a,[,c])=>a+Object.values(c).reduce((x,v)=>x+v.length,0),0);
  $("pbody").innerHTML=`
    <dl class="facts">
      <div class="fact"><dt>最新價</dt><dd class="${cls(q.change_pct)}">${q.price.toFixed(2)}
        <small>${esc(q.currency)}</small></dd></div>
      <div class="fact"><dt>漲跌</dt><dd class="${cls(q.change_pct)}">
        ${(q.change>0?"+":"")+q.change.toFixed(2)}<small>前收 ${q.previous.toFixed(2)}</small></dd></div>
      <div class="fact"><dt>漲跌幅</dt><dd class="${cls(q.change_pct)}">${pct(q.change_pct)}%</dd></div>
      <div class="fact"><dt>報價時間</dt><dd style="font-size:.82rem">
        ${esc((q.as_of||"").replace("T"," ").slice(0,16))}<small>來源可能延遲</small></dd></div>
    </dl>
    ${dirs.length?dirs.map(([dir,cats])=>{
      const n=Object.values(cats).reduce((x,v)=>x+v.length,0);
      return `<div class="dsec"><h3>${esc(dir)}<span>${n} 個台廠連結</span></h3>
      <div class="card" style="padding:4px 16px 12px">${Object.entries(cats)
        .sort((a,b)=>b[1].length-a[1].length).map(([cat,codes])=>
        `<div class="rel"><span class="rc">${esc(cat)}<small>${codes.length} 家</small></span>
         <span style="display:flex;flex-wrap:wrap;gap:5px">${codes.map(c=>
           D.stocks[c]?chip(c,true):`<span class="ext">${esc(c)}</span>`).join("")}</span></div>`).join("")}</div></div>`;
      }).join(""):`<p class="note">這家沒有收錄台廠供應鏈連結。</p>`}
    <p class="note">供應鏈分類來自 MoneyDJ，為靜態整理；報酬為台股當日漲跌幅。
      共 ${total} 個連結，點任一台廠可開個股明細。</p>`;
  $("pd").showModal();
}
$("px").onclick=()=>$("pd").close();

/* 族群排行 */
let sortK="r1",sortDesc=true,open=new Set();
function renderGroups(){
  const c=+$("fc").value, lo=+$("fn").value, hi=+$("fx").value;
  const rows=D.groups.filter(g=>(c<=-9||(g.comovement!=null&&g.comovement>=c))&&g.members>=lo&&g.members<=hi)
    .sort((a,b)=>{const x=a[sortK],y=b[sortK];
      if(x==null)return 1; if(y==null)return -1;
      return typeof x==="string"?(sortDesc?y.localeCompare(x):x.localeCompare(y)):(sortDesc?y-x:x-y);});
  $("gn").textContent=`${rows.length} 個族群`;
  $("gtb").innerHTML=rows.map(g=>{
    const mem=open.has(g.name)?`<tr class="mem"><td colspan="8"><div class="memin">${
      g.members_list.map(c=>chip(c,true)).join("")}</div></td></tr>`:"";
    return `<tr class="g" data-g="${esc(g.name)}"><td><strong>${esc(g.name)}</strong></td>
      <td style="color:var(--muted)">${esc(g.parent)}</td>
      <td class="num mono">${g.members}${g.quoted<g.members?`<span class="hint"> /${g.quoted}</span>`:""}</td>
      <td class="num">${cchip(g.comovement)}</td>
      <td class="num mono ${cls(g.r1)}">${pct(g.r1)}</td><td class="num mono ${cls(g.r5)}">${pct(g.r5)}</td>
      <td class="num mono ${cls(g.r20)}">${pct(g.r20)}</td><td class="num">${bar(g.breadth)}</td></tr>${mem}`;
  }).join("")||`<tr><td colspan="8" class="empty">沒有符合篩選條件的族群</td></tr>`;
  $("gtb").querySelectorAll("tr.g").forEach(tr=>tr.onclick=()=>{
    const k=tr.dataset.g; open.has(k)?open.delete(k):open.add(k); renderGroups();});
}
document.querySelectorAll("thead th[data-k]").forEach(th=>th.onclick=()=>{
  const k=th.dataset.k;
  if(sortK===k)sortDesc=!sortDesc; else{sortK=k;sortDesc=!["name","parent"].includes(k);}
  document.querySelectorAll("thead th[data-k]").forEach(x=>x.classList.toggle("on",x===th));
  renderGroups();});
["fc","fn","fx"].forEach(i=>$(i).onchange=renderGroups);
renderGroups();

/* 個股明細 */
const money=v=>{if(v==null)return "—";const a=Math.abs(v);
  return a>=1e12?(v/1e12).toFixed(2)+" 兆":a>=1e8?(v/1e8).toFixed(2)+" 億":a>=1e4?(v/1e4).toFixed(1)+" 萬":v.toFixed(0);};
const lots=v=>v==null?"—":v>=1e8?(v/1e8).toFixed(2)+" 億股":(v/1e4).toFixed(1)+" 萬股";
const chip=(c,click)=>{const s=D.stocks[c];
  return s?`<span class="m${click?" click":""}" data-s="${c}"><span>${esc(s.n)}</span><span class="c mono">${c}</span><span class="r mono ${cls(s.r1)}">${pct(s.r1)}</span></span>`
          :`<span class="m"><span class="c mono">${c}</span><span class="c">無報價</span></span>`;};

function find(q){
  q=q.trim(); if(!q)return null;
  if(D.stocks[q])return q;
  const hit=Object.keys(D.stocks).filter(c=>D.stocks[c].n.includes(q));
  return hit.length?hit[0]:null;
}
function openStock(code){
  const s=D.stocks[code]; if(!s)return;
  const p=D.profiles[code]||{}, pos=D.positions[code]||[];
  const all=D.groups.filter(g=>g.members_list.includes(code));
  const tight=all.filter(g=>g.comovement!=null&&g.comovement>=.35&&g.breadth!=null);
  const best=tight.length?Math.max(...tight.map(g=>g.breadth)):null;
  $("dnm").textContent=s.n; $("dcd").textContent=code;
  const tag=$("dtag");
  if(!tight.length){tag.className="tag solo";tag.textContent="無可用族群";}
  else if(best>=.6){tag.className="tag grp";tag.textContent="集體行情";}
  else if(best<=.3){tag.className="tag solo";tag.textContent="單一事件";}
  else{tag.className="tag mix";tag.textContent="廣度普通";}

  const facts=[["今日",`<span class="${cls(s.r1)}">${pct(s.r1)}%</span>`],
    ["5 日",`<span class="${cls(s.r5)}">${pct(s.r5)}%</span>`],
    ["20 日",`<span class="${cls(s.r20)}">${pct(s.r20)}%</span>`],
    [`自 ${D.since}`,`<span class="${cls(s.since)}">${pct(s.since)}%</span>`],
    ["股本",money(p.capital),p.market||""],
    ["已發行股數",lots(p.shares)],
    ["市值",money(p.market_cap),p.market_cap?`收盤 × 股數`:""],
    ["累計營收",money(p.revenue_cum?p.revenue_cum*1000:null),
      p.revenue_ym?`${p.revenue_ym.slice(0,3)-0+1911} 年 ${+p.revenue_ym.slice(3)} 月止`+
        (p.revenue_yoy!=null?` · 年增 ${pct(p.revenue_yoy)}%`:""):""]];
  const fl=D.flows[code]||{};
  const flowFacts=[["外陸資",fl.foreign_value==null?"—":
      `<span class="${cls(fl.foreign_value)}">${oku(fl.foreign_value)} 億</span>`,
      fl.foreign_shares==null?"":`${(fl.foreign_shares/1000).toLocaleString("zh-TW",{maximumFractionDigits:0})} 張`],
    ["投信",fl.trust_value==null?"—":
      `<span class="${cls(fl.trust_value)}">${oku(fl.trust_value)} 億</span>`,
      fl.trust_shares==null?"":`${(fl.trust_shares/1000).toLocaleString("zh-TW",{maximumFractionDigits:0})} 張`],
    ["券商同步買超",fl.buyers==null?"—":`${fl.buyers} 家`,
      fl.buyers==null?"分點榜單尚未同步":(fl.sellers?`另 ${fl.sellers} 家賣超`:"")],
    ["成交重心",fl.turnover_rank?`第 ${fl.turnover_rank} 名`:(D.leader_coverage.turnover?"未入榜":"—"),
      fl.turnover_amount?money(fl.turnover_amount):""],
    ["5 日法人",fl.inst_value_5d==null?"—":
      `<span class="${cls(fl.inst_value_5d)}">${oku(fl.inst_value_5d)} 億</span>`,
      fl.inst_value_5d==null?"":`外資 ${oku(fl.foreign_value_5d)}／投信 ${oku(fl.trust_value_5d)}`],
    ["5 日淨買天數",fl.inst_days?`${fl.inst_positive_days} / ${fl.inst_days} 日`:"—"]];
  const chainHtml=pos.length?groupChains(code,pos):
    `<p class="note">產業價值鏈資訊平台沒有收錄這檔股票。</p>`;
  const st=D.structure[code]||[];
  const relLabel={upstream:["上游供應商","向誰買"],peer:["同業競爭者","誰在搶"],downstream:["下游客戶","賣給誰"]};
  const structHtml=st.length?["upstream","peer","downstream"].map(rel=>{
    const rows=st.filter(x=>x[0]===rel); if(!rows.length)return "";
    const byCat={}; rows.forEach(([,cat,name,sym])=>(byCat[cat]=byCat[cat]||[]).push([name,sym]));
    return `<div class="rel"><span class="rc">${relLabel[rel][0]}<small>${relLabel[rel][1]} · ${rows.length} 家</small></span>
      <span>${Object.entries(byCat).sort((a,b)=>b[1].length-a[1].length).map(([cat,list])=>
        `<div style="margin-bottom:7px"><div class="hint" style="margin-bottom:4px">${esc(cat)}</div>
         <div style="display:flex;flex-wrap:wrap;gap:5px">${list.map(([name,sym])=>
           sym&&D.stocks[sym]?chip(sym,true):`<span class="ext">${esc(name)}</span>`).join("")}</div></div>`).join("")}</span></div>`;
  }).join(""):"";

  const factList=f=>f.map(([k,v,sub])=>
    `<div class="fact"><dt>${esc(k)}</dt><dd>${v}${sub?`<small>${esc(sub)}</small>`:""}</dd></div>`).join("");
  $("dbody").innerHTML=`
  <dl class="facts">${factList(facts)}</dl>

  <div class="dsec"><h3>當日資金流<span>${D.date} · 外陸資不含外資自營商</span></h3>
    <dl class="facts">${factList(flowFacts)}</dl></div>

  ${st.length?`<div class="dsec"><h3>營運結構<span>${st.length} 筆 · 來源：產業營運結構</span></h3>
    <div class="card" style="padding:4px 16px 12px">${structHtml}</div>
    <p class="note">對手方含未上市與外國企業，灰底代表本機沒有報價。清單為靜態整理，非即時更新。</p></div>`:""}

  <div class="dsec"><h3>台股產業鏈位置<span>${pos.length} 個環節 · 來源：產業價值鏈資訊平台</span></h3>
    ${chainHtml}
    <p class="note">佔比＝同環節「已上市櫃且有資料」成員的合計佔比，不是真實產品市占率：
      同環節不含外商與未上市競爭者，公司營收也通常橫跨多項業務。</p></div>

  <div class="dsec"><h3>所屬族群<span>${all.length} 個，其中 ${tight.length} 個共動夠緊</span></h3>
    <div class="card"><div class="scroll" style="max-height:300px"><table>
      <thead><tr><th>族群</th><th>產業別</th><th class="num">成員</th><th class="num">共動</th>
        <th class="num">今日%</th><th class="num">廣度</th></tr></thead><tbody>${
      all.slice().sort((a,b)=>(b.comovement??-9)-(a.comovement??-9)).map(g=>`<tr>
        <td><strong>${esc(g.name)}</strong></td><td style="color:var(--muted)">${esc(g.parent)}</td>
        <td class="num mono">${g.members}</td><td class="num">${cchip(g.comovement)}</td>
        <td class="num mono ${cls(g.r1)}">${pct(g.r1)}</td><td class="num">${bar(g.breadth)}</td></tr>`).join("")
      ||`<tr><td colspan="6" class="empty">這檔沒有被歸入任何族群</td></tr>`}</tbody></table></div></div></div>`;
  if(!$("sd").open)$("sd").showModal();
  $("dbody").scrollTop=0;
}
function groupChains(code,rows){
  const pos=rows.map(([chain,segment,members,cap,capN,rev,revN])=>({chain,segment,members,
    cap_share:cap,cap_peers:capN,rev_share:rev,rev_peers:revN}));
  const byChain={};
  pos.forEach(x=>(byChain[x.chain]=byChain[x.chain]||[]).push(x));
  return Object.entries(byChain).map(([ck,mine])=>{
    const chain=D.chains[ck]; if(!chain)return "";
    const own=new Set(mine.map(x=>x.segment));
    const tiers=["上游","中游","下游","未分層"].filter(t=>chain.segments.some(s=>s.tier===t));
    const rails=tiers.map(t=>`<div class="rail"><span class="tier">${t}</span><span class="segs">${
      chain.segments.filter(s=>s.tier===t).map(s=>
        `<span class="seg${own.has(s.code)?" me":""}">${esc(s.name)}<b>${s.symbols.length}</b></span>`).join("")}</span></div>`).join("");
    const detail=mine.map(x=>{
      const seg=chain.segments.find(s=>s.code===x.segment)||{symbols:[]};
      const peers=seg.symbols.filter(c=>c!==code);
      return `<div class="shares"><span>在「<b>${esc(seg.name||x.segment)}</b>」（同環節 ${x.members} 家）</span>
        <span>市值佔比 <b>${x.cap_share==null?"—":x.cap_share.toFixed(1)+"%"}</b>
          <span class="hint">${x.cap_peers} 家有市值</span></span>
        <span>營收佔比 <b>${x.rev_share==null?"—":x.rev_share.toFixed(1)+"%"}</b>
          <span class="hint">${x.rev_peers} 家有營收</span></span></div>
        ${peers.length?`<div class="peers">${peers.map(c=>chip(c,true)).join("")}</div>`:""}`;}).join("");
    return `<div class="chain"><div class="cn">${esc(chain.name)}產業鏈</div>${rails}${detail}</div>`;
  }).join("");
}
$("q").oninput=e=>{
  const c=find(e.target.value);
  $("qhint").textContent=c?`找到 ${D.stocks[c].n} ${c}，按 Enter 開啟`:
    (e.target.value.trim()?"找不到這檔股票":"輸入後按 Enter，或點畫面上任何股票");};
$("q").onkeydown=e=>{if(e.key==="Enter"){const c=find(e.target.value);if(c)openStock(c);}};
$("dx").onclick=()=>$("sd").close();
document.addEventListener("click",e=>{
  const el=e.target.closest("[data-s]"); if(!el)return;
  if($("pd").open)$("pd").close();
  openStock(el.dataset.s);});

</script></body></html>
'''


def build(date=None):
    report = sectors.report(date)
    window = json.loads(report['meta'].get('last_computed', '{}'))
    def trim(value, digits=2):
        return None if value is None else round(value, digits)

    # positions 只存代碼與數字，鏈名／環節名由前端查 chains，避免字串重複膨脹。
    positions = {symbol: [[x['chain'], x['segment'], x['members'],
                           trim(x['cap_share'], 1), x['cap_peers'],
                           trim(x['rev_share'], 1), x['rev_peers']] for x in rows]
                 for symbol, rows in report['positions'].items() if symbol in report['stocks']}
    profiles = {k: {'capital': v['capital'], 'shares': v['shares'], 'market': v['market'],
                    'market_cap': trim(v['market_cap'], 0), 'revenue_ym': v['revenue_ym'],
                    'revenue_cum': v['revenue_cum'], 'revenue_yoy': trim(v['revenue_yoy'], 1)}
                for k, v in report['profiles'].items()}
    stocks = {k: {'n': v['n'], 'r1': trim(v['r1']), 'r5': trim(v['r5']),
                  'r20': trim(v['r20']), 'since': trim(v['since'])}
              for k, v in report['stocks'].items()}
    chains = {code: {'name': body['name'],
                     'segments': [{'code': s['code'], 'name': s['name'], 'tier': s['tier'],
                                   'symbols': s['symbols']} for s in body['segments']]}
              for code, body in report['chains'].items()
              if any(s['symbols'] for s in body['segments'])}
    payload = {
        'date': report['date'], 'meta': report['meta'], 'window': window, 'since': report['since'],
        'stocks': stocks, 'profiles': profiles, 'positions': positions, 'chains': chains,
        'structure': report['structure'], 'partner_quotes': report['partner_quotes'],
        'leader_coverage': report['leader_coverage'],
        'leaders': [{k: (trim(v, 0) if k.endswith('_value') or k == 'turnover_amount'
                         else trim(v, 3) if k == 'breadth' else trim(v) if isinstance(v, float) else v)
                     for k, v in g.items() if k != 'contributors'}
                    | {'contributors': [{'symbol': c['symbol'], 'inst_value': trim(c['inst_value'], 0),
                                         'inst_value_5d': trim(c['inst_value_5d'], 0),
                                         'buyers': c['buyers'], 'turnover_rank': c['turnover_rank']}
                                        for c in g['contributors']]}
                    for g in report['leaders']],
        'flows': {k: {'foreign_shares': v['foreign_shares'], 'trust_shares': v['trust_shares'],
                      'foreign_value': trim(v['foreign_value'], 0), 'trust_value': trim(v['trust_value'], 0),
                      'foreign_value_5d': trim(v['foreign_value_5d'], 0),
                      'trust_value_5d': trim(v['trust_value_5d'], 0),
                      'inst_value_5d': trim(v['inst_value_5d'], 0),
                      'inst_days': v['inst_days'], 'inst_positive_days': v['inst_positive_days'],
                      'buyers': v['buyers'], 'sellers': v['sellers'],
                      'turnover_rank': v['turnover_rank'], 'turnover_amount': v['turnover_amount']}
                  for k, v in report['flows'].items()
                  if v['foreign_shares'] is not None or v['buyers'] or v['turnover_rank']},
        'groups': [{'name': g['name'], 'parent': g['parent'], 'members': g['members'],
                    'quoted': g['quoted'], 'comovement': trim(g['comovement']),
                    'r1': trim(g['r1']), 'r5': trim(g['r5']), 'r20': trim(g['r20']),
                    'breadth': trim(g['breadth'], 3), 'members_list': g['members_list']}
                   for g in report['groups']],
        'foreign': [{'partner': name, 'code': body['code'], 'links': body['links']}
                    for name, body in sorted(report['foreign'].items(),
                                             key=lambda kv: -sum(len(v) for cats in kv[1]['links'].values()
                                                                 for v in cats.values()))],
    }
    html = TEMPLATE.replace('__DATA__', json.dumps(payload, ensure_ascii=False,
                                                   separators=(',', ':'), allow_nan=False))
    # Readers always see a complete last-good board while another process updates it.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=OUT.parent,
                                     prefix='.board-', suffix='.html', delete=False) as tmp:
        tmp.write(html)
        temporary = Path(tmp.name)
    try:
        os.replace(temporary, OUT)
    finally:
        temporary.unlink(missing_ok=True)
    return OUT, len(payload['groups']), len(payload['stocks'])


if __name__ == '__main__':
    path, groups, stocks = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f'{path} · 族群 {groups} · 個股 {stocks} · {path.stat().st_size/1024:.0f} KB')
