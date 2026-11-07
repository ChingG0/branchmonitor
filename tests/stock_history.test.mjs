import test from 'node:test'
import assert from 'node:assert/strict'
import {stockHistory} from '../frontend/src/stockHistory.ts'

const calendar=['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26']
const trade=(symbol,entry_date,exit_date,pnl,holding_sessions)=>({symbol,entry_date,exit_date,pnl,holding_sessions})
const model={as_of:'2026-10-26',trades:[
 trade('2330','2026-10-19','2026-10-21',10,2),
 trade('2330','2026-10-22','2026-10-26',-5,2),
 trade('2330','2026-10-23','2026-10-23',0,0),
 trade('2330','2026-10-26',undefined,100,0),
 trade('1101','2026-10-19','2026-10-20',-1,1),
]}
test('same stock, same account; completed trades only at selected close',()=>{
 const old=structuredClone(model)
 const stats=stockHistory(model,'2330','2026-10-21',calendar,'v1')
 assert.deepEqual([stats.completed,stats.wins,stats.winRate,stats.all.avgDays],[1,1,100,2])
 assert.equal(stockHistory(model,'2330','2026-10-20',calendar,'v1').winRate,null)
 const latest=stockHistory(model,'2330','2026-10-26',calendar,'v1')
 assert.deepEqual([latest.completed,latest.wins,latest.losses,latest.flat],[3,1,1,1])
 assert.ok(Math.abs(latest.winRate-100/3)<1e-12)
 assert.equal(latest.all.avgDays,4/3)
 assert.equal(latest.win.avgDays,2);assert.equal(latest.loss.avgDays,2)
 assert.equal(stockHistory({as_of:model.as_of,trades:[]},'2330',model.as_of,calendar,'v10').completed,0)
 assert.deepEqual(model,old)
})
test('calendar fallback counts trading sessions and preserves same-day trades',()=>{
 const saved={as_of:'2026-10-26',trades:[trade('2330','2026-10-23','2026-10-26',-1),trade('2330','2026-10-22','2026-10-22',1,0)]}
 const stats=stockHistory(saved,'2330',saved.as_of,calendar,'v3')
 assert.equal(stats.win.avgDays,0);assert.equal(stats.loss.avgDays,1)
 assert.equal(stats.all.avgDays,.5)
})
test('missing time does not bias win rate; missing profit is never a loss',()=>{
 const saved={as_of:'2026-10-26',trades:[trade('2330','2026-09-01','2026-09-02',1),trade('2330','2026-10-19','2026-10-20',undefined,1)]}
 const stats=stockHistory(saved,'2330',saved.as_of,calendar,'v3')
 assert.equal(stats.completed,1);assert.equal(stats.winRate,100)
 assert.equal(stats.all.samples,0);assert.equal(stats.all.avgDays,null)
 assert.equal(stats.missingResult,1)
})
test('ETF symbolless lots belong only to V5 00663L and use costed returns',()=>{
 const saved={as_of:'2026-10-26',trades:[{entry_date:'2026-10-19',exit_date:'2026-10-20',net_return_pct:-.2,holding_sessions:1}]}
 assert.equal(stockHistory(saved,'00663L',saved.as_of,calendar,'v5').losses,1)
 assert.equal(stockHistory(saved,'00663L',saved.as_of,calendar,'v1').completed,0)
 assert.equal(stockHistory(saved,'2330',saved.as_of,calendar,'v5').completed,0)
})
test('invalid and future dates never leak into saved account history',()=>{
 const saved={as_of:'2026-10-21',trades:[...model.trades,trade('2330','2026-02-30','2026-03-01',1,1),trade('2330','2026-10-21','2026-10-20',1,0)]}
 assert.equal(stockHistory(saved,'2330','2026-10-30',calendar,'v10').completed,1)
 assert.equal(stockHistory(saved,'2330','invalid',calendar,'v10').completed,0)
 assert.equal(stockHistory(undefined,'2330','2026-10-21',calendar,'v10').asOf,null)
})
