# 呱呱 FastAPI 后端

FastAPI + SQLAlchemy + MySQL 8.0。负责链接解析、每日额度、幂等扣次、分享奖励、广告奖励、累计解析数及微信云存储转存。部署见[根目录部署说明](../../部署说明.md)，前后端整体使用见[根 README](../../README.md)。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `run.py`、`local_dev.py` | 正式与本地启动入口 |
| `app/main.py`、`identity.py`、`settings.py` | 接口、微信网关身份和环境变量 |
| `app/quota.py` | 账号、北京时间日切、事务扣次、幂等、累计成功解析数 |
| `app/sharing.py`、`app/ads.py` | 分享奖励、广告会话和幂等奖励 |
| `app/parsers/` | 各平台公开分享链接解析 |
| `app/download_tasks.py` | 账号隔离的下载任务与 MySQL 状态 |
| `app/cloud_transfer.py`、`wechat_storage.py`、`transfer_http.py` | 流式转存、内部临时凭证、文件元数据和外部请求校验 |
| `migrations/` | 完整建表脚本与 001～005 增量迁移 |
| `tests/` | 解析、身份、数据库事务、广告与存储回归 |

## 本地运行

先准备本机专用 MySQL 开发库，库名必须以 `guagua_test_` 开头，导入 `migrations/latest_schema.sql`。在本目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.local.example .env.local
```

修改 `.env.local` 的 `LOCAL_DATABASE_URL` 后运行：

```bash
python run.py
```

监听 `127.0.0.1:8080`，固定测试身份 `wxTestApp / openidA`。拒绝非回环访问和客户端提交的微信身份头；不热加载。`http://127.0.0.1:8080/docs` 可查看接口。本机下载转存需要的微信云托管内部凭证接口不可用，完整存储链路在云端验证。

## 正式运行

使用 Python 3.12 生产镜像。注入 `.env.example` 所列变量并完成数据库迁移、入口隔离后：

```bash
python run.py --production
```

监听 `0.0.0.0:8080`。生产模式不自动加载 `.env`，不使用本地固定身份；仅信任微信私有网关。`WECHAT_PRIVATE_INGRESS_ONLY=true` 只是运维确认，必须实际关闭所有业务公网入口。

可从仓库根目录执行 `docker build -t guagua-api .`，或以本目录为上下文执行 `docker build -t guagua-api api/media_parser_fastapi`。两种 Dockerfile 都明确使用正式模式。

## 主要接口

除健康检查外，下列接口均校验可信微信身份；本地入口用固定测试身份替代。请求体不接收 OpenID 等身份字段。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/health` | 进程检查，不检查 MySQL |
| GET | `/api/v1/quota` | 自动建用户、获取当日余额与累计解析数 |
| POST | `/api/v1/parse` | `{ "request_id": "16至64位编号", "url": "分享文案或链接" }`，成功扣 1 |
| GET | `/api/v1/platform?url=...` | 识别平台，不扣次数 |
| POST | `/api/share/log` | `{ "inviter": "邀请标识", "entry_id": "32位小写十六进制编号", "source": "wechat_share" }` |
| GET | `/api/share/profile` | 获取自己的分享标识和奖励规则 |
| GET | `/api/share/my-invitees?before=0&limit=20` | 自己的邀请记录，limit 最大 50 |
| POST | `/api/ads/start` | `{}`，创建或复用待完成广告奖励会话 |
| POST | `/api/ads/complete` | `{ "session_id": "会话编号" }`，同会话只奖励一次 |
| POST | `/api/download/submit` | `{ "url": "媒体直链" }`，返回任务号 |
| GET | `/api/download/status?task_id=...` | 仅查询自己的任务，成功返回 fileID |

`POST /api/v1/parse/batch` 已关闭，认证后返回 410。当前不提供公网 `/proxy`、`/api/count`、任意改余额、退款或删账号接口。

解析 `request_id` 限 16～64 位字母、数字、下划线和连字符，输入 `url` 限 1～10000 字符，拒绝额外字段。成功响应包含 `success`、`request_id`、`quota`、`total_parses` 和 `data`；`data` 按作品类型含 `video_url`、`backup_video_urls`、`image_urls` 等。无效链接或媒体一般返回 400，上游解析失败 502，身份错误 401/403，编号与原输入冲突 409，字段错误 422，额度不足 429，数据库暂不可用 503。

同账号原编号原输入返回持久化结果，不重复解析和扣次。扣次、结果和流水在一个 MySQL 事务中提交；网络超时继续沿用原编号。历史重放中的余额是当时快照，当前余额通过额度接口刷新。CDN 地址可能过期，需要用户重新发起新解析。

每日免费次数默认 5，失败不扣，多图只扣 1；北京时间换日不结转。分享奖励由 `SHARE_REWARD` 控制，默认 5。广告每完整观看一次固定奖励 5，奖励依赖客户端完成事件上报与服务端幂等会话，不等于微信服务端验签。

## 云存储下载

私有入口提交任务 → 后端流式拉取外部媒体 → 使用内部临时凭证上传 COS 并写入元数据 → 状态接口返回 fileID → 前端 `wx.cloud.downloadFile` 下载保存相册。凭证不返回前端，完整媒体不在服务器磁盘落盘。

任务状态保存到 `download_tasks`，有效期 1 小时，同账号并发限 1，每进程最多 4。后台任务不跨实例重启自动续传，运行中需要至少保留 1 个实例。详情见[云存储转存说明](云存储转存部署说明.md)。

## 测试和日志

从仓库根目录执行：

```bash
python -m pip install -r api/media_parser_fastapi/requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=api/media_parser_fastapi \
  python -m pytest api/media_parser_fastapi/tests -q -p no:cacheprovider
```

不设置 `TEST_MYSQL_URL` 时跳过真实 MySQL 集成测试。数据库测试必须另建以 `guagua_test_` 开头的可丢弃空库；测试会建表、删除表和记录，不能指向保留数据的库。模拟上游和云存储测试不代表线上平台及权限已验证。

输出使用终端/容器标准输出，不配置本地日志文件。`.env.local`、日志、虚拟环境、缓存和数据库文件均被 Git 忽略。
