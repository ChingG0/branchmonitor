import test from 'node:test'
import assert from 'node:assert/strict'
import {selectRows} from '../frontend/src/table.ts'
const rows=[
 {symbol:'A',name:'甲',holding_days:0,pnl:0,win_rate:100,closed_count:1,status:'open'},
 {symbol:'B',name:'乙',holding_days:12.5,pnl:-2000,win_rate:50,closed_count:10,status:'open'},
 {symbol:'C',name:'丙',holding_days:null,pnl:null,win_rate:null,closed_count:0,status:'closed'},
 {symbol:'D',name:'丁',holding_days:5,pnl:8000,win_rate:80,closed_count:5,status:'closed'}]
const ids=(r:typeof rows)=>r.map(x=>x.symbol)
test('sort signed losses correctly and leave null last in both directions',()=>{
 assert.deepEqual(ids(selectRows(rows,{sortKey:'pnl',descending:false})),['B','A','D','C'])
 assert.deepEqual(ids(selectRows(rows,{sortKey:'pnl'})),['D','A','B','C'])
})
test('combine fractional holding days and pnl ranges inclusively',()=>{
 assert.deepEqual(ids(selectRows(rows,{minDays:'5',maxDays:'12.5',minPnl:'-2000',maxPnl:'0'})),['B'])
 assert.deepEqual(ids(selectRows(rows,{minDays:'0',maxDays:'0',minPnl:'0',maxPnl:'0'})),['A'])
})
test('empty ranges include null, active ranges exclude null',()=>{
 assert.equal(selectRows(rows,{minDays:'',maxPnl:''}).length,4)
 assert.deepEqual(ids(selectRows(rows,{minPnl:'-999999'})),['A','B','D'])
 assert.equal(selectRows(rows,{minDays:'10',maxDays:'2'}).length,0)
})
test('win rate threshold also respects sample size',()=>{
 assert.deepEqual(ids(selectRows(rows,{minRate:'60',minSamples:'3',sortKey:'win_rate'})),['D'])
})
test('status and case-insensitive name search combine without mutating input',()=>{
 assert.deepEqual(ids(selectRows(rows,{search:' d ',status:'closed'})),['D'])
 assert.deepEqual(ids(selectRows(rows,{search:'乙',status:'open'})),['B'])
 assert.deepEqual(ids(rows),['A','B','C','D'])
})

test('numeric input values support negative, fractional and zero boundaries',()=>{
 assert.deepEqual(ids(selectRows(rows,{minDays:5,maxDays:12.5,minPnl:-2000,maxPnl:0})),['B'])
 assert.deepEqual(ids(selectRows(rows,{minDays:0,maxDays:0,minPnl:0,maxPnl:0})),['A'])
 assert.deepEqual(ids(selectRows(rows,{minRate:60,minSamples:3})),['D'])
})
test('cleared and invalid numeric fields do not become a zero bound',()=>{
 for(const value of ['', '  ', null, undefined, NaN, Infinity, '-Infinity', '-']){
  assert.equal(selectRows(rows,{minDays:value,maxDays:value,minPnl:value,maxPnl:value,minRate:value,minSamples:value}).length,4)
 }
})
test('Vue number v-model can filter, clear and re-enter without throwing',async()=>{
 const {vModelText}=await import('vue')
 const listeners:Record<string,Function[]>={}
 let result=rows
 let model:unknown
 const input={type:'number',value:'',addEventListener:(event:string,fn:Function)=>{(listeners[event]??=[]).push(fn)}}
 const created=vModelText.created as Function
 created(input,{modifiers:{}},{props:{type:'number','onUpdate:modelValue':(value:string|number)=>{
  model=value;result=selectRows(rows,{minPnl:value})
 }}})
 const type=(value:string)=>{input.value=value;for(const fn of listeners.input||[])fn({target:input})}
 type('-2000');assert.equal(model,-2000);assert.deepEqual(ids(result),['A','B','D'])
 type('0');assert.equal(model,0);assert.deepEqual(ids(result),['A','D'])
 type('');assert.equal(model,'');assert.deepEqual(ids(result),['A','B','C','D'])
 type('8000');assert.equal(model,8000);assert.deepEqual(ids(result),['D'])
})
