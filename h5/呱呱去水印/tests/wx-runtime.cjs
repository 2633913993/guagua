const fs = require('fs')
const vm = require('vm')
const assert = require('assert/strict')
const Plugin = require('../build/wechat-native-wx-plugin.cjs')
// 用当前编译器的真实框架模块复现首条报错，而非用假业务函数代替。
const runtimeFile = process.argv[2] || '/Applications/HBuilderX.app/Contents/HBuilderX/plugins/uniapp-cli/node_modules/@dcloudio/uni-mp-weixin/dist/wx.js'
const source = fs.readFileSync(runtimeFile,'utf8').replace('export default target[key]', 'result = target[key]')
function native() {
  return { canIUse: () => true, getLaunchOptionsSync: () => ({scene:1001}), getSystemInfoSync: () => ({language:'zh_CN'}), cloud:{callContainer(){}}, createInterstitialAd(){} }
}
let api = native()
assert.throws(() => vm.runInNewContext(source,{globalThis:{},wx:api}), /getLaunchOptionsSync/, '复现调试环境分离时的原始错误')
let context={globalThis:{},global:{},wx:api}
vm.runInNewContext(Plugin.BOOTSTRAP+source,context)
assert.equal(context.result.getLaunchOptionsSync,api.getLaunchOptionsSync)
assert.equal(context.result.getSystemInfoSync,api.getSystemInfoSync)
assert.equal(context.result.cloud,api.cloud)
assert.equal(context.result.createInterstitialAd,api.createInterstitialAd)
assert.equal(context.global.wx,api)
api=native();context={wx:api};vm.runInNewContext(Plugin.BOOTSTRAP+source,context)
assert.equal(context.result.getSystemInfoSync,api.getSystemInfoSync,'正常全局环境不受影响')
const existing=native();context={globalThis:{wx:existing},wx:native()}
vm.runInNewContext(Plugin.BOOTSTRAP+source,context)
assert.equal(context.result.getLaunchOptionsSync,existing.getLaunchOptionsSync,'不覆盖已有 globalThis.wx')
context={globalThis:{},global:{}}
vm.runInNewContext(Plugin.BOOTSTRAP,context)
assert.equal(context.globalThis.wx,undefined,'没有原生 wx 时不伪造 API')
// 验证构建插件将桥接代码放在 app/vendor 执行前，且重复执行不重复添加。
let emit
new Plugin().apply({hooks:{emit:{tap:(_options,fn)=>{emit=fn}}}})
const assets={assets:{'app.js':{source:()=>"require('./common/runtime.js')"},'common/vendor.js':{source:()=>"global['webpackJsonp'].push([])"},'pages/index/index.js':{source:()=>"original"}}}
emit(assets);emit(assets)
assert.equal(assets.assets['app.js'].source().split(Plugin.MARKER).length,2)
assert.equal(assets.assets['app.js'].source().indexOf(Plugin.MARKER) < assets.assets['app.js'].source().indexOf('require('),true)
assert.equal(assets.assets['common/vendor.js'].source().startsWith('/* '+Plugin.MARKER),true)
assert.equal(assets.assets['pages/index/index.js'].source(),'original')
console.log('PASS: reproduced original wx failure; bootstrap fixes split globals; native API identity preserved; existing wx preserved; missing native API not faked; build hook order/idempotence')
