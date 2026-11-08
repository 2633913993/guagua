// HBuilderX 的部分 Vue2 编译器从 globalThis.wx 读取 API。
// 微信调试沙箱可能只提供原生 wx 变量；在加载框架前同步真实对象。
const MARKER = 'guagua-native-wx-bootstrap-v1'
const BOOTSTRAP = `/* ${MARKER} */
;(function (nativeWx) {
  if (!nativeWx || typeof nativeWx.getLaunchOptionsSync !== 'function') return;
  if (typeof globalThis !== 'undefined' && !globalThis.wx) globalThis.wx = nativeWx;
  if (typeof global !== 'undefined' && !global.wx) global.wx = nativeWx;
})(typeof wx !== 'undefined' ? wx : undefined);
`

class WechatNativeWxPlugin {
  apply(compiler) {
    // emit 在 JS 压缩后执行，避免 ProvidePlugin 将这里的原生 wx
    // 又替换为依赖 globalThis.wx 的框架模块，造成循环依赖。
    compiler.hooks.emit.tap({ name: 'WechatNativeWxPlugin', stage: 10000 }, compilation => {
      for (const name of ['app.js', 'common/vendor.js']) {
        const asset = compilation.assets[name]
        if (!asset) continue
        const source = asset.source().toString()
        if (source.includes(MARKER)) continue
        const output = BOOTSTRAP + source
        compilation.assets[name] = {
          source: () => output,
          size: () => Buffer.byteLength(output)
        }
      }
    })
  }
}
module.exports = WechatNativeWxPlugin
module.exports.BOOTSTRAP = BOOTSTRAP
module.exports.MARKER = MARKER
