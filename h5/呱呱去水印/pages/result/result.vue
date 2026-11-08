<template>
  <view class="page">
    <view class="header">
      <view class="header-copy">
        <view class="header-title"><text class="underline">{{ mediaList.length ? '解析成功' : '解析结果' }}</text></view>
        <view class="header-desc">{{ resultSummary }}</view>
      </view>
      <view v-if="mediaList.length" class="frog frog-success" aria-hidden="true"><image class="frog-sheet" src="/static/frog-success.png" mode="scaleToFill" /></view>
    </view>

    <view v-if="mediaList.length" class="grid">
      <view v-for="item in mediaList" :key="item.id" class="card">
        <view class="media-box">
          <image v-if="item.type === 'image'" class="image" :src="item.url" mode="aspectFill" lazy-load @tap="previewImage(item)" />
          <video v-else class="video" :src="item.url" :poster="item.cover" :show-center-play-btn="true" :controls="true" object-fit="contain" />
        </view>
        <view class="card-info">
          <view class="type">
            <view class="media-icon" :class="{ 'media-icon--video': item.type === 'video' }"><view class="media-icon__detail"></view></view>
            <text>{{ item.type === 'video' ? '视频' : '图片' }}</text>
          </view>
          <text class="index">{{ formatNumber(item.id) }}</text>
        </view>
        <button class="download" hover-class="download--pressed" :disabled="!!savingId"
          open-type="agreePrivacyAuthorization" @agreeprivacyauthorization="saveAfterPrivacy(item)">
          <view class="download-icon"><view class="download-icon__arrow"></view></view>
          <text>{{ savingId === item.id ? '正在保存…' : (privacyNeeded ? '同意隐私协议并保存' : (item.type === 'video' ? '保存视频' : '保存图片')) }}</text>
        </button>
      </view>
    </view>
    <view v-if="mediaList.length" class="privacy-note">保存前请阅读<text @tap="openPrivacyContract">《小程序隐私保护指引》</text></view>

    <view v-if="!mediaList.length" class="empty">
      <view class="empty-leaf"></view>
      <view class="empty-title">暂无解析内容</view>
      <view class="empty-desc">返回输入页，粘贴分享链接后再试试吧</view>
    </view>
  </view>
</template>

<script>
import { PARSE_RESULT_KEY } from '../../config/features.js'
import { saveMediaToAlbum, mediaSaveError, ensureMediaSaveAuthorization } from '../../services/media-save.js'

export default {
  data() {
    return {
      mediaList: [],
      savingId: 0, privacyNeeded: true
    }
  },
  onLoad() {
    const result = uni.getStorageSync(PARSE_RESULT_KEY)
    if (!result || typeof result !== 'object') return
    const items = []
    if (typeof result.video_url === 'string' && result.video_url.trim()) {
      items.push({ type: 'video', url: result.video_url, downloadUrl: result.download_url || '', cover: result.cover || '' })
    }
    if (Array.isArray(result.image_urls)) {
      result.image_urls.forEach((url, index) => {
        if (typeof url !== 'string' || !url.trim()) return
        const downloadUrl = Array.isArray(result.image_download_urls) ? result.image_download_urls[index] : ''
        items.push({ type: 'image', url, downloadUrl: downloadUrl || '' })
      })
    }
    this.mediaList = items.map((item, index) => ({ ...item, id: index + 1 }))
    // #ifdef MP-WEIXIN
    if (this.mediaList.length && typeof wx !== 'undefined' && typeof wx.createInterstitialAd === 'function') {
      try {
        this._interstitialAd = wx.createInterstitialAd({ adUnitId: 'adunit-22fd03b92ffb2e30' })
        this._interstitialLoad = () => console.log('插屏广告加载成功')
        this._interstitialError = error => console.error('插屏广告加载失败', error)
        this._interstitialClose = () => console.log('插屏广告关闭')
        this._interstitialAd.onLoad(this._interstitialLoad)
        this._interstitialAd.onError(this._interstitialError)
        this._interstitialAd.onClose(this._interstitialClose)
      } catch (error) { console.error('插屏广告初始化失败', error) }
    }
    // #endif
  },
  onReady() {
    // #ifdef MP-WEIXIN
    if (!this._interstitialAd || this._interstitialShown || this._disposed || this._resultVisible === false) return
    this._interstitialShown = true
    try {
      Promise.resolve(this._interstitialAd.show()).catch(error => console.error('插屏广告显示失败', error))
    } catch (error) { console.error('插屏广告显示失败', error) }
    // #endif
  },
  onHide() { this._resultVisible = false },
  onShow() {
    this._resultVisible = true
    if (typeof wx !== 'undefined' && typeof wx.getPrivacySetting === 'function') {
      wx.getPrivacySetting({ success: state => { this.privacyNeeded = !!state.needAuthorization } })
    }
  },
  onUnload() {
    this._disposed = true
    this._resultVisible = false
    const ad = this._interstitialAd
    if (ad) {
      if (typeof ad.offLoad === 'function') ad.offLoad(this._interstitialLoad)
      if (typeof ad.offError === 'function') ad.offError(this._interstitialError)
      if (typeof ad.offClose === 'function') ad.offClose(this._interstitialClose)
      if (typeof ad.destroy === 'function') ad.destroy()
      this._interstitialAd = null
    }
  },
  computed: {
    resultSummary() {
      const images = this.mediaList.filter(item => item.type === 'image').length
      const videos = this.mediaList.filter(item => item.type === 'video').length
      if (!images && !videos) return '美好内容，等你来发现'
      const counts = []
      if (images) counts.push(`${images} 张图片`)
      if (videos) counts.push(`${videos} 个视频`)
      return `共 ${counts.join('、')}，原画质保存`
    }
  },
  methods: {
    saveAfterPrivacy(item) {
      // This callback is emitted by WeChat after the native consent button is clicked.
      this.privacyNeeded = false
      return this.downloadMedia(item)
    },
    openPrivacyContract() {
      if (typeof wx !== 'undefined' && typeof wx.openPrivacyContract === 'function') {
        wx.openPrivacyContract({ fail: () => uni.showToast({ title: '隐私指引暂时无法打开，请稍后重试', icon: 'none' }) })
      }
    },
    formatNumber(num) {
      return num < 10 ? `0${num}` : num
    },

    previewImage(item) {
      const urls = this.mediaList
        .filter(item => item.type === 'image')
        .map(item => item.url)

      uni.previewImage({
        current: item.url,
        urls
      })
    },

    async downloadMedia(item) {
      if (this.savingId) return
      if (!item || !item.url) {
        uni.showToast({ title: '媒体地址无效', icon: 'none' })
        return
      }

      // #ifdef H5
      uni.showToast({ title: '请在微信小程序内保存到相册', icon: 'none' })
      return
      // #endif

      // #ifndef H5
      this.savingId = item.id
      try {
        // Direct WeChat native scope request before downloading the media.
        if (!await ensureMediaSaveAuthorization()) return
        if (this._disposed) return
        uni.showLoading({ title: '正在转存文件', mask: true })
        try {
          await saveMediaToAlbum(item, { onStage: title => uni.showLoading({ title, mask: true }) })
        } finally { uni.hideLoading() }
        if (!this._disposed) uni.showToast({ title: '已保存到相册', icon: 'success', duration: 2000 })
      } catch (error) {
        if (!this._disposed) uni.showModal({ title: '保存提示', content: mediaSaveError(error), showCancel: false })
      } finally {
        this.savingId = 0
      }
      // #endif
    }
  }
}
</script>

<style scoped>
.page { box-sizing: border-box; min-height: 100vh; padding: 0 22rpx 40rpx; background: #f5faf6; color: #123b31; }
.header { height: 220rpx; position: relative; padding: 40rpx 10rpx 0; box-sizing: border-box; }
.header-copy { position: relative; z-index: 1; }
.header-title { font-size: 53rpx; line-height: 1.35; font-weight: 800; letter-spacing: 1rpx; color: #063e30; }
.underline { position: relative; z-index: 1; }
.underline::after { content: ''; position: absolute; width: 100rpx; height: 8rpx; background: #bde59e; left: -3rpx; bottom: -4rpx; border-radius: 8rpx; transform: rotate(-4deg); z-index: -1; }
.header-desc { margin-top: 20rpx; color: #73808d; font-size: 28rpx; line-height: 1.5; letter-spacing: 1rpx; }
.frog { position: absolute; overflow: hidden; pointer-events: none; }
.frog-sheet { display: block; width: 100%; height: 100%; }
.frog-success { width: 195rpx; height: 176rpx; right: 16rpx; top: 16rpx; }

.grid { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; }
.card { width: calc(50% - 9rpx); box-sizing: border-box; margin-bottom: 20rpx; padding: 0 0 14rpx; background: #fff; border-radius: 25rpx; overflow: hidden; box-shadow: 0 8rpx 24rpx rgba(34,82,51,.035); }
.media-box { width: 100%; height: 336rpx; overflow: hidden; background: #edf3ef; }
.image, .video { width: 100%; height: 336rpx; display: block; }
.card-info { height: 72rpx; padding: 0 20rpx; box-sizing: border-box; display: flex; align-items: center; justify-content: space-between; }
.type { display: flex; align-items: center; color: #344b53; font-size: 24rpx; gap: 13rpx; }
.media-icon { width: 26rpx; height: 24rpx; border: 3rpx solid #219951; border-radius: 5rpx; box-sizing: border-box; position: relative; overflow: hidden; }
.media-icon::before { content: ''; position: absolute; width: 5rpx; height: 5rpx; border-radius: 50%; background: #219951; left: 3rpx; top: 3rpx; }
.media-icon__detail { width: 14rpx; height: 14rpx; position: absolute; border-left: 3rpx solid #219951; border-top: 3rpx solid #219951; transform: rotate(45deg); top: 10rpx; left: 5rpx; border-radius: 2rpx; }
.media-icon--video { overflow: visible; width: 25rpx; }
.media-icon--video::before { border-radius: 2rpx; background: transparent; border: 3rpx solid #219951; width: 5rpx; height: 10rpx; top: 3rpx; left: 20rpx; transform: skewY(-20deg); }
.media-icon--video .media-icon__detail { display: none; }
.index { font-size: 23rpx; line-height: 1; color: #96a3b0; font-weight: 400; }
.download { height: 70rpx; margin: 0 14rpx; border-radius: 22rpx; display: flex; align-items: center; justify-content: center; gap: 20rpx; color: #084434; background: #edf7e9; font-size: 26rpx; line-height: 1.4; font-weight: 600; padding: 0; }
.download::after { border: 0; }
.privacy-note { margin: 12rpx 6rpx; color: #73808d; font-size: 23rpx; line-height: 1.7; }
.privacy-note text { color: #16834e; }
.download--pressed { background: #def0d7; opacity: .8; }
.download-icon { position: relative; width: 28rpx; height: 25rpx; box-sizing: border-box; border: 3rpx solid #084434; border-top: 0; border-radius: 0 0 5rpx 5rpx; margin-top: 7rpx; }
.download-icon__arrow { position: absolute; width: 3rpx; height: 21rpx; background: #084434; border-radius: 3rpx; left: 10rpx; top: -10rpx; }
.download-icon__arrow::after { content: ''; position: absolute; width: 10rpx; height: 10rpx; border-right: 3rpx solid #084434; border-bottom: 3rpx solid #084434; left: -5rpx; bottom: 1rpx; transform: rotate(45deg); border-radius: 1rpx; }
.empty { background: #fff; padding: 80rpx 28rpx; text-align: center; border-radius: 32rpx; box-shadow: 0 8rpx 24rpx rgba(34,82,51,.03); }
.empty-leaf { height: 44rpx; width: 32rpx; background: #96d375; border-radius: 85% 0 85% 0; margin: 0 auto 28rpx; transform: rotate(15deg); }
.empty-title { color: #164b39; font-size: 30rpx; font-weight: 600; }
.empty-desc { color: #8b9a96; margin-top: 18rpx; font-size: 24rpx; line-height: 1.6; }
</style>
