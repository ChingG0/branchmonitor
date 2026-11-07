import json,tempfile,unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from backend import daily_follow as follow,db


class DailyFollowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=patch.object(db,'DATA',Path(self.temp.name));self.data.start();db.initialize()
        with db.connect() as c:
            c.execute('DELETE FROM branches');c.executemany('INSERT INTO branches VALUES (?,?,?,1)',[('A','1','高勝率分點'),('B','2','大量低勝率分點')])
            for day in ('2026-09-01','2026-09-02'):
                for broker,branch in [('A','1'),('B','2')]:c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',(broker,branch,day,'observed',12,'','now'))
            history=[('2026-08-10',90),('2026-08-11',95),('2026-08-12',100),('2026-08-13',95),('2026-08-14',90),('2026-08-17',90),('2026-08-18',90),('2026-08-19',95),('2026-08-20',100),('2026-08-21',105),('2026-08-24',110),('2026-08-25',110),('2026-08-26',105),('2026-08-27',100),('2026-08-28',100),('2026-08-31',100)]
            for day,_ in history:c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
            for i in range(12):
                symbol=str(1101+i);net=1000-i*20 if i<6 else -(1000-(i-6)*20)
                for day in ('2026-09-01','2026-09-02'):
                    c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',('A','1',day,symbol,'股'+symbol,max(net,0),max(-net,0),net,'test'))
                    c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',('B','2',day,symbol,'股'+symbol,max(net*50,0),max(-net*50,0),net*50,'test'))
                    c.execute('INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,'股'+symbol,100,100,101,99,1000,'TWSE'))
                for day,price in history:c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,'股'+symbol,price,price,price,price,1000,'TWSE'))
                for day,value in [('2026-08-10',10),('2026-08-12',-10),('2026-08-20',10),('2026-08-25',-10)]:
                    c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',('A','1',day,symbol,'股'+symbol,max(value,0),max(-value,0),value,'test'))
                for day,value in [('2026-08-12',10),('2026-08-18',-10),('2026-08-25',10),('2026-08-27',-10)]:
                    c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',('B','2',day,symbol,'股'+symbol,max(value,0),max(-value,0),value,'test'))
                for broker,branch in [('A','1'),('B','2')]:c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',(broker,branch,symbol,'2026-08-10','2026-08-31','now'))
            groups={mode:[] for mode in ('20','60','ytd')}
            for i in range(12):
                symbol=str(1101+i);rs=95-i*3 if i<6 else 10+(i-6)*3
                row={'symbol':symbol,'name':'股'+symbol,'market':'TWSE','close':100,'return_pct':rs-50,'ytd_pct':rs-40,'excess_pct':rs-60,'rs':rs}
                groups['20'].append(row);groups['ytd'].append(row);groups['60'].append(row)
            payload={'groups':groups,'index_ytd':10,'anchors':{'20':'2026-08-01','60':'2026-06-01','ytd':'2025-12-31'}}
            for day in ('2026-09-01','2026-09-02'):c.execute('INSERT INTO rs_days VALUES (?,?,?)',(day,json.dumps(payload),'now'))
        self.sectors=patch.object(follow.sector_flow,'classification',return_value={'memberships':{str(1101+i):['測試族群'] for i in range(12)},'snapshot':'v1'});self.sectors.start()
        self.records_patch=patch.object(follow,'_branch_track_records',side_effect=self.records);self.records_patch.start()

    def tearDown(self):
        self.records_patch.stop();self.sectors.stop();self.data.stop();self.temp.cleanup()

    def records(self,_day,keys):
        result={}
        for broker,branch,symbol in keys:
            good=broker=='A';base={'name':'高勝率分點' if good else '大量低勝率分點','history_verified':True,
                'cycle_win_rate':75 if good else 20,'cycle_samples':4,'cycle_avg_return_pct':3 if good else -2,
                'dominant_style':'5–20 日短線','overnight_share':0}
            for side in ('buy','sell'):
                base[side]={'direction':side,'win_rate':80 if good else 20,'quality_score':70 if good else 30,
                    'effective_samples':8,'forward_5':{'samples':4,'wins':3 if good else 1,'win_rate':75 if good else 25,'avg_return_pct':2 if good else -2},
                    'forward_10':{'samples':4,'wins':3 if good else 1,'win_rate':75 if good else 25,'avg_return_pct':3 if good else -3},
                    'style_penalty':0,'style_compatible':True,'provisional_qualified':good,'qualified':good}
            result[(broker,branch,symbol)]=base
        return result

    def test_tpex_index_parser_and_dual_sma20_filter(self):
        payload={'tables':[{'fields':['日期','開市','最高','最低','收市','漲/跌'],'data':[
            ['2026/05/04','100','102','99','101','1'],['2026/05/05','101','103','100','102','1']]}]}
        rows=follow._parse_tpex_index(payload,'2026-05-01')
        self.assertEqual(rows[0],{'date':'2026-05-04','open':100.,'high':102.,'low':99.,'close':101.})
        with self.assertRaises(ValueError):follow._parse_tpex_index(payload,'2026-06-01')

        days=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(21)]
        taiex=[{'date':day,'close':100. if i<20 else 90.} for i,day in enumerate(days)]
        tpex=[{'date':day,'close':100. if i<20 else 90.} for i,day in enumerate(days)]
        state=follow._dual_index_sma20_filter(taiex,tpex)[days[-1]]
        self.assertTrue(state['blocked'])
        tpex[-1]['close']=110.
        self.assertFalse(follow._dual_index_sma20_filter(taiex,tpex)[days[-1]]['blocked'])

    def test_v1_market_filter_blocks_buys_but_preserves_candidates(self):
        payload={'date':'2026-09-01','buy':[{'symbol':'1101'}],'sell':[],'warnings':[]}
        state={'date':'2026-09-01','available':True,'blocked':True}
        filtered=follow._apply_v1_market_filter(payload,state)
        self.assertEqual(filtered['buy'],[])
        self.assertEqual(filtered['blocked_buy'][0]['symbol'],'1101')
        self.assertIn('V1 暫停買進',filtered['warnings'][0])

    def test_generates_five_each_and_freezes_saved_selection(self):
        result=follow.generate('2026-09-01')
        self.assertEqual(len(result['buy']),5);self.assertEqual(len(result['sell']),5)
        self.assertEqual(len(result['v4']['buy']),5);self.assertEqual(len(result['v4']['sell']),5)
        self.assertTrue(all(r['sectors']==['測試族群'] for r in result['buy']))
        self.assertTrue(all([b['name'] for b in r['qualified_branches']]==['高勝率分點'] for r in result['buy']+result['sell']))
        self.assertTrue(all(r['ignored_branch_count']==1 for r in result['buy']+result['sell']))
        original=[r['symbol'] for r in result['buy']]
        with db.connect() as c:c.execute("UPDATE flows SET net=-net,buy=sell,sell=buy WHERE date='2026-09-01'")
        self.assertEqual([r['symbol'] for r in follow.generate('2026-09-01')['buy']],original)

    def test_requires_same_day_rs_and_marks_old_reports_as_replay(self):
        with db.connect() as c:c.execute("DELETE FROM rs_days WHERE date='2026-09-02'")
        self.assertEqual(follow.available_dates(),['2026-09-01'])
        with self.assertRaises(ValueError):follow.generate('2026-09-02')
        with db.connect() as c:c.execute("INSERT INTO market_days VALUES ('2026-09-02','ok','ok','')")
        self.assertEqual(follow.generate('2026-09-01')['mode'],'replay')
        self.assertEqual(follow._mode('2026-09-04','2026-09-05T12:00:00+08:00'),'forward')
        self.assertEqual(follow._mode('2026-09-04','2026-09-07T09:00:00+08:00'),'replay')

    def test_next_open_horizons_and_inverse_sell_return(self):
        follow.generate('2026-09-01')
        with db.connect() as c:
            for n,day in enumerate(('2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09','2026-09-10','2026-09-11','2026-09-14','2026-09-15')):
                c.execute('INSERT OR REPLACE INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
                for i in range(12):
                    symbol=str(1101+i);close=110 if i<6 else 90
                    c.execute('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,'股'+symbol,close,100,111,89,1000,'TWSE'))
        with db.connect() as c:
            before=[tuple(r) for r in c.execute('SELECT date,payload FROM daily_follow_reports ORDER BY date')]
            calendar=[r[0] for r in c.execute('SELECT date FROM market_days ORDER BY date')]
            quotes={(r['date'],r['symbol']):dict(r) for r in c.execute('SELECT * FROM prices')}
        result=follow.report('2026-09-01');selected=result['selected']
        # Archived calculation helpers remain available; page reads no longer
        # evaluate or rewrite retired V1/V4 lists.
        for side in ('buy','sell'):
            for signal in selected[side]:
                signal['backtest']=follow._evaluate(signal,selected['date'],side,calendar,quotes)
        self.assertEqual(selected['buy'][0]['backtest']['entry_date'],'2026-09-02')
        self.assertGreater(selected['buy'][0]['backtest']['horizons']['1']['net_return_pct'],0)
        self.assertGreater(selected['sell'][0]['backtest']['horizons']['10']['net_return_pct'],0)
        summary=follow._summary([selected])
        self.assertEqual(summary['10']['combined']['samples'],10)
        self.assertEqual(summary['10']['combined']['hit_rate'],100)
        self.assertEqual(list(result['model_performance']['models']),['v1','v10','v11','v2','v3','v4','v5','v8','v9'])
        self.assertEqual(result['summary'],{})
        with db.connect() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT date,payload FROM daily_follow_reports ORDER BY date')])

    def test_twenty_and_forty_day_horizons_wait_then_score(self):
        calendar=[f'2026-01-{day:02d}' for day in range(1,32)]+[f'2026-02-{day:02d}' for day in range(1,11)]
        quotes={(day,'1101'):{'open':100,'close':100+i} for i,day in enumerate(calendar[1:],1)}
        signal={'symbol':'1101'}
        result=follow._evaluate(signal,calendar[0],'buy',calendar,quotes)
        self.assertEqual(result['horizons']['20']['status'],'scored')
        self.assertEqual(result['horizons']['40']['status'],'scored')
        self.assertEqual(result['horizons']['40']['exit_date'],calendar[40])
        short=follow._evaluate(signal,calendar[0],'buy',calendar[:21],quotes)
        self.assertEqual(short['horizons']['20']['status'],'scored')
        self.assertEqual(short['horizons']['40']['status'],'pending')
        no_future=follow._evaluate(signal,calendar[-1],'buy',calendar,quotes)
        self.assertEqual(set(no_future['horizons']),{'1','5','10','20','40'})
        self.assertTrue(all(value['status']=='pending' for value in no_future['horizons'].values()))

    def test_v1_takes_profit_at_eight_percent_and_respects_gap_open(self):
        calendar=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(22)]
        quotes={(day,'1101'):{'open':100.,'high':101.,'low':99.,'close':100.} for day in calendar[1:]}
        quotes[(calendar[2],'1101')].update(high=109.,close=104.)
        result=follow._evaluate_v1({'symbol':'1101'},calendar[0],calendar,quotes)
        self.assertEqual((result['exit_date'],result['exit_price'],result['exit_reason']),
                         (calendar[2],108.,'take_profit'))
        self.assertEqual(result['holding_sessions'],2)
        self.assertAlmostEqual(result['gross_return_pct'],8.)
        self.assertGreater(result['net_return_pct'],0.)

        quotes[(calendar[2],'1101')].update(open=110.,high=111.,close=110.)
        gap=follow._evaluate_v1({'symbol':'1101'},calendar[0],calendar,quotes)
        self.assertEqual((gap['exit_price'],gap['exit_reason']),(110.,'take_profit_gap'))
        self.assertAlmostEqual(gap['gross_return_pct'],10.)

    def test_v1_stops_at_eight_percent_and_uses_conservative_same_day_order(self):
        calendar=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(22)]
        quotes={(day,'1101'):{'open':100.,'high':101.,'low':99.,'close':100.} for day in calendar[1:]}
        quotes[(calendar[2],'1101')].update(high=109.,low=91.)
        result=follow._evaluate_v1({'symbol':'1101'},calendar[0],calendar,quotes)
        self.assertEqual((result['exit_price'],result['exit_reason']),(92.,'stop_loss'))
        self.assertAlmostEqual(result['gross_return_pct'],-8.)

        quotes[(calendar[2],'1101')].update(open=89.,high=95.,low=88.)
        gap=follow._evaluate_v1({'symbol':'1101'},calendar[0],calendar,quotes)
        self.assertEqual((gap['exit_price'],gap['exit_reason']),(89.,'stop_loss_gap'))

    def test_v1_exits_at_day_twenty_close_when_target_is_not_reached(self):
        calendar=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(22)]
        quotes={(day,'1101'):{'open':100.,'high':104.,'low':99.,'close':100.} for day in calendar[1:]}
        quotes[(calendar[20],'1101')]['close']=98.
        result=follow._evaluate_v1({'symbol':'1101'},calendar[0],calendar,quotes)
        self.assertEqual((result['exit_date'],result['exit_price'],result['exit_reason']),
                         (calendar[20],98.,'day20'))
        self.assertEqual(result['holding_sessions'],20)
        self.assertLess(result['net_return_pct'],0.)

    def test_buy_and_sell_direction_records_are_never_combined(self):
        calendar=[f'2026-08-{day:02d}' for day in range(1,12)]
        prices={(day,'1101'):100+i*2 for i,day in enumerate(calendar)}
        flows=[{'date':calendar[0],'symbol':'1101','net':10},{'date':calendar[0],'symbol':'1101','net':-10}]
        buy=follow._forward_record(flows,'1101',calendar,prices,'buy')
        sell=follow._forward_record(flows,'1101',calendar,prices,'sell')
        self.assertEqual((buy['5']['samples'],buy['5']['wins']),(1,1))
        self.assertEqual((sell['5']['samples'],sell['5']['wins']),(1,0))
        self.assertGreater(buy['5']['win_rate'],sell['5']['win_rate'])

    def test_branch_average_cost_uses_fifo_inventory_through_signal_day(self):
        rows=[
            {'date':'2026-08-01','symbol':'1101','net':100},
            {'date':'2026-08-02','symbol':'1101','net':100},
            {'date':'2026-08-03','symbol':'1101','net':-100},
            {'date':'2026-08-04','symbol':'1101','net':50},
        ]
        prices={(row['date'],'1101'):price for row,price in zip(rows,(10.,20.,25.,30.))}
        result=follow._estimated_open_position(rows,prices)
        self.assertEqual(result['average_cost_status'],'available')
        self.assertEqual(result['estimated_position'],150.)
        self.assertAlmostEqual(result['average_cost'],23.3333,places=4)
        closed=follow._estimated_open_position(rows+[{'date':'2026-08-05','symbol':'1101','net':-150}],prices)
        self.assertEqual(closed,{'average_cost':None,'estimated_position':0.,'average_cost_status':'no_position'})

    def test_saved_signal_gets_branch_specific_cost_without_changing_selection(self):
        proof={'name':'高勝率分點','broker':'A','branch':'1'}
        payload={'date':'2026-09-01','buy':[{'symbol':'1101','branches':[proof],'qualified_branches':[]}],
                 'sell':[],'v4':{'buy':[],'sell':[]}}
        changed=follow._attach_branch_average_costs(payload)
        self.assertTrue(changed)
        self.assertEqual(payload['buy'][0]['symbol'],'1101')
        self.assertEqual(proof['average_cost_status'],'available')
        self.assertEqual(proof['average_cost'],100.)
        self.assertEqual(proof['estimated_position'],1000.)
        self.assertEqual(proof['average_cost_as_of'],'2026-09-01')

    def test_targeted_history_plan_only_repairs_shortlisted_stale_pairs(self):
        with db.connect() as c:c.execute("INSERT OR REPLACE INTO market_days VALUES ('2026-09-01','ok','ok','')")
        def stale(day,keys):
            records=self.records(day,keys)
            for row in records.values():
                row['history_verified']=False
                for side in ('buy','sell'):row[side]['qualified']=False
            return records
        with patch.object(follow,'_branch_track_records',side_effect=stale):
            plan=follow._targeted_history_plan('2026-09-02',5)
        self.assertEqual(plan['cutoff'],'2026-09-01')
        self.assertEqual(len(plan['rows']),10)
        self.assertTrue(all(row['broker']=='A' for row in plan['rows']))
        self.assertTrue(all(row['start']=='2026-09-01' and row['end']=='2026-09-01' for row in plan['rows']))

    def test_v4_open_and_pullback_entries_hold_twenty_sessions_from_fill(self):
        calendar=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(35)]
        quotes={(day,'1101'):{'open':100.,'high':102.,'low':98.,'close':100.} for day in calendar}
        quotes[(calendar[0],'1101')]['close']=100.
        quotes[(calendar[1],'1101')]['open']=101.
        quotes[(calendar[20],'1101')]['close']=120.
        opened=follow._evaluate_v4({'symbol':'1101','close':100},calendar[0],'hybrid20',calendar,quotes)
        self.assertEqual((opened['entry_kind'],opened['entry_date']),('up_open',calendar[1]))
        self.assertEqual(opened['exit_date'],calendar[20])
        self.assertGreater(opened['net_return_pct'],0)

        quotes[(calendar[1],'1101')].update(open=99.,low=98.)
        quotes[(calendar[2],'1101')].update(open=98.,low=95.)
        quotes[(calendar[21],'1101')]['close']=110.
        pulled=follow._evaluate_v4({'symbol':'1101','close':100},calendar[0],'hybrid20',calendar,quotes)
        self.assertEqual((pulled['entry_kind'],pulled['entry_price'],pulled['wait_sessions']),('pullback',95.,2))
        self.assertEqual(pulled['exit_date'],calendar[21])

        for day in calendar[1:6]:quotes[(day,'1101')].update(open=99.,low=96.)
        unfilled=follow._evaluate_v4({'symbol':'1101','close':100},calendar[0],'hybrid20',calendar,quotes)
        self.assertEqual(unfilled['status'],'unfilled')
        self.assertEqual(unfilled['target_price'],95.)

    def test_model_result_keeps_filled_return_and_per_signal_expectation_separate(self):
        days=[
            {'date':'2026-08-01','return_pct':5.,'eligible':2,'entries':2,'wins':1,'filled_returns':[10.,0.]},
            {'date':'2026-08-02','return_pct':2.5,'eligible':2,'entries':1,'wins':1,'filled_returns':[5.]},
        ]
        result=follow._model_result('v4b','V4-B','test',days)
        self.assertEqual(result['fill_rate'],75.)
        self.assertEqual(result['avg_return_pct'],5.)
        self.assertEqual(result['expected_return_pct'],3.75)
        self.assertEqual([point['date'] for point in result['curve']],['2026-08-01','2026-08-02'])
        self.assertAlmostEqual(result['curve'][-1]['value'],3.75)

    def test_live_model_curve_marks_open_positions_and_carries_realized_returns_to_latest_day(self):
        calendar=[(date(2026,8,1)+timedelta(days=i)).isoformat() for i in range(25)]
        quotes={}
        for index,day in enumerate(calendar):
            quotes[(day,'A')]={'open':100.,'close':120. if index==20 else (80. if index>20 else 100.)}
            quotes[(day,'B')]={'open':100.,'close':105. if index==24 else 102.}
        reports=[
            {'date':calendar[0],'buy':[{'symbol':'A'}]},
            {'date':calendar[22],'buy':[{'symbol':'B'}]},
        ]
        curve=follow._model_live_curve(reports,'v1',calendar,quotes)
        realized=(120/100-1)*100-follow.ROUND_TRIP_PCT
        unrealized=(105/100-1)*100-follow.ROUND_TRIP_PCT
        self.assertIsNone(curve[0]['value'])
        self.assertEqual((curve[-1]['date'],curve[-1]['open_positions'],curve[-1]['closed_positions']),
                         (calendar[-1],1,1))
        self.assertAlmostEqual(curve[21]['value'],realized,places=4)
        self.assertAlmostEqual(curve[-1]['value'],realized,places=4)
        self.assertAlmostEqual(curve[-1]['realized_value'],realized,places=4)
        self.assertAlmostEqual(curve[-1]['unrealized_value'],unrealized,places=4)
        self.assertAlmostEqual(curve[-1]['invested_cost_units'],2.,places=4)
        self.assertAlmostEqual(curve[-1]['realized_cost_units'],1.,places=4)
        self.assertAlmostEqual(curve[-1]['unrealized_cost_units'],1.,places=4)
        ranked=follow._with_live_curve({},reports,'v1',calendar,quotes)
        self.assertEqual((ranked['eligible_signals'],ranked['entries'],ranked['closed_position_count']),(2,2,1))
        self.assertEqual(ranked['hit_rate'],100.)
        self.assertEqual(ranked['top_realized'][0]['symbol'],'A')
        self.assertEqual(ranked['top_realized'][0]['holding_sessions'],20)
        self.assertEqual(ranked['top_unrealized'][0]['symbol'],'B')

    def test_removed_v4a_is_not_a_live_model(self):
        calendar=[(date(2026,8,1)+timedelta(days=i)).isoformat() for i in range(21)]
        quotes={(day,'A'):{'open':101.,'high':102.,'low':99.,'close':101.} for day in calendar}
        report={'date':calendar[0],'v4':{'buy':[{'symbol':'A','close':100.}]}}
        with self.assertRaises(ValueError):follow._model_day(report,'v4a',calendar,quotes)
        self.assertIsNone(follow._model_day(report,'v4b',calendar,quotes))

    def test_strategy_history_counts_only_prior_completed_trades_per_model(self):
        def v1_signal(symbol,status='scored',exit_date='2026-08-29',hit=True,value=5.):
            return {'symbol':symbol,'v1_strategy':{'status':status,'exit_date':exit_date,'hit':hit,'net_return_pct':value},
                    'backtest':{'horizons':{'20':{'status':status,'exit_date':exit_date,'hit':hit,'net_return_pct':value}}}}
        def v4_signal(symbol,b_status='scored'):
            return {'symbol':symbol,'strategies':{
                'hybrid20':{'status':b_status,'exit_date':'2026-08-30','hit':False,'net_return_pct':-3.},
            }}
        reports=[
            {'date':'2026-09-01','buy':[v1_signal('2330',status='pending')],'sell':[v1_signal('2330',status='pending')],'v4':{'buy':[v4_signal('2330')],'sell':[{'symbol':'2330','risk_backtest':v1_signal('2330',status='pending')['backtest']}]}},
            {'date':'2026-08-02','buy':[v1_signal('2330',hit=False,value=-4.)],'sell':[v1_signal('2330',hit=True)],'v4':{'buy':[v4_signal('2330',b_status='unfilled')],'sell':[{'symbol':'2330','risk_backtest':v1_signal('2330',hit=False)['backtest']}]}},
            {'date':'2026-08-01','buy':[v1_signal('2330')],'sell':[v1_signal('2330',hit=False)],'v4':{'buy':[v4_signal('2330')],'sell':[{'symbol':'2330','risk_backtest':v1_signal('2330',hit=True)['backtest']}]}},
        ]
        follow._attach_strategy_history(reports)
        latest=reports[0]
        self.assertEqual(latest['buy'][0]['strategy_history']['v1']['wins'],1)
        self.assertEqual(latest['buy'][0]['strategy_history']['v1']['losses'],1)
        self.assertNotIn('v4a',latest['v4']['buy'][0]['strategy_history'])
        self.assertEqual(latest['v4']['buy'][0]['strategy_history']['v4b']['samples'],1)
        self.assertEqual(latest['sell'][0]['strategy_history']['v1_sell']['wins'],1)
        self.assertEqual(latest['sell'][0]['strategy_history']['v1_sell']['losses'],1)
        self.assertEqual(latest['v4']['sell'][0]['strategy_history']['v4_sell']['wins'],1)
        self.assertEqual(latest['v4']['sell'][0]['strategy_history']['v4_sell']['losses'],1)
        self.assertEqual(reports[-1]['buy'][0]['strategy_history']['v1']['samples'],0)

    def test_v5_00663l_bias_strategy_uses_next_open_for_entry_and_exit(self):
        days=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(23)]
        tsmc=[{'date':day,'open':100.,'high':101.,'low':99.,'close':100.} for day in days]
        tsmc[19].update(open=95.,high=96.,low=94.,close=95.)
        tsmc[20].update(open=100.,high=111.,low=99.,close=110.)
        tsmc[21].update(open=108.,high=109.,low=104.,close=105.)
        asset=[{'date':day,'open':50.,'high':51.,'low':49.,'close':50.} for day in days]
        asset[20].update(open=50.,high=53.,low=49.,close=52.)
        asset[21].update(open=55.,high=57.,low=54.,close=56.)
        result=follow._v5_bias_result(tsmc,asset);trade=result['trades'][0]
        self.assertEqual((trade['signal_date'],trade['entry_date']),(days[19],days[20]))
        self.assertEqual((trade['exit_signal_date'],trade['exit_date']),(days[20],days[21]))
        self.assertGreater(trade['net_return_pct'],0)

    def test_market_volatility_has_native_daily_and_weekly_windows(self):
        days=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(180)]
        bars=[];close=100.
        for i,day in enumerate(days):
            close*=1.01 if i%2 else .99
            bars.append({'date':day,'close':close})
        result=follow._market_volatility(bars)
        self.assertTrue(result['daily']['curve'])
        self.assertTrue(result['weekly']['curve'])
        self.assertEqual(result['daily']['as_of'],days[-1])
        self.assertGreater(result['daily']['current_pct'],0)
        self.assertGreater(result['weekly']['current_pct'],0)
        self.assertTrue(0<result['daily']['percentile']<=100)
        self.assertTrue(0<result['weekly']['percentile']<=100)
        self.assertEqual((result['daily']['window'],result['weekly']['window']),(20,20))


    def test_holding_duration_reads_manual_snapshot_without_mutating_it(self):
        import copy
        saved={'key':'v3','as_of':'2026-09-02','available':True,'trades':[
            {'entry_date':'2026-08-28','exit_date':'2026-09-02','pnl':10}],
            'holdings':[{'entry_date':'2026-08-31','pnl':1000}]}
        before=copy.deepcopy(saved)
        with db.connect() as c:
            c.executemany('INSERT OR IGNORE INTO market_days VALUES (?,?,?,?)',
                         [(d,'ok','ok','') for d in ['2026-09-01','2026-09-02']])
            calendar=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' ORDER BY date")]
        with patch('backend.v3.report',return_value=saved),patch('backend.v3.apply_review',side_effect=AssertionError('must not publish')):
            value=follow.report()
        self.assertEqual(saved,before)
        summary=value['model_performance']['holding_duration']
        self.assertEqual(value['model_performance']['trading_calendar'], calendar)
        self.assertEqual(set(summary),{'v1','v2','v3','v4','v5','v8','v9','v10','v11'})
        self.assertEqual(summary['v3']['win'],{'samples':1,'avg_days':3})
        self.assertIsNone(summary['v3']['loss']['avg_days'])


if __name__=='__main__':unittest.main()
