import { FEATURES } from '../config/features.js'
import { callContainer } from './container.js'

// 解析、额度共用同一个请求模式、后端地址与超时配置。
export function requestApi({ path, method = 'POST', data = {}, header = {}, timeout }) {
  const config = FEATURES.request
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) {
    return Promise.reject(new Error('接口路径必须是 / 开头的相对路径。'))
  }
  if (config.mode === 'container') return callContainer({ path, method, data, header, timeout })
  if (config.mode !== 'http') {
    return Promise.reject(new Error('请在 config/features.js 将 request.mode 配置为 http 或 container。'))
  }
  const baseUrl = typeof config.httpBaseUrl === 'string' ? config.httpBaseUrl.trim().replace(/\/+$/, '') : ''
  if (!/^https?:\/\/[^\s/?#]+$/i.test(baseUrl)) {
    return Promise.reject(new Error('请在 config/features.js 配置有效的 request.httpBaseUrl（例如 http://127.0.0.1:8080）。'))
  }
  return new Promise((resolve, reject) => {
    uni.request({
      url: baseUrl + path,
      method,
      data,
      header: { 'content-type': 'application/json', ...header },
      timeout: timeout || config.timeout,
      success: resolve,
      fail: reject
    })
  })
}
