import { requestApi } from './request.js'

const KEY = 'guagua.quota.server.v2'
const TOTAL_KEY = 'guagua.parse.total.v1'
let quotaRequest = null
export function readTotalParses() {
  try {
    const value = uni.getStorageSync(TOTAL_KEY)
    return Number.isSafeInteger(value) && value >= 0 ? value : null
  } catch (_) { return null }
}
export function cacheTotalParses(value) {
  if (!Number.isSafeInteger(value) || value < 0) return readTotalParses()
  const previous = readTotalParses()
  const total = previous === null ? value : Math.max(previous, value)
  try { uni.setStorageSync(TOTAL_KEY, total) } catch (_) { /* Display cache only. */ }
  return total
}
// Display only. Never initialize a balance, reserve, refund or grant locally.
export function readQuota() { return uni.getStorageSync(KEY) || null }
export function cacheQuota(quota) {
  if (!quota || !/^\d{4}-\d{2}-\d{2}$/.test(quota.quota_date) ||
      !Number.isSafeInteger(quota.remaining) || quota.remaining < 0 ||
      !Number.isSafeInteger(quota.initial_quota) || quota.initial_quota < 0 ||
      !Number.isFinite(Date.parse(quota.server_time))) throw new Error('次数响应格式错误')
  try { uni.setStorageSync(KEY, quota) } catch (_) { /* 缓存失败不影响服务器结果 */ }
  cacheTotalParses(quota.total_parses)
  return quota
}
export function fetchQuota() {
  // 整个重试过程只保留一个请求，避免 onShow、下拉刷新同时请求。
  if (quotaRequest) return quotaRequest
  quotaRequest = loadQuotaWithRetry().finally(() => { quotaRequest = null })
  return quotaRequest
}
const RETRY_DELAYS = [1000, 2000]
function temporaryFailure(error) {
  if (error && typeof error.retryable === 'boolean') return error.retryable
  const message = String(error && (error.errMsg || error.message) || '')
  return !!(error && error.errCode) || /callContainer:fail|request:fail|network|timeout|timed out|connection|网络|超时/i.test(message)
}
async function loadQuotaWithRetry() {
  for (let attempt = 0; ; attempt++) {
    try { return await loadQuota() }
    catch (error) {
      const retry = attempt < RETRY_DELAYS.length && temporaryFailure(error)
      // 不记录身份、链接、请求体；保留状态码和 SDK 错误以排查首次失败。
      console.warn('解析次数读取失败', {
        attempt: attempt + 1,
        statusCode: error && error.statusCode,
        errCode: error && error.errCode,
        message: String(error && (error.errMsg || error.message) || '未知错误'),
        retryIn: retry ? RETRY_DELAYS[attempt] : null
      })
      if (!retry) throw error
      await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[attempt]))
    }
  }
}
async function loadQuota() {
  // 额度查询独立超时；解析等耗时接口仍沿用原配置。
  const response = await requestApi({ path: '/api/v1/quota', method: 'GET', data: {}, timeout: 15000 })
  const statusCode = response && response.statusCode
  let body = response && response.data
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch (_) { body = null }
  }
  if (statusCode !== 200 || !body || body.success !== true) {
    const error = new Error(body && typeof body.detail === 'string' ? body.detail : '次数读取失败')
    error.statusCode = statusCode
    // 网关 HTML 错误也按状态码重试；配置、权限及格式错误不反复请求。
    error.retryable = statusCode === 408 || statusCode === 429 || (statusCode >= 500 && statusCode <= 599)
    throw error
  }
  return cacheQuota(body.quota)
}
// Scheduling affects display refresh only. The server decides the quota date.
export function untilMidnight(serverTime) {
  const now = Date.parse(serverTime)
  if (!Number.isFinite(now)) return 60000
  return 86400000 - ((now + 8 * 3600000) % 86400000)
}
