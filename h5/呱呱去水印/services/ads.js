import { FEATURES } from '../config/features.js'
import { requestApi } from './request.js'
import { cacheQuota } from './quota.js'

const PENDING = 'guagua.pending.ad.v1'

function failure(label, error) {
  const detail = error && (error.errMsg || error.message)
  const code = error && error.errCode
  return new Error(label + (code !== undefined ? '（' + code + '）' : '') + (detail ? '：' + detail : '，请稍后重试'))
}

async function api(path, data) {
  const starting = path === '/api/ads/start'
  let response
  try { response = await requestApi({ path, data }) }
  catch (error) {
    console.error('广告奖励接口请求失败', path, error)
    throw failure(starting ? '奖励服务连接失败，尚未播放广告' : '奖励领取失败，请重试，勿重复观看', error)
  }
  let body = response && response.data
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch (_) { throw new Error('广告奖励响应格式错误，请稍后重试') }
  }
  if (!response || response.statusCode !== 200 || !body || body.success !== true || !body.data) {
    const detail = body && typeof body.detail === 'string' ? body.detail : '请检查后端部署与广告配置'
    const error = new Error((starting ? '奖励服务未就绪，尚未播放广告' : '奖励领取失败，请重试，勿重复观看') +
      (response ? '（HTTP ' + response.statusCode + '）' : '') + '：' + detail)
    error.status = response && response.statusCode
    console.error('广告奖励接口响应异常', path, response && response.statusCode, body)
    throw error
  }
  return body.data
}

// Client completion is reported to an account-bound, idempotent server session.
// Only a server receipt updates the displayed balance.
export function createAdController(callbacks = {}) {
  let video, unit, loadListener, closeListener, errorListener, finishPlayback
  let inFlight, lastSdkError, shown = false, disposed = false
  const emit = (key, value) => { if (!disposed && callbacks[key]) callbacks[key](value) }
  function detach() {
    if (video) {
      if (loadListener) video.offLoad(loadListener)
      if (closeListener) video.offClose(closeListener)
      if (errorListener) video.offError(errorListener)
    }
    video = unit = loadListener = closeListener = errorListener = null
  }
  function init() {
    if (disposed) return false
    const config = FEATURES.ads
    if (!config.enabled || !/^adunit-[A-Za-z0-9]+$/.test(config.adUnitId || '')) {
      throw new Error('激励视频广告暂未配置，请稍后再试或分享获取次数')
    }
    if (typeof wx === 'undefined' || typeof wx.createRewardedVideoAd !== 'function') {
      throw new Error('当前环境不支持激励视频，请使用微信小程序并检查基础库版本')
    }
    if (video && unit === config.adUnitId) return true
    detach()
    try {
      video = wx.createRewardedVideoAd({ adUnitId: config.adUnitId })
      unit = config.adUnitId
      loadListener = () => { lastSdkError = null; console.log('激励视频广告 加载成功') }
      errorListener = error => {
        lastSdkError = error
        console.error('激励视频广告加载失败', error)
        // A failed first show may emit onError before its rejection. Let the
        // official show/load/show retry handle errors until show succeeds.
        if (finishPlayback && shown) finishPlayback(failure('广告播放失败', error))
      }
      closeListener = result => {
        if (finishPlayback) finishPlayback(null, !!result && result.isEnded === true)
      }
      video.onLoad(loadListener)
      video.onError(errorListener)
      video.onClose(closeListener)
      return true
    } catch (error) {
      console.error('激励视频广告实例创建失败', error)
      detach()
      throw failure('广告初始化失败', error)
    }
  }
  async function claim() {
    const pending = uni.getStorageSync(PENDING)
    if (!pending || !pending.session_id) return null
    let receipt
    try { receipt = await api('/api/ads/complete', { session_id: pending.session_id }) }
    catch (error) {
      if ([403, 404, 410, 422].includes(error.status)) uni.removeStorageSync(PENDING)
      throw error
    }
    const quota = cacheQuota(receipt.quota)
    uni.removeStorageSync(PENDING)
    emit('quota', quota)
    emit('message', receipt.duplicate ? '广告奖励已到账' : '已获得 5 次解析机会')
    return quota
  }
  function playback() {
    return new Promise((resolve, reject) => {
      let settled = false
      shown = false
      lastSdkError = null
      const finish = (error, complete) => {
        if (settled) return
        settled = true
        finishPlayback = null
        shown = false
        if (error) reject(error)
        else resolve(complete)
      }
      finishPlayback = finish
      const current = video
      ;(async () => {
        try {
          try { await current.show() }
          catch (error) {
            console.warn('激励视频首次展示失败，加载后重试', error)
            if (settled || disposed) return
            await current.load()
            if (settled || disposed) return
            await current.show()
          }
          if (!settled) shown = true
        } catch (error) {
          console.error('激励视频广告显示失败', error)
          finish(failure('激励视频广告显示失败', error && error.errCode !== undefined ? error : (lastSdkError || error)))
        }
      })()
    })
  }
  function run(work) {
    if (disposed) return Promise.resolve(null)
    if (inFlight) return inFlight
    emit('busy', true)
    inFlight = Promise.resolve().then(work).finally(() => {
      inFlight = null
      emit('busy', false)
    })
    return inFlight
  }
  return {
    init,
    recover() {
      if (!uni.getStorageSync(PENDING)) return Promise.resolve(null)
      return run(claim)
    },
    show() {
      return run(async () => {
        const recovered = await claim()
        if (recovered) return recovered
        if (disposed) return null
        init()
        // Reserve the server reward before showing: backend failures must not
        // make the user watch a video that cannot be credited.
        const session = await api('/api/ads/start', {})
        if (disposed) return null
        if (session.ad_unit_id !== unit || !/^[A-Za-z0-9_-]{43}$/.test(session.session_id)) {
          throw new Error('前后端广告位配置不一致，请联系管理员')
        }
        if (!await playback()) {
          emit('message', '未完整观看广告，本次不增加解析次数')
          return null
        }
        uni.setStorageSync(PENDING, { session_id: session.session_id })
        return claim()
      })
    },
    dispose() {
      disposed = true
      if (finishPlayback) finishPlayback(new Error('页面已关闭'))
      detach()
    }
  }
}
