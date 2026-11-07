import contextlib
import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import sys
import types
import unittest
from unittest.mock import patch, Mock

module = Path(__file__).with_name('drive_backup_p5.py')
if not module.exists(): module = Path(__file__).resolve().parents[1] / 'scripts/drive_backup.py'
spec = importlib.util.spec_from_file_location('backup', module)
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'app'; self.home = self.root / 'backup'
        for name in b.ROOTS: (self.source / name).mkdir(parents=True)
        for name in b.EXPORTS:
            p = self.source / 'frontend/public/v6-research' / name
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')
        (self.source / 'data/a.json').write_text('{"before":1}')
        self.backup = b.Backup(self.source, self.home)
        self.backup.base.mkdir(parents=True)
        rows = []
        for rel in b.inventory(self.source):
            p = self.source / rel
            rows.append({'path': rel, 'bytes': p.stat().st_size, 'sha256': b.sha(p),
                         'source_mtime_ns': p.stat().st_mtime_ns, 'kind': 'file'})
        (self.backup.base / 'FILES.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        (self.backup.base / 'DATABASES.json').write_text('[]')
        receipt = self.home / '00-計畫與盤點/包4傳輸/UPLOAD_RECEIPT.json'
        receipt.parent.mkdir(parents=True); receipt.write_text('{}')
        self.pin = patch.object(b, 'BASE_RECEIPT_SHA', b.sha(receipt)); self.pin.start()
        self.pins = [patch.object(b,'BASE_FILES_SHA', b.sha(self.backup.base/'FILES.jsonl')),
                     patch.object(b,'BASE_DB_SHA',b.sha(self.backup.base/'DATABASES.json'))]
        for pin in self.pins: pin.start()

    def tearDown(self):
        self.pin.stop()
        for pin in self.pins: pin.stop()
        self.temp.cleanup()

    def confirm(self, version, corrupt=False):
        version = Path(version); plan = json.loads((version / 'UPLOAD.json').read_text())
        # Synthetic connector attestation for isolated unit tests only.
        receipt = {'folderId': 'test-private', 'ownerOnly': True, 'verifiedAt': b.now(),
                   'files': [{**x, 'fileId': 'test-'+str(i), 'readBackSha256': x['sha256']}
                             for i,x in enumerate(plan['files'])]}
        if corrupt: receipt['files'][0]['readBackSha256'] = '0'*64
        p = self.root / 'receipt.json'; b.atomic(p, receipt)
        return self.backup.confirm(version, p)

    def test_unchanged_is_manifest_only_and_pending_until_readback(self):
        result = self.backup.prepare(); p = Path(result['version'])
        self.assertEqual(result['changedFiles'], 0)
        self.assertEqual(len(json.loads((p/'UPLOAD.json').read_text())['files']), 1)
        self.assertFalse((p/'CONFIRMED.json').exists())
        self.assertTrue(self.backup.prepare()['resumed'])

    def test_incremental_change_delete_and_parent_replay(self):
        (self.source/'data/a.json').write_text('new contents')
        result = self.backup.prepare(); self.confirm(result['version'])
        rows,parent = self.backup.state()
        self.assertEqual(rows['data/a.json']['sha256'], b.sha(self.source/'data/a.json'))
        self.assertEqual(parent['kind'], 'incremental')
        (self.source/'data/a.json').unlink()
        second = self.backup.prepare(); p=Path(second['version'])
        self.assertEqual(json.loads((p/'MANIFEST.json').read_text())['deleted'], ['data/a.json'])
        self.confirm(p)
        self.assertNotIn('data/a.json', self.backup.state()[0])

    def test_corrupt_receipt_does_not_acknowledge_update(self):
        b.queue('v3', home=self.home, source=self.source)
        result = self.backup.prepare()
        with self.assertRaises(ValueError): self.confirm(result['version'], corrupt=True)
        self.assertEqual(self.backup.status()['pendingManualUpdates'], 1)
        self.confirm(result['version'])
        self.assertEqual(self.backup.status()['pendingManualUpdates'], 0)

    def test_new_queue_during_upload_remains_pending(self):
        b.queue('v6', home=self.home, source=self.source)
        result = self.backup.prepare()
        b.queue('v10', home=self.home, source=self.source)
        self.confirm(result['version'])
        self.assertEqual(self.backup.status()['pendingManualUpdates'],1)

    def test_low_space_preserves_source_and_marks_failed(self):
        p=self.source/'data/a.json'; p.write_text('new')
        with patch.object(b, 'space', side_effect=OSError('full')):
            with self.assertRaises(OSError): self.backup.prepare()
        self.assertEqual(p.read_text(), 'new')
        self.assertFalse(list(self.backup.control.glob('versions/*/CONFIRMED.json')))
        self.assertTrue(list(self.backup.control.glob('versions/*/FAILED.json')))

    def test_excludes_secrets_and_rejects_symlinks(self):
        (self.source/'data/.env').write_text('secret')
        self.assertNotIn('data/.env',b.inventory(self.source))
        (self.source/'data/link').symlink_to(self.source/'data/a.json')
        with self.assertRaises(ValueError): b.inventory(self.source)
        for path in ('../a', '/a', 'a/../b'):
            with self.assertRaises(ValueError): b.safe(path)

    def test_sqlite_consistent_capture(self):
        p=self.source/'data/new.sqlite3'
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE x(a)');db.execute('INSERT INTO x VALUES (7)')
        result=self.backup.prepare(); m=json.loads((Path(result['version'])/'MANIFEST.json').read_text())
        row=next(x for x in m['changed'] if x['path']=='data/new.sqlite3')
        output=self.root/'restored.sqlite3'
        with output.open('wb') as out:
            for part in row['chunks']:
                out.write(b.gzip.decompress((self.backup.control/'objects'/part['name']).read_bytes()))
        with sqlite3.connect(output) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT a FROM x').fetchone()[0],7)

    def test_corrupt_cached_chunk_rejected(self):
        p=self.source/'data/a.json'; p.write_text('changed')
        _,parts=self.backup.chunk(p)
        (self.backup.control/'objects'/parts[0]['name']).write_bytes(b'broken')
        with self.assertRaises(Exception): self.backup.chunk(p)

    def test_no_deletion_ever_enabled(self):
        self.assertFalse(self.backup.status()['deletionEnabled'])

    def test_full_receipt_pin_checked(self):
        p=self.home/'00-計畫與盤點/包4傳輸/UPLOAD_RECEIPT.json';p.write_text('{"changed":true}')
        with self.assertRaises(ValueError): self.backup.prepare()

    def test_source_mutation_rejected(self):
        original=b.sha
        def mutate(path):
            digest=original(path)
            if Path(path).resolve()==(self.source/'data/a.json').resolve(): Path(path).write_text('raced')
            return digest
        with patch.object(b,'sha',side_effect=mutate):
            with self.assertRaises(ValueError): self.backup.prepare()
        self.assertFalse(list(self.backup.control.glob('versions/*/MANIFEST.json')))

    def test_wal_mutation_detected(self):
        p=self.source/'data/new.sqlite3'
        with sqlite3.connect(p) as db: db.execute('CREATE TABLE x(a)')
        original=b.sidecars; count=0
        def sides(source,files):
            nonlocal count
            count+=1; result=original(source,files)
            if count==2: result['data/new.sqlite3-wal']=(1,2,3,4)
            return result
        with patch.object(b,'sidecars',side_effect=sides):
            with self.assertRaises(ValueError): self.backup.prepare()


class ManualCLIHooks(unittest.TestCase):
    """Execute the actual CLI body with mocked strategies, no app DB imports."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.decision=Path(self.temp.name)/'decision.json';self.decision.write_text('{}')
        self.root=Path(__file__).resolve().parents[1]
        if not (self.root/'backend/v3.py').exists(): self.root=Path('/Users/ching/Desktop/branch-monitor')

    def run_cli(self,name,args,fail=False):
        tree=ast.parse((self.root/'backend'/ (name+'.py')).read_text())
        main=next((x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='main'),None)
        notify=Mock();module=types.ModuleType('backend.manual_backup');module.notify=notify
        result={'revision':1,'as_of':'2026-11-07','cash':1,'holdings':[],'pending_orders':[]}
        publish=Mock(return_value=result,side_effect=ValueError('publish failed') if fail else None)
        env={'__package__':'backend','__name__':'test_cli','argparse':argparse,'json':json,'Path':Path,
             'publish':publish,'manual_update':publish,'report':Mock(return_value=result),
             'export':Mock(return_value=result),'apply_review':publish,'initialize':publish,
             'market_context':Mock(return_value={})}
        with patch.dict(sys.modules,{'backend.manual_backup':module}),patch.object(sys,'argv',[name,*args]),contextlib.redirect_stdout(io.StringIO()):
            if main:
                exec(compile(ast.Module(body=[main],type_ignores=[]),'cli-test','exec'),env)
                env['main']()
            else:
                body=tree.body[-1].body
                exec(compile(ast.Module(body=body,type_ignores=[]),'cli-test','exec'),env)
        return notify,publish

    def test_successful_cli_queues_once(self):
        for module,command,model in [('v3','review','v3'),('v10','publish','v10'),('v11','publish','v11'),('v6_evening_forecast','publish','v6')]:
            args=[command]+(['--decision',str(self.decision)] if module!='v6_evening_forecast' else [])
            with self.subTest(module=module):
                notify,_=self.run_cli(module,args);notify.assert_called_once_with(model)

    def test_read_only_clis_do_not_queue(self):
        for module in ('v3','v10','v11','v6_evening_forecast'):
            with self.subTest(module=module):
                notify,publish=self.run_cli(module,['report'])
                notify.assert_not_called();publish.assert_not_called()

    def test_failed_publish_does_not_reach_queue(self):
        for module in ('v3','v10','v11','v6_evening_forecast'):
            args=['review' if module=='v3' else 'publish']+(['--decision',str(self.decision)] if module!='v6_evening_forecast' else [])
            with self.subTest(module=module),self.assertRaises(ValueError):self.run_cli(module,args,True)

    def test_custom_account_does_not_queue_production_backup(self):
        for module,option,command in [('v3','--account','review'),('v10','--store','publish'),('v11','--store','publish')]:
            notify,_=self.run_cli(module,[command,'--decision',str(self.decision),option,str(self.decision.parent)])
            notify.assert_not_called()

    def test_weather_cli_only_queues_once(self):
        notify,publish=self.run_cli('v6_weather',[str(self.decision)])
        publish.assert_called_once();notify.assert_called_once_with('v6')


if __name__=='__main__': unittest.main(verbosity=2)
