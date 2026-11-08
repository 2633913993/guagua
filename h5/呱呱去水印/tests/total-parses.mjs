import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import * as quotas from '../services/quota.js'
import * as parses from '../services/parse.js'
import { FEATURES, PARSE_RESULT_KEY } from '../config/features.js'
FEATURES.request.mode = 'http'
const storage = new Map(), calls = []
let total = 41, remaining = 5, profileCalls = 0, navigation = 0
const now = new Date()
const day = new Date(Date.now()+8*3600000).toISOString().slice(0,10)
const quota = () => ({quota_date:day,remaining,initial_quota:5,server_time:now.toISOString(),total_parses:total})
globalThis.uni = {
  getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
  request: o=> {
    calls.push(o)
    queueMicrotask(()=> {
      if(o.url.endsWith('/quota')) return o.success({statusCode:200,data:{success:true,quota:quota()}})
      if(o.url.endsWith('/parse')) {total++; remaining--;return o.success({statusCode:200,data:{success:true,request_id:o.data.request_id,quota:quota(),total_parses:total,data:{video_url:'https://cdn.example/1.mp4'}}})}
      assert.fail('Unexpected extra request')
    })
  },navigateTo:o=>{navigation++;o.success()},showModal:o=>assert.fail(o.content),stopPullDownRefresh:()=>{}
}
assert.equal(quotas.readTotalParses(),null,'首次没有数据时显示占位，不捏造总数')
const pair=await Promise.all([quotas.fetchQuota(),quotas.fetchQuota()])
assert.equal(calls.length,1,'并发额度读取合并为一次请求')
assert.equal(quotas.readTotalParses(),41)
assert.equal(quotas.cacheTotalParses(39),41,'旧响应不能让累计次数倒退')
assert.equal(quotas.cacheTotalParses(-1),41)
const context={FEATURES,...quotas,...parses,uni,PARSE_RESULT_KEY,
  createAdController:()=>({recover:async()=>{},dispose:()=>{}}),
  captureShareEntry:()=>{},shareMessage:()=>({}),flushShareEntries:async()=>false,
  prepareShare:async()=>{profileCalls++;return {reward_per_friend:5}},
  setTimeout:()=>1,clearTimeout:()=>{}
}
const source=fs.readFileSync(new URL('../pages/home/index.vue',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
vm.runInNewContext(source.replace(/^import .*$/mg,'').replace('export default','globalThis.page ='),context)
const page={...context.page.data(),...context.page.methods,_disposed:false}
assert.equal(page.totalParses,41,'缓存总数立即展示')
await page.refreshQuota()
await page.refreshSharing()
const before=calls.length
context.page.onShow.call(page)
await new Promise(resolve=>setImmediate(resolve))
assert.equal(calls.length,before,'短时间回前台不重复查额度')
assert.equal(profileCalls,1,'短时间回前台不重复查分享配置')
page.linkText='https://v.douyin.com/test'
await page.handleAnalyze()
assert.equal(calls.length,before+1,'解析只请求解析接口，不追加额度查询')
assert.equal(page.totalParses,42)
assert.equal(page.remainingParses,4)
assert.equal(navigation,1)
await page.refreshSharing(true)
assert.equal(profileCalls,2,'手动刷新能强制更新配置')
console.log('PASS: shared totals cached, stale responses cannot decrease count, no extra parse requests, quota single-flight and foreground TTL')
