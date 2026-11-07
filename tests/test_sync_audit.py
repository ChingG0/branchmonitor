import tempfile,unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from backend import db,sync_audit


class SyncAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(db,'DATA',self.root/'data'),patch.object(db,'ROOT',self.root)]
        for item in self.patches:item.start()
        db.initialize();self.days=[(date(2026,8,20)+timedelta(days=i)).isoformat() for i in range(25)]
        with db.connect() as c:
            c.execute("UPDATE branches SET broker='A',branch='1',name='甲',active=1")
            c.execute("INSERT INTO branches VALUES ('B','2','乙',0)")
            for day in self.days:c.execute("INSERT INTO market_days VALUES (?,'ok','ok','')",(day,))
            for broker,branch in [('A','1'),('B','2')]:
                for day in self.days:c.execute("INSERT INTO snapshots VALUES (?,?,?,'observed',0,'','test')",(broker,branch,day))
            for symbol in ['OPEN','LATEST','RECENT','RECON','SLEEP','IGNORE']:
                c.execute("INSERT INTO candidates VALUES ('A','1',?,?)",(symbol,symbol))
            c.execute("INSERT INTO candidates VALUES ('B','2','OLD','OLD')")
            self.flow(c,'OPEN',self.days[0],10,'history')
            self.flow(c,'LATEST',self.days[-1],0,'https://test/zgb0.djhtm')
            self.flow(c,'RECENT',self.days[-3],10,'history');self.flow(c,'RECENT',self.days[-2],-10,'history')
            self.flow(c,'RECON',self.days[0],-5,'history')
            self.flow(c,'SLEEP',self.days[0],10,'history');self.flow(c,'SLEEP',self.days[1],-10,'history')
            for symbol in ['OPEN','LATEST','RECENT','RECON']:
                c.execute("INSERT INTO history_intervals VALUES ('A','1',?,?,?,'test')",(symbol,self.days[0],self.days[-5]))
            for symbol in ['SLEEP','IGNORE']:
                c.execute("INSERT INTO history_intervals VALUES ('A','1',?,?,?,'test')",(symbol,self.days[0],self.days[-1]))
        db.update_settings({'ignored_symbols':['IGNORE']})

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def flow(self,c,symbol,day,net,source):
        c.execute("INSERT INTO flows VALUES ('A','1',?,?,?, ?,?,?,?)",(day,symbol,symbol,max(net,0),max(-net,0),net,source))

    def test_local_audit_classifies_daily_and_repair_work(self):
        result=sync_audit.run(20);summary=result['run']['summary']
        self.assertEqual(summary['network_requests'],0);self.assertEqual(summary['total'],7)
        with db.connect() as c:rows={r['symbol']:dict(r) for r in c.execute('SELECT * FROM candidate_sync_state')}
        self.assertEqual(rows['OPEN']['daily_tier'],'P0_OPEN');self.assertEqual(rows['OPEN']['repair_tier'],'R0_OPEN')
        self.assertEqual(rows['LATEST']['daily_tier'],'P1_LATEST')
        self.assertEqual(rows['RECENT']['daily_tier'],'P2_RECENT')
        self.assertEqual(rows['RECON']['daily_tier'],'P3_RECONCILE')
        self.assertEqual(rows['SLEEP']['daily_tier'],'SLEEP');self.assertEqual(rows['SLEEP']['repair_tier'],'DONE')
        self.assertEqual(rows['IGNORE']['daily_tier'],'IGNORED');self.assertEqual(rows['OLD']['daily_tier'],'INACTIVE')
        self.assertEqual(rows['OPEN']['next_query_start'],self.days[-4])

    def test_report_filters_and_validation(self):
        sync_audit.run();result=sync_audit.report(10,daily_tier='P0_OPEN')
        self.assertEqual([row['symbol'] for row in result['rows']],['OPEN'])
        with self.assertRaises(ValueError):sync_audit.run(2)
        with self.assertRaises(ValueError):sync_audit.report(10,daily_tier='BAD')

    def test_daily_scope_uses_current_rankings_and_only_verified_prior_state(self):
        sync_audit.run()
        with db.connect() as c:
            c.execute("UPDATE history_intervals SET end=? WHERE broker='A' AND branch='1' AND symbol='OPEN'",(self.days[-1],))
            c.execute("INSERT INTO ranking_entries VALUES ('A','1',?,'E','LATEST','LATEST',10,10,0,'test')",(self.days[-1],))
        sync_audit.run()
        with db.connect() as c:scope=sync_audit.daily_scope(c,'A','1',self.days[-1])
        self.assertEqual({row['symbol'] for row in scope['rows']},{'OPEN','LATEST'})
        self.assertNotIn('RECENT',{row['symbol'] for row in scope['rows']})


if __name__=='__main__':unittest.main()
