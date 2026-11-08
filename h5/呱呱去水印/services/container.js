import { FEATURES } from '../config/features.js'

let initialized = false

export function cloudClient() {
  if (typeof wx === 'undefined' || !wx.cloud || typeof wx.cloud.downloadFile !== 'function') {
    throw new Error('请在已开通云服务的微信小程序中保存文件。')
  }
  if (!initialized) {
    wx.cloud.init({ env: FEATURES.request.cloudStorageEnv || FEATURES.request.cloudEnv, traceUser: false })
    initialized = true
  }
  return wx.cloud
}

// 云托管统一格式：config.env + path + X-WX-SERVICE + method + data。
export function callContainer({ path, method = 'POST', data = {}, header = {}, timeout }) {
  const config = FEATURES.request
  if (typeof wx === 'undefined' || !wx.cloud || typeof wx.cloud.callContainer !== 'function') {
    return Promise.reject(new Error('微信云托管请求仅支持已开通云服务的微信小程序，请升级微信后重试。'))
  }
  if (!config.cloudEnv || !config.cloudService) {
    return Promise.reject(new Error('请先在 config/features.js 配置云托管环境 ID 和服务名。'))
  }
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) {
    return Promise.reject(new Error('云托管接口路径必须是 / 开头的相对路径。'))
  }
  // 重试由页面使用持久化 request_id 发起；不降级到公网 HTTP。
  return new Promise((resolve, reject) => {
    try {
      if (!initialized) {
        wx.cloud.init({ env: FEATURES.request.cloudStorageEnv || FEATURES.request.cloudEnv, traceUser: false })
        initialized = true
      }
      const result = wx.cloud.callContainer({
        config: { env: config.cloudEnv },
        path,
        header: {
          'content-type': 'application/json',
          ...header,
          'X-WX-SERVICE': config.cloudService
        },
        method,
        data,
        timeout: timeout || config.timeout,
        success: resolve,
        fail: reject
      })
      if (result && typeof result.then === 'function') result.then(resolve, reject)
    } catch (error) { reject(error) }
  })
}
