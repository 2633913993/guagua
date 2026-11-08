import assert from 'node:assert/strict'
import { FEATURES } from '../config/features.js'
import { fetchQuota, readQuota } from '../services/quota.js'

const originalTimeout = globalThis.setTimeout
const originalWarn = console.warn
const storage = new Map(), calls = [], delays = [], warnings = []
let results = []
const quota = { quota_date: '2026-11-07', initial_quota: 5, remaining: 4, server_time: '2026-11-07T12:00:00+08:00' }
const success = { statusCode: 200, data: { success: true, quota } }
globalThis.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value) }
globalThis.wx = { cloud: {
  init() {},
  callContainer(options) {
    calls.push(options)
    const result = results.shift()
    assert.ok(result, '不允许超出预期次数的请求')
    return result.reject ? Promise.reject(result.reject) : Promise.resolve(result)
  }
} }
FEATURES.request.mode = 'container'
globalThis.setTimeout = (callback, delay) => { delays.push(delay); queueMicrotask(callback); return 1 }
console.warn = (...args) => warnings.push(args)
function reset(next) { results = [...next]; calls.length = 0; delays.length = 0; warnings.length = 0 }
try {
  reset([{ reject: { errMsg: 'cloud.callContainer:fail timeout', errCode: -1 } }, success])
  const first = fetchQuota()
  assert.equal(fetchQuota(), first, '并发刷新共享整个重试过程')
  assert.deepEqual(await first, quota)
  assert.deepEqual(delays, [1000])
  assert.equal(calls.length, 2)
  assert.ok(calls.every(call => call.timeout === 15000 && call.method === 'GET' && call.path === '/api/v1/quota'))
  assert.deepEqual(readQuota(), quota)

  reset([{ statusCode: 503, data: '<html>starting</html>' }, { statusCode: 502 }, success])
  assert.deepEqual(await fetchQuota(), quota)
  assert.deepEqual(delays, [1000, 2000], '冷启动和非 JSON 网关错误均可恢复')
  assert.equal(calls.length, 3)
  assert.equal(warnings[0][1].statusCode, 503)

  reset(Array.from({ length: 3 }, () => ({ statusCode: 503, data: { detail: '服务暂不可用' } })))
  await assert.rejects(fetchQuota(), /服务暂不可用/)
  assert.equal(calls.length, 3, '总共最多三次，不能持续请求')
  assert.deepEqual(delays, [1000, 2000])
  assert.equal(warnings.at(-1)[1].retryIn, null)
  assert.equal(readQuota().remaining, 4, '失败不伪造或修改余额')
  reset([success])
  await fetchQuota()
  assert.equal(calls.length, 1, '最终失败后仍允许新的手动刷新')

  for (const statusCode of [400, 401, 403, 404]) {
    reset([{ statusCode, data: { detail: '不可重试' } }])
    await assert.rejects(fetchQuota(), /不可重试/)
    assert.equal(calls.length, 1)
    assert.deepEqual(delays, [])
  }
  reset([{ statusCode: 200, data: { success: true, quota: { ...quota, remaining: -1 } } }])
  await assert.rejects(fetchQuota(), /格式错误/)
  assert.equal(calls.length, 1)
  reset([{ statusCode: 200, data: 'not JSON' }])
  await assert.rejects(fetchQuota(), /次数读取失败/)
  assert.equal(calls.length, 1)
  reset([{ statusCode: 429 }, success])
  await fetchQuota()
  assert.deepEqual(delays, [1000])
  console.log('PASS: quota retry recovers transport/5xx failures, caps attempts, deduplicates, logs errors, preserves balances, skips permanent errors')
} finally {
  globalThis.setTimeout = originalTimeout
  console.warn = originalWarn
}
