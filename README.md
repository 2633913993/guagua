# 呱呱去水印：前后端说明

uni-app（Vue 2）微信小程序 + FastAPI + MySQL 8.0。此仓库同时包含前端、后端、数据库迁移和测试，源码基于当前使用的桌面项目整理。

## 目录

| 路径 | 内容 |
| --- | --- |
| `h5/呱呱去水印/` | 小程序前端源码，使用 HBuilderX 导入；目录名虽为 h5，正式运行目标为微信小程序 |
| `api/media_parser_fastapi/` | FastAPI 后端、解析器、次数与奖励、下载转存任务 |
| `api/media_parser_fastapi/migrations/` | MySQL 初始化与增量迁移 |
| `tests/frontend.cjs` | 前端服务与页面回归入口 |
| `Dockerfile` | 从仓库根目录构建后端的生产镜像 |
| `部署说明.md` | 云托管、数据库、存储和发布步骤 |
| `kuaishou.py`、`requirements-kuaishou.txt` | 独立快手解析脚本，不是小程序后端启动入口 |

详细说明：[前端 README](h5/呱呱去水印/README.md)、[后端 README](api/media_parser_fastapi/README.md)、[部署说明](部署说明.md)。

## 工作流程

```text
微信小程序
  ├─ wx.cloud.callContainer → FastAPI 私有入口 → MySQL
  │    ├─ 链接解析、每日次数、累计成功解析数
  │    ├─ 分享记录与激励视频奖励
  │    └─ 提交/查询下载任务 → 拉取媒体 → 上传微信云存储
  └─ wx.cloud.downloadFile → 临时文件 → 保存相册 → 清理临时文件
```

视频播放和图片预览使用解析结果中的媒体地址；保存相册使用云存储转存后的 fileID。后端采用流式传输，不把完整视频保存到本机磁盘。云存储临时凭证只在后端使用。

正式身份来自私有微信网关的 `X-WX-APPID`、`X-WX-OPENID`，前端不提交用户身份或直连 MySQL。必须实际关闭业务服务的全部公网入口，才能信任这些请求头；`WECHAT_PRIVATE_INGRESS_ONLY=true` 只表示部署者已完成入口检查。

## 功能与次数规则

- 支持抖音、小红书、快手、Bilibili、最右、皮皮虾、剪映的公开分享链接；可粘贴完整分享文案。上游失效、权限限制或接口变化可能导致解析失败，剪映付费模板不返回默认示例视频冒充成功结果。
- 默认每账号每天 5 次，成功解析扣 1 次，多图只扣 1 次，失败不扣；北京时间零点按新日期生成额度，剩余和奖励不结转。
- 同账号相同 `request_id` 与原输入重试会重放原结果，不重复扣次；网络超时应沿用原编号。
- 好友通过分享进入默认奖励邀请人 5 次；同一次进入重试不重复奖励，自邀不奖励。规则见[分享说明](api/media_parser_fastapi/分享功能说明.md)。
- 完整观看激励视频后，通过账号绑定的服务端会话领取固定 5 次奖励，同会话重复上报不会重复发放。当前依据客户端完成事件上报，不是微信服务端验签。
- 累计成功解析数由服务端按小程序统计，前端不能用旧响应降低已缓存的总数。

## 获取代码

```bash
git clone https://github.com/2633913993/guagua.git
cd guagua
```

默认分支为 `master`。

## 本地启动后端

建议使用 Python 3.12、MySQL 8.0，以及支持 `--experimental-default-type=module` 的 Node.js（用于现有前端测试）。HBuilderX 用于编译前端。

先在本机创建专用开发库，例如 `guagua_test_local`，然后导入完整建表脚本：

```bash
mysql -h 127.0.0.1 -P 3306 -u YOUR_USER -p guagua_test_local \
  < api/media_parser_fastapi/migrations/latest_schema.sql
```

在后端目录安装依赖并配置本地连接：

```bash
cd api/media_parser_fastapi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.local.example .env.local
```

将 `.env.local` 的 `LOCAL_DATABASE_URL` 改为实际的本机开发库连接。只允许回环主机和 `guagua_test_` 开头的数据库，密码中的特殊字符需做 URL 编码。然后启动：

```bash
python run.py
```

本地入口为 `http://127.0.0.1:8080/docs`，健康检查为 `/api/v1/health`，额度接口为 `/api/v1/quota`。固定测试身份为 `wxTestApp / openidA`，改代码后需要重启。普通本机环境无法调用微信云托管内部存储接口，完整云存储下载需在云端验证。

## 启动前端

1. 用 HBuilderX 导入 `h5/呱呱去水印`，这是 HBuilderX 工程，不依赖 `npm run dev`。
2. 本机联调时，将 `config/features.js` 的 `request.mode` 从当前的 `container` 改为 `http`，核对 `httpBaseUrl`。
3. 在 HBuilderX 运行到微信开发者工具。电脑模拟器本地联调需关闭合法域名校验；手机上的 `127.0.0.1` 指向手机自身。
4. 正式发布时改回 `container`，核对 `cloudEnv`、`cloudService`、`cloudStorageEnv`、`cloudStorageBucket` 及 `manifest.json` 的小程序 AppID。
5. 通过 HBuilderX 发行微信小程序，将 `unpackage/dist/build/mp-weixin` 导入微信开发者工具预览、上传。

仓库保留当前项目的 AppID、环境 ID、存储桶和广告位配置；这些标识不是密码，部署到其他账号或环境时需成套替换。后端的 `REWARDED_AD_UNIT_ID` 必须与前端激励视频广告位一致。

## 测试

从仓库根目录执行前端回归和微信运行时兼容检查：

```bash
node tests/frontend.cjs
node h5/呱呱去水印/tests/wx-runtime.cjs
```

运行后端测试，不生成 Python 字节码和 pytest 缓存：

```bash
python -m pip install -r api/media_parser_fastapi/requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=api/media_parser_fastapi \
  python -m pytest api/media_parser_fastapi/tests -q -p no:cacheprovider
```

未设置 `TEST_MYSQL_URL` 时会跳过真实 MySQL 集成测试。需要数据库验证时，另建可丢弃的空测试库，库名以 `guagua_test_` 开头，再通过环境变量传入连接串；测试会创建、删除表和数据，不要使用现有开发库或生产库。部分转存测试会启动短时回环 HTTP 测试服务。

编译后可检查生产包：

```bash
node h5/呱呱去水印/scripts/check-weixin.cjs h5/呱呱去水印/unpackage/dist/build/mp-weixin
node h5/呱呱去水印/tests/wx-built-runtime.cjs h5/呱呱去水印/unpackage/dist/build/mp-weixin
```

## 日志与本机文件

运行和测试输出默认显示在终端，不配置本地日志文件，不使用 `tee` 或输出重定向保存日志。生产服务由云托管接收标准输出。`.gitignore` 已排除 `*.log`、日志目录、本机 `.env`、数据库文件、虚拟环境、编译产物以及 `output/` 中的历史备份；仅提交不含真实密码的环境变量模板。仓库不保存数据库数据、云存储媒体或操作排查记录。
