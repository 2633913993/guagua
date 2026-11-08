import assert from 'node:assert/strict'
import {ensureMediaSaveAuthorization,isPrivacyPermissionError,isAlbumPermissionError,saveLocalMediaToAlbum,mediaSaveError} from '../services/media-save.js'
let scope, needPrivacy, privacyCalls, authCalls, settingCalls, writes, system, modal, failWrite, files
function reset(){
 scope=true;needPrivacy=false;privacyCalls=authCalls=settingCalls=writes=0;system='authorized';failWrite=null;files=[];modal=null
 globalThis.wx={getPrivacySetting:o=>o.success({needAuthorization:needPrivacy}),requirePrivacyAuthorize:o=>{privacyCalls++;needPrivacy=false;o.success({})},
 getSystemInfoSync:()=>({platform:'ios',albumAuthorized:system!=='denied'}),getSetting:o=>o.success({authSetting:{'scope.writePhotosAlbum':scope}}),
 authorize:o=>{authCalls++;scope=true;o.success({})},showModal:o=>{modal=o},openSetting:o=>{settingCalls++;scope=true;o.success({authSetting:{'scope.writePhotosAlbum':true}})},
 saveImageToPhotosAlbum:o=>{writes++;files.push(o.filePath);failWrite?o.fail(failWrite):o.success({})}}
 globalThis.uni={saveImageToPhotosAlbum:()=>assert.fail('Use native wx save API')}
}
reset();needPrivacy=true
await assert.rejects(ensureMediaSaveAuthorization(),/同意隐私协议并保存/)
assert.equal(authCalls,0,'Do not ask for album scope when privacy consent is missing')
assert.equal(privacyCalls,0,'Do not wait on requirePrivacyAuthorize without a consent button')
needPrivacy=false
assert.equal(await ensureMediaSaveAuthorization(),true)
assert.equal(authCalls,0,'Already-granted album permission remains granted')
reset();system='denied';await assert.rejects(ensureMediaSaveAuthorization(),/手机系统/);assert.equal(authCalls,0)
reset();scope=undefined;await ensureMediaSaveAuthorization();assert.equal(authCalls,1)
for(const errMsg of ['downloadFile:fail permission denied','cloud.downloadFile:fail auth deny','request:fail permission denied','readFile:fail permission denied']){
 assert.equal(isAlbumPermissionError({errMsg}),false)
}
reset();failWrite={errMsg:'saveImageToPhotosAlbum:fail auth deny',errCode:2001}
await assert.rejects(saveLocalMediaToAlbum('saveImageToPhotosAlbum','/tmp/local.jpg'),/相册权限已开启/)
assert.equal(settingCalls,0,'Do not ask for album authorization repeatedly when it is already granted')
assert.equal(modal,null);assert.equal(writes,1)
reset();scope=false;failWrite={errMsg:'saveImageToPhotosAlbum:fail auth deny'}
const pending=saveLocalMediaToAlbum('saveImageToPhotosAlbum','/tmp/local.jpg')
await new Promise(r=>setImmediate(r));assert.equal(writes,1);assert.ok(modal)
failWrite=null;modal.success({confirm:true});await pending
assert.equal(settingCalls,1);assert.deepEqual(files,['/tmp/local.jpg','/tmp/local.jpg'],'Retry the downloaded file instead of downloading again')
reset();failWrite={errMsg:'saveImageToPhotosAlbum:fail privacy permission is not authorized',errCode:112}
await assert.rejects(saveLocalMediaToAlbum('saveImageToPhotosAlbum','/tmp/local.jpg'),/后台隐私指引/)
assert.equal(settingCalls,0);assert.equal(privacyCalls,0)
reset();failWrite={errMsg:'saveImageToPhotosAlbum:fail invalid image format'}
await assert.rejects(saveLocalMediaToAlbum('saveImageToPhotosAlbum','/tmp/local.jpg'),/invalid image format/)
assert.equal(settingCalls,0)
assert.equal(isPrivacyPermissionError({isMediaSaveError:true,message:'privacy'}),false,'Typed privacy diagnostics must not cause recovery loops')
console.log('PASS: privacy and album independent, system denial, permission misclassification, granted-but-write-denied, native save and same-file recovery')

const fs=await import('node:fs')
const pageSource=fs.readFileSync(new URL('../pages/result/result.vue',import.meta.url),'utf8')
assert.match(pageSource,/open-type="agreePrivacyAuthorization" @agreeprivacyauthorization="saveAfterPrivacy\(item\)"/)
assert.doesNotMatch(pageSource,/@tap="downloadMedia\(item\)"/,'No path may start saving before native privacy agreement')
assert.doesNotMatch(pageSource,/privacy-mask/,'No custom authorization dialog')
