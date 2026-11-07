import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from backend import v1,v10

class V10Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Path(self.tmp.name)/'v10_manual'
        self.now=datetime(2026,10,22,10,tzinfo=ZoneInfo('Asia/Taipei'))
        self.model=dict(key='v1',label='V1 獲利保護優化',version=v1.VERSION,
            available=True,as_of='2026-10-21',initial=1000000.,cash=650000.,equity=1040000.,
            portfolio_return_pct=4.,days={'2026-10-21':{'holdings':[{'symbol':'2330','qty':100}]}},
            curve=[{'date':'2026-10-21','value':4.}],trades=[])
        self.decision=dict(expected_revision=0,as_of='2026-10-21',analysis='依照已完成收盤資料保存 V1 策略複本，保留已知資料時間。',sources=[])

    def save(self,decision=None,model=None,now=None):
        with patch.object(v10.v1,'report',return_value=model or self.model):
            return v10.publish(decision or self.decision,store=self.store,now=now or self.now)

    def test_get_never_runs_v1_or_initializes(self):
        with patch.object(v10.v1,'report',side_effect=AssertionError('must not calculate')):
            result=v10.report(self.store)
        self.assertFalse(result['available']);self.assertFalse(self.store.exists())
        self.save();before={p.name:p.read_bytes() for p in self.store.iterdir()}
        with patch.object(v10.v1,'report',side_effect=AssertionError('must not calculate')):
            for _ in range(3):self.assertEqual(v10.report(self.store)['as_of'],'2026-10-21')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.store.iterdir()})

    def test_clone_preserves_v1_data_and_cache(self):
        before=copy.deepcopy(self.model);result=self.save()
        saved=json.loads(next(self.store.glob('*.json')).read_text())
        self.assertEqual(saved['model'],before);self.assertEqual(self.model,before)
        self.assertEqual(result['cash'],before['cash']);self.assertEqual(result['days'],before['days'])
        self.assertEqual(result['key'],'v10');self.assertEqual(result['source_version'],v1.VERSION)
        self.assertEqual(result['recorded_at'],'2026-10-22T10:00:00+08:00')
        self.model['days']['2026-10-21']['holdings'][0]['qty']=200
        self.assertEqual(v10.report(self.store)['days']['2026-10-21']['holdings'][0]['qty'],100)

    def test_second_publication_retains_old_snapshot(self):
        self.save();old=next(self.store.glob('*.json'));before=old.read_bytes()
        newer=copy.deepcopy(self.model);newer['cash']=600000.
        result=self.save(dict(self.decision,expected_revision=1),newer,
                         self.now.replace(hour=11))
        self.assertEqual(old.read_bytes(),before);self.assertEqual(len(list(self.store.glob('*.json'))),2)
        self.assertEqual(result['revision'],2);self.assertEqual(result['cash'],600000.)
        self.assertEqual(len(result['snapshot_history']),2)

    def test_stale_revision_and_backward_publication_do_not_write(self):
        self.save();before={p.name:p.read_bytes() for p in self.store.glob('*.json')}
        for decision,now in [(self.decision,self.now),
            (dict(self.decision,expected_revision=1),self.now.replace(hour=9))]:
            with self.assertRaises(ValueError):self.save(decision,now=now)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.store.glob('*.json')})

    def test_future_intraday_missing_or_mismatched_data_rejected(self):
        for day in ['2026-10-23','2026-10-22']:
            with self.assertRaises(ValueError):self.save(dict(self.decision,as_of=day))
        for field,value in [('available',False),('as_of','2026-10-20'),('version','changed')]:
            bad=dict(self.model,**{field:value})
            with self.assertRaises(ValueError):self.save(model=bad)
        bad=copy.deepcopy(self.model);bad['days']['2026-10-22']={}
        with self.assertRaises(ValueError):self.save(model=bad)
        self.assertFalse(self.store.exists())

    def test_invalid_review_or_nan_keeps_original(self):
        self.save();before={p.name:p.read_bytes() for p in self.store.glob('*.json')}
        for override in [dict(expected_revision=True),dict(analysis=''),dict(sources=[{'title':'bad','url':'javascript:foo'}])]:
            with self.assertRaises(ValueError):self.save(dict(self.decision,**override))
        with self.assertRaises(ValueError):self.save(dict(self.decision,expected_revision=1),dict(self.model,cash=float('nan')))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.store.glob('*.json')})

if __name__=='__main__':unittest.main()
