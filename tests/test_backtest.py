import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,research,sources,backtest as b

class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.tmp.name));self.p.start();db.initialize();b.initialize()
    def tearDown(self):self.p.stop();self.tmp.cleanup()
    def row(self,**kw):return dict(id='r:2330',report_id='r',symbol='2330',name='台積電',basis_date='2026-09-18',created_at='2026-09-19T20:00:00+08:00',entry_date='2026-09-21',take_profit=110,stop_loss=95,rule_set_at='2026-09-19T20:00:00+08:00',reason='test',**kw)
    def bar(self,o=100,h=108,l=98,c=105):return {'open':o,'high':h,'low':l,'close':c}
    def ev(self,quotes,**kw):return b.evaluate(self.row(),['2026-09-21','2026-09-22','2026-09-23'],quotes,kw.get('as_of','2026-09-23'))
    def evidence(self):return {'date':'2026-09-18','candidates':[{'symbol':'2330','name':'台積電','close':100}],'execution':{'target_date':'2026-09-21','contract':'202610','basis':self.bar(100,120,90,105),'feedback':{}}}
    def tracking(self):return {'observations':[{'symbol':'2330','take_profit':110,'stop_loss':95,'reason':'數值規則'}],'futures':{'support2':80,'support1':90,'open':100,'close':105,'resistance1':110,'resistance2':120,'reason':'test'}}
    def insert(self,ident='r',created='2026-09-19T20:00:00+08:00'):
        with research.store() as c:b.ingest(c,ident,'2026-09-18',created,{'tracking':b.validate_tracking(self.tracking(),self.evidence())},self.evidence())
    def test_next_open_and_fixed_levels(self):
        result=self.ev({'2026-09-21':self.bar(),'2026-09-22':self.bar(106,112,103,109)})
        self.assertEqual(result['status'],'closed');self.assertEqual(result['entry_price'],100);self.assertEqual(result['exit_price'],110);self.assertEqual(result['pnl'],10000);self.assertEqual(result['held_days'],1)
    def test_gap_down_uses_open_and_both_touched_uses_stop(self):
        down=self.ev({'2026-09-21':self.bar(),'2026-09-22':self.bar(90,97,88,94)})
        self.assertEqual(down['exit_price'],90);self.assertEqual(down['return_pct'],-10)
        both=self.ev({'2026-09-21':self.bar(100,115,92,104)})
        self.assertEqual(both['exit_price'],95);self.assertTrue(both['ambiguous']);self.assertEqual(both['held_days'],0)
    def test_entry_open_already_beyond_target_exits_same_open(self):
        r=self.ev({'2026-09-21':self.bar(112,115,109,113)})
        self.assertEqual(r['entry_price'],112);self.assertEqual(r['exit_price'],112);self.assertEqual(r['pnl'],0)
    def test_missing_intermediate_bar_cannot_be_skipped(self):
        r=self.ev({'2026-09-21':self.bar(),'2026-09-23':self.bar(111,115,109,112)})
        self.assertEqual(r['status'],'gap');self.assertIsNone(r['pnl']);self.assertIsNone(r['unrealized_pnl'])
    def test_missing_entry_no_later_entry_and_unrealized_not_realized(self):
        r=self.ev({'2026-09-22':self.bar()});self.assertEqual(r['status'],'gap');self.assertIsNone(r['entry_price'])
        r=self.ev({'2026-09-21':self.bar()},as_of='2026-09-21');self.assertEqual(r['status'],'open');self.assertEqual(r['unrealized_pnl'],5000);self.assertIsNone(r['pnl'])
    def test_no_price_rules_does_not_invent_exit(self):
        row=self.row();row['take_profit']=None;row['stop_loss']=None
        self.assertEqual(b.evaluate(row,[],{},'')['status'],'rules_missing')
    def test_invalid_prices_and_levels_rejected(self):
        self.assertFalse(b.valid_bar(self.bar(100,98,90,95)))
        for v in [True,float('nan'),-1]:self.assertFalse(b.number(v))
        for field,v in [('take_profit',99),('stop_loss',101)]:
            data=self.tracking();data['observations'][0][field]=v
            with self.assertRaises(ValueError):b.validate_tracking(data,self.evidence())
        data=self.tracking();data['futures']['open']=150
        with self.assertRaises(ValueError):b.validate_tracking(data,self.evidence())
        data=self.tracking();data['observations'][0]['symbol']='9999'
        with self.assertRaises(ValueError):b.validate_tracking(data,self.evidence())
    def test_immutable_ingestion_no_duplicate_versions_in_statistics(self):
        self.insert();data=self.tracking();data['observations'][0]['take_profit']=120
        with research.store() as c:
            b.ingest(c,'r','2026-09-18','2026-09-20T20:00:00+08:00',{'tracking':data},self.evidence())
        self.insert('r2','2026-09-20T20:00:00+08:00')
        rows,forecasts=b.saved_rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['take_profit'],110);self.assertTrue(rows[0]['eligible']);self.assertEqual(len(forecasts),2)
    def test_new_symbol_in_revised_report_counts_and_other_days_remain_separate(self):
        self.insert()
        e=self.evidence();e['candidates'].append({'symbol':'2303','name':'聯電','close':100})
        t=self.tracking();t['observations'].append({'symbol':'2303','take_profit':115,'stop_loss':90,'reason':'新增'})
        with research.store() as c:
            b.ingest(c,'r2','2026-09-18','2026-09-20T10:00:00+08:00',{'tracking':b.validate_tracking(t,e)},e)
            e['execution']['target_date']='2026-09-22'
            b.ingest(c,'r3','2026-09-21','2026-09-21T20:00:00+08:00',{'tracking':b.validate_tracking(t,e)},e)
        rows,_=b.saved_rows();self.assertEqual(len(rows),4);self.assertTrue(all(r['eligible'] for r in rows))
        self.assertEqual(len([r for r in rows if r['basis_date']=='2026-09-18' and r['symbol']=='2330']),1)
    def test_pending_rules_are_replaced_once_using_actual_rule_timestamp(self):
        e=self.evidence();t=self.tracking();t['observations'][0].update(take_profit=None,stop_loss=None)
        with research.store() as c:b.ingest(c,'old','2026-09-18','2026-09-19T20:00:00+08:00',{'tracking':t},e)
        self.insert('complete','2026-09-21T10:00:00+08:00')
        rows,_=b.saved_rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['report_id'],'complete');self.assertFalse(rows[0]['eligible'])
        with research.store() as c:self.assertEqual(c.execute('SELECT count(*) FROM bt_observation_archive').fetchone()[0],1)
        self.insert('again','2026-09-22T10:00:00+08:00')
        self.assertEqual(b.saved_rows()[0][0]['report_id'],'complete')
    def test_existing_duplicates_are_archived_and_unique_index_blocks_reinsertion(self):
        self.insert('valid')
        with research.store() as c:
            c.execute('DROP INDEX bt_one_symbol_per_day')
            old=self.row();old.update(id='old:2330',report_id='old',created_at='2026-09-18T20:00:00+08:00',take_profit=None,stop_loss=None,rule_set_at=None)
            c.execute('INSERT INTO bt_observations ('+','.join(old)+') VALUES ('+','.join('?' for _ in old)+')',tuple(old.values()))
            c.execute('INSERT INTO bt_results VALUES (?,?,?)',('old:2330','{"status":"rules_missing"}',research.now()))
            b.init(c)
            self.assertEqual(c.execute('SELECT id FROM bt_observations').fetchone()[0],'valid:2330')
            archived=c.execute('SELECT payload,result FROM bt_observation_archive WHERE id=?',('old:2330',)).fetchone()
            self.assertEqual(json.loads(archived[0])['id'],'old:2330');self.assertEqual(json.loads(archived[1])['status'],'rules_missing')
            self.assertEqual(c.execute('SELECT count(*) FROM bt_results WHERE id=?',('old:2330',)).fetchone()[0],0)
            b.init(c)
        self.insert('duplicate')
        self.assertEqual(len(b.saved_rows()[0]),1)

    def test_late_report_excluded_and_exact_open_timestamp_is_late(self):
        self.insert('r','2026-09-21T09:00:00+08:00');rows,forecasts=b.saved_rows();self.assertFalse(rows[0]['eligible']);self.assertFalse(forecasts[0]['eligible'])
    def test_legacy_only_observation_chapter_all_symbols_no_fake_rules(self):
        result={'parts':[{'text':'# 收盤\n- 2303 聯電\n# 個股觀察建議\n- 2330 台積電：觀察\n- 2330 台積電：重複\n# 風險\n- 2303 聯電'}]}
        rows=b.legacy_picks(result,{'candidates':[{'symbol':'2330'},{'symbol':'2303'}]});self.assertEqual([r['symbol'] for r in rows],['2330']);self.assertIsNone(rows[0]['take_profit'])
    def test_rules_freeze_once_and_late_rules_not_forward(self):
        e=self.evidence();data=self.tracking();data['observations'][0].update(take_profit=None,stop_loss=None)
        with research.store() as c:b.ingest(c,'r',e['date'],'2026-09-19T00:00:00+08:00',{'tracking':data},e)
        with patch.object(research,'now',return_value='2026-09-22T10:00:00+08:00'):b.set_rules({'id':'r:2330','take_profit':110,'stop_loss':95})
        rows,_=b.saved_rows();self.assertFalse(rows[0]['eligible'])
        with self.assertRaises(ValueError):b.set_rules({'id':'r:2330','take_profit':120,'stop_loss':90})
    def test_actual_position_uses_latest_close_and_report_rules(self):
        self.insert();saved=b.set_actual_position({'id':'r:2330','buy_date':'2026-09-21','buy_price':100,'shares':1000});self.assertEqual(saved['symbol'],'2330')
        with research.store() as c:c.execute('INSERT INTO bt_prices VALUES (?,?,?)',('2026-09-22','2330',json.dumps(self.bar(105,109,103,109))))
        report=b.report();position=report['actual_positions'][0]
        self.assertEqual(position['unrealized_pnl'],9000);self.assertEqual(position['action_tone'],'watch');self.assertIn('接近停利',position['action']);self.assertEqual(report['actual_summary']['needs_action'],1)
        self.assertTrue(b.delete_actual_position({'symbol':'2330'})['deleted']);self.assertEqual(b.report()['actual_positions'],[])
        with self.assertRaises(ValueError):b.set_actual_position({'id':'r:2330','buy_date':'2026-09-21','buy_price':100,'shares':1.5})
    def test_holidays_not_weekdays_alone(self):
        cal=b.parse_calendar({'stat':'ok','date':'20260101','fields':['日期','名稱','說明'],'data':[['2026-09-25','中秋節','放假'],['2026-09-28','教師節','放假'],['2026-01-02','國曆新年開始交易日','']]},2026)
        with research.store() as c:c.execute('INSERT INTO bt_calendars VALUES (?,?)',(2026,json.dumps(cal)))
        self.assertEqual(b.next_day('2026-09-24'),'2026-09-29');self.assertEqual(b.next_day('2026-09-18'),'2026-09-21');self.assertIsNone(b.next_day('2027-01-01'))
    def test_futures_session_date_and_contract_parser(self):
        html='<input name="queryDate" value="2026/09/18"><input name="marketCode" value="0"><p>2026/09/18 08:45~13:45 一般交易時段行情表</p><table class="table_f"><tr>'+''.join('<td>'+v+'</td>' for v in ['TX','202610','100','110','90','105'])+'</tr></table>'
        rows=b.parse_futures(html,'2026-09-18');self.assertEqual(rows[0]['contract'],'202610')
        with self.assertRaises(ValueError):b.parse_futures(html,'2026-09-17')
        with self.assertRaises(ValueError):b.parse_futures(html.replace('value="0"','value="1"'),'2026-09-18')
    def test_futures_openapi_never_mixes_night_and_spreads(self):
        fields={'Date':'20260918','Contract':'TX','ContractMonth(Week)':'202610','Open':'47080','High':'47464','Low':'46897','Last':'47418','TradingSession':'一般'}
        rows=b.parse_futures_api([fields,{**fields,'TradingSession':'盤後','Open':'46000'},{**fields,'ContractMonth(Week)':'202610/202611'}])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['open'],47080);self.assertEqual(rows[0]['date'],'2026-09-18')

    def test_forecast_error_not_trading_win_rate(self):
        row={'payload':json.dumps(self.tracking()['futures'])};r=b.prediction_result(row,self.bar(105,115,85,110))
        self.assertEqual(r['open_error'],-5);self.assertEqual(r['absolute_error'],5);self.assertEqual(r['close_error'],-5);self.assertTrue(r['direction_correct']);self.assertTrue(r['inside_outer']);self.assertTrue(r['touches']['support1']);self.assertFalse(r['touches']['support2'])

    def test_v2_requires_close_forecast_but_legacy_remains_readable(self):
        legacy=self.tracking();legacy['futures'].pop('close')
        self.assertIsNone(b.validate_tracking(legacy,self.evidence())['futures']['close'])
        current=self.evidence();current['generation_policy']={'version':'research-v2'}
        with self.assertRaisesRegex(ValueError,'收盤預測'):b.validate_tracking(legacy,current)

    def test_futures_only_report_accepts_no_observations_and_never_writes_stock_rows(self):
        evidence=self.evidence();evidence['generation_policy']={'version':'research-futures-v3','stock_tracking':False}
        tracking=b.validate_tracking({'futures':self.tracking()['futures']},evidence)
        self.assertEqual(tracking['observations'],[])
        with research.store() as c:
            b.ingest(c,'future-only',evidence['date'],'2026-09-19T20:00:00+08:00',{'tracking':tracking},evidence)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bt_observations').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bt_predictions').fetchone()[0],1)

    def test_futures_report_never_returns_archived_stock_data(self):
        self.insert()
        result=b.futures_report()
        self.assertEqual(len(result['forecasts']),1)
        self.assertNotIn('rows',result);self.assertNotIn('actual_positions',result);self.assertNotIn('curve',result)
        self.assertNotIn('closed',result['summary']);self.assertNotIn('stock',result.get('model') or {})

    def test_futures_prepare_does_not_refresh_or_read_stock_history(self):
        with patch.object(b,'refresh_futures') as refresh,patch.object(b,'next_day',return_value='2026-09-21'),patch.object(b,'fetch_futures',return_value=[]),patch.object(b,'feedback',return_value={}) as feedback:
            b.prepare('2026-09-18')
        refresh.assert_called_once_with(wait=True);feedback.assert_called_once_with('2026-09-18',stocks=False)

    def test_active_futures_refresh_never_migrates_or_reads_stock_history(self):
        with patch.object(b,'migrate',side_effect=AssertionError('legacy migrate called')),patch.object(b,'saved_rows',side_effect=AssertionError('stock history read')),patch.object(b,'saved_forecasts',return_value=[]):
            b.refresh_futures(wait=True)
        self.assertEqual(b.job()['status'],'complete')

    def test_model_snapshot_is_persisted_with_report(self):
        evidence=self.evidence();evidence['alpha_model']={'version':'alpha-ridge-v1','stock':{'status':'warming','samples':0},'futures':{}}
        tracking=b.validate_tracking(self.tracking(),evidence)
        with research.store() as c:
            b.ingest(c,'model-run','2026-09-18','2026-09-19T20:00:00+08:00',{'tracking':tracking},evidence)
            saved=json.loads(c.execute('SELECT payload FROM bt_model_runs WHERE report_id=?',('model-run',)).fetchone()[0])
        self.assertEqual(saved['version'],'alpha-ridge-v1')
    def test_feedback_excludes_future_and_posthoc(self):
        self.insert()
        with research.store() as c:c.execute('INSERT INTO bt_future_results VALUES (?,?,?)',('r',json.dumps({'status':'scored','open_error':10,'actual':{'open':90}}),research.now()))
        self.assertEqual(b.feedback('2026-09-18')['forecast_samples'],0);self.assertEqual(b.feedback('2026-09-21')['forecast_samples'],1)
    def test_completed_only_metrics_not_unrealized_or_revision(self):
        self.insert();self.insert('r2','2026-09-20T20:00:00+08:00')
        result=self.ev({'2026-09-21':self.bar(100,115,98,110)})
        with research.store() as c:
            for ident in ['r','r2']:c.execute('INSERT INTO bt_results VALUES (?,?,?)',(ident+':2330',json.dumps(result),research.now()))
        s=b.report()['summary'];self.assertEqual(s['closed'],1);self.assertEqual(s['win_rate'],100);self.assertEqual(s['realized_pnl'],10000);self.assertEqual(s['expected_value_pct'],10)
    def test_candidate_recommendation_is_not_gated_by_box_signal(self):
        e=self.evidence();e['box_strategy']={'signals':[],'levels':[{'symbol':'2330','signal':False,'box_bottom':96,'failures':['收盤未進入箱底30%區']}]}
        frozen=b.validate_tracking(self.tracking(),e)
        self.assertEqual(frozen['observations'][0]['take_profit'],110)
        self.assertEqual(frozen['observations'][0]['box_bottom'],96)
        self.assertEqual(frozen['version'],b.VERSION)
        with self.assertRaisesRegex(ValueError,'未產生'):b.validate_tracking({**self.tracking(),'observations':[]},e)
    def test_new_report_requires_dmi_but_sar_is_only_frozen_bonus(self):
        e=self.evidence();technical={'dmi_pass':True,'label':'早期轉強','plus_di':22,'minus_di':18,'adx':13,'sar':{'bullish':False,'label':'SAR尚未確認','value':103}}
        e['technical_policy']={'version':'dmi-sar-v1','dmi_required':True,'eligible':1};e['candidates'][0]['technical']=technical
        tracking=b.validate_tracking(self.tracking(),e)
        self.assertEqual(tracking['observations'][0]['technical'],technical)
        with research.store() as c:b.ingest(c,'dmi','2026-09-18','2026-09-19T20:00:00+08:00',{'tracking':tracking},e)
        row=next(r for r in b.saved_rows()[0] if r['report_id']=='dmi')
        self.assertEqual(row['technical']['label'],'早期轉強');self.assertFalse(row['technical']['sar']['bullish'])
        e['candidates'][0]['technical']={**technical,'dmi_pass':False}
        with self.assertRaisesRegex(ValueError,'DMI未轉多'):b.validate_tracking(self.tracking(),e)
    def test_no_dmi_eligible_candidate_allows_empty_observation_day(self):
        e=self.evidence();e['technical_policy']={'version':'dmi-sar-v1','dmi_required':True,'eligible':0}
        e['candidates'][0]['technical']={'dmi_pass':False,'label':'DMI空方／轉弱'}
        tracking=b.validate_tracking({**self.tracking(),'observations':[]},e)
        self.assertEqual(tracking['observations'],[]);self.assertEqual(tracking['futures']['open'],100)
    def test_recommendation_uses_next_open_fixed_levels_and_costs(self):
        row=self.row();quotes={'2026-09-21':self.bar(100,103,98,101),'2026-09-22':self.bar(101,112,100,110)}
        out=b.evaluate_recommendation(row,['2026-09-21','2026-09-22'],quotes,'2026-09-22')
        self.assertEqual(out['status'],'closed');self.assertEqual(out['entry_price'],100);self.assertEqual(out['exit_price'],110)
        self.assertLess(out['net_pnl'],out['pnl']);self.assertIsNotNone(out['r_multiple']);self.assertEqual(out['strategy'],'研究推薦固定價位')
    def test_recommendation_persists_box_bottom_as_reference_only(self):
        e=self.evidence();e['box_strategy']={'signals':[],'levels':[{'symbol':'2330','box_bottom':96,'signal':False}]}
        tracking=b.validate_tracking(self.tracking(),e)
        with research.store() as c:b.ingest(c,'new','2026-09-18','2026-09-19T20:00:00+08:00',{'tracking':tracking},e)
        row=b.saved_rows()[0][0]
        self.assertEqual(row['box_bottom'],96);self.assertIsNone(row['box_rule']);self.assertEqual(row['recommendation_rule']['version'],b.VERSION)
    def test_box_time_exit_charges_cost_and_records_r(self):
        row=self.row();row.update(take_profit=115,stop_loss=94)
        rule={'initial_stop':94,'tp1':115,'box_top':108,'box_bottom':96,'atr20':2,'probe_size_pct':60,'add_size_pct':40,'time_stop_days':2,'circuit_breaker_pct':8}
        quotes={'2026-09-21':self.bar(100,103,98,101),'2026-09-22':self.bar(101,103,99,102)}
        out=b.evaluate_box(row,rule,['2026-09-21','2026-09-22'],quotes,'2026-09-22')
        self.assertEqual(out['status'],'closed');self.assertIn('時間停損',out['message']);self.assertLess(out['net_pnl'],out['pnl']);self.assertIsNotNone(out['r_multiple'])
    def test_attach_tracking_keeps_citations_and_canonical_observations(self):
        original={'parts':[{'text':'# 新聞\n來源','url':'https://www.twse.com.tw/'},{'text':'\n<backtest>'+json.dumps(self.tracking())+'</backtest>'}]}
        r=research.attach_tracking(original,self.evidence());self.assertEqual(r['parts'][0]['url'],'https://www.twse.com.tw/');self.assertEqual(len(r['tracking']['observations']),1)
        text=''.join(p['text'] for p in r['parts']);self.assertNotIn('<backtest>',text);self.assertIn('停利 110',text);self.assertIn('箱底參考',text);self.assertIn('預測開盤 100',text)
    def test_attach_tracking_ignores_tag_name_mentioned_in_prose(self):
        body='# 個股\n正式清單以 `<backtest>` 結構為準。\n\n<backtest>'+json.dumps(self.tracking())+'</backtest>'
        result=research.attach_tracking({'parts':[{'text':body}],'raw_text':body},self.evidence())
        self.assertEqual(result['tracking']['observations'][0]['symbol'],'2330')
        text=''.join(part['text'] for part in result['parts'])
        self.assertIn('正式清單以 `<backtest>` 結構為準。',text)
        self.assertIn('停利 110',text)
    def test_citation_title_quotes_cannot_corrupt_tracking_json(self):
        text='# 新聞\n來源甲\n<backtest>'+json.dumps(self.tracking(),ensure_ascii=False)+'</backtest>'
        # Model output is valid JSON, but the rendered citation title contains unescaped quotes.
        start=text.index('數值規則')
        response={'status':'completed','output':[{'type':'web_search_call','status':'completed'},{'type':'message','content':[{'type':'output_text','text':text,'annotations':[{'type':'url_citation','start_index':start,'end_index':start+4,'url':'https://www.twse.com.tw/','title':'公司"最新"公告'}]}]}]}
        parsed=research.parse_result(response)
        self.assertIn('公司"最新"公告',''.join(p['text'] for p in parsed['parts']))
        result=research.attach_tracking(parsed,self.evidence())
        self.assertEqual(result['tracking']['observations'][0]['reason'],'數值規則')
        self.assertNotIn('raw_text',result)
        self.assertNotIn('<backtest>',''.join(p['text'] for p in result['parts']))
    def test_tracking_only_repairs_unambiguous_formatting(self):
        self.assertEqual(research.decode_tracking('```json\n{"text":"字串,}不能改", "price":110,}\n```'),{'text':'字串,}不能改','price':110})
        self.assertEqual(research.decode_tracking('{"price":110}}'),{'price':110})
        for bad in ['{"price":NaN}','{"price":100,"price":200}','{"price":','{"reason":"未跳脫的"引用""}']:
            with self.assertRaises(ValueError):research.decode_tracking(bad)
    def test_format_failure_preserves_model_output_without_publishing_or_retrying(self):
        class Response:
            status_code=200
            def json(self):return {'status':'completed','output':[{'type':'web_search_call','status':'completed'},{'type':'message','content':[{'type':'output_text','text':'新聞來源 <backtest>{"price":</backtest>','annotations':[{'type':'url_citation','start_index':2,'end_index':4,'url':'https://www.twse.com.tw/','title':'來源'}]}]}]}
        with patch.object(research.requests,'post',return_value=Response()) as request:
            with self.assertRaises(research.ReportFormatError) as failure:research.request_report(self.evidence(),'sk-secret','test-model')
            self.assertIn('<backtest>',failure.exception.draft['raw_text']);self.assertNotIn('sk-secret',json.dumps(failure.exception.draft));self.assertEqual(request.call_count,1)
            body=request.call_args.kwargs['json'];self.assertEqual(body['tools'][0]['search_context_size'],'low');self.assertEqual(body['max_output_tokens'],4200)

    def test_report_worker_publishes_forecast_without_new_stock_observations(self):
        class Immediate:
            def __init__(self,target,**kw):self.target=target
            def start(self):self.target()
        result={'parts':[{'text':'新聞','url':'https://www.twse.com.tw/'}],'tracking':b.validate_tracking(self.tracking(),self.evidence())}
        with patch.object(research,'available_dates',return_value=['2026-09-18']),patch.object(research,'context',return_value=self.evidence()),patch.object(research,'credentials',return_value=('sk-test','chat-latest')),patch.object(b,'prepare',return_value={**self.evidence()['execution'],'warnings':[]}),patch.object(research,'request_report',return_value=result),patch.object(research.threading,'Thread',Immediate),patch.object(research,'now',return_value='2026-09-19T20:00:00+08:00'):
            generated=research.generate();rows,forecasts=b.saved_rows()
            self.assertEqual(rows,[]);self.assertEqual(forecasts[0]['report_id'],generated['id'])
            with research.store() as c:self.assertEqual(c.execute('SELECT status FROM reports WHERE id=?',(generated['id'],)).fetchone()[0],'complete')
            cached=research.generate();self.assertTrue(cached['cached']);self.assertEqual(len(b.saved_rows()[0]),0)

    def test_get_is_read_only_for_api_and_does_not_fetch(self):
        with patch.object(sources,'get') as get,patch.object(research.requests,'post') as post:
            b.report();get.assert_not_called();post.assert_not_called()

if __name__=='__main__':unittest.main()
