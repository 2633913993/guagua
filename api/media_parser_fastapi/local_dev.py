"""Loopback-only local testing. This module is excluded from the Docker image."""
import os
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy.engine import make_url

from app.identity import Identity, trusted_identity
from app.main import create_app
from app.settings import Settings, DEFAULT_REWARDED_AD_UNIT_ID

TEST_IDENTITY = Identity('wxTestApp', 'openidA')


def local_database_url():
    value = os.getenv('LOCAL_DATABASE_URL', '')
    if not value:
        config = Path(__file__).with_name('.env.local')
        if config.exists():
            for line in config.read_text(encoding='utf-8').splitlines():
                key, sep, raw = line.partition('=')
                if sep and key.strip() == 'LOCAL_DATABASE_URL':
                    value = raw.strip().strip('"').strip("'")
                    break
    if not value:
        raise RuntimeError('本地启动需配置 .env.local 的 LOCAL_DATABASE_URL，参见 .env.local.example')
    return value


def local_identity(request: Request):
    # Never trust forwarded headers, Host or client-supplied WeChat identity.
    if not request.client or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(403, detail='本地测试接口仅允许回环地址访问')
    if request.headers.get('x-wx-appid') or request.headers.get('x-wx-openid'):
        raise HTTPException(400, detail='本地模式仅使用固定测试账号，不接收微信身份头')
    return TEST_IDENTITY


def create_local_app(database_url=None, engine=None, parser=None):
    value = database_url or local_database_url()
    url = make_url(value)
    if (url.drivername != 'mysql+pymysql' or
            url.host not in ('127.0.0.1', 'localhost', '::1') or
            not url.database or not url.database.startswith('guagua_test_')):
        raise RuntimeError('本地模式仅允许本机 MySQL 的 guagua_test_ 测试库')
    app = create_app(Settings(value, frozenset([TEST_IDENTITY.appid]),
                                    rewarded_ad_unit_id=os.getenv("REWARDED_AD_UNIT_ID", DEFAULT_REWARDED_AD_UNIT_ID).strip()), engine, parser)
    app.title = '呱呱去水印 API（本地测试账号）'
    app.description = '仅本机调试，固定测试账号 wxTestApp / openidA；不是实际微信登录身份。'
    app.dependency_overrides[trusted_identity] = local_identity
    return app
