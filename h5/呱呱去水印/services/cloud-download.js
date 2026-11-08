import { FEATURES } from '../config/features.js'
import { callContainer, cloudClient } from './container.js'

function failure(code, message) {
  const error = new Error(message)
  error.code = code
  error.isMediaSaveError = true
  return error
}
function bodyOf(response) {
  const data = response && response.data
  if (!response || response.statusCode < 200 || response.statusCode >= 300 || !data || typeof data !== 'object') {
    throw failure('TRANSFER_API', data && (data.detail || data.msg) || '云存储转存接口暂不可用，请稍后重试')
  }
  return data
}
function cloudFileError(error) {
  const reason = String(error && (error.errMsg || error.message) || 'cloud.downloadFile:fail')
    .replace(/(?:https?:\/\/|cloud:\/\/)[^\s]+/gi, '[文件地址已隐藏]').slice(0, 240)
  const code = error && (error.errCode !== undefined ? error.errCode : error.errno)
  return failure('CLOUD_FILE', '云存储文件下载失败，尚未写入相册。请检查云存储访问权限或微信隐私接口配置' +
    (code !== undefined ? '\n错误码：' + code : '') + '\n微信返回：' + reason)
}
function cloudFile(fileID) {
  return new Promise((resolve, reject) => {
    try {
      const cloud = cloudClient()
      const result = cloud.downloadFile({
        fileID,
        config: { env: FEATURES.request.cloudStorageEnv || FEATURES.request.cloudEnv },
        success: resolve,
        fail: error => reject(cloudFileError(error))
      })
      if (result && typeof result.then === 'function') result.then(resolve, error => reject(cloudFileError(error)))
    } catch (error) { reject(error) }
  })
}

// Only small JSON crosses callContainer. File bytes are fetched by the WeChat
// cloud storage SDK, never uni.downloadFile or a public backend endpoint.
export async function downloadThroughCloud(url, options = {}) {
  const wait = options.wait || (ms => new Promise(resolve => setTimeout(resolve, ms)))
  const now = options.now || Date.now
  const deadline = now() + 420000
  const stage = options.onStage || (() => {})
  stage('正在转存文件')
  const submitted = bodyOf(await callContainer({
    path: '/api/download/submit', data: { url }, timeout: 14000
  }))
  if (submitted.code !== 0 || !/^[a-f0-9]{32}$/.test(submitted.task_id || '')) {
    throw failure('TRANSFER_SUBMIT', '云存储转存任务提交失败，请稍后重试')
  }
  let failures = 0
  while (now() < deadline) {
    await wait(2000)
    let response
    try {
      response = await callContainer({ path: '/api/download/status?task_id=' + encodeURIComponent(submitted.task_id),
        method: 'GET', data: {}, timeout: 14000 })
      failures = 0
    } catch (error) {
      if (++failures < 3) continue
      throw failure('TRANSFER_NETWORK', '查询转存任务失败，请检查微信网络后重试')
    }
    const task = bodyOf(response)
    if (task.status === 'failed') throw failure('TRANSFER_FAILED', task.msg || '文件转存失败，请重新解析后重试')
    if (task.status === 'success') {
      if (typeof task.fileID !== 'string' || !task.fileID.startsWith('cloud://')) {
        throw failure('TRANSFER_FILE_ID', '云存储返回的文件标识无效，请联系管理员')
      }
      const config = FEATURES.request
      const prefix = `cloud://${config.cloudStorageEnv || config.cloudEnv}.${config.cloudStorageBucket}/`
      if (!config.cloudStorageBucket || !task.fileID.startsWith(prefix)) {
        throw failure('TRANSFER_FILE_ID', '云存储文件所属环境或存储桶不匹配，请联系管理员')
      }
      stage('正在下载文件')
      const file = await cloudFile(task.fileID)
      return { ...file, statusCode: 200, header: { 'content-type': task.contentType || '' } }
    }
    if (task.status !== 'processing') throw failure('TRANSFER_STATUS', '转存任务状态异常，请重试')
  }
  throw failure('TRANSFER_TIMEOUT', '下载转存等待超时，请稍后重新保存')
}
