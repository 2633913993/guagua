import { requestApi } from './request.js'

const PENDING = 'guagua.pending.parse.v2'
function token() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}
// Persist before sending: network timeout, app restart and navigation failure
// all retain the same request ID. A different input starts a new operation.
function pendingList() {
  const stored = uni.getStorageSync(PENDING)
  return Array.isArray(stored) ? stored : stored && stored.url ? [stored] : []
}
export function pendingParse(url) {
  const all = pendingList()
  const old = all.find(item => item.url === url && /^[A-Za-z0-9_-]{16,64}$/.test(item.request_id))
  if (old) return old
  const pending = { url, request_id: token() }
  uni.setStorageSync(PENDING, [...all, pending])
  return pending
}
export function finishPending(requestId) {
  const rest = pendingList().filter(item => item.request_id !== requestId)
  if (rest.length) uni.setStorageSync(PENDING, rest)
  else uni.removeStorageSync(PENDING)
}
export function requestParse(pending) {
  return requestApi({ path: '/api/v1/parse', method: 'POST', data: pending })
}
export function responseBody(response) {
  let body = response && response.data
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch (_) { throw new Error('接口返回格式错误，请保留原内容重试。') }
  }
  return body
}
export function parseResponse(response) {
  const body = responseBody(response)
  if (!response || response.statusCode !== 200 || !body || body.success !== true || !body.data) {
    throw new Error(body && typeof body.detail === 'string' ? body.detail : '解析失败，请稍后重试。')
  }
  const result = body.data
  const valid = value => typeof value === 'string' && /^https?:\/\/\S+$/i.test(value.trim())
  if (!valid(result.video_url) && !(Array.isArray(result.image_urls) && result.image_urls.some(valid))) {
    throw new Error('未解析到图片或视频，请保留原内容重试。')
  }
  return result
}
export function requestError(error) {
  const message = (error && (error.message || error.errMsg)) || ''
  if (/timeout|request:fail|callContainer:fail|network/i.test(message)) {
    return '暂未收到解析结果，次数以服务器为准。请保留原内容重试，将查询同一次解析。'
  }
  return message || '解析失败，请稍后重试。'
}
