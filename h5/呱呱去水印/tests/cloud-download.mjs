import assert from 'node:assert/strict'
import { downloadThroughCloud } from '../services/cloud-download.js'
import { saveMediaToAlbum } from '../services/media-save.js'
const requests = [], downloads = [], files = new Map(), saved = []
const bytes = Uint8Array.from([0,0,0,24,102,116,121,112,105,115,111,109])
const id = 'a'.repeat(32), fileID = 'cloud://prod-d3gk2acox7105dd30.7072-prod-d3gk2acox7105dd30-1385311964/video/test.mp4'
let statuses = [{ status:'processing' }, { status:'success', fileID, contentType:'video/mp4' }]
let stateError, cloudError
const fs = {
  stat: o => o.success({stats:{size:files.get(o.path).length}}),
  readFile: o => { assert.ok(o.length <= 512); o.success({data: files.get(o.filePath).buffer}) },
  copyFile: o => { files.set(o.destPath, files.get(o.srcPath)); o.success({}) },
  unlink: o => { files.delete(o.filePath); o.success({}) }
}
globalThis.wx = {
  env:{USER_DATA_PATH:'wxfile://usr'},getFileSystemManager:()=>fs,
  cloud: {
    init() {},
    callContainer: o => {
      requests.push(o)
      assert.equal(o.timeout,14000)
      assert.equal(o.header['X-WX-SERVICE'],'pwqsy-fastapi')
      if (o.path.includes('submit')) return o.success({statusCode:200,data:{code:0,task_id:id}})
      if (stateError) return o.success({statusCode:403,data:{detail:'查询未授权'}})
      o.success({statusCode:200,data:statuses.shift() || {status:'success',fileID}})
    },
    downloadFile: o => {
      downloads.push(o)
      if (cloudError) return o.fail({errMsg:'cloud download failed'})
      files.set('wxfile://tmp.bin',bytes)
      o.success({tempFilePath:'wxfile://tmp.bin'})
    }
  }
}
globalThis.uni = {
  downloadFile() { throw new Error('must never download via public HTTP') },
  saveVideoToPhotosAlbum: o => { assert.ok(files.has(o.filePath)); saved.push(o.filePath); o.success({}) }
}
const wait = async () => {}
const url = 'http://cdn.example/test.mp4?token=a%2Fb'
const stages=[]
const result = await downloadThroughCloud(url,{wait,onStage:stage=>stages.push(stage)})
assert.deepEqual(stages,['正在转存文件','正在下载文件'])
assert.equal(result.tempFilePath,'wxfile://tmp.bin')
assert.equal(requests[0].data.url,url)
assert.equal(downloads[0].fileID,fileID)
assert.equal(downloads[0].config.env,'prod-d3gk2acox7105dd30')
assert.ok(requests[1].path.startsWith('/api/download/status?task_id='))
assert.equal(requests[1].method,'GET')
statuses=[{status:'failed',msg:'文件源拒绝下载'}]
await assert.rejects(downloadThroughCloud(url,{wait}),/文件源拒绝/)
stateError=true
await assert.rejects(downloadThroughCloud(url,{wait}),/查询未授权/)
stateError=false
statuses=[{status:'success',fileID:'https://public.example/file'}]
await assert.rejects(downloadThroughCloud(url,{wait}),/标识无效/)
statuses=[{status:'success',fileID:'cloud://another-env.another-bucket/video/a.mp4'}]
await assert.rejects(downloadThroughCloud(url,{wait}),/环境或存储桶不匹配/)
statuses=[{status:'success',fileID}];cloudError=true
await assert.rejects(downloadThroughCloud(url,{wait}),/云存储文件下载失败/)
cloudError=false
statuses=[{status:'success',fileID}]
// Skip only polling timers; use the actual submit/poll/cloud-download/save pipeline.
const realTimer=globalThis.setTimeout
globalThis.setTimeout=(fn)=>realTimer(fn,0)
await saveMediaToAlbum({type:'video',url,downloadUrl:'https://old-proxy.example/ignored'})
globalThis.setTimeout=realTimer
assert.match(saved[0],/\.mp4$/)
assert.equal(requests.filter(o=>o.path.includes('submit')).at(-1).data.url,url)
assert.equal(files.size,0)
console.log('PASS: private container submit/poll, cloud fileID download, HTTP errors, failed jobs, format detection, album save and cleanup; no public file download')
