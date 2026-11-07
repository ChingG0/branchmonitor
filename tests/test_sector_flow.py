import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import sector_flow as s

class ImmediateThread:
    def __init__(self,target,**kwargs):self.target=target
    def start(self):self.target()

class SectorFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.p=patch.object(s,'PROTOTYPE',self.path);self.p.start()
        with sqlite3.connect(self.path/'sectors.sqlite3') as c:
            c.executescript('CREATE TABLE sector_groups(name TEXT,parent TEXT);CREATE TABLE sector_members(name TEXT,symbol TEXT);CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE sector_days(date TEXT);CREATE TABLE institutional_days(date TEXT);')
            c.executemany('INSERT INTO sector_groups VALUES (?,?)',[('晶圓代工','半導體'),('IC製造','半導體')])
            c.executemany('INSERT INTO sector_members VALUES (?,?)',[('晶圓代工','2330'),('IC製造','2330'),('晶圓代工','2330'),('missing','9999')])
            c.execute("INSERT INTO meta VALUES ('dictionary_snapshot','2026-09-11')")
            c.execute("INSERT INTO sector_days VALUES ('2026-09-18')")
            c.execute("INSERT INTO institutional_days VALUES ('2026-09-18')")
        self.job=patch.object(s,'_job',{'status':'idle','finished_at':None});self.job.start()
    def tearDown(self):self.job.stop();self.p.stop();self.temp.cleanup()
    def test_shared_dictionary_preserves_multiple_memberships_without_duplicates(self):
        data=s.classification();self.assertEqual(data['memberships']['2330'],['IC製造','晶圓代工']);self.assertNotIn('9999',data['memberships']);self.assertEqual(data['snapshot'],'2026-09-11')
        conn=s.connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):conn.execute('DELETE FROM sector_groups')
        finally:conn.close()
    def test_missing_dictionary_does_not_fallback_to_user_tags_or_create_database(self):
        (self.path/'sectors.sqlite3').unlink();data=s.classification()
        self.assertEqual(data['memberships'],{});self.assertTrue(data['error']);self.assertFalse((self.path/'sectors.sqlite3').exists())
    def test_board_routes_only_sector_actions_and_does_not_fetch_on_read(self):
        (self.path/'board.html').write_text('<html><body><h1>族群廣度</h1><script>let polling=null,lastFinish=null;fetch("/api/status");fetch("/api/refresh",{});</script></body></html>')
        with patch.object(s.subprocess,'run',side_effect=AssertionError('read must not update')):
            html=s.board().decode();state=s.status()
        self.assertIn('/api/sector-flow/refresh',html);self.assertNotIn('fetch("/api/refresh"',html)
        self.assertIn("new URLSearchParams(location.search)",html);self.assertIn('族群流向',html);self.assertEqual(state['computed'],'2026-09-18')
        self.assertNotIn('/api/sync',html)
    def test_manual_refresh_reuses_pipeline_in_correct_directory_and_records_partial(self):
        (self.path/'update.py').write_text('')
        output=type('Result',(),{'returncode':0,'stdout':'log\nSECTOR_RESULT:'+json.dumps({'ok':False,'errors':['missing quote'],'target':'2026-09-18'})})()
        with patch.object(s.threading,'Thread',ImmediateThread),patch.object(s.subprocess,'run',return_value=output) as run:
            state=s.refresh();self.assertEqual(state['status'],'partial');self.assertEqual(state['errors'],['missing quote']);self.assertEqual(run.call_args.kwargs['cwd'],self.path)
            self.assertIn('update.run(force=False)',run.call_args.args[0][2])
        with self.assertRaises(ValueError):s.refresh('yes')
    def test_duplicate_refresh_does_not_start_second_process(self):
        (self.path/'update.py').write_text('')
        with s._lock,patch.object(s.threading,'Thread') as thread:s.refresh();thread.assert_not_called()

    def test_board_policy_allows_same_origin_frame_and_exact_inline_scripts_only(self):
        import base64,hashlib
        script=b'const x=1;'
        policy=s.board_policy(b'<script>'+script+b'</script>')
        self.assertIn("frame-ancestors 'self'",policy)
        self.assertIn(base64.b64encode(hashlib.sha256(script).digest()).decode(),policy)
        self.assertNotIn("script-src 'unsafe-inline'",policy)
        self.assertNotIn("'unsafe-eval'",policy)
