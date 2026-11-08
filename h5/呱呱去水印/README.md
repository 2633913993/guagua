# 呱呱去水印前端

HBuilderX uni-app（Vue 2）工程，主要发布到微信小程序。前后端整体说明见[根 README](../../README.md)，部署步骤见[部署说明](../../部署说明.md)。

## 目录

| 路径 | 用途 |
| --- | --- |
| `pages/home/index.vue` | 首页、链接输入、次数、分享、激励广告、累计统计 |
| `pages/result/result.vue` | 视频/图片结果、保存相册、解析成功插屏广告 |
| `pages/invite-list/index.vue` | 邀请记录与分页 |
| `pages/index/index.vue` | 兼容旧页面路径 |
| `services/request.js`、`container.js` | HTTP/微信云托管请求 |
| `services/quota.js`、`parse.js`、`share.js`、`ads.js` | 服务端额度、解析、分享和广告会话 |
| `services/cloud-download.js`、`media-save.js` | 转存任务、云存储下载、格式识别和相册保存 |
| `config/features.js` | 请求环境和广告位 |
| `manifest.json`、`pages.json` | 小程序 AppID、页面和构建配置 |
| `build/wechat-native-wx-plugin.cjs` | 微信运行时适配，不是编译输出，需保留 |

## 运行与编译

1. HBuilderX 导入本目录，不需要 `npm run dev`。
2. 运行到微信开发者工具可调试；发行微信小程序会生成 `unpackage/dist/build/mp-weixin`。
3. 在微信开发者工具导入生成目录，预览或上传。源码和生产包不是同一目录，改源码后需重新编译。

## 请求配置

当前提交默认 `FEATURES.request.mode='container'`，业务全部走私有 `wx.cloud.callContainer`。确认 `cloudEnv`、`cloudService` 与已部署后端一致。

电脑本机联调可改为 `mode: 'http'`，`httpBaseUrl: 'http://127.0.0.1:8080'`，并启动本机后端。模拟器需关闭合法域名校验；手机不能用此回环地址访问电脑。HTTP 模式只供本机测试，不会在云模式失败后自动降级。发布前改回 `container`。

云存储 `cloudStorageEnv`、`cloudStorageBucket` 要与后端 `CLOUD_STORAGE_ENV`、`COS_BUCKET` 一致。AppID 在 `manifest.json` 的 `mp-weixin.appid`。激励视频 `FEATURES.ads.adUnitId` 与后端 `REWARDED_AD_UNIT_ID` 一致，顶部广告使用 `homeVideoAdUnitId`。

前端不提交 AppSecret、数据库密码或 OpenID，不决定余额。分享进入编号和解析请求编号会缓存以便幂等重试；余额和奖励以服务端回执为准。额度读取失败支持有限重试、点击/下拉重试及再次进入前台恢复。

## 保存相册

使用私有入口提交转存任务、轮询后获得 fileID，调用微信云存储 SDK 下载。按实际文件头识别图片/视频格式，校验错误页面和空文件，再保存到相册并清理此次临时文件。需在微信后台配置隐私声明，并在真机验证隐私授权与相册权限。普通本机环境不能验证云托管内部存储接口。

## 验证

仓库根目录执行 `node tests/frontend.cjs` 和 `node h5/呱呱去水印/tests/wx-runtime.cjs`。覆盖 HTTP/云模式、次数重试、分享、存储、相册权限、激励广告、插屏广告和累计次数。

生成生产包后，可运行 `scripts/check-weixin.cjs` 及 `tests/wx-built-runtime.cjs`，路径参数为生成的 `unpackage/dist/build/mp-weixin`。构建输出、缓存和本机日志不提交；项目的 `build/` 目录包含必要构建源码，已保留。
