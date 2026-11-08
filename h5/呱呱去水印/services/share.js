import { requestApi } from './request.js'

const PENDING = 'guagua.pending.share.v1'
const HOME = '/pages/home/index'
const TOKEN = /^[A-Za-z0-9_-]{43}$/
const ENTRY_ID = /^[a-f0-9]{32}$/
function newEntryId() {
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
}
let profile = null, preparing = null, reporting = null, lastEntry = '', launchEntry = '', memoryPending = null

async function shareRequest(options) {
  const response = await requestApi(options)
  let body = response && response.data
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch (_) { body = null }
  }
  if (!response || response.statusCode !== 200 || !body || body.success !== true || !body.data) {
    const error = new Error(body && body.detail || '分享服务暂不可用，请稍后重试')
    error.statusCode = response && response.statusCode
    throw error
  }
  return body.data
}

// 预先获取服务器生成的邀请标识，分享回调只需同步返回卡片。
// 未准备好时使用普通分享路径，不伪造 userId、OpenID 或邀请码。
export function prepareShare() {
  if (preparing) return preparing
  preparing = shareRequest({ path: '/api/share/profile', method: 'GET' }).then(value => {
    if (!TOKEN.test(value.inviter) || !Number.isSafeInteger(value.reward_per_friend) || value.reward_per_friend < 0) {
      throw new Error('分享配置响应格式错误')
    }
    profile = value
    return value
  }).finally(() => { preparing = null })
  return preparing
}
export function shareMessage() {
  return {
    title: '呱呱去水印｜好内容，轻松保存',
    path: profile ? `${HOME}?inviter=${encodeURIComponent(profile.inviter)}&source=${encodeURIComponent('wechat_share')}` : HOME,
    imageUrl: '/static/frog-hello.png'
  }
}
function pendingEntries() {
  if (memoryPending !== null) return memoryPending
  let entries
  try { entries = uni.getStorageSync(PENDING) } catch (_) { entries = [] }
  memoryPending = Array.isArray(entries) ? entries.filter(item => item && TOKEN.test(item.inviter) && item.source === 'wechat_share' && ENTRY_ID.test(item.entry_id || '')).slice(-100) : []
  return memoryPending
}
function savePending(entries) {
  memoryPending = entries
  // 存储空间不足时仍在本次运行内保留邀请，不阻断小程序启动。
  try {
    if (entries.length) uni.setStorageSync(PENDING, entries)
    else uni.removeStorageSync(PENDING)
  } catch (_) { /* 下次登录/前台重试仍可读取内存待上报项 */ }
}
export function captureShareEntry(query = {}, force = false) {
  if (!TOKEN.test(query.inviter || '') || query.source !== 'wechat_share') return false
  const key = `${query.inviter}:${query.source}`
  if (!force && key === lastEntry) return false // App 与页面的同一次进入合并。
  const entry = { inviter: query.inviter, source: query.source, entry_id: newEntryId() }
  savePending([...pendingEntries(), entry].slice(-100))
  lastEntry = key
  return true
}
// 应用 onShow 每次携带分享参数进入都生成新编号；冷启动 onLaunch
// 与紧接着的 onShow 共用一次事件，页面 onLoad 仅做兜底。
export function captureAppEntry(options = {}, phase = 'show') {
  let query = options.query
  if (!query && typeof wx !== 'undefined' && typeof wx.getEnterOptionsSync === 'function') {
    try { query = wx.getEnterOptionsSync().query } catch (_) { return false }
  }
  query = query || {}
  const key = TOKEN.test(query.inviter || '') && query.source === 'wechat_share' ? `${query.inviter}:${query.source}` : ''
  if (phase === 'launch') {
    launchEntry = key
    return captureShareEntry(query)
  }
  if (key && launchEntry === key) { launchEntry = ''; return false }
  launchEntry = ''
  return captureShareEntry(query, true)
}

export function flushShareEntries() {
  if (reporting) return reporting
  reporting = (async () => {
    let accepted = false
    while (pendingEntries().length) {
      const entry = pendingEntries()[0]
      try {
        const receipt = await shareRequest({ path: '/api/share/log', method: 'POST', data: entry })
        accepted = accepted || receipt.recorded && !receipt.duplicate
      } catch (error) {
        // 登录身份尚未就绪、网络失败或服务异常：保留待上报项，恢复后重试。
        if (![400, 404, 409, 422].includes(error.statusCode)) throw error
      }
      const rest = pendingEntries().filter(item => item.entry_id !== entry.entry_id)
      savePending(rest)
    }
    return accepted
  })().finally(() => { reporting = null })
  return reporting
}
export function fetchInvitees(before = 0) {
  return shareRequest({ path: '/api/share/my-invitees', method: 'GET', data: { before, limit: 20 } })
}
