import gzip,hashlib,importlib.util,io,json,sqlite3,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
module=Path(__file__).with_name('cloud_runtime.py')
if not module.exists():module=ROOT/'scripts/cloud_runtime.py'
spec=importlib.util.spec_from_file_location('cloud',module);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)


class Cloud(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
  self.drive=self.base/'cloud/70';self.drive.mkdir(parents=True);self.cache=self.base/'cache';self.root=self.base/'app';self.root.mkdir()
  folder=self.drive.parent/c.BASE;folder.mkdir();self.folder=folder
  db=self.base/'fixture.sqlite3'
  with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE x(a)');conn.execute('INSERT INTO x VALUES (42)')
  self.contents={'data/value.json':b'{"value":1}','data/raw/cold.json':b'old raw data','data/monitor.sqlite3':db.read_bytes(),'research/v6_example/model.json':b'{"frozen":true}'}
  archive=self.base/'archive.tar.gz';rows=[]
  with tarfile.open(archive,'w:gz') as tar:
   for name,value in self.contents.items():
    info=tarfile.TarInfo('files/'+name);info.size=len(value);tar.addfile(info,io.BytesIO(value))
    rows.append({'path':name,'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest(),'archive':'base.tar.gz','kind':'sqlite_backup' if name.endswith('sqlite3') else 'file'})
  raw=archive.read_bytes();cut=len(raw)//2;segments=[]
  for i,value in enumerate((raw[:cut],raw[cut:])):
   name=f'part{i}';(folder/name).write_bytes(value);segments.append({'name':name,'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()})
  catalog={'format':c.CATALOG_FORMAT,'snapshot':'fixture','files':rows,'archives':[{'name':'base.tar.gz','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'segments':segments}]}
  p=self.drive/'BASE.json.gz';p.write_bytes(gzip.compress(json.dumps(catalog).encode()))
  c.atomic(self.drive/'CURRENT.json',{'catalog':p.name,'catalogSha256':c.sha(p),'layers':[]})
  self.runtime=c.Runtime(self.drive,self.cache,self.root)

 def test_cloud_source_and_sqlite_integrity(self):
  self.runtime.warm()
  self.assertFalse((self.cache/'data/raw/cold.json').exists())
  with sqlite3.connect(self.cache/'data/monitor.sqlite3') as conn:self.assertEqual(conn.execute('SELECT a FROM x').fetchone()[0],42)
  self.assertEqual((self.cache/'research/v6_example/model.json').read_bytes(),self.contents['research/v6_example/model.json'])

 def test_existing_working_changes_never_overwritten(self):
  self.runtime.ensure(['data/value.json']);p=self.cache/'data/value.json';p.write_text('changed')
  self.runtime.ensure(['data/value.json']);self.assertEqual(p.read_text(),'changed')

 def test_cloud_corruption_installs_nothing(self):
  p=self.folder/'part1';data=p.read_bytes();p.write_bytes(data[:-1]+bytes([data[-1]^1]))
  with self.assertRaises(Exception):self.runtime.ensure(['data/value.json'])
  self.assertFalse((self.cache/'data/value.json').exists())

 def test_catalog_tamper_rejected(self):
  (self.drive/'BASE.json.gz').write_bytes(b'wrong')
  with self.assertRaises(ValueError):c.Runtime(self.drive,self.base/'other',self.root)

 def test_cache_cannot_be_in_drive(self):
  with self.assertRaises(ValueError):c.Runtime(self.drive,self.drive/'cache',self.root)

 def test_cache_symlink_escape_rejected(self):
  outside=self.base/'outside';outside.mkdir();(self.cache/'data').symlink_to(outside)
  with self.assertRaises(ValueError):self.runtime.ensure(['data/value.json'])

 def test_sync_waits_for_readback_and_never_deletes_cold_files(self):
  self.runtime.ensure(['data/value.json']);(self.cache/'data/value.json').write_text('new')
  head=(self.drive/'CURRENT.json').read_bytes();result=self.runtime.prepare_sync()
  self.assertEqual((self.drive/'CURRENT.json').read_bytes(),head)
  self.assertEqual(result['changedFiles'],1)
  pending=json.loads((self.cache/'control/PENDING.json').read_text());layer=json.loads((self.drive/pending['layer']).read_text())
  self.assertEqual([r['path'] for r in layer['changed']],['data/value.json'])
  self.assertNotIn('deleted',layer)
  self.assertTrue(self.runtime.prepare_sync()['resumed'])

 def test_confirm_and_new_machine_reads_delta(self):
  self.runtime.ensure(['data/value.json']);(self.cache/'data/value.json').write_text('new value')
  self.runtime.prepare_sync();pending=json.loads((self.cache/'control/PENDING.json').read_text())
  receipt={'verifiedAt':c.stamp(),'ownerOnly':True,'files':[{**r,'fileId':'synthetic-test-'+str(i),'readBackSha256':r['sha256']} for i,r in enumerate(pending['files'])]}
  p=self.base/'receipt.json';c.atomic(p,receipt);self.runtime.confirm(p)
  other=c.Runtime(self.drive,self.base/'new-device',self.root);other.ensure(['data/value.json'])
  self.assertEqual((other.cache/'data/value.json').read_text(),'new value')

 def test_confirm_bad_receipt_does_not_promote(self):
  self.runtime.ensure(['data/value.json']);(self.cache/'data/value.json').write_text('new');self.runtime.prepare_sync()
  previous=(self.drive/'CURRENT.json').read_bytes();p=self.base/'bad.json';c.atomic(p,{'verifiedAt':c.stamp(),'ownerOnly':True,'files':[]})
  with self.assertRaises(ValueError):self.runtime.confirm(p)
  self.assertEqual((self.drive/'CURRENT.json').read_bytes(),previous)

 def test_low_space_no_partial_output(self):
  with patch.object(c,'disk',side_effect=OSError('full')):
   with self.assertRaises(OSError):self.runtime.ensure(['data/value.json'])
  self.assertFalse((self.cache/'data/value.json').exists())

 def test_new_cloud_version_not_silently_overwrites_workspace(self):
  head=json.loads((self.drive/'CURRENT.json').read_text());head['newVersion']=1;c.atomic(self.drive/'CURRENT.json',head)
  with self.assertRaises(ValueError):c.Runtime(self.drive,self.cache,self.root)

 def test_replaced_checkout_export_included_in_sync(self):
  rel='frontend/public/v6-research/evening-forecast.json'
  self.runtime.rows[rel]={'path':rel,'sha256':'0'*64,'bytes':0,'archive':'base.tar.gz'}
  p=self.root/rel;p.parent.mkdir(parents=True);p.write_text('{"saved":"new"}')
  self.runtime.prepare_sync();pending=json.loads((self.cache/'control/PENDING.json').read_text())
  layer=json.loads((self.drive/pending['layer']).read_text())
  self.assertEqual(layer['changed'][0]['path'],rel)

 def test_cloud_queue_does_not_publish_or_touch_dataset(self):
  head=(self.drive/'CURRENT.json').read_bytes();self.runtime.queue('v6')
  self.assertEqual(len(list((self.cache/'control/queue').glob('*.json'))),1)
  self.assertEqual((self.drive/'CURRENT.json').read_bytes(),head)

 def test_display_does_not_hydrate_frozen_rebuild_inputs(self):
  with patch.object(self.runtime,'ensure') as ensure:
   self.runtime.route('/api/margin-statistics/overview')
   ensure.assert_called_once_with(['data/margin_statistics/sources.sqlite3'])


if __name__=='__main__':unittest.main(verbosity=2)
