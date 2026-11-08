import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
const script=fs.readFileSync(new URL('../pages/result/result.vue',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/mg,'').replace('export default','globalThis.page=')
let creates=0,shows=0,destroys=0,offs=0,result,showFailure=false,logs=[]
const listeners={}
const wx={createInterstitialAd:options=>{
 creates++;assert.equal(options.adUnitId,'adunit-22fd03b92ffb2e30')
 return {onLoad:fn=>listeners.load=fn,onError:fn=>listeners.error=fn,onClose:fn=>listeners.close=fn,
 offLoad:fn=>{assert.equal(fn,listeners.load);offs++},offError:fn=>{assert.equal(fn,listeners.error);offs++},offClose:fn=>{assert.equal(fn,listeners.close);offs++},
 destroy:()=>destroys++,show:()=>{shows++;return showFailure?Promise.reject(new Error('no fill')):Promise.resolve()}}
}}
const context={PARSE_RESULT_KEY:'result',wx,uni:{getStorageSync:()=>result,showModal:()=>assert.fail('Ad failure must not block results')},console:{log:()=>{},error:(...args)=>logs.push(args)}}
vm.runInNewContext(script,context)
const create=()=>{
 const page={...context.page.data(),...context.page.methods}
 context.page.onLoad.call(page)
 return page
}
result={video_url:'https://cdn.example/v.mp4'}
let page=create();assert.equal(creates,1);assert.equal(shows,0,'Create onLoad, display only after page ready')
context.page.onShow.call(page);context.page.onReady.call(page)
assert.equal(shows,1);context.page.onReady.call(page);context.page.onShow.call(page);assert.equal(shows,1,'No repeat when returning from preview, permissions or save')
context.page.onUnload.call(page);assert.equal(offs,3);assert.equal(destroys,1)
context.page.onReady.call(page);assert.equal(shows,1)
result=null;page=create();context.page.onReady.call(page);assert.equal(creates,1)
result={image_urls:[]};page=create();context.page.onReady.call(page);assert.equal(creates,1,'Empty result has no ad')
result={image_urls:['https://cdn.example/a.jpg']};showFailure=true
page=create();context.page.onReady.call(page);await new Promise(resolve=>setImmediate(resolve))
assert.equal(page.mediaList.length,1);assert.match(logs.at(-1)[0],/显示失败/)
page=create();context.page.onHide.call(page);context.page.onReady.call(page);assert.equal(shows,2,'Do not show in background')
context.wx={};page=create();context.page.onReady.call(page);assert.equal(page.mediaList.length,1)
console.log('PASS: create onLoad, show once onReady after valid image/video result, cleanup, background guard, unsupported SDK and ad failures preserve results')
