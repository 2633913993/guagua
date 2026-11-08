import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { FEATURES, PARSE_RESULT_KEY } from '../config/features.js'
import { createAdController } from '../services/ads.js'
import { pendingParse, finishPending, requestParse, responseBody, parseResponse, requestError } from '../services/parse.js'
import { fetchQuota, untilMidnight, cacheQuota, readTotalParses, cacheTotalParses } from '../services/quota.js'
const storage = new Map(), calls = [], notices = []
let remaining = 0, completed = false, failClaim = false, creates = 0, shows = 0, loads = 0, sidCount = 0, firstShowFails = false, close, sdkError, adLoaded, nav = 0, startFails = false, loadFails = false
const quota = () => ({remaining, initial_quota:5,quota_date:'2026-10-21',server_time:'2026-10-21T12:00:00+08:00'})
FEATURES.request.mode = 'http'
FEATURES.ads.adUnitId = ''
globalThis.uni = {
  getStorageSync:k=>storage.get(k), setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
  showToast:v=>notices.push(v.title),showModal:v=>v.success({confirm:true}),
  navigateTo:v=>{nav++;v.success()},
  request:o=>{
    calls.push(o)
    if (o.url.endsWith('/api/v1/quota')) return o.success({statusCode:200,data:{success:true,quota:quota()}})
    if (o.url.endsWith('/api/ads/start')) {if(startFails) return o.success({statusCode:503,data:{detail:'激励视频广告暂未配置'}});sidCount++;completed=false;return o.success({statusCode:200,data:{success:true,data:{session_id:String(sidCount).padStart(43,'s'),ad_unit_id:FEATURES.ads.adUnitId,reward_amount:5}}})}
    if (o.url.endsWith('/api/ads/complete')) {
      if(failClaim) return o.fail(new Error('network timeout'))
      if(!completed) { remaining+=5; completed=true }
      return o.success({statusCode:200,data:{success:true,data:{reward_amount:5,quota:quota()}}})
    }
    if (o.url.endsWith('/api/v1/parse')) {
      if(remaining===0) return o.success({statusCode:429,data:{success:false,request_id:o.data.request_id,error_code:'QUOTA_EXHAUSTED',quota:quota()}})
      remaining--;return o.success({statusCode:200,data:{success:true,request_id:o.data.request_id,quota:quota(),data:{video_url:'https://cdn.example/v.mp4'}}})
    }
    assert.fail('unexpected request')
  }
}
globalThis.wx = {createRewardedVideoAd:options=>{
  assert.equal(options.adUnitId,'adunit-test'); creates++
  return {onLoad:fn=>{adLoaded=fn},offLoad:fn=>{if(adLoaded===fn)adLoaded=null},onClose:fn=>{close=fn},offClose:fn=>{if(close===fn)close=null},onError:fn=>{sdkError=fn},offError:fn=>{if(sdkError===fn)sdkError=null},
    show:async()=>{shows++;if(firstShowFails){firstShowFails=false;throw new Error('not loaded')}},load:async()=>{loads++;if(loadFails){const error={errCode:1004,errMsg:'no ad'};sdkError(error);throw error}adLoaded()}}
}}
const tick = async()=>{for(let i=0;i<40;i++)await Promise.resolve()}
let busy=[]
let c=createAdController({message:m=>notices.push(m),busy:v=>busy.push(v)})
await assert.rejects(c.show(),/暂未配置/)
assert.equal(creates,0);assert.equal(calls.length,0)
FEATURES.ads.adUnitId='adunit-test'
c.init();assert.equal(creates,1,'Create the SDK instance on page load, before any reward request')
assert.equal(calls.length,0);assert.equal(shows,0,'Preloading never shows an ad automatically')
c.init();assert.equal(creates,1,'Repeated init reuses the page instance')
startFails=true;await assert.rejects(c.show(),/奖励服务未就绪.*HTTP 503.*暂未配置/)
assert.equal(shows,0,'Do not show an unrewardable video when session creation fails')
startFails=false
let p=c.show();let same=c.show();assert.equal(p,same);await tick()
let listener=close;listener({isEnded:false});assert.equal(await p,null)
assert.equal(remaining,0);assert.equal(calls.filter(o=>o.url.endsWith('/complete')).length,0)
p=c.show();await tick();listener=close;listener({isEnded:true});listener({isEnded:true});await p
assert.equal(remaining,5);assert.equal(typeof close,'function','Keep the instance and its listeners until page unload')
p=c.show();await tick();close({isEnded:true});await p
assert.equal(remaining,10,'Every separate complete viewing adds another five')
firstShowFails=true;p=c.show();await tick();assert.equal(loads,1);close({isEnded:true});await p
assert.equal(remaining,15,'One show/load/show retry')
p=c.show();await tick();close(undefined);await p
assert.equal(remaining,15,'Missing completion status is not a completed video')
assert.equal(creates,1,'Repeated viewings reuse the official page-scoped SDK instance')
firstShowFails=true;loadFails=true;p=c.show();await assert.rejects(p,/显示失败.*1004.*no ad/)
assert.equal(remaining,15,'SDK failure never credits a reward')
loadFails=false
failClaim=true;p=c.show();await tick();close({isEnded:true});await assert.rejects(p,/network/)
assert.ok(storage.has('guagua.pending.ad.v1'))
const count=creates;failClaim=false;c.dispose();c=createAdController()
await c.recover();assert.equal(creates,count,'Recovery must not force another viewing');assert.equal(remaining,20)
assert.ok(!storage.has('guagua.pending.ad.v1'))
p=c.show();await tick();sdkError();await assert.rejects(p,/播放失败/)
assert.equal(remaining,20)
p=c.show();await tick();c.dispose();await assert.rejects(p,/页面已关闭/);assert.equal(close,null)
assert.ok(!storage.has('guagua.pending.ad.v1'))
assert.deepEqual(busy.slice(0,2),[true,false])

// Real homepage: authoritative zero -> completed video -> a new parse ID -> 4 left.
const source=fs.readFileSync(new URL('../pages/home/index.vue',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
const context={FEATURES,uni,wx,createAdController,PARSE_RESULT_KEY,fetchQuota,untilMidnight,pendingParse,finishPending,requestParse,responseBody,parseResponse,requestError,setTimeout:()=>1,clearTimeout:()=>{},captureShareEntry:()=>{}}
Object.assign(context, { cacheQuota, readTotalParses, cacheTotalParses });
vm.runInNewContext(source.replace(/^import .*$/mg,'').replace('export default','globalThis.page ='),context)
let page={...context.page.data(),...context.page.methods,_disposed:false}
const beforePageLoad=creates
context.page.onLoad.call(page,{})
assert.equal(creates,beforePageLoad+1,'Homepage onLoad initializes the SDK')
remaining=0;page.linkText='https://v.douyin.com/test';p=page.handleAnalyze();await tick();listener=close;assert.equal(typeof listener,'function');listener({isEnded:true});await p
assert.equal(nav,1);assert.equal(remaining,4)
const requests=calls.filter(o=>o.url.endsWith('/parse')).slice(-2)
assert.notEqual(requests[0].data.request_id,requests[1].data.request_id,'Exhausted persisted ID must not be replayed after reward')
remaining=2;page.remainingParses=0;await page.handleAnalyze();assert.equal(nav,2,'Cached zero must not discard results')
remaining=0;p=page.handleAnalyze();await tick();close({isEnded:false});await p;assert.equal(nav,2);assert.equal(remaining,0)
assert.ok(calls.filter(o=>o.url.endsWith('/complete')).every(o=>Object.keys(o.data).join()==='session_id'))
console.log('PASS: onLoad preload, instance reuse, backend failure diagnostics, SDK error codes, placeholder ID, complete/partial/missing status, repeat +5, duplicate callback, single-flight, SDK retry/error, disposal, timeout recovery, zero→ad→parse and cached-zero replay')
