import assert from 'node:assert/strict'
import { FEATURES } from '../config/features.js'
import { requestApi } from '../services/request.js'
import { fetchQuota } from '../services/quota.js'
import { requestParse } from '../services/parse.js'

assert.equal(FEATURES.request.mode, 'container')
FEATURES.request.mode = 'http' // explicit local test mode
const storage = new Map(), calls = []
const quota = { quota_date: '2026-10-18', initial_quota: 5, remaining: 4, server_time: '2026-10-18T21:00:00+08:00' }
let result = { statusCode: 200, data: { success: true, quota } }, failure
globalThis.uni = {
  getStorageSync: key => storage.get(key),
  setStorageSync: (key, value) => storage.set(key, value),
  request: options => {
    calls.push(options)
    if (failure) options.fail(failure)
    else options.success(result)
  }
}
globalThis.wx = { cloud: {
  init: () => assert.fail('本地模式不能初始化云服务'),
  callContainer: () => assert.fail('本地模式不能调用云托管')
} }
assert.deepEqual(await fetchQuota(), quota)
const pending = { request_id: 'local-request-test-001', url: 'https://v.douyin.com/test/' }
await requestParse(pending)
assert.equal(calls[0].url, 'http://127.0.0.1:8080/api/v1/quota')
assert.equal(calls[0].method, 'GET')
assert.equal(calls[1].url, 'http://127.0.0.1:8080/api/v1/parse')
assert.equal(calls[1].method, 'POST')
assert.deepEqual(calls[1].data, pending)
assert.equal(calls[0].timeout, 15000, '次数查询使用独立超时')
assert.equal(calls[1].timeout, FEATURES.request.timeout, '解析接口保持原超时')
assert.ok(calls.every(call => !call.header['X-WX-SERVICE']))
FEATURES.request.httpBaseUrl = 'http://127.0.0.1:9090/'
await fetchQuota()
await requestParse(pending)
assert.ok(calls.slice(-2).every(call => call.url.startsWith('http://127.0.0.1:9090/api/v1/')))
failure = new Error('request:fail timeout')
await assert.rejects(fetchQuota(), /timeout/)
await assert.rejects(requestParse(pending), /timeout/)
failure = null
result = { statusCode: 404, data: { detail: 'Not Found' } }
await assert.rejects(fetchQuota(), /Not Found/)
await assert.rejects(requestApi({ path: '//other.example/api' }), /相对路径/)
FEATURES.request.httpBaseUrl = ''
await assert.rejects(fetchQuota(), /httpBaseUrl/)
FEATURES.request.mode = 'invalid'
await assert.rejects(fetchQuota(), /request.mode/)
// 即使没有微信 SDK，本地请求依然可用。
delete globalThis.wx
FEATURES.request.mode = 'http'
FEATURES.request.httpBaseUrl = 'http://127.0.0.1:8080'
result = { statusCode: 200, data: { success: true, quota } }
assert.deepEqual(await fetchQuota(), quota)
console.log('PASS: quota/parse share local HTTP config, never call cloud, propagate errors, work without wx')
