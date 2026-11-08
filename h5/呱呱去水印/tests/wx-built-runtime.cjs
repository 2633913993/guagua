const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path')
const {BOOTSTRAP}=require('../build/wechat-native-wx-plugin.cjs')
const build=path.resolve(process.argv[2] || 'unpackage/dist/build/mp-weixin')
const source=fs.readFileSync(path.join(build,'common/vendor.js'),'utf8')
function run(code) {
  const native={canIUse:()=>true,getLaunchOptionsSync:()=>({scene:1001}),getSystemInfoSync:()=>({language:'zh_CN'}),cloud:{}}
  const context={wx:native,globalThis:{},global:{webpackJsonp:[]}}
  vm.runInNewContext(code,context)
  const modules=context.global.webpackJsonp.flatMap(chunk=>Object.values(chunk[1]))
  const moduleFunction=modules.find(fn=>fn.toString().includes('["w","x"].join')&&fn.toString().includes('getLaunchOptionsSync'))
  assert(moduleFunction,'当前编译器 wx 适配模块未找到，需要重新审查框架兼容处理')
  const module={exports:{}}
  const webpackRequire=()=>{throw Error('Unexpected adapter dependency')}
  // Production Webpack marks ES modules before executing the wx adapter.
  webpackRequire.r=exports=>Object.defineProperty(exports,'__esModule',{value:true})
  moduleFunction(module,module.exports,webpackRequire)
  assert.equal(module.exports.default.getSystemInfoSync,native.getSystemInfoSync)
}
assert(source.startsWith(BOOTSTRAP),'vendor 缺少启动桥接')
assert(fs.readFileSync(path.join(build,'app.js'),'utf8').startsWith(BOOTSTRAP),'app 入口缺少启动桥接')
assert.throws(()=>run(source.slice(BOOTSTRAP.length)),/getLaunchOptionsSync/)
run(source)
console.log('PASS: final vendor.js reproduces failure without bootstrap and successfully initializes actual wx adapter with bootstrap')
