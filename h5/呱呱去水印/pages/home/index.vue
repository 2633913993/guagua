<template>
  <view class="page">
    <!-- #ifdef MP-WEIXIN -->
    <view v-if="!topAdHidden" class="top-ad-slot" aria-label="广告">
      <ad-custom unit-id="adunit-e20bf1e52e878d13"
        @load="adLoad" @error="adError" @close="adClose" />
    </view>
    <!-- #endif -->
    <view class="total-parses">
      <text class="total-parses__dot"></text><text>累计成功解析</text><text class="total-parses__number">{{ totalParses === null ? '0' : totalParses }}</text><text>次</text>
    </view>
    <view class="support-note">本工具免费使用，广告收入用于支持服务器运行与维护。若有打扰，还请谅解。感谢你的使用与支持，作者感激不尽。</view>
    <view class="hero">
      <view class="hero-copy">
        <view class="hero-title"><text class="underline">好内容</text><text>，轻松保存</text></view>
        <text class="hero-desc">粘贴分享链接，剩下的交给呱呱</text>
      </view>
      <view class="frog frog-hello" aria-hidden="true"><image class="frog-sheet" src="/static/frog-hello.png" mode="scaleToFill" /></view>
    </view>

    <view class="input-card">
      <view class="input-card__header">
        <text class="input-card__title">分享文案或链接</text>
        <view class="clear-btn" hover-class="tap-soft" @tap="clearInput"><view class="icon-link"></view><text>清空</text></view>
      </view>
      <view class="input-area">
        <textarea v-if="!tutorialVisible" v-model="linkText" class="link-textarea" maxlength="-1" :focus="textareaFocus"
          placeholder="长按粘贴视频 / 图片分享链接
包含分享文案也能自动识别"
          placeholder-class="link-textarea__placeholder" cursor-spacing="24" />
        <view class="paste-note"><view class="clipboard-icon"></view><text>长按输入框即可粘贴</text></view>
      </view>
      <view class="platforms" aria-label="支持解析抖音、快手、小红书、最右、皮皮虾">
        <view class="platforms__tag platforms__tag--douyin"><text class="platform-mark platform-mark--music">♪</text><text>抖音</text></view>
        <view class="platforms__tag platforms__tag--kuaishou"><view class="camera-icon"></view><text>快手</text></view>
        <view class="platforms__tag platforms__tag--red"><text class="platform-mark platform-mark--red">红</text><text>小红书</text></view>
        <view class="platforms__tag platforms__tag--zuiyou"><text class="platform-mark platform-mark--smile">☺</text><text>最右</text></view>
        <view class="platforms__tag platforms__tag--pipixia"><text class="platform-mark platform-mark--shrimp">P</text><text>皮皮虾</text></view>
      </view>
      <button class="analyze-btn" :class="{ 'analyze-btn--loading': isLoading }" :disabled="isLoading || adBusy" hover-class="analyze-btn--pressed" @tap="handleAnalyze">
        <view v-if="!isLoading" class="sparkle-icon"><text class="sparkle-main">✦</text><text class="sparkle-small">✦</text></view>
        <view v-else class="loading-spinner"></view>
        <text>{{ adBusy ? '广告奖励处理中…' : (isLoading ? '正在解析…' : (!quotaUnavailable && remainingParses === 0 ? '看视频 +5 次并解析' : '立即解析')) }}</text>
      </button>
      <view class="safe-note">{{ !quotaUnavailable && remainingParses === 0 ? '完整观看视频可获得 5 次解析' : '原画质 · 视频 / 图文' }}</view>
    </view>

    <view class="quota-card">
      <view class="quota-summary">
        <view class="quota-balance"><text class="quota-label">今日剩余解析</text><text class="quota-number">{{ quotaUnavailable ? '0' : remainingParses }}</text><text class="quota-unit">次</text></view>
        <text class="invite-link" @tap="openInviteList">我的邀请记录<text class="chevron">›</text></text>
      </view>
      <text class="quota-tip" @tap="retryQuota">{{ quotaUnavailable ? (quotaLoading ? '正在读取额度…' : '读取失败，点击重试') : '每日额度自动更新 · 点击刷新' }}</text>
      <view class="quota-actions">
        <!-- #ifdef MP-WEIXIN -->
        <button class="share-btn" :disabled="!shareReady" open-type="share" hover-class="share-btn--pressed"><text class="share-btn__icon">＋</text>{{ shareReward > 0 ? '分享得次数' : '分享给好友' }}<text class="chevron">›</text></button>
        <!-- #endif -->
        <!-- #ifndef MP-WEIXIN -->
        <button class="share-btn" :disabled="!shareReady" hover-class="share-btn--pressed" @tap="showShareInfo"><text class="share-btn__icon">＋</text>{{ shareReward > 0 ? '分享得次数' : '分享给好友' }}<text class="chevron">›</text></button>
        <!-- #endif -->
        <button class="video-reward-btn" :disabled="adBusy || isLoading" hover-class="video-reward-btn--pressed" @tap="showAd(false)"><text class="play-icon">▶</text>{{ adBusy ? '奖励处理中…' : '看视频 +5 次' }}</button>
      </view>
      <text v-if="shareError" class="reward-status reward-status--error" @tap="openInviteList">{{ shareError }}</text>
      <text v-else-if="shareReady && shareReward > 0" class="reward-status">好友每次进入奖励 +{{ shareReward }} 次，当日有效</text>
    </view>

    <button class="tutorial-btn" hover-class="tap-soft" @tap="tutorialVisible = true"><text>图文教程</text><text class="tutorial-btn__hint">三步学会复制链接与解析 ›</text></button>

    <!-- #ifdef MP-WEIXIN -->
    <view v-if="!homeAdHidden" class="ad-slot" aria-label="视频广告">
		<ad-custom :unit-id="homeVideoAdUnitId" @load="onAdload" @error="onAdError" @close="onAdclose"></ad-custom>
    </view>
    <!-- #endif -->

    <view class="tips-card">
      <text class="tips-card__title">使用贴士</text>
      <view class="tip-item"><view class="tip-number">1</view><text class="tip-text">复制分享链接</text></view>
      <view class="tip-item"><view class="tip-number">2</view><text class="tip-text">粘贴完整文案</text></view>
      <view class="tip-item"><view class="tip-number">3</view><text class="tip-text">解析并保存高清内容</text></view>
      <view class="frog frog-peek" aria-hidden="true"><image class="frog-sheet" src="/static/frog-peek.png" mode="scaleToFill" /></view>
    </view>
    <view class="page-footer">呱呱去水印 · 让保存更简单</view>

    <view v-if="tutorialVisible" class="tutorial-modal">
      <view class="tutorial-backdrop" @tap="tutorialVisible = false" @touchmove.stop.prevent></view>
      <view class="tutorial-panel" role="dialog" aria-label="图文教程">
        <view class="tutorial-header">
          <view><view class="tutorial-title">图文教程</view><view class="tutorial-subtitle">按顺序操作，上下滑动查看截图</view></view>
          <button class="tutorial-close" aria-label="关闭教程" @tap="tutorialVisible = false">关闭</button>
        </view>
        <scroll-view class="tutorial-scroll" scroll-y>
          <view v-for="(step, index) in tutorialSteps" :key="step.image" class="tutorial-step">
            <view class="tutorial-step__title"><text class="tutorial-step__number">{{ index + 1 }}</text><text>{{ step.title }}</text></view>
            <image lazy-load class="tutorial-image" :src="step.image" mode="widthFix" :aria-label="step.title" />
          </view>
          <view class="tutorial-footer">复制链接后，返回小程序粘贴并点击“立即解析”</view>
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script>
import { createAdController } from '../../services/ads.js'
import { FEATURES, PARSE_RESULT_KEY } from '../../config/features.js'
import { fetchQuota, untilMidnight, cacheQuota, readTotalParses, cacheTotalParses } from '../../services/quota.js'
import { pendingParse, finishPending, requestParse, responseBody, parseResponse, requestError } from '../../services/parse.js'
import { shareMessage, prepareShare, captureShareEntry, flushShareEntries } from '../../services/share.js'

export default {
  data() {
    return { linkText: '', textareaFocus: false, isLoading: false,
      totalParses: readTotalParses(),
      homeVideoAdUnitId: FEATURES.ads.homeVideoAdUnitId, homeAdHidden: false, topAdHidden: false,
      tutorialVisible: false,
      tutorialSteps: [
        { title: '在作品页面点击分享箭头', image: '/static/tutorial/step-1.jpg' },
        { title: '在分享面板点击“复制链接”', image: '/static/tutorial/step-2.jpg' },
        { title: '返回小程序，粘贴链接并点击“立即解析”', image: '/static/tutorial/step-3.jpg' }
      ],
      remainingParses: 0, quotaUnavailable: true, quotaLoading: false, dailyFreeParses: null, adBusy: false, shareReady: false, shareReward: 0, shareError: '' }
  },
  onLoad(options = {}) {
    this._disposed = false
    captureShareEntry(options)
    this.initRewardedAd()
  },
  onShow() {
    this._disposed = false
    this._visible = true
    this.totalParses = readTotalParses()
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
    if (!this._quotaRetryAfter || Date.now() >= this._quotaRetryAfter) {
      if (!this._lastQuotaRefresh || Date.now() - this._lastQuotaRefresh >= 30000 || this._quotaDay !== today) this.refreshQuota()
      else this.scheduleReset(this._quotaServerTime)
    }
    this.refreshSharing()
    this.recoverAdReward()
    // #ifdef MP-WEIXIN
    if (typeof wx !== 'undefined' && typeof wx.showShareMenu === 'function') {
      wx.showShareMenu({ menus: ['shareAppMessage'] })
    }
    // #endif
  },
  async onPullDownRefresh() {
    try { await Promise.all([this.refreshQuota(), this.refreshSharing(true)]) } finally { uni.stopPullDownRefresh() }
  },
  onHide() { this._visible = false; clearTimeout(this._quotaTimer) },
  onUnload() {
    this._disposed = true
    this._visible = false
    clearTimeout(this._quotaTimer)
    clearTimeout(this._focusTimer)
    if (this._adController) this._adController.dispose()
  },
  onShareAppMessage() { return shareMessage() },
  methods: {
    adLoad() { console.log('原生模板广告加载成功') },
    adError(error) {
      this.topAdHidden = true
      console.error('原生模板广告加载失败', error && error.detail ? error.detail : error)
    },
    adClose() { this.topAdHidden = true; console.log('原生模板广告关闭') },
    onAdplay() { console.log('视频贴片广告 播放开始') },
    onAdload() { console.log('视频贴片广告 加载成功') },
    onAdclose() { this.homeAdHidden = true; console.log('视频贴片广告 关闭成功') },
    onAdError(error) {
      this.homeAdHidden = true
      console.error('视频贴片广告 加载失败', error && error.detail ? error.detail : error)
    },
    openInviteList() {
      uni.navigateTo({ url: '/pages/invite-list/index', fail: () => uni.showToast({ title: '邀请记录打开失败，请重试', icon: 'none' }) })
    },
    async refreshSharing(force = false) {
      try {
        let profile = this._shareProfile
        if (force || !profile || Date.now() - this._lastShareRefresh >= 300000) {
          profile = await prepareShare()
          this._shareProfile = profile
          this._lastShareRefresh = Date.now()
        }
        if (this._disposed) return
        this.shareReward = profile.reward_per_friend
        this.shareReady = true
        this.shareError = ''
      } catch (error) {
        if (!this._disposed) { this.shareReady = false; this.shareError = '邀请配置读取失败，可进入记录页重试' }
      }
      try {
        const accepted = await flushShareEntries()
        if (accepted && !this._disposed) uni.showToast({ title: '已记录邀请', icon: 'success' })
      } catch (error) {
        if (!this._disposed) this.shareError = '邀请暂未上报成功，返回页面将自动重试'
      }
    },
    applyQuota(quota) {
      cacheQuota(quota)
      this._quotaGeneration = (this._quotaGeneration || 0) + 1
      this.quotaLoading = false
      this.remainingParses = quota.remaining
      this.dailyFreeParses = quota.initial_quota
      this.quotaUnavailable = false
      this._quotaRetryAfter = 0
      this.totalParses = cacheTotalParses(quota.total_parses)
      this._lastQuotaRefresh = Date.now()
      this._quotaDay = quota.quota_date
      this._quotaServerTime = quota.server_time
      this.scheduleReset(quota.server_time)
    },
    retryQuota() { if (!this.quotaLoading) return this.refreshQuota() },
    async refreshQuota() {
      if (this.quotaLoading || this._disposed) return
      clearTimeout(this._quotaTimer)
      this._quotaTimer = null
      const generation = this._quotaGeneration = (this._quotaGeneration || 0) + 1
      this.quotaLoading = true
      try {
        const quota = await fetchQuota()
        if (this._disposed || generation !== this._quotaGeneration) return
        this.applyQuota(quota)
      } catch (_) {
        if (this._disposed || generation !== this._quotaGeneration) return
        this.quotaLoading = false
        this.quotaUnavailable = true
        // 服务层已有限重试；返回前台至少间隔 30 秒再试，手动刷新可立即重试。
        this._quotaRetryAfter = Date.now() + 30000
        clearTimeout(this._quotaTimer)
        this._quotaTimer = null
      }
    },
    scheduleReset(serverTime) {
      clearTimeout(this._quotaTimer)
      this._quotaTimer = null
      if (!this._visible || this._disposed || !Number.isFinite(Date.parse(serverTime))) return
      const elapsed = serverTime === this._quotaServerTime && Number.isFinite(this._lastQuotaRefresh)
        ? Math.max(0, Date.now() - this._lastQuotaRefresh) : 0
      this._quotaTimer = setTimeout(() => this.refreshQuota(), Math.max(0, untilMidnight(serverTime) - elapsed) + 100)
    },
    clearInput() {
      this.linkText = ''
      this.$nextTick(() => {
        this.textareaFocus = true
        clearTimeout(this._focusTimer)
        this._focusTimer = setTimeout(() => { this.textareaFocus = false }, 80)
      })
    },
    showShareInfo() {
      uni.showModal({ title: '分享邀请说明', content: this.shareReady ? ('好友每次通过分享进入，邀请人奖励 ' + this.shareReward + ' 次。同一好友重新打开可再次奖励，网络重试不重复奖励，自邀不奖励，奖励当日有效。') : '分享配置暂未就绪，请返回页面重试。', showCancel: false })
    },
    initRewardedAd() {
      // #ifdef MP-WEIXIN
      try { this.adController().init() }
      catch (error) { console.error('激励视频广告初始化失败', error) }
      // #endif
    },
    adController() {
      if (!this._adController) this._adController = createAdController({
        busy: value => { if (!this._disposed) this.adBusy = value },
        quota: quota => {
          if (this._disposed) return
          this._quotaGeneration = (this._quotaGeneration || 0) + 1
          this.quotaLoading = false
          this.remainingParses = quota.remaining
          this.dailyFreeParses = quota.initial_quota
          this.quotaUnavailable = false
          this.scheduleReset(quota.server_time)
        },
        message: title => { if (!this._disposed) uni.showToast({ title, icon: 'none' }) }
      })
      return this._adController
    },
    async recoverAdReward() {
      try { await this.adController().recover() }
      catch (error) { if (!this._disposed) uni.showToast({ title: error.message, icon: 'none' }) }
    },
    async showAd(exhausted = false) {
      if (this.adBusy || (this.isLoading && !exhausted)) return false
      try {
        const confirmed = await new Promise(resolve => uni.showModal({
          title: exhausted ? '解析次数已用完' : '看视频得次数', content: '完整观看一段视频广告，即可获得 5 次解析。中途关闭不增加次数。',
          confirmText: '观看视频', cancelText: '暂不观看',
          success: result => resolve(result.confirm), fail: () => resolve(false)
        }))
        if (!confirmed || this._disposed) return false
        const quota = await this.adController().show()
        return !!quota && quota.remaining > 0
      } catch (error) {
        if (!this._disposed) uni.showModal({ title: '激励广告提示', content: error.message, showCancel: false })
        return false
      }
    },
    async handleAnalyze() {
      if (this.isLoading || this.adBusy) return
      const url = (this.linkText || '').trim()
      if (!url) { uni.showToast({ title: '请先粘贴分享链接', icon: 'none' }); return }
      if (!/https?:\/\/[^\s]+/i.test(url)) {
        uni.showToast({ title: '请输入有效的 http/https 分享链接', icon: 'none' }); return
      }
      // Never block a replay using a cached zero balance: the request may
      // have committed successfully before the client timed out.
      this.isLoading = true
      try {
        for (let attempt = 0; attempt < 2; attempt++) {
          const pending = pendingParse(url)
          uni.removeStorageSync(PARSE_RESULT_KEY)
          const response = await requestParse(pending)
          if (this._disposed) return
          const body = responseBody(response)
          if (body) {
            this.totalParses = cacheTotalParses(body.total_parses)
            if (body.request_id === pending.request_id && body.quota) this.applyQuota(body.quota)
          }
          // Definitive, persisted failure: a later attempt may use a new ID.
          if (body && body.request_id === pending.request_id && body.success === false && body.error_code) {
            finishPending(pending.request_id)
          }
          // Let the server resolve old timed-out parses before deciding an ad
          // is needed. A cached zero must never discard a committed result.
          if (response.statusCode === 429 && body && body.error_code === 'QUOTA_EXHAUSTED' && body.request_id === pending.request_id) {
            this.remainingParses = 0
            this.quotaUnavailable = false
            if (attempt === 0 && await this.showAd(true) && !this._disposed) {
              if ((this.linkText || '').trim() !== url) return
              continue
            }
            return
          }
          const result = parseResponse(response)
          uni.setStorageSync(PARSE_RESULT_KEY, result)
          uni.navigateTo({
            url: '/pages/result/result',
            success: () => finishPending(pending.request_id),
            fail: () => uni.showToast({ title: '结果页打开失败，请重试', icon: 'none' })
          })
          return
        }
      } catch (error) {
        if (!this._disposed) uni.showModal({ title: '解析提示', content: requestError(error), showCancel: false })
      } finally {
        if (!this._disposed) { this.isLoading = false }
      }
    }
  }
}
</script>

<style>
.top-ad-slot { margin: 16rpx 0; }
.total-parses { display: flex; align-items: center; justify-content: center; gap: 10rpx; padding: 18rpx 12rpx 0; color: #71877c; font-size: 22rpx; line-height: 1.5; }
.total-parses__dot { width: 10rpx; height: 10rpx; border-radius: 50%; background: #23ad65; }
.total-parses__number { color: #16834e; font-size: 26rpx; font-weight: 700; font-variant-numeric: tabular-nums; }
.support-note { padding: 14rpx 24rpx 0; color: #7a8c80; font-size: 22rpx; line-height: 1.8; text-align: center; overflow-wrap: break-word; }

page { background: #f2fbf6; color: #17382e; }
.page { box-sizing: border-box; min-height: 100vh; padding: 0 26rpx 20rpx; padding-bottom: calc(20rpx + constant(safe-area-inset-bottom)); padding-bottom: calc(20rpx + env(safe-area-inset-bottom)); background: radial-gradient(ellipse at 90% 6%, #e5fadd 0%, rgba(229,250,221,0) 32%), #f2fbf6; }
.hero { height: 190rpx; position: relative; display: flex; align-items: flex-start; padding: 38rpx 16rpx 0; box-sizing: border-box; overflow: hidden; }
.hero-copy { position: relative; z-index: 2; }
.hero-title { font-size: 42rpx; line-height: 1.4; font-weight: 800; letter-spacing: -1rpx; color: #083d2c; white-space: nowrap; }
.underline { position: relative; }
.underline::after { content: ''; position: absolute; height: 7rpx; background: #bce98d; left: 0; right: 0; bottom: -3rpx; border-radius: 8rpx; transform: rotate(-3deg); z-index: -1; }
.hero-desc { display: block; margin-top: 16rpx; font-size: 24rpx; line-height: 1.5; color: #71877c; }
.frog { position: absolute; overflow: hidden; pointer-events: none; }
.frog-sheet { display: block; width: 100%; height: 100%; }
.frog-hello { width: 206rpx; height: 139rpx; right: -8rpx; bottom: 0; border-radius: 80rpx 80rpx 0 0; }
.input-card, .quota-card, .tips-card { background: #fff; border-radius: 28rpx; box-shadow: 0 8rpx 30rpx rgba(31,85,57,.025); }
.input-card { position: relative; z-index: 3; padding: 22rpx 24rpx 0; }
.input-card__header { display: flex; align-items: center; justify-content: space-between; min-height: 48rpx; margin-bottom: 16rpx; }
.input-card__title { font-size: 29rpx; font-weight: 700; color: #172d29; }
.clear-btn { display: flex; align-items: center; padding: 10rpx 0 10rpx 24rpx; color: #8a989f; font-size: 24rpx; }
.icon-link { width: 30rpx; height: 30rpx; position: relative; transform: rotate(-44deg); margin-right: 12rpx; }
.icon-link::before, .icon-link::after { content: ''; position: absolute; width: 23rpx; height: 13rpx; border: 3rpx solid #71858a; border-radius: 12rpx; box-sizing: border-box; left: 4rpx; }
.icon-link::before { top: 3rpx; }
.icon-link::after { bottom: 2rpx; }
.tap-soft { opacity: .65; }
.input-area { background: #f0f9f5; border-radius: 22rpx; overflow: hidden; }
.link-textarea { box-sizing: border-box; width: 100%; height: 148rpx; padding: 24rpx 22rpx 10rpx; color: #3e5349; font-size: 25rpx; line-height: 1.8; }
.link-textarea__placeholder { color: #9aada6; font-size: 25rpx; line-height: 1.8; }
.paste-note { display: flex; align-items: center; padding: 8rpx 24rpx 20rpx; font-size: 21rpx; color: #9aaaa5; line-height: 30rpx; }
.clipboard-icon { width: 20rpx; height: 24rpx; border: 2rpx solid #879b96; border-radius: 4rpx; margin-right: 12rpx; position: relative; box-sizing: border-box; }
.clipboard-icon::before { content: ''; position: absolute; width: 9rpx; height: 5rpx; top: -5rpx; left: 3rpx; border: 2rpx solid #879b96; border-radius: 3rpx; background: #f0f9f5; }
.platforms { display: flex; align-items: center; justify-content: space-between; margin-top: 14rpx; }
.platforms__tag { display: flex; align-items: center; justify-content: center; height: 46rpx; padding: 0 12rpx; border-radius: 28rpx; font-size: 21rpx; color: #23332d; white-space: nowrap; box-sizing: border-box; }
.platforms__tag--douyin, .platforms__tag--red { background: #fff0f3; }
.platforms__tag--kuaishou { background: #fff5e8; }
.platforms__tag--zuiyou { background: #fffbe3; }
.platforms__tag--pipixia { background: #fff0f4; }
.platform-mark { margin-right: 8rpx; font-size: 29rpx; line-height: 1; font-weight: 700; }
.platform-mark--music { color: #17282a; text-shadow: -2rpx 0 #66d9d8, 2rpx 1rpx #f27b9a; }
.platform-mark--red { color: #fff; background: #ff3e60; padding: 4rpx; font-size: 19rpx; border-radius: 7rpx; }
.platform-mark--smile { color: #b69200; }
.platform-mark--shrimp { color: #e67a94; }
.camera-icon { width: 24rpx; height: 20rpx; border: 3rpx solid #ff8829; border-radius: 5rpx; box-sizing: border-box; margin-right: 8rpx; position: relative; }
.camera-icon::before, .camera-icon::after { content: ''; position: absolute; top: -12rpx; width: 9rpx; height: 9rpx; border: 2rpx solid #ff8829; border-radius: 50%; box-sizing: border-box; }
.camera-icon::before { left: -3rpx; }
.camera-icon::after { right: -3rpx; }
.analyze-btn { display: flex; align-items: center; justify-content: center; width: 100%; height: 78rpx; border-radius: 22rpx; padding: 0; margin: 20rpx 0 0; background: linear-gradient(110deg, #43d438, #0cba6b); color: #fff; box-shadow: 0 6rpx 18rpx rgba(34,178,89,.13), inset 0 2rpx 2rpx rgba(255,255,255,.2); font-size: 32rpx; line-height: 1.4; font-weight: 700; border: none; }
.analyze-btn::after, .share-btn::after { border: none; }
.analyze-btn[disabled], .analyze-btn--loading { background: linear-gradient(110deg, #43d438, #0cba6b); color: #fff; opacity: .72; }
.analyze-btn--pressed { transform: translateY(2rpx); opacity: .9; }
.sparkle-icon { position: relative; width: 34rpx; height: 38rpx; margin-right: 16rpx; }
.sparkle-main { position: absolute; font-size: 33rpx; line-height: 1; bottom: 0; left: 0; }
.sparkle-small { position: absolute; font-size: 19rpx; line-height: 1; top: 0; right: -2rpx; }
.loading-spinner { width: 28rpx; height: 28rpx; margin-right: 16rpx; border: 4rpx solid rgba(255,255,255,.4); border-top-color: #fff; border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.safe-note { display: flex; justify-content: center; align-items: center; height: 56rpx; color: #8a989f; font-size: 22rpx; }
.quota-card { margin-top: 16rpx; padding: 18rpx 24rpx; }
.quota-summary, .quota-detail { display: flex; align-items: center; justify-content: space-between; }
.quota-balance { display: flex; align-items: baseline; }
.quota-label { font-size: 24rpx; color: #253d32; font-weight: 600; }
.quota-number { font-size: 50rpx; line-height: 1.2; color: #16ac49; font-weight: 800; font-variant-numeric: tabular-nums; margin: 0 10rpx 0 18rpx; }
.quota-unit { font-size: 24rpx; color: #34463f; }
.quota-actions { display: flex; align-items: center; margin-top: 14rpx; padding-bottom: 5rpx; }
.quota-actions .share-btn { flex: 1; margin: 0 16rpx 0 0; }
.video-reward-btn { display: flex; align-items: center; justify-content: center; flex: 1; margin: 0; padding: 0 10rpx; height: 64rpx; border: 2rpx solid #3bb26c; border-radius: 16rpx; background: #effbf2; color: #188b4a; font-size: 24rpx; font-weight: 700; line-height: 1.2; box-sizing: border-box; box-shadow: 0 4rpx 0 #ccebd6; }
.video-reward-btn::after { border: none; }
.video-reward-btn[disabled] { color: #91aa96; background: #f1f6f1; border-color: #d5e4d8; }
.video-reward-btn--pressed { transform: translateY(3rpx); box-shadow: none; }
.play-icon { margin-right: 10rpx; font-size: 21rpx; }
/* A solid raised action button, visually distinct from platform chips. */
.share-btn { display: flex; align-items: center; justify-content: center; flex-shrink: 0; margin: 0 0 0 12rpx; padding: 0 20rpx; height: 64rpx; border: 2rpx solid #139247; border-radius: 16rpx; font-size: 24rpx; line-height: 1.2; color: #fff; background: linear-gradient(150deg, #38bd68, #159b53); font-weight: 700; box-shadow: 0 5rpx 0 #118342, 0 8rpx 16rpx rgba(21,155,83,.18); box-sizing: border-box; }
.share-btn::after { border: none; }
.share-btn__icon { display: flex; align-items: center; justify-content: center; width: 27rpx; height: 27rpx; border: 2rpx solid rgba(255,255,255,.7); border-radius: 7rpx; margin-right: 9rpx; font-size: 24rpx; font-weight: 500; }
.share-btn--pressed { transform: translateY(4rpx); box-shadow: 0 1rpx 0 #118342; opacity: .9; }
.share-btn[disabled] { color: #e5f1e8; background: #83b795; border-color: #74a786; box-shadow: 0 4rpx 0 #6b9e7f; }
.chevron { margin-left: 10rpx; font-size: 30rpx; line-height: 1; }
.quota-detail { margin-top: 12rpx; }
.quota-tip { display: block; margin-top: 8rpx; font-size: 20rpx; color: #929f99; line-height: 1.6; padding: 4rpx 0; }
.invite-link { display: flex; align-items: center; font-size: 21rpx; color: #899a93; padding: 4rpx 0 4rpx 10rpx; white-space: nowrap; }
.reward-status { display: block; font-size: 19rpx; color: #8fa397; line-height: 1.5; margin-top: 8rpx; }
.reward-status--error { color: #a17948; }
.ad-slot { overflow: hidden; margin: 18rpx 6rpx 0; border-radius: 20rpx; background: #edf5ef; }
.home-video-ad { display: block; width: 100%; height: 376rpx; }
.tips-card { position: relative; overflow: hidden; padding: 20rpx 24rpx; margin-top: 18rpx; }
.tips-card__title { display: block; font-size: 29rpx; line-height: 42rpx; font-weight: 700; color: #19392b; margin-bottom: 8rpx; }
.tip-item { display: flex; align-items: center; min-height: 52rpx; position: relative; z-index: 1; }
.tip-number { flex: 0 0 34rpx; width: 34rpx; height: 34rpx; display: flex; align-items: center; justify-content: center; background: #e3f9e0; color: #25a549; border-radius: 50%; font-size: 23rpx; font-weight: 600; margin-right: 18rpx; }
.tip-text { font-size: 23rpx; line-height: 1.6; color: #5d7166; }
.frog-peek { width: 202rpx; height: 118rpx; right: -6rpx; bottom: 0; z-index: 0; }
.page-footer { padding: 20rpx 0 0; text-align: center; color: #a3b1a8; font-size: 19rpx; line-height: 1.6; }
@media (max-width: 350px) {
  .hero-title { font-size: 39rpx; }
  .hero-desc { font-size: 22rpx; }
  .frog-hello { width: 190rpx; height: 128rpx; }
  .platforms__tag { padding: 0 8rpx; }
  .share-btn { padding: 0 16rpx; font-size: 21rpx; }
  .quota-number { margin-left: 12rpx; }
  .frog-peek { width: 175rpx; height: 102rpx; }
}
.tutorial-btn { display: flex; align-items: center; justify-content: space-between; margin: 24rpx 0 0; padding: 20rpx 22rpx; border-radius: 18rpx; background: #edf7e9; color: #164b39; font-size: 27rpx; line-height: 1.5; font-weight: 600; text-align: left; }
.tutorial-btn::after, .tutorial-close::after { border: none; }
.tutorial-btn__hint { margin-left: 14rpx; font-size: 21rpx; color: #64836c; font-weight: 400; }
.tutorial-modal { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center; }
.tutorial-backdrop { position: absolute; inset: 0; background: rgba(10, 32, 24, .6); }
.tutorial-panel { position: relative; width: 690rpx; max-width: 92vw; overflow: hidden; border-radius: 30rpx; background: #f5faf6; }
.tutorial-header { display: flex; align-items: center; justify-content: space-between; padding: 26rpx 24rpx; background: #fff; }
.tutorial-title { color: #0b4032; font-size: 32rpx; font-weight: 700; }
.tutorial-subtitle { margin-top: 8rpx; color: #7a8c80; font-size: 22rpx; }
.tutorial-close { flex-shrink: 0; margin: 0 0 0 16rpx; padding: 12rpx 20rpx; border-radius: 30rpx; background: #edf7e9; color: #164b39; font-size: 24rpx; line-height: 1.5; }
.tutorial-scroll { height: 68vh; }
.tutorial-step { padding: 24rpx 20rpx 0; }
.tutorial-step__title { display: flex; align-items: center; margin-bottom: 16rpx; font-size: 25rpx; line-height: 1.6; color: #164b39; font-weight: 600; }
.tutorial-step__number { display: flex; flex-shrink: 0; align-items: center; justify-content: center; width: 40rpx; height: 40rpx; margin-right: 12rpx; border-radius: 50%; background: #dff2d5; color: #16834e; }
.tutorial-image { display: block; width: 100%; border-radius: 16rpx; }
.tutorial-footer { padding: 24rpx 24rpx 32rpx; color: #7a8c80; font-size: 22rpx; line-height: 1.7; text-align: center; }
</style>
