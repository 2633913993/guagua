import { cacheQuota, readTotalParses, cacheTotalParses } from '../services/quota.js'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { FEATURES } from '../config/features.js'
import { prepareShare, shareMessage, captureShareEntry, captureAppEntry, flushShareEntries, fetchInvitees } from '../services/share.js'

const storage = new Map(), calls = []
const tokenA = 'A'.repeat(43), tokenB = 'B'.repeat(43), tokenC = 'C'.repeat(43)
let failStatus = 0, resolveLog
FEATURES.request.mode = 'http'
globalThis.uni = {
  getStorageSync: key => storage.get(key), setStorageSync: (key,value) => storage.set(key,value),
  removeStorageSync: key => storage.delete(key),
  request: options => {
    calls.push(options)
    if (options.url.endsWith('/profile')) return options.success({ statusCode: 200, data: { success:true, data: { inviter:tokenA, reward_per_friend:5 } } })
    if (options.url.endsWith('/log')) {
      if (resolveLog) { resolveLog(options); return }
      return options.success(failStatus ? { statusCode:failStatus, data:{detail:'暂未就绪'} } : { statusCode:200, data:{ success:true, data:{recorded:true, duplicate:false, reward_amount:5} } })
    }
    options.success({ statusCode:200, data:{success:true, data:{items:[], total:0, total_reward:0, next_cursor:null, reward_per_friend:5}} })
  }
}
assert.equal(shareMessage().path, '/pages/home/index', '未就绪使用默认路径')
await prepareShare()
assert.equal(shareMessage().path, `/pages/home/index?inviter=${tokenA}&source=wechat_share`)
assert.equal(shareMessage().imageUrl, '/static/frog-hello.png')
assert.equal(captureShareEntry({inviter:'forged', source:'wechat_share'}), false)
assert.equal(captureShareEntry({inviter:tokenB, source:'wrong'}), false)
assert.equal(captureShareEntry({inviter:tokenB, source:'wechat_share'}), true)
assert.equal(captureShareEntry({inviter:tokenB, source:'wechat_share'}), false, '冷热启动重复回调合并')
failStatus = 401
await assert.rejects(flushShareEntries(), /暂未就绪/)
assert.equal(storage.get('guagua.pending.share.v1').length, 1, '身份未就绪保留邀请')
failStatus = 503
await assert.rejects(flushShareEntries())
assert.equal(storage.get('guagua.pending.share.v1').length, 1, '服务失败保留邀请')
failStatus = 0
assert.equal(await flushShareEntries(), true)
assert.equal(storage.has('guagua.pending.share.v1'), false)
const logPayload = calls.find(call => call.url.endsWith('/log')).data
assert.equal(logPayload.inviter, tokenB)
assert.equal(logPayload.source, 'wechat_share')
assert.match(logPayload.entry_id, /^[a-f0-9]{32}$/)
assert.ok(calls.filter(call => call.url.endsWith('/log')).every(call => call.data.entry_id === logPayload.entry_id), '失败重试沿用进入编号')
assert.deepEqual(Object.keys(logPayload).sort(), ['entry_id','inviter','source'])
assert.ok(calls.every(call => !call.header['X-WX-OPENID'] && !call.header['X-WX-APPID']))
// 微信热启动的应用 query，以及 getEnterOptionsSync 兼容入口。
assert.equal(captureAppEntry({query:{inviter:tokenC,source:'wechat_share'}}), true)
let held
resolveLog = options => { held = options }
const first = flushShareEntries(), second = flushShareEntries()
assert.equal(first, second, '并行生命周期复用一条在途请求')
held.success({ statusCode:200, data:{success:true,data:{recorded:true,duplicate:true,reward_amount:0}} })
await first
resolveLog = null
globalThis.wx = {getEnterOptionsSync:()=>({query:{inviter:tokenB,source:'wechat_share'}})}
assert.equal(captureAppEntry({}), true)
failStatus = 404
await flushShareEntries()
assert.equal(storage.has('guagua.pending.share.v1'), false, '永久无效链接不重复上报')
// 每次重新打开同一链接生成不同编号，冷启动重复生命周期只生成一次。
failStatus = 0
const beforeEntries = calls.filter(call => call.url.endsWith('/log')).length
captureAppEntry({query:{inviter:tokenB,source:'wechat_share'}})
captureAppEntry({query:{inviter:tokenB,source:'wechat_share'}})
await flushShareEntries()
const newLogs = calls.filter(call => call.url.endsWith('/log')).slice(beforeEntries)
assert.equal(newLogs.length, 2)
assert.notEqual(newLogs[0].data.entry_id, newLogs[1].data.entry_id)
assert.equal(captureAppEntry({query:{inviter:tokenC,source:'wechat_share'}}, 'launch'), true)
assert.equal(captureAppEntry({query:{inviter:tokenC,source:'wechat_share'}}), false)
assert.equal(captureShareEntry({inviter:tokenC,source:'wechat_share'}), false)
await flushShareEntries()
await fetchInvitees(20)
assert.deepEqual(calls.at(-1).data, {before:20,limit:20})
assert.ok(!JSON.stringify([...storage]).includes('remaining'), '分享流程不修改缓存余额')

// 使用真实邀请列表页脚本核对 onShow、下拉刷新、分页及卸载保护。
const source = fs.readFileSync(new URL('../pages/invite-list/index.vue', import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
let stopped = 0, round = 0
const context = {
  prepareShare, shareMessage,
  uni:{stopPullDownRefresh:()=>stopped++,showToast:()=>{}},
  fetchInvitees: async before => ({items:[{id:before?2:1}],total:2,total_reward:10,reward_per_friend:5,next_cursor:before?null:1})
}
Object.assign(context, { cacheQuota, readTotalParses, cacheTotalParses });
vm.runInNewContext(source.replace(/import\s+[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','globalThis.page ='),context)
const page = {...context.page.data(),...context.page.methods,_disposed:false}
await page.reload()
assert.equal(page.items.length,1)
await page.loadMore()
assert.equal(page.items.length,2)
await context.page.onPullDownRefresh.call(page)
assert.equal(stopped,1)
assert.equal(page.items.length,1)
context.page.onUnload.call(page)
assert.equal(page._disposed,true)
console.log('PASS: server-issued share card, cold/warm entry, deferred auth/network retry, concurrent dedupe, no client identity/credits, invitation pagination and pull refresh')
