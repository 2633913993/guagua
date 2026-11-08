import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { FEATURES } from '../config/features.js'
import * as quotaService from '../services/quota.js'

let calls = 0, fail = true, finish, hold = false, timerId = 0
let now = Date.parse('2026-11-07T12:00:00+08:00')
const timers = new Map(), storage = new Map()
const quota = { quota_date: '2026-11-07', server_time: '2026-11-07T12:00:00+08:00', remaining: 5, initial_quota: 5, total_parses: 10 }
globalThis.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), stopPullDownRefresh() {} }
class Clock extends Date { static now() { return now } }
const context = { ...quotaService, FEATURES, uni, Date: Clock,
  fetchQuota: async () => { calls++; if (hold) await new Promise(resolve => { finish = resolve }); if (fail) throw Error('HTTP 503'); return quota },
  setTimeout: (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id },
  clearTimeout: id => timers.delete(id)
}
const raw = fs.readFileSync(new URL('../pages/home/index.vue', import.meta.url), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
vm.runInNewContext(raw.replace(/^import .*$/mg, '').replace('export default', 'globalThis.page='), context)
const page = { ...context.page.data(), ...context.page.methods, refreshSharing: async () => {}, recoverAdReward: async () => {} }
const tick = () => new Promise(resolve => setImmediate(resolve))
context.page.onShow.call(page); await tick()
assert.equal(calls, 1)
assert.equal(timers.size, 0, '有限重试结束后不建立循环定时器')
assert.equal(page.quotaLoading, false)
assert.equal(page.quotaUnavailable, true)
for (let i = 0; i < 5; i++) context.page.onShow.call(page)
await tick(); assert.equal(calls, 1, '快速切换前后台不会反复请求')
now += 30000
context.page.onShow.call(page); await tick()
assert.equal(calls, 2, '间隔 30 秒返回前台可以自动恢复，不再永久暂停')
await page.retryQuota()
assert.equal(calls, 3, '手动刷新不受前台冷却时间限制')
await context.page.onPullDownRefresh.call(page)
assert.equal(calls, 4, '下拉刷新可立即重试')
fail = false
await page.retryQuota()
assert.equal(calls, 5)
assert.equal(page.quotaUnavailable, false)
assert.equal(page.remainingParses, 5)
assert.equal(page._quotaRetryAfter, 0)
assert.equal(timers.size, 1, '成功后恢复跨日刷新')
context.page.onShow.call(page); await tick()
assert.equal(calls, 5, '成功后短时间返回前台不重复请求')
assert.ok([...timers.values()].every(timer => Number.isFinite(timer.delay) && timer.delay > 100))
page.scheduleReset(undefined); assert.equal(timers.size, 0, '无服务端时间不创建定时器')
hold = true; fail = true
const first = page.refreshQuota()
await page.refreshQuota(); assert.equal(calls, 6, '请求中不重复查询')
finish(); await first
assert.equal(timers.size, 0)
assert.equal(page.quotaLoading, false)
assert.equal(page.quotaUnavailable, true)
fail = false
const last = page.refreshQuota()
context.page.onUnload.call(page)
finish(); await last
assert.equal(page.quotaUnavailable, true, '卸载后不应用迟到的响应')
assert.equal(timers.size, 0)
console.log('PASS: quota failure cooldown, foreground/manual recovery, loading deduplication, midnight timer and unloaded-page protection')
