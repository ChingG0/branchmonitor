import test from 'node:test'
import assert from 'node:assert/strict'
import {localFetch,readJson} from '../frontend/src/http.ts'
test('empty, truncated and non-JSON responses produce an actionable local-service error',async()=>{
 for (const body of ['', '{"half":', '<html>Error</html>']) {
  await assert.rejects(readJson(new Response(body)), /啟動 8765.*不要重複送出/)
 }
 await assert.rejects(readJson(new Response('',{status:502})), /HTTP 502/)
 assert.deepEqual(await readJson(new Response('{"value":12}')), {value:12})
})
test('failed fetch identifies stopped service and never retries a write',async(t)=>{
 const old=globalThis.fetch;let calls=0
 t.after(()=>{globalThis.fetch=old})
 globalThis.fetch=async()=>{calls++;throw new TypeError('fetch failed')}
 await assert.rejects(localFetch('/api/research/generate',{method:'POST'}), /8765.*已停止/)
 assert.equal(calls,1)
})

test('cloud runtime errors use port3001 rather than8765',async(t)=>{
 const oldWindow=globalThis.window;const oldFetch=globalThis.fetch
 t.after(()=>{if(oldWindow===undefined)delete globalThis.window;else globalThis.window=oldWindow;globalThis.fetch=oldFetch})
 globalThis.window={location:{port:'3001'}}
 globalThis.fetch=async()=>{throw new TypeError('fetch failed')}
 await assert.rejects(localFetch('/api/health'), /3001.*已停止/)
 await assert.rejects(readJson(new Response('')), /啟動 3001/)
})
