"""Drive desktop snapshots -> independent local working cache. Never publish models.

Only immutable archives/objects are read from Drive. SQLite connections always
use the local cache. An absent Drive or snapshot is an error, never an empty DB.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
BASE='20261107T140242-首次完整備份'
CATALOG_FORMAT='branch-monitor-drive-runtime-1'
CHUNK=8*1024**2
RESERVE=512*1024**2
_instance=None
_instance_lock=threading.Lock()


def stamp():return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='microseconds')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
def safe(s):
 p=PurePosixPath(s)
 if not p.parts or p.is_absolute() or '..' in p.parts or '\\' in s or str(p)!=s:raise ValueError('Unsafe cloud path')
 return s
def atomic(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.write-',dir=p.parent)
 try:
  with os.fdopen(fd,'w') as f:json.dump(v,f,ensure_ascii=False,sort_keys=True,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def disk(p,n):
 if shutil.disk_usage(p).free<n+RESERVE:raise OSError('雲端工作暫存空間不足；未刪除任何資料')


def locate():
 matches=[]
 for root in (Path.home()/'Library/CloudStorage').glob('GoogleDrive-*'):
  for name in ('我的雲端硬碟','My Drive'):
   p=root/name/'Branch Monitor 備份'/'70-雲端運行資料'
   if p.is_dir():matches.append(p)
 if len(matches)!=1:raise ValueError('請登入Google Drive桌面版並選串流；需要唯一的Branch Monitor雲端運行資料夾')
 return matches[0]


class Joined:
 """Concatenate verified Drive transport parts without a second compressed copy."""
 def __init__(self,folder,row):
  self.folder=folder;self.row=row;self.i=0;self.file=None;self.part=None;self.n=0;self.total=0
  self.h=hashlib.sha256();self.whole=hashlib.sha256()
 def read(self,n=-1):
  if n<0:raise ValueError('Bounded reads only')
  out=bytearray()
  while len(out)<n:
   if self.file is None:
    if self.i==len(self.row['segments']):
     if self.total!=self.row['bytes'] or self.whole.hexdigest()!=self.row['sha256']:raise ValueError('Cloud archive hash mismatch')
     break
    self.part=self.row['segments'][self.i];p=self.folder/safe(self.part['name'])
    if p.is_symlink() or p.stat().st_size!=self.part['bytes']:raise ValueError('Cloud transport size/type mismatch')
    self.file=p.open('rb');self.n=0;self.h=hashlib.sha256()
   b=self.file.read(n-len(out))
   if b:self.h.update(b);self.whole.update(b);self.n+=len(b);self.total+=len(b);out.extend(b)
   else:
    self.file.close();self.file=None
    if self.n!=self.part['bytes'] or self.h.hexdigest()!=self.part['sha256']:raise ValueError('Cloud segment hash mismatch')
    self.i+=1
  return bytes(out)
 def close(self):
  if self.file:self.file.close();self.file=None


class Runtime:
 def __init__(self,drive,cache,root=ROOT):
  self.drive=Path(drive).resolve();self.cache=Path(cache).resolve();self.root=Path(root).resolve();self.mutex=threading.RLock()
  mount=next((p for p in self.drive.parents if p.name.startswith('GoogleDrive-')),self.drive)
  if self.cache.is_relative_to(mount) or self.cache.is_relative_to(self.root):raise ValueError('工作暫存必須獨立於Drive和GitHub程式資料夾')
  current=json.loads((self.drive/'CURRENT.json').read_text())
  catalog=self.drive/safe(current['catalog'])
  if sha(catalog)!=current['catalogSha256']:raise ValueError('Cloud catalog SHA256 mismatch')
  with gzip.open(catalog,'rt') as f:self.catalog=json.load(f)
  if self.catalog.get('format')!=CATALOG_FORMAT:raise ValueError('Unsupported cloud catalog')
  self.rows={r['path']:r for r in self.catalog['files']};self.archives={r['name']:r for r in self.catalog['archives']}
  self.current=current;self.cache.mkdir(parents=True,exist_ok=True)
  if not (self.drive.parent/BASE).is_dir():raise ValueError('Cloud full baseline missing')
  self.layers=[]
  for layer in current.get('layers',[]):
   p=self.drive/safe(layer['path'])
   if sha(p)!=layer['sha256']:raise ValueError('Cloud layer hash mismatch')
   value=json.loads(p.read_text());self.layers.append(value)
   for r in value['changed']:self.rows[safe(r['path'])]=r
  self.head=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
  marker=self.cache/'HEAD.json'
  if marker.exists() and json.loads(marker.read_text())['head']!=self.head:
   raise ValueError('雲端版本已改變，先核對本機未同步變更，不能覆寫工作資料')
  if not marker.exists():atomic(marker,{'head':self.head,'createdAt':stamp(),'source':'Google Drive desktop'})

 def dest(self,rel):
  p=self.cache/safe(rel)
  if not p.resolve().is_relative_to(self.cache):raise ValueError('Cache link escapes root')
  return p

 @contextmanager
 def locked(self):
  with self.mutex:
   with (self.cache/'.runtime.lock').open('a') as f:
    fcntl.flock(f,fcntl.LOCK_EX);yield

 def ensure(self,paths):
  with self.locked():
   requested=[p for p in paths if p in self.rows and not self.dest(p).exists()]
   groups=defaultdict(list)
   for p in requested:groups[self.rows[p].get('archive','layer')].append(p)
   for name,names in groups.items():
    disk(self.cache,sum(self.rows[p]['bytes'] for p in names))
    with tempfile.TemporaryDirectory(prefix='.hydrate-',dir=self.cache) as tmp:
     tmp=Path(tmp);seen=set()
     if name=='layer':
      for p in names:
       r=self.rows[p];out=tmp/safe(p);out.parent.mkdir(parents=True,exist_ok=True)
       with out.open('wb') as f:
        for c in r['chunks']:
         q=self.drive/safe(c['path'])
         if sha(q)!=c['sha256']:raise ValueError('Cloud object hash mismatch')
         data=gzip.decompress(q.read_bytes())
         if len(data)!=c['rawBytes']:raise ValueError('Cloud chunk size mismatch')
         f.write(data)
       if sha(out)!=r['sha256']:raise ValueError('Cloud file hash mismatch')
       seen.add(p)
     else:
      stream=Joined(self.drive.parent/BASE,self.archives[name])
      try:
       with gzip.GzipFile(fileobj=stream,mode='rb') as gz:
        with tarfile.open(fileobj=gz,mode='r|') as tar:
         wanted=set(names)
         for item in tar:
          rel=item.name.removeprefix('files/')
          if rel not in wanted:continue
          if not item.isreg() or not item.name.startswith('files/') or rel in seen:raise ValueError('Unsafe/duplicate archive member')
          r=self.rows[rel]
          if item.size!=r['bytes']:raise ValueError('Cloud member size mismatch')
          out=tmp/safe(rel);out.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256()
          with tar.extractfile(item) as f,out.open('wb') as target:
           for b in iter(lambda:f.read(1024**2),b''):h.update(b);target.write(b)
          if h.hexdigest()!=r['sha256']:raise ValueError('Cloud member SHA256 mismatch')
          seen.add(rel)
        while gz.read(1024**2):pass
       while stream.read(1024**2):pass
      finally:stream.close()
     if seen!=set(names):raise ValueError('Cloud members missing')
     for rel in names:
      out=tmp/rel;dest=self.dest(rel)
      if self.rows[rel].get('kind')=='sqlite_backup':
       with sqlite3.connect(out.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Cloud SQLite integrity failure')
      row=self.rows[rel]
      if row.get('source_mtime_ns'):os.utime(out,ns=(row['source_mtime_ns'],row['source_mtime_ns']))
      os.chmod(out,row.get('mode',0o600)&0o777)
      dest.parent.mkdir(parents=True,exist_ok=True);os.replace(out,dest)
     print(json.dumps({'cloud_archive':name,'materialized_files':len(names),'verified':True}),flush=True)
   return len(requested)

 def warm(self):
  def chosen(p):
   if p.startswith('data/'):
    return not (p.startswith(('data/raw/','data/code-backup','data/margin_statistics/raw/','data/alpha_beta/raw/','data/v11_manual/inputs/')))
   return p.startswith(('prototype/','frontend/public/v6-research/','research/v6'))
  return self.ensure([p for p in self.rows if chosen(p)])

 def route(self,path):
  if path.startswith('/api/margin-statistics'):
   self.ensure([p for p in self.rows if p.startswith('research/margin')])

 def link(self):
  for rel in ('data','research'):
   dest=self.root/rel;target=self.cache/rel;target.mkdir(exist_ok=True)
   if dest.is_symlink():
    if dest.resolve()!=target:raise ValueError('Checkout points to another dataset')
   elif dest.exists():raise ValueError('Checkout already has local data; do not overwrite')
   else:dest.symlink_to(target,target_is_directory=True)
  # Only ignored, non-code prototype files and saved exports are linked.
  tracked=set(subprocess.check_output(['git','-C',str(self.root),'ls-files','-z']).decode().split('\0'))
  for rel in self.rows:
   if not rel.startswith(('prototype/','frontend/public/v6-research/')) or rel in tracked:continue
   target=self.dest(rel)
   if not target.exists():continue
   dest=self.root/rel;dest.parent.mkdir(parents=True,exist_ok=True)
   if dest.is_symlink() and dest.resolve()==target:continue
   if dest.exists():continue  # A manual export may replace its original symlink.
   dest.symlink_to(target)

 def status(self):
  return {'mode':'google_drive_stream','url':'http://localhost:3001','drive':str(self.drive),
          'workingCache':str(self.cache),'baseSnapshot':self.catalog['snapshot'],
          'head':self.head,'remoteFiles':len(self.rows),'storageNote':'資料由Drive取得；SQLite只在本機工作暫存讀寫；雲端寫入需另外確認'}

 def queue(self,model):
  identity=uuid.uuid4().hex
  atomic(self.cache/'control/queue'/(identity+'.json'),{'model':model,'savedAt':stamp(),'id':identity,'status':'pending_cloud'})

 def prepare_sync(self):
  """Stage changed working files in Drive. No pointer promotion or strategies."""
  with self.locked():
   if (self.cache/'control/PENDING.json').exists():return {'status':'pending_drive_upload_and_readback','plan':str(self.cache/'control/PENDING.json'),'resumed':True}
   identifier=datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%dT%H%M%S%f')
   work=self.drive/'versions'/identifier;work.mkdir(parents=True)
   changed=[];upload=[]
   candidates={p.relative_to(self.cache).as_posix():p for base in ('data','research','prototype','frontend') for p in (self.cache/base).rglob('*')}
   for rel in self.rows:
    if rel.startswith(('prototype/','frontend/public/v6-research/')):
     p=self.root/rel
     if p.is_file() and not p.is_symlink():candidates[rel]=p
   captured={}
   for rel,p in sorted(candidates.items()):
     if not p.is_file() or p.is_symlink() or p.name.endswith(('-wal','-shm','-journal','.lock','.pid','.pem','.key','.p12')) or p.name in ('.lock','.env','credentials.json','token.json') or p.name.startswith(('.env.','client_secret','service-account')):continue
     before=p.stat();old=self.rows.get(rel)
     side_before={s:(q.stat().st_size,q.stat().st_mtime_ns) for s in ('-wal','-journal') if (q:=Path(str(p)+s)).exists()}
     if old and sha(p)==old['sha256'] and not any(v[0] for v in side_before.values()):continue
     capture=p
     with tempfile.TemporaryDirectory(prefix='.snapshot-',dir=self.cache) as tmp:
      if p.suffix in ('.sqlite3','.sqlite','.db'):
       disk(self.cache,before.st_size);capture=Path(tmp)/'db.sqlite3'
       with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(capture) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite backup integrity failure')
      h=hashlib.sha256();chunks=[];total=0
      with capture.open('rb') as f:
       for b in iter(lambda:f.read(CHUNK),b''):
        h.update(b);total+=len(b);content=gzip.compress(b,mtime=0);digest=hashlib.sha256(content).hexdigest()
        name='objects/'+digest+'.gz';target=work/name;target.parent.mkdir(exist_ok=True)
        if not target.exists():
         disk(self.cache,len(content))
         used=2_578_983_665+sum(q.stat().st_size for q in self.drive.rglob('*') if q.is_file())
         if used+len(content)>20_000_000_000:raise OSError('雲端運行資料超過20GB專案預算；未刪除舊版')
         target.write_bytes(content)
        chunks.append({'path':target.relative_to(self.drive).as_posix(),'sha256':digest,'rawBytes':len(b)})
        upload.append({'path':target.relative_to(self.drive).as_posix(),'bytes':len(content),'sha256':digest})
      after=p.stat()
      if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Working source changed during sync')
      side_after={s:(q.stat().st_size,q.stat().st_mtime_ns) for s in ('-wal','-journal') if (q:=Path(str(p)+s)).exists()}
      if side_before!=side_after:raise ValueError('Working WAL changed during sync')
      captured[p]=(after.st_size,after.st_mtime_ns)
      changed.append({'path':rel,'bytes':total,'sha256':h.hexdigest(),'chunks':chunks,'kind':'sqlite_backup' if capture!=p else 'file'})
   if any((p.stat().st_size,p.stat().st_mtime_ns)!=signature for p,signature in captured.items()):raise ValueError('Working files changed before sync finished')
   layer={'format':CATALOG_FORMAT,'id':identifier,'createdAt':stamp(),'parentHead':self.head,'changed':changed,
          'queueIds':[p.stem for p in (self.cache/'control/queue').glob('*.json')],'cloudStatus':'pending_verification'}
   atomic(work/'MANIFEST.json',layer)
   upload.append({'path':(work/'MANIFEST.json').relative_to(self.drive).as_posix(),'bytes':(work/'MANIFEST.json').stat().st_size,'sha256':sha(work/'MANIFEST.json')})
   atomic(self.cache/'control/PENDING.json',{'layer':(work/'MANIFEST.json').relative_to(self.drive).as_posix(),'files':list({r['path']:r for r in upload}.values())})
   return {'status':'pending_drive_upload_and_readback','plan':str(self.cache/'control/PENDING.json'),'changedFiles':len(changed)}

 def confirm(self,receipt):
  with self.locked():
   pending=json.loads((self.cache/'control/PENDING.json').read_text());proof=json.loads(Path(receipt).read_text())
   by={r['path']:r for r in proof.get('files',[])}
   if len(by)!=len(pending['files']) or len(by)!=len(proof.get('files',[])) or not proof.get('verifiedAt') or proof.get('ownerOnly') is not True:raise ValueError('Incomplete actual cloud readback receipt')
   for row in pending['files']:
    r=by.get(row['path'],{})
    if r.get('readBackSha256')!=row['sha256'] or r.get('bytes')!=row['bytes'] or not r.get('fileId'):raise ValueError('Cloud readback mismatch')
    if sha(self.drive/safe(row['path']))!=row['sha256']:raise ValueError('Staged file changed')
   layer=self.drive/safe(pending['layer']);value=json.loads(layer.read_text())
   current=json.loads((self.drive/'CURRENT.json').read_text())
   if hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()!=value['parentHead']:raise ValueError('Cloud writer conflict')
   atomic(layer.parent/'RECEIPT.json',proof)
   current.setdefault('layers',[]).append({'path':pending['layer'],'sha256':sha(layer)})
   current['updatedAt']=stamp();atomic(self.drive/'CURRENT.json',current)
   self.head=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest();atomic(self.cache/'HEAD.json',{'head':self.head,'updatedAt':stamp()})
   atomic(self.cache/'control/LAST_CONFIRMED.json',{'head':self.head,'verifiedAt':proof['verifiedAt'],'queueIds':value['queueIds']})
   (self.cache/'control/PENDING.json').unlink()
   return {'status':'data_verified_pointer_pending_drive_sync','head':self.head}


def instance(root=ROOT):
 global _instance
 with _instance_lock:
  if _instance is None:
   p=Path(root)/'cloud-runtime.local.json'
   config=json.loads(p.read_text());_instance=Runtime(config['drive'],config['cache'],root)
 return _instance


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['configure','warm','launch','status','prepare-sync','confirm'])
 p.add_argument('--drive',type=Path);p.add_argument('--cache',type=Path);p.add_argument('--receipt',type=Path);a=p.parse_args()
 if a.action=='configure':
  drive=a.drive or locate();cache=a.cache or Path.home()/'Library/Caches/BranchMonitorCloud/workspace'
  atomic(ROOT/'cloud-runtime.local.json',{'drive':str(drive),'cache':str(cache),'port':3001});print('Configured Google Drive desktop runtime');return
 runtime=instance()
 if a.action in ('warm','launch'):runtime.warm();runtime.link()
 if a.action=='launch':
  os.environ['BRANCH_MONITOR_CLOUD']='1';os.environ['BRANCH_MONITOR_DATA']=str(runtime.cache/'data')
  os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/launch.py'),'--port','3001','--browser-host','localhost','--no-scheduler'])
 elif a.action=='prepare-sync':print(json.dumps(runtime.prepare_sync(),ensure_ascii=False))
 elif a.action=='confirm':
  if not a.receipt:p.error('--receipt required')
  print(json.dumps(runtime.confirm(a.receipt),ensure_ascii=False))
 else:print(json.dumps(runtime.status(),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
