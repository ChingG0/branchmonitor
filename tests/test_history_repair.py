import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db,history_repair,service

A={'broker':'9300','branch':'9339','name':'華南永昌-竹北'}
TARGET='2026-09-23'


class ImmediateThread:
    def __init__(self,target,args,daemon):self.target,self.args=target,args
    def start(self):self.target(*self.args)


class HistoryRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'config/watchlist.json').write_text(json.dumps({'branches':[A]}))
        self.patches=[patch.object(db,'DATA',self.root/'data'),patch.object(db,'ROOT',self.root),
            patch.object(history_repair.sync_audit,'run',return_value={}),
            patch.object(history_repair.threading,'Thread',ImmediateThread),
            patch.object(history_repair.sources,'history',return_value=[])]
        for item in self.patches:item.start()
        db.initialize()
        with db.connect() as c:
            c.execute("INSERT INTO sync_audit_runs(id,started_at,finished_at,status,as_of,total,summary) VALUES ('audit','2026-09-24','2026-09-24','complete',?,4,'{}')",(TARGET,))
            self.state(c,'2330','台積電','R0_OPEN','2026-09-16')
            self.state(c,'2317','鴻海','R1_LATEST','2026-09-16')
            self.state(c,'2454','聯發科','R2_RECENT','2026-09-22')
            self.state(c,'6806','森崴能源','IGNORED','2026-09-16',ignored=1)
            for symbol in ['2330','2317','2454','6806']:
                c.execute('INSERT INTO candidates VALUES (?,?,?,?)',(A['broker'],A['branch'],symbol,symbol))
                c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',(A['broker'],A['branch'],symbol,'2026-01-01','2026-09-16' if symbol!='2454' else '2026-09-22','before'))

    def tearDown(self):
        if service._sync_lock.locked():service._sync_lock.release()
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def state(self,c,symbol,name,tier,last,ignored=0):
        c.execute('''INSERT INTO candidate_sync_state
          (broker,branch,symbol,name,branch_active,daily_tier,repair_tier,estimated_position,unmatched_outflow,
           first_flow_date,last_flow_date,last_ranking_date,last_success_date,target_date,next_query_start,
           recent_cutoff,coverage_complete,ignored,reason,audited_at)
          VALUES (?,?,?,?,1,'P0_OPEN',?,1,0,'2026-01-01',?,?,?,?,'2026-01-01','2026-08-01',0,?,'test','2026-09-24')''',
          (A['broker'],A['branch'],symbol,name,tier,last,last,last,TARGET,ignored))

    def test_plan_is_local_prioritized_and_idempotent(self):
        report=history_repair.plan('2026-09-16',False)
        self.assertEqual(report['counts']['queued'],2)
        self.assertEqual(report['queued_by_tier']['R0_OPEN'],1)
        self.assertEqual(report['queued_by_tier']['R1_LATEST'],1)
        self.assertEqual(report['plan']['summary']['network_requests'],0)
        history_repair.sources.history.assert_not_called()
        rows=history_repair.report(10,status='queued')['rows']
        self.assertEqual([r['symbol'] for r in rows],['2330','2317'])
        self.assertTrue(all((r['start'],r['end'])==('2026-01-01',TARGET) for r in rows))
        again=history_repair.plan('2026-09-16',False)
        self.assertEqual(again['counts']['queued'],2)
        self.assertEqual(again['plan']['summary']['inserted'],0)

    def test_plan_includes_candidate_without_prior_history(self):
        with db.connect() as c:
            self.state(c,'2603','長榮','R0_OPEN',None)
            c.execute('INSERT INTO candidates VALUES (?,?,?,?)',(A['broker'],A['branch'],'2603','長榮'))
        report=history_repair.plan(None,False)
        self.assertEqual(report['counts']['queued'],4)
        row=next(r for r in history_repair.report(10,status='queued')['rows'] if r['symbol']=='2603')
        self.assertEqual((row['start'],row['end']),('2026-01-01',TARGET))

    def test_new_plan_extends_existing_open_interval_without_duplicate(self):
        history_repair.plan('2026-09-16',False)
        new_target='2026-10-02'
        with db.connect() as c:
            c.execute("UPDATE candidate_sync_state SET target_date=? WHERE symbol IN ('2330','2317')",(new_target,))
            c.execute("UPDATE sync_audit_runs SET as_of=?",(new_target,))
        report=history_repair.plan('2026-09-16',False)
        self.assertEqual(report['counts']['queued'],2)
        rows=history_repair.report(10,status='queued')['rows']
        self.assertTrue(all((r['start'],r['end'])==('2026-01-01',new_target) for r in rows))

    def test_batch_limit_repairs_only_selected_prefix(self):
        history_repair.plan('2026-09-16',False)
        result=history_repair.start(1)
        self.assertEqual(result['job']['status'],'complete')
        self.assertEqual(result['job']['succeeded'],1)
        self.assertEqual(result['counts']['complete'],1)
        self.assertEqual(result['counts']['queued'],1)
        call=history_repair.sources.history.call_args
        self.assertEqual(call.args[:4],(A['broker'],A['branch'],'2330','台積電'))
        self.assertEqual(call.args[4:6],('2026-01-01',TARGET))
        with db.connect() as c:
            spans=[tuple(r) for r in c.execute("SELECT start,end FROM history_intervals WHERE symbol='2330'")]
        self.assertEqual(spans,[('2026-01-01',TARGET)])

    def test_failure_is_recorded_without_erasing_old_coverage(self):
        history_repair.plan('2026-09-16',False)
        history_repair.sources.history.side_effect=RuntimeError('temporary source failure')
        result=history_repair.start(1)
        self.assertEqual(result['job']['status'],'partial')
        self.assertEqual(result['counts']['failed'],1)
        failed=history_repair.report(10,status='failed')['rows'][0]
        self.assertIn('temporary source failure',failed['last_error'])
        self.assertEqual(failed['attempts'],1)
        self.assertEqual(failed['error_kind'],'unexpected')
        self.assertTrue(failed['next_retry_at']);self.assertEqual(failed['retryable'],1)
        self.assertTrue(result['job']['checkpoint_at'])
        self.assertEqual(json.loads(result['job']['checkpoint_item'])['symbol'],'2330')
        self.assertEqual(result['retry']['waiting'],1)
        with db.connect() as c:
            self.assertEqual(tuple(c.execute("SELECT start,end FROM history_intervals WHERE symbol='2330'").fetchone()),('2026-01-01','2026-09-16'))

    def test_daily_sync_lock_and_validation(self):
        history_repair.plan('2026-09-16',False)
        service._sync_lock.acquire()
        try:
            with self.assertRaises(ValueError):history_repair.start(1)
        finally:service._sync_lock.release()
        for value in [0,history_repair.MAX_BATCH+1,True,'10']:
            with self.assertRaises(ValueError):history_repair.start(value)
            with self.assertRaises(ValueError):history_repair.requeue(value)
        with self.assertRaises(ValueError):history_repair.start(1,resume='yes')
        with self.assertRaises(ValueError):history_repair.requeue(1,force='yes')
        for value in ['bad','2026-02-30',20260916]:
            with self.assertRaises(ValueError):history_repair.plan(value,False)
        with self.assertRaises(ValueError):history_repair.report(10,status='unknown')

    def test_forced_due_retry_succeeds_without_repeating_completed_items(self):
        history_repair.plan('2026-09-16',False)
        with db.connect() as c:c.execute("DELETE FROM history_repair_queue WHERE symbol!='2330'")
        history_repair.sources.history.side_effect=RuntimeError('temporary')
        first=history_repair.start(1)
        self.assertEqual(first['counts']['failed'],1)
        self.assertEqual(history_repair.requeue(10,False)['activated'],0)
        forced=history_repair.requeue(10,True)
        self.assertEqual(forced['activated'],1);self.assertEqual(forced['counts']['queued'],1)
        history_repair.sources.history.side_effect=None;history_repair.sources.history.return_value=[]
        done=history_repair.start(1)
        self.assertEqual(done['counts']['complete'],1)
        row=history_repair.report(10,status='complete')['rows'][0]
        self.assertEqual(row['attempts'],2);self.assertEqual(row['retryable'],0)
        self.assertIsNone(row['next_retry_at'])

    def test_interrupted_item_resumes_on_next_explicit_batch(self):
        history_repair.plan('2026-09-16',False)
        with db.connect() as c:
            c.execute("DELETE FROM history_repair_queue WHERE symbol!='2330'")
            c.execute("UPDATE history_repair_queue SET status='interrupted',attempts=1,error_kind='interrupted'")
        result=history_repair.start(1,resume=True)
        self.assertEqual(result['counts']['complete'],1)
        row=history_repair.report(10,status='complete')['rows'][0]
        self.assertEqual(row['attempts'],2)

    def test_stale_running_items_are_recovered_after_restart(self):
        history_repair.plan('2026-09-16',False)
        with db.connect() as c:
            c.execute("UPDATE history_repair_queue SET status='running',run_id='old-run'")
            c.execute("INSERT INTO history_repair_runs(id,started_at,status,total,message) VALUES ('old-run','2026-09-24','running',2,'running')")
        result=history_repair.report(10)
        self.assertEqual(result['counts']['running'],0)
        self.assertEqual(result['counts']['interrupted'],2)
        self.assertEqual(result['job']['status'],'failed')

    def test_fifth_failure_is_exhausted_and_force_does_not_bypass_cap(self):
        history_repair.plan('2026-09-16',False)
        with db.connect() as c:
            c.execute("DELETE FROM history_repair_queue WHERE symbol!='2330'")
            c.execute('UPDATE history_repair_queue SET attempts=?',(history_repair.MAX_ATTEMPTS-1,))
        history_repair.sources.history.side_effect=history_repair.sources.SourceError('still unavailable')
        result=history_repair.start(1)
        self.assertEqual(result['retry']['exhausted'],1)
        row=history_repair.report(10,status='failed')['rows'][0]
        self.assertEqual(row['attempts'],history_repair.MAX_ATTEMPTS)
        self.assertEqual(row['retryable'],0);self.assertIsNone(row['next_retry_at'])
        self.assertEqual(history_repair.requeue(10,True)['activated'],0)


if __name__=='__main__':unittest.main()
