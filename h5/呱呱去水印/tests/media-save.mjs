import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { detectMediaFormat, mediaSaveError, isAlbumPermissionError, requestAlbumAuthorization, isPrivacyPermissionError } from '../services/media-save.js'
const item={id:1,type:'video',url:'https://cdn.example/media?id=123'}
const proxyUrl='https://old.example/file'
const mp4=Uint8Array.from([0,0,0,24,102,116,121,112,105,115,111,109])
assert.deepEqual(detectMediaFormat(mp4,{'content-type':'application/json'}),{type:'video',ext:'mp4'})
assert.deepEqual(detectMediaFormat(Uint8Array.from([255,216,255])),{type:'image',ext:'jpg'})
assert.deepEqual(detectMediaFormat(Uint8Array.from([137,80,78,71,13,10,26,10])),{type:'image',ext:'png'})
for (const value of ['{"error":"denied"}','<html>expired</html>']) {
 assert.throws(()=>detectMediaFormat(Uint8Array.from(Buffer.from(value)),{'content-type':'video/mp4'}),/JSON 或网页/)
}
assert.throws(()=>detectMediaFormat(new Uint8Array()),/为空/)
assert.match(mediaSaveError({errMsg:'saveVideoToPhotosAlbum:fail auth deny'}),/允许保存到相册/)
// 页面在原生保存完成前保持加载状态，并阻止重复点击。
const raw=fs.readFileSync(new URL('../pages/result/result.vue',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
const source=raw.replace(/^import .*$/mg,'').replace(/\/\/ #ifdef H5[\s\S]*?\/\/ #endif/g,'')
let completeSave, begin=0, end=0, saveCalls=0, modal
const notifications=[], events=[]
const context={
  PARSE_RESULT_KEY:'parseResult',mediaSaveError,isAlbumPermissionError,requestAlbumAuthorization,isPrivacyPermissionError,
  ensureMediaSaveAuthorization:async()=>true,
  saveMediaToAlbum:()=>{saveCalls++;return new Promise(resolve=>{completeSave=resolve})},
  uni:{showLoading:()=>{begin++;events.push('loading')},hideLoading:()=>{end++;events.push('hide')},showToast:options=>{notifications.push(options);events.push('toast')},showModal:options=>{modal=options;events.push('modal')}}
}
vm.runInNewContext(source.replace('export default','globalThis.page='),context)
context.uni.getStorageSync = () => ({
  video_url: item.url, download_url: proxyUrl,
  image_urls: [null, 'https://cdn.example/image.jpg'],
  image_download_urls: [null, proxyUrl + '&image=1']
})
const loaded = context.page.data()
context.page.onLoad.call(loaded)
assert.equal(loaded.mediaList[0].url,item.url,'结果页播放原始视频地址')
assert.equal(loaded.mediaList[0].downloadUrl,proxyUrl)
assert.equal(loaded.mediaList[1].downloadUrl,proxyUrl + '&image=1','过滤无效图片时保持下载链接索引对应')
const page={...context.page.data(),...context.page.methods}
const pending=page.downloadMedia(item)
await new Promise(resolve=>setImmediate(resolve))
await page.downloadMedia(item)
assert.equal(saveCalls,1)
assert.equal(begin,1)
assert.equal(end,0)
assert.equal(notifications.length,0,'保存接口成功前不显示成功提示')
completeSave({type:'video',ext:'mp4'})
await pending
assert.equal(end,1)
assert.equal(page.savingId,0)
assert.equal(notifications[0].title,'已保存到相册')
assert.equal(notifications[0].duration,2000)
assert.equal(notifications[0].icon,'success')
assert.deepEqual(events,['loading','hide','toast'],'先关闭加载提示，避免 hideLoading 关闭成功 toast')
events.length=0
context.saveMediaToAlbum=async()=>{throw new Error('download permission denied')}
await page.downloadMedia(item)
assert.equal(modal.showCancel,false)
assert.match(modal.content,/下载失败/)
assert.equal(page.savingId,0)
console.log('PASS: media format detection, loading until save completion, duplicate taps, failure does not report success')

for (const code of ['AUTHORIZE_PRIVACY','ALBUM_PRIVACY','CLOUD_DOWNLOAD','ALBUM_WRITE_GRANTED','ALBUM_WRITE']) {
 const message=mediaSaveError({code,isMediaSaveError:true,message:'阶段：AUTHORIZE_PRIVACY 错误码：112 微信返回：authorize:fail api scope is not declared in the privacy agreement'})
 assert.doesNotMatch(message,/112|AUTHORIZE|阶段|错误码|微信返回|api scope/)
}
