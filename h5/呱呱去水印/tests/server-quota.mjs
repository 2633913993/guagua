import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { FEATURES, PARSE_RESULT_KEY } from '../config/features.js'
import { callContainer } from '../services/container.js'
import { readQuota, fetchQuota, cacheQuota, untilMidnight, readTotalParses, cacheTotalParses } from '../services/quota.js'
import { pendingParse, finishPending, requestParse, responseBody, parseResponse, requestError } from '../services/parse.js'
import { shareMessage } from '../services/share.js'
import { createAdController } from '../services/ads.js'

const storage = new Map()
// 显式验证正式模式；默认本地模式由 local-request.mjs 验证。
FEATURES.request.mode = 'container'
let calls = [], response, failure, navigations = 0, navFailure = false, notices = [], initOptions
const quota = { quota_date: '2026-10-18', initial_quota: 5, remaining: 4, server_time: '2026-10-18T23:59:59+08:00' }
globalThis.uni = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key),
  request: () => assert.fail('No public HTTP fallback'),
  showToast: value => notices.push(value), showModal: value => { notices.push(value); if (value.success) value.success({ confirm: false }) },
  navigateTo: options => { navigations++; if (navFailure) options.fail(); else options.success() }
}
globalThis.wx = { cloud: {
  init: options => { initOptions = options },
  callContainer: options => {
    calls.push(options)
    if (options.path === '/api/v1/quota') return Promise.resolve({ statusCode: 200, data: { success: true, quota } })
    return failure ? Promise.reject(failure) : Promise.resolve(response)
  }
} }
assert.equal(readQuota(), null, 'No locally initialized free balance')
assert.deepEqual(await fetchQuota(), quota)
assert.equal(initOptions.traceUser, false)
assert.equal(initOptions.env, FEATURES.request.cloudStorageEnv, '云存储默认环境与下载环境一致')
assert.equal(calls[0].config.env, FEATURES.request.cloudEnv)
assert.equal(calls[0].header['X-WX-SERVICE'], FEATURES.request.cloudService)
assert.equal(calls[0].header['X-WX-OPENID'], undefined)
assert.equal(untilMidnight(quota.server_time), 1000)
assert.throws(() => cacheQuota({ ...quota, remaining: -1 }))
const first = pendingParse('https://v.douyin.com/test')
assert.equal(pendingParse(first.url).request_id, first.request_id)
pendingParse('https://v.douyin.com/other')
assert.equal(pendingParse(first.url).request_id, first.request_id, 'Changing input does not lose unresolved operations')
failure = new Error('callContainer:fail timeout')
await assert.rejects(requestParse(first))
assert.equal(pendingParse(first.url).request_id, first.request_id, 'Timeout retains durable request')
assert.doesNotMatch(requestError(failure), /已退回/)
assert.equal(readQuota().remaining, 4, 'No automatic refund')
await assert.rejects(callContainer({ path: 'https://public.example' }), /相对路径/)
const before = calls.length
FEATURES.request.cloudEnv = ''
await assert.rejects(fetchQuota(), /环境 ID/)
assert.equal(calls.length, before)
FEATURES.request.cloudEnv = 'test-env'
const configuredAdUnitId = FEATURES.ads.adUnitId
FEATURES.ads.adUnitId = ''
await assert.rejects(createAdController().show(), /暂未配置/)
FEATURES.ads.adUnitId = configuredAdUnitId
assert.equal(readQuota().remaining, 4, 'Ads cannot mint local credits')
assert.equal(shareMessage().path, '/pages/home/index')

// Page uses actual service functions with a mocked SDK, including storage/nav failures.
const source = fs.readFileSync(new URL('../pages/home/index.vue', import.meta.url), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
const context = { FEATURES,
  uni, wx, createAdController, PARSE_RESULT_KEY, fetchQuota, untilMidnight, pendingParse, finishPending,
  requestParse, responseBody, parseResponse, requestError, shareMessage,
  setTimeout: () => 1, clearTimeout: () => {}
}
Object.assign(context, { cacheQuota, readTotalParses, cacheTotalParses });
vm.runInNewContext(source.replace(/^import .*$/mg, '').replace('export default', 'globalThis.page ='), context)
const page = { ...context.page.data(), ...context.page.methods, _disposed: false }
await page.handleAnalyze()
assert.equal(navigations, 0)
page.linkText = 'https://v.douyin.com/test'
failure = new Error('network timeout')
await page.handleAnalyze()
const request1 = calls.filter(call => call.path === '/api/v1/parse').at(-1).data
assert.match(notices.at(-1).content, /同一次解析/)
assert.equal(page.isLoading, false)
failure = null
response = { statusCode: 200, data: { success: true, request_id: request1.request_id, quota,
  data: { image_urls: ['https://cdn.example/1.jpg', 'https://cdn.example/2.jpg'] } } }
navFailure = true
await page.handleAnalyze()
assert.equal(calls.filter(call => call.path === '/api/v1/parse').at(-1).data.request_id, request1.request_id)
assert.equal(pendingParse(page.linkText).request_id, request1.request_id, 'Navigation failure keeps same ID')
navFailure = false
await page.handleAnalyze()
assert.equal(uni.getStorageSync(PARSE_RESULT_KEY).image_urls.length, 2)
assert.notEqual(pendingParse(page.linkText).request_id, request1.request_id, 'Successful navigation completes pending request')
// Definitive failure can be retried as a fresh operation (e.g. quota tomorrow).
const pending = pendingParse(page.linkText)
response = { statusCode: 429, data: { success: false, request_id: pending.request_id, error_code: 'QUOTA_EXHAUSTED', detail: '今日次数已用完' } }
await page.handleAnalyze()
assert.notEqual(pendingParse(page.linkText).request_id, pending.request_id)
assert.equal(notices.at(-1).title, '解析次数已用完')
// Local cache tampering cannot determine the balance shown after refresh.
cacheQuota({ ...quota, remaining: 99999 })
await page.refreshQuota()
assert.equal(page.remainingParses, 4)
const sdkCall = wx.cloud.callContainer
wx.cloud.callContainer = () => Promise.resolve({ statusCode: 503, data: { detail: '服务不可用' } })
await page.refreshQuota()
assert.equal(page.quotaUnavailable, true, 'Unavailable backend must not display a fabricated balance')
assert.equal(page.quotaLoading, false, 'Failed quota lookup must not remain in loading state')
wx.cloud.callContainer = sdkCall
await page.refreshQuota()
assert.equal(page.quotaUnavailable, false)
assert.ok(calls.every(call => !/count|refund|reward/.test(call.path)))
console.log('PASS: server balance, no local grants/refunds, callContainer only, durable retry IDs, navigation failure recovery, definitive failure retry, no identity submission')
