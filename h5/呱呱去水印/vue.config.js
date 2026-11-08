// 微信真机调试也使用压缩代码，避免上传未压缩脚本；H5 配置保持默认。
module.exports = {
  productionSourceMap: false,
  configureWebpack: config => {
    if (process.env.UNI_PLATFORM === 'mp-weixin') {
      const WechatNativeWxPlugin = require('./build/wechat-native-wx-plugin.cjs')
      config.plugins = config.plugins || []
      config.plugins.push(new WechatNativeWxPlugin())
      config.devtool = false
      config.optimization = config.optimization || {}
      config.optimization.minimize = true
    }
  }
}
