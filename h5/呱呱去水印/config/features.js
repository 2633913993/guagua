export const FEATURES = {
  request: {
    // 业务接口仅走微信云托管私有入口；文件由微信云存储 SDK 下载。
    mode: 'container',//本地：http 正式：container
    httpBaseUrl: 'http://127.0.0.1:8080',
    timeout: 120000,
    cloudEnv: 'prod-d3gk2acox7105dd30',
    cloudService: 'pwqsy-fastapi',
    // 必须与后端 CLOUD_STORAGE_ENV 相同，并关联当前小程序。
    cloudStorageEnv: 'prod-d3gk2acox7105dd30',
    cloudStorageBucket: '7072-prod-d3gk2acox7105dd30-1385311964'
  },
  ads: {
    enabled: true,
    // 后端 REWARDED_AD_UNIT_ID 需与此保持一致。
    adUnitId: 'adunit-5db13f7adc24db74',
    homeVideoAdUnitId: 'adunit-7407adebb19f16c2'
  }
}
export const PARSE_RESULT_KEY = 'parseResult'
