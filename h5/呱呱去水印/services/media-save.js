import { downloadThroughCloud } from './cloud-download.js'

const FORMATS = {
  jpg: ['image', 'jpg'], jpeg: ['image', 'jpg'], png: ['image', 'png'],
  gif: ['image', 'gif'], webp: ['image', 'webp'], bmp: ['image', 'bmp'],
  tif: ['image', 'tiff'], tiff: ['image', 'tiff'], heic: ['image', 'heic'],
  heif: ['image', 'heif'], avif: ['image', 'avif'],
  mp4: ['video', 'mp4'], m4v: ['video', 'mp4'], mov: ['video', 'mov'],
  webm: ['video', 'webm'], avi: ['video', 'avi'], '3gp': ['video', '3gp']
}
const MIMES = {
  'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png', 'image/gif': 'gif',
  'image/webp': 'webp', 'image/bmp': 'bmp', 'image/tiff': 'tiff',
  'image/heic': 'heic', 'image/heif': 'heif', 'image/avif': 'avif',
  'video/mp4': 'mp4', 'video/x-m4v': 'mp4', 'video/quicktime': 'mov',
  'video/webm': 'webm', 'video/x-msvideo': 'avi', 'video/3gpp': '3gp'
}
let sequence = 0
function failure(code, message) { const error = new Error(message); error.code = code; error.isMediaSaveError = true; return error }
function format(ext) { return { type: FORMATS[ext][0], ext: FORMATS[ext][1] } }
function headerValue(headers, name) {
  const key = Object.keys(headers || {}).find(key => key.toLowerCase() === name)
  return key ? String(headers[key]) : ''
}
function ascii(bytes, start, end) { return String.fromCharCode(...bytes.slice(start, end)) }

function downloadHost(url) {
  const match = /^https?:\/\/([^/?#]+)/i.exec(String(url))
  return match ? match[1].split('@').pop() : '未知域名'
}

// 保存原始错误以区分正式版域名校验、TLS、超时与 CDN 连接问题。
// 签名链接的查询参数不进入提示或日志。
export function mediaDownloadError(error, url) {
  const reason = String(error && (error.errMsg || error.message) || 'downloadFile:fail')
    .replace(/(https?:\/\/[^\s?]+)\?[^\s]*/gi, '$1?[参数已隐藏]').slice(0, 240)
  let code = 'DOWNLOAD_NETWORK', message = '媒体下载连接失败，请检查网络或重新解析后重试'
  if (/url not in domain list|not in.*domain|domain.*not.*(?:allow|config)|不在.*合法域名/i.test(reason)) {
    code = 'DOWNLOAD_DOMAIN'
    message = '媒体域名未通过微信下载域名校验，请配置 downloadFile 合法域名'
  } else if (/ssl|tls|certificate|cert authority|证书/i.test(reason)) {
    code = 'DOWNLOAD_TLS'
    message = '媒体服务器 HTTPS 证书或 TLS 连接失败'
  } else if (/timeout|timed out|超时/i.test(reason)) {
    code = 'DOWNLOAD_TIMEOUT'
    message = '媒体下载超时，请检查网络或重新解析后重试'
  } else if (/resolve|dns|name_not_resolved|host not found/i.test(reason)) {
    code = 'DOWNLOAD_DNS'
    message = '无法解析媒体下载域名，请检查网络或重新解析'
  }
  const result = failure(code, `${message}\n下载域名：${downloadHost(url)}\n原始错误：${reason}`)
  result.downloadHost = downloadHost(url)
  result.originalMessage = reason
  return result
}

// 内容签名优先：文件名或 Content-Type 错标为 JSON 时，仍识别真实二进制媒体。
export function detectMediaFormat(bytes, headers = {}, url = '') {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  if (!b.length) throw failure('EMPTY_FILE', '下载文件为空，请重新解析')
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return format('jpg')
  if (b.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((v, i) => b[i] === v)) return format('png')
  if (/^GIF8[79]a$/.test(ascii(b, 0, 6))) return format('gif')
  if (ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 12) === 'WEBP') return format('webp')
  if (ascii(b, 0, 2) === 'BM') return format('bmp')
  if (ascii(b, 0, 4) === 'II*\x00' || ascii(b, 0, 4) === 'MM\x00*') return format('tiff')
  if (b.length >= 12 && ascii(b, 4, 8) === 'ftyp') {
    const brand = ascii(b, 8, 12)
    const compatible = ascii(b, 8, Math.min(b.length, 64))
    if (/avif|avis/.test(compatible)) return format('avif')
    if (/heic|heix|hevc|hevx/.test(compatible)) return format('heic')
    if (/mif1|msf1/.test(compatible)) return format('heif')
    if (brand === 'qt  ') return format('mov')
    if (/^3g/.test(brand)) return format('3gp')
    return format('mp4')
  }
  if (ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 12) === 'AVI ') return format('avi')
  const prefix = ascii(b, 0, Math.min(b.length, 128)).replace(/^\xef\xbb\xbf/, '').trimStart()
  if (/^[\[{<]/.test(prefix)) throw failure('NOT_MEDIA', '下载地址返回了 JSON 或网页信息，请重新解析后保存')
  const mime = headerValue(headers, 'content-type').split(';')[0].trim().toLowerCase()
  if (/json|^text\/|xml/.test(mime)) throw failure('NOT_MEDIA', '下载地址未返回图片或视频，请重新解析')
  if (/mpegurl|m3u8/.test(mime) || /^#EXTM3U/.test(prefix) || /\.m3u8(?:[?#]|$)/i.test(url)) {
    throw failure('STREAM_PLAYLIST', '该地址是视频播放列表，暂不能直接保存到相册')
  }
  if (MIMES[mime]) return format(MIMES[mime])
  // 部分 CDN 没有 Content-Type；仅接受明确的媒体扩展名，不使用 .json。
  const disposition = headerValue(headers, 'content-disposition')
  const fileName = /filename\*?\s*=\s*(?:UTF-8''|["'])?([^"';\r\n]+)/i.exec(disposition)
  const candidates = [fileName && fileName[1], String(url).split(/[?#]/)[0]]
  for (const candidate of candidates) {
    const match = candidate && /\.([a-z0-9]+)$/i.exec(candidate.trim())
    if (match && FORMATS[match[1].toLowerCase()]) return format(match[1].toLowerCase())
  }
  throw failure('UNKNOWN_FORMAT', '无法识别下载文件格式，请重新解析后保存')
}

function invoke(target, name, options) {
  return new Promise((resolve, reject) => {
    target[name]({ ...options, success: resolve, fail: reject })
  })
}
function fileTools() {
  if (typeof wx !== 'undefined' && typeof wx.getFileSystemManager === 'function') {
    return { fs: wx.getFileSystemManager(), root: wx.env && wx.env.USER_DATA_PATH }
  }
  if (typeof uni.getFileSystemManager === 'function') {
    return { fs: uni.getFileSystemManager(), root: uni.env && uni.env.USER_DATA_PATH }
  }
  throw failure('FILE_SYSTEM', '当前环境无法读取媒体文件，请在微信小程序内保存')
}
async function removeFile(fs, path) {
  if (fs && path) {
    try { await invoke(fs, 'unlink', { filePath: path }) } catch (_) { /* 系统可能已回收临时文件 */ }
  }
}

export async function saveMediaToAlbum(item, options = {}) {
  const url = item && item.url
  if (typeof url !== 'string' || !/^https?:\/\/\S+$/i.test(url.trim())) {
    throw failure('INVALID_URL', '媒体地址无效，请重新解析')
  }
  const { fs, root } = fileTools()
  let original, named
  try {
    let response
    try { response = await downloadThroughCloud(url.trim(), options) }
    catch (error) { throw diagnostic('CLOUD_DOWNLOAD', error, '文件转存或下载失败，尚未写入相册；无需重复开启相册权限') }
    original = response.tempFilePath || response.filePath
    if (response.statusCode !== 200 || !original) {
      if ([401, 403, 404, 410].includes(response.statusCode)) {
        throw failure('DOWNLOAD_HTTP', `媒体服务器拒绝下载或地址已失效（HTTP ${response.statusCode}），请重新解析\n下载域名：${downloadHost(url)}`)
      }
      throw failure('DOWNLOAD_HTTP', `媒体下载失败${response.statusCode ? '（HTTP ' + response.statusCode + '）' : ''}，请重新解析`)
    }
    const stat = await invoke(fs, 'stat', { path: original })
    const size = stat.stats && stat.stats.size
    if (!Number.isFinite(size) || size <= 0) throw failure('EMPTY_FILE', '下载文件为空，请重新解析')
    // 最多读 512 字节，避免把整个视频加载到内存。
    const head = await invoke(fs, 'readFile', { filePath: original, position: 0, length: Math.min(size, 512) })
    const media = detectMediaFormat(head.data, response.header, item.url)
    let path = original
    if (!new RegExp('\\.' + media.ext + '$', 'i').test(original)) {
      if (!root) throw failure('FILE_SYSTEM', '无法创建媒体文件，请重启小程序重试')
      named = `${root}/guagua-media-${Date.now()}-${++sequence}.${media.ext}`
      await invoke(fs, 'copyFile', { srcPath: original, destPath: named })
      path = named
    }
    const method = media.type === 'image' ? 'saveImageToPhotosAlbum' : 'saveVideoToPhotosAlbum'
    if (options.onStage) options.onStage('正在保存到相册')
    await saveLocalMediaToAlbum(method, path)
    return media
  } finally {
    // 等相册保存完成（或失败）后再清理，不会抢先删除仍在保存的文件。
    await removeFile(fs, named)
    await removeFile(fs, original)
  }
}

function rawMessage(error) {
  return String(error && (error.errMsg || error.message) || '')
    .replace(/(?:https?:\/\/|cloud:\/\/)[^\s]+/gi, '[文件地址已隐藏]').slice(0, 300)
}
function diagnostic(stage, error, message) {
  const code = error && (error.errCode !== undefined ? error.errCode : error.errno)
  const detail = rawMessage(error)
  console.error('[媒体保存]', { stage, errCode: code, errMsg: detail })
  return failure(stage, message + '\n阶段：' + stage + (code !== undefined ? '\n错误码：' + code : '') + (detail ? '\n微信返回：' + detail : ''))
}

export function isAlbumPermissionError(error) {
  if (error && error.isMediaSaveError) return false
  const message = rawMessage(error)
  if (/privacy|cancel|download|cloud|request|readFile|copyFile/i.test(message)) return false
  return /^(?:saveImageToPhotosAlbum|saveVideoToPhotosAlbum|authorize):fail.*(?:auth deny|auth denied|permission|scope\.writePhotosAlbum)/i.test(message)
}
export function isPrivacyPermissionError(error) {
  if (error && error.isMediaSaveError) return false
  return /privacy/i.test(rawMessage(error))
}

async function checkNativePrivacy() {
  if (typeof wx.getPrivacySetting !== 'function') return
  const before = await invoke(wx, 'getPrivacySetting', {})
  if (!before.needAuthorization) return
  throw failure('PRIVACY_CONSENT', '微信尚未确认隐私同意，请点击“同意隐私协议并保存”按钮。若仍失败，请检查微信后台隐私指引配置')
}
function checkSystemAlbum() {
  if (typeof wx.getSystemInfoSync !== 'function') return
  const setting = wx.getSystemInfoSync()
  if (setting && setting.platform === 'ios' && setting.albumAuthorized === false) {
    throw failure('SYSTEM_ALBUM_DENIED', '手机系统已禁止微信访问照片，请到手机系统设置中允许微信添加照片，再返回保存')
  }
}

// Keep the downloaded file until native album save and a single permission retry finish.
export async function saveLocalMediaToAlbum(method, path) {
  const target = typeof wx !== 'undefined' && typeof wx[method] === 'function' ? wx : uni
  try { await invoke(target, method, { filePath: path }); return }
  catch (error) {
    if (isPrivacyPermissionError(error)) {
      throw diagnostic('ALBUM_PRIVACY', error, '微信拒绝了保存接口的隐私校验。相册权限与此不同，请检查小程序后台隐私指引是否声明保存图片/视频接口')
    }
    if (!isAlbumPermissionError(error)) throw diagnostic('ALBUM_WRITE', error, '文件已下载，但微信写入相册失败')
    const state = await invoke(wx, 'getSetting', {})
    checkSystemAlbum()
    if (state.authSetting && state.authSetting['scope.writePhotosAlbum'] === true) {
      throw diagnostic('ALBUM_WRITE_GRANTED', error, '相册权限已开启，但微信仍拒绝写入。请检查手机系统对微信的照片权限')
    }
    if (!await requestAlbumAuthorization()) throw failure('SAVE_CANCELLED', '已取消保存')
    try { await invoke(target, method, { filePath: path }) }
    catch (retryError) { throw diagnostic('ALBUM_WRITE_RETRY', retryError, '授权后微信仍无法写入相册，请根据原始错误排查') }
  }
}

function authorizeNativeAlbum() {
  return new Promise((resolve, reject) => {
    wx.authorize({ scope: 'scope.writePhotosAlbum', success: () => resolve(true), fail: reject })
  })
}

export async function ensureMediaSaveAuthorization(privacyRetried = false) {
  if (typeof wx === 'undefined' || typeof wx.authorize !== 'function') {
    throw failure('ALBUM_PLATFORM', '请在微信小程序中保存到相册')
  }
  // These are independent from scope.writePhotosAlbum, even when it is already true.
  await checkNativePrivacy()
  checkSystemAlbum()
  const setting = await new Promise((resolve, reject) => {
    wx.getSetting({ success: resolve, fail: reject })
  })
  const allowed = setting.authSetting && setting.authSetting['scope.writePhotosAlbum']
  if (allowed === true) return true
  if (allowed === false) return requestAlbumAuthorization()
  try {
    return await authorizeNativeAlbum()
  } catch (error) {
    if (isPrivacyPermissionError(error)) {
      if (privacyRetried) throw error
      throw diagnostic('AUTHORIZE_PRIVACY', error, '微信隐私校验未通过，请检查后台隐私指引和微信返回信息')
    }
    if (isAlbumPermissionError(error)) return requestAlbumAuthorization()
    throw error
  }
}

// Call openSetting synchronously inside the user's native confirmation callback.
// WeChat does not repeat the first-time authorize dialog after a prior refusal.
export function requestAlbumAuthorization() {
  return new Promise((resolve, reject) => {
    wx.showModal({
      title: '需要相册权限',
      content: '请点击确定，在微信设置中开启“保存到相册”，返回后将自动继续保存。',
      confirmText: '确定', cancelText: '取消',
      success: result => {
        if (!result.confirm) return resolve(false)
        wx.openSetting({
          success: setting => resolve(!!(setting.authSetting && setting.authSetting['scope.writePhotosAlbum'])),
          fail: () => reject(failure('ALBUM_SETTING', '无法打开微信授权设置，请点击右上角“…”→“设置”，开启“保存到相册”后重新保存'))
        })
      },
      fail: reject
    })
  })
}

// User-facing messages only. Technical diagnostics stay in the console.
export function mediaSaveError(error) {
  const messages = {
    PRIVACY_CONSENT: '请先阅读并同意隐私保护指引，再保存到相册',
    AUTHORIZE_PRIVACY: '暂时无法保存，请稍后重试',
    ALBUM_PRIVACY: '暂时无法保存，请稍后重试',
    SYSTEM_ALBUM_DENIED: '请在手机设置中允许微信添加照片后重试',
    ALBUM_WRITE_GRANTED: '请检查手机设置中微信的照片权限后重试',
    ALBUM_WRITE: '保存失败，请稍后重试',
    ALBUM_WRITE_RETRY: '保存失败，请稍后重试',
    CLOUD_DOWNLOAD: '文件下载失败，请稍后重试或重新解析',
    CLOUD_FILE: '文件下载失败，请稍后重试或重新解析',
    ALBUM_SETTING: '无法打开微信设置，请通过右上角“…”进入设置后重试',
    ALBUM_PLATFORM: '请在微信小程序中保存到相册',
    SAVE_CANCELLED: '已取消保存',
    INVALID_URL: '媒体地址无效，请重新解析',
    EMPTY_FILE: '下载文件为空，请重新解析',
    NOT_MEDIA: '下载内容无效，请重新解析',
    UNKNOWN_FORMAT: '暂不支持此文件格式，请重新解析',
    STREAM_PLAYLIST: '该视频暂不支持保存',
    DOWNLOAD_HTTP: '下载地址已失效，请重新解析',
    FILE_SYSTEM: '文件读取失败，请重启小程序后重试'
  }
  if (error && messages[error.code]) return messages[error.code]
  const message = String(error && (error.errMsg || error.message) || '')
  if (/cancel/i.test(message)) return '已取消保存'
  if (/privacy/i.test(message)) return '暂时无法保存，请稍后重试'
  if (isAlbumPermissionError(error)) return '请在小程序设置中允许保存到相册'
  if (/format|unsupported|not support|decode|invalid.*(?:video|image)/i.test(message)) return '当前媒体格式暂不支持保存，请重新解析'
  if (/space|quota|storage.*(?:full|limit)/i.test(message)) return '存储空间不足，请清理空间后重试'
  if (/download|network|timeout|url not in domain list/i.test(message)) return '文件下载失败，请稍后重试或重新解析'
  if (/ENOENT|no such file|file not found/i.test(message)) return '下载文件已失效，请重新下载'
  return '保存失败，请稍后重试'
}
