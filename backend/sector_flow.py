"""Serve the user's sector board and share its authoritative multi-label dictionary."""
from __future__ import annotations
import base64,hashlib,json,re,sqlite3,subprocess,sys,threading
from datetime import datetime
from pathlib import Path
from . import db

PROTOTYPE=db.ROOT/'prototype'
_lock=threading.Lock()
_job={'status':'idle','message':'使用已保存的族群看板','started_at':None,'finished_at':None,'errors':[]}

def connect():
    conn=sqlite3.connect((PROTOTYPE/'sectors.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=10)
    conn.row_factory=sqlite3.Row
    return conn

def classification():
    try:
        conn=connect()
        try:
            conn.execute('BEGIN')
            groups=[dict(r) for r in conn.execute('SELECT name,parent FROM sector_groups ORDER BY name')]
            memberships={}
            for row in conn.execute('SELECT DISTINCT m.symbol,m.name FROM sector_members m JOIN sector_groups g USING(name) ORDER BY m.symbol,m.name'):
                memberships.setdefault(row['symbol'],[]).append(row['name'])
            meta={r['key']:r['value'] for r in conn.execute("SELECT key,value FROM meta WHERE key IN ('dictionary_snapshot','dictionary_source')")}
        finally:conn.close()
        if not groups:raise ValueError('族群字典尚無資料')
        return dict(memberships=memberships,groups=groups,snapshot=meta.get('dictionary_snapshot'),source='prototype/sectors.sqlite3',error='')
    except (sqlite3.Error,ValueError) as e:
        return dict(memberships={},groups=[],snapshot=None,source='prototype/sectors.sqlite3',error='族群字典目前無法讀取，未分類不代表該股沒有族群')

def status():
    result=dict(_job)
    try:
        conn=connect()
        try:
            result['computed']=conn.execute('SELECT MAX(date) FROM sector_days').fetchone()[0]
            result['institutional']=conn.execute('SELECT MAX(date) FROM institutional_days').fetchone()[0]
            row=conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
            result['last_update']=row[0] if row else None
        finally:conn.close()
    except sqlite3.Error:
        result.update(computed=None,institutional=None,last_update=None)
    result.update(available=result.get('computed'),behind=False)
    return result

def refresh(force=False):
    if not isinstance(force,bool):raise ValueError('force 需為布林值')
    if not (PROTOTYPE/'update.py').is_file():raise ValueError('找不到族群更新程式')
    if not _lock.acquire(blocking=False):return status()
    _job.update(status='running',message='更新族群行情、法人與看板中',started_at=datetime.now().isoformat(timespec='seconds'),finished_at=None,errors=[])
    def worker():
        try:
            # Reuse the existing update pipeline in an isolated process; never start branch sync.
            script="import json,update; r=update.run(force="+str(force)+"); print('SECTOR_RESULT:'+json.dumps(r,ensure_ascii=False))"
            run=subprocess.run([sys.executable,'-c',script],cwd=PROTOTYPE,capture_output=True,text=True,timeout=900)
            lines=[s.removeprefix('SECTOR_RESULT:') for s in run.stdout.splitlines() if s.startswith('SECTOR_RESULT:')]
            if run.returncode or not lines:raise ValueError('族群更新未完成，請查看原型資料與來源狀態')
            summary=json.loads(lines[-1]);ok=summary.get('ok',False)
            _job.update(status='complete' if ok else 'partial',message='族群更新完成' if ok else '更新完成，部分來源仍有缺漏',errors=summary.get('errors',[]),target=summary.get('target'))
        except Exception as e:_job.update(status='failed',message=str(e)[:180],errors=[str(e)[:180]])
        finally:_job['finished_at']=datetime.now().isoformat(timespec='seconds');_lock.release()
    try:threading.Thread(target=worker,daemon=True).start()
    except Exception:_job['status']='failed';_lock.release();raise
    return status()

# Inject after the original script has declared its helpers. Read query text as data, never code.
BRIDGE=r'''
<script>
const requestedGroup=new URLSearchParams(location.search).get('group');
if(requestedGroup && D.groups.some(g=>g.name===requestedGroup)){
  $('fc').value='-9';$('fn').value='1';$('fx').value='9999';
  open.add(requestedGroup);renderGroups();
  const row=Array.from(document.querySelectorAll('tr.g')).find(r=>r.dataset.g===requestedGroup);
  if(row){
    row.style.background='#edf4df';
    const scroller=row.closest('.scroll');
    if(scroller){
      scroller.scrollTop+=row.getBoundingClientRect().top-scroller.getBoundingClientRect().top-48;
      window.scrollTo(0,window.scrollY+scroller.getBoundingClientRect().top-16);
    }
  }
}
</script>'''

def board():
    path=PROTOTYPE/'board.html'
    if not path.is_file():raise ValueError('尚未產生族群看板，請先按更新')
    page=path.read_text(encoding='utf-8')
    # Remap only the board's explicit API strings. Do not expose the prototype directory.
    page=page.replace('fetch("/api/status")','fetch("/api/sector-flow/status")').replace('fetch("/api/refresh",','fetch("/api/sector-flow/refresh",')
    page=page.replace('族群廣度 · 分點觀測站','族群流向 · 分點觀測站').replace('<h1>族群廣度</h1>','<h1>族群流向</h1>')
    page=page.replace('</style>', '.wrap{max-width:none;padding:20px 8px 50px}</style>',1)
    page=page.replace('let polling=null,lastFinish=null;', 'let polling=null,lastFinish=null,wasUpdating=false;')
    page=page.replace('const running=s.status==="running";', 'const running=s.status==="running"; if(running)wasUpdating=true; if(!running&&wasUpdating&&s.finished_at){location.reload();return;}')
    return page.replace('</body>',BRIDGE+'</body>').encode('utf-8')


def board_policy(page):
    # Only the two trusted inline scripts in the generated board can execute.
    hashes=["'sha256-"+base64.b64encode(hashlib.sha256(script).digest()).decode()+"'"
            for script in re.findall(rb'<script>(.*?)</script>',page,re.S)]
    return "default-src 'self'; script-src "+' '.join(hashes)+"; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"
