const { spawnSync } = require('node:child_process')
for (const test of ['local-request.mjs', 'server-quota.mjs', 'quota-retry.mjs', 'quota-failure.mjs', 'share-flow.mjs', 'media-save.mjs', 'cloud-download.mjs', 'album-privacy.mjs', 'rewarded-ads.mjs', 'interstitial-ad.mjs', 'total-parses.mjs']) {
  const result = spawnSync(process.execPath, ['--experimental-default-type=module', `h5/呱呱去水印/tests/${test}`], { stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status ?? 1)
}
