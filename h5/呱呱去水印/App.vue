<script>
import { captureAppEntry, flushShareEntries } from './services/share.js'
export default {
  onLaunch(options) { captureAppEntry(options, 'launch') },
  onShow(options) {
    captureAppEntry(options)
    // 前后端身份未就绪或请求失败时保留邀请，后续前台进入自动重试。
    flushShareEntries().catch(() => {})
  }
}
</script>
<style>
/* 每个页面公共样式 */
</style>
