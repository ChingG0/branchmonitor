import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import db, service


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'config').mkdir()
        self.catalog=[{'broker':'A','branch':'001','name':'甲'},
                      {'broker':'B','branch':'003900420032006e','name':'乙'},
                      {'broker':'C','branch':'003','name':'未選分點'}]
        (self.root/'config/watchlist.json').write_text(json.dumps({'branches':self.catalog}))
        self.patches=[patch.object(db,'DATA',self.root/'data'),patch.object(db,'ROOT',self.root),
                      patch.object(service.sources,'branch_catalog',return_value=self.catalog)]
        for p in self.patches:p.start()
        db.initialize()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def keys(self):
        return [(b['broker'],b['branch']) for b in service.branches()]

    def test_replace_exact_scope_keeps_encoded_ids_and_screenshot_order(self):
        service.replace_watchlist([self.catalog[1],self.catalog[0]])
        self.assertEqual(self.keys(),[('B','003900420032006e'),('A','001')])
        with db.connect() as c:self.assertEqual(c.execute("SELECT active FROM branches WHERE broker='C'").fetchone()[0],0)

    def test_bad_entries_do_not_partially_change_scope(self):
        original=self.keys()
        bads=[[],None,'all',[self.catalog[0],{'broker':'unknown','branch':'oops'}],
              [self.catalog[0],self.catalog[0]],[{'broker':[], 'branch':'001'}]]
        for bad in bads:
            with self.assertRaises(ValueError):service.replace_watchlist(bad)
            self.assertEqual(self.keys(),original)

    def test_existing_history_preserved_and_completion_date_invalidated(self):
        with db.connect() as c:
            c.execute("INSERT INTO candidates VALUES ('C','003','2330','台積電')")
        db.update_settings({'last_sync_date':'2026-09-10'})
        service.replace_watchlist([self.catalog[0]])
        self.assertNotIn('last_sync_date',db.settings())
        with db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM candidates WHERE broker='C'").fetchone()[0],1)

    def test_restart_does_not_reactivate_unselected_branches(self):
        service.replace_watchlist([self.catalog[1]])
        db.initialize()
        self.assertEqual(self.keys(),[('B','003900420032006e')])

    def test_active_sync_blocks_scope_mutation(self):
        original=self.keys()
        service._sync_lock.acquire()
        try:
            with self.assertRaises(ValueError):service.replace_watchlist([self.catalog[0]])
            self.assertEqual(self.keys(),original)
        finally:service._sync_lock.release()

    def test_job_saves_frozen_selected_scope_not_all_catalog(self):
        service.replace_watchlist([self.catalog[1]])
        with patch.object(service.threading,'Thread') as thread:
            try:
                job=service.sync(14)
                self.assertEqual([(b['broker'],b['branch']) for b in job['scope']],self.keys())
                args=thread.call_args.kwargs['args']
                self.assertEqual(len(args[1]),1)
                self.assertEqual(args[1][0]['branch'],'003900420032006e')
            finally:service._sync_lock.release()

    def test_catalog_failure_releases_mutex(self):
        with patch.object(service.sources,'branch_catalog',side_effect=ValueError('offline')):
            with self.assertRaises(ValueError):service.replace_watchlist([self.catalog[0]])
        service.replace_watchlist([self.catalog[0]])
        self.assertEqual(len(service.branches()),1)

if __name__=='__main__':unittest.main()
