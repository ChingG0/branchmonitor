import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import v6_weather as W
import pandas as pd
from backend import v6_comparison as C
from backend.v6_source_availability import verified_positions

DAY='2026-11-03'
NOW=pd.Timestamp(DAY+'T18:45:00+08:00')

def fixture():
    keys=['vix','feargreed','twd','oil','spx','financial','electronics','tsm','rates-proxy','foreign','retail','sox','spy','tlt','gold','dollar']
    return pd.DataFrame({k+'_available':[pd.Timestamp(DAY+('T20:00:00+08:00' if k in ('foreign','retail') else 'T08:00:00+08:00'))] for k in keys},index=[DAY])

class SourceAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.entries=[]
        for name in ['positions-TXF-2026.csv','positions-MXF-2026.csv','mtx-month-2026-11.csv']:
            p=self.root/name;p.write_bytes(b'verified official response')
            self.entries.append({'key':name,'cutoff':DAY,'url':'https://www.taifex.com.tw/cht/3/'+('futDataDown' if name.startswith('mtx-') else 'futContractsDateDown'),'observed_at':DAY+'T18:40:00+08:00','sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        self.save()
    def save(self,entries=None):
        (self.root/'comparison-manifest.json').write_text(json.dumps({'cash_cutoff':DAY,'sources':self.entries if entries is None else entries}))
    def test_verified_pair_unblocks_only_current_publication(self):
        proof=verified_positions(self.root,NOW,DAY);self.assertEqual(set(proof),{'foreign','retail'})
        for kind in ['legacy','cross']:
            rows,missing=C.latest_sources(fixture(),NOW,kind,proof)
            self.assertEqual(missing,[])
            for row in rows:
                if row['key'] in proof:
                    self.assertEqual(row['availability_basis'],'verified_fetch');self.assertEqual(row['state'],'ready');self.assertIn('T20:',row['assumed_available_at'])
                    self.assertEqual(row['as_of'],DAY)
    def test_no_evidence_remains_pending_not_missing(self):
        for kind in ['legacy','cross']:
            rows,missing=C.latest_sources(fixture(),NOW,kind)
            self.assertEqual(missing,['foreign','retail'])
            self.assertTrue(all(r['state']=='pending' for r in rows if not r['fresh']))
        (self.root/'comparison-manifest.json').unlink()
        self.assertEqual(verified_positions(self.root,NOW,DAY),{})
    def test_future_observation_cannot_rewrite_earlier_forecast(self):
        self.assertEqual(verified_positions(self.root,NOW-pd.Timedelta(hours=1),DAY),{})
        proof=verified_positions(self.root,NOW,DAY)
        self.assertEqual(C.latest_sources(fixture(),NOW-pd.Timedelta(hours=1),'cross',proof)[1],['foreign','retail'])
    def test_date_mismatch_or_changed_file_rejected(self):
        self.assertEqual(verified_positions(self.root,NOW,'2026-11-02'),{})
        (self.root/'positions-TXF-2026.csv').write_bytes(b'changed')
        proof=verified_positions(self.root,NOW,DAY);self.assertNotIn('foreign',proof);self.assertIn('retail',proof)
        f=fixture();f.index=['2026-11-02'];self.assertEqual(C.latest_sources(f,NOW,'cross',proof)[1],['foreign','retail'])
    def test_retail_needs_both_files_and_valid_official_url(self):
        self.save(self.entries[:2]);proof=verified_positions(self.root,NOW,DAY)
        self.assertIn('foreign',proof);self.assertNotIn('retail',proof)
        for replacement in [None,DAY+'T21:00:00+08:00',DAY+'T18:40:00']:
            entries=copy.deepcopy(self.entries);entries[0]['observed_at']=replacement;self.save(entries)
            self.assertNotIn('foreign',verified_positions(self.root,NOW,DAY))
        entries=copy.deepcopy(self.entries);entries[0]['url']='https://example.com/cht/3/futContractsDateDown';self.save(entries)
        self.assertNotIn('foreign',verified_positions(self.root,NOW,DAY))
    def test_other_sources_keep_time_and_staleness_rules(self):
        proof=verified_positions(self.root,NOW,DAY);f=fixture();f['spx_available']=NOW+pd.Timedelta(hours=2)
        rows,missing=C.latest_sources(f,NOW,'cross',proof);self.assertEqual(missing,['spx'])
        f['spx_available']=NOW-pd.Timedelta(days=5)
        rows,missing=C.latest_sources(f,NOW,'cross',proof);self.assertEqual(missing,['spx']);self.assertEqual(next(r['state'] for r in rows if r['key']=='spx'),'stale')
        rows,missing=C.latest_sources(fixture(),NOW+pd.Timedelta(hours=2),'cross')
        self.assertEqual(missing,[]);self.assertTrue(all(r['availability_basis']=='assumed_schedule' for r in rows))

class WeatherComparisonSelectionTests(unittest.TestCase):
    def test_saved_report_always_reads_its_own_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Path(tmp)/'v6_weather';store.mkdir()
            row={'id':'weather-old','prepared_at':DAY+'T18:40:00+08:00','market_as_of':DAY,'stage':'高風險','comparison_id':'linked-partial'}
            (store/'weather-old.json').write_text(json.dumps(row))
            with patch.object(W.v6_forecast,'research_summary',return_value={}),patch.object(W.v6_comparison,'report',return_value={'snapshot':{'id':'linked-partial'}}) as read:
                self.assertEqual(W.report(store=store)['comparison']['snapshot']['id'],'linked-partial')
                read.assert_called_once_with('linked-partial',store=Path(tmp)/'v6_comparison')
                read.reset_mock();W.report('weather-old',store=store)
                read.assert_called_once_with('linked-partial',store=Path(tmp)/'v6_comparison')
    def test_unlinked_legacy_report_keeps_timestamp_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Path(tmp)/'v6_weather';store.mkdir()
            row={'id':'old','prepared_at':DAY+'T18:40:00+08:00','market_as_of':DAY,'stage':'高風險'}
            (store/'old.json').write_text(json.dumps(row))
            with patch.object(W.v6_forecast,'research_summary',return_value={}),patch.object(W.v6_comparison,'report',return_value={'snapshot':None}) as read:
                W.report('old',store=store)
                read.assert_called_once_with(before=row['prepared_at'],store=Path(tmp)/'v6_comparison')

if __name__=='__main__':unittest.main()
