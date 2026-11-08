"""WeChat private ingress + FastAPI + MySQL. No cloud functions/databases."""
from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .identity import Identity, trusted_identity
from .settings import Settings
from .download_tasks import DownloadTasks, add_download_routes
from .cloud_transfer import TransferSettings
from .quota import Quotas
from .sharing import Sharing
from .ads import Ads
from .parsers.core import parse_one_url
from .parsers.common import detect_platform, clean_url


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,64}$')
    url: str = Field(min_length=1, max_length=10000)


class ShareLogRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inviter: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    entry_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    source: str = Field(default='wechat_share', pattern=r'^wechat_share$')


class AdCompleteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class AdStartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


def create_app(settings=None, engine=None, parser=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env()
        app.state.quotas = Quotas(app.state.settings, engine)
        app.state.sharing = Sharing(app.state.quotas)
        app.state.ads = Ads(app.state.quotas)
        app.state.download_tasks = DownloadTasks(TransferSettings.from_env(), engine=app.state.quotas.engine)
        try:
            yield
        finally:
            app.state.download_tasks.close()
            if engine is None:
                app.state.quotas.engine.dispose()

    app = FastAPI(title='呱呱去水印 API', version='2.0.0', lifespan=lifespan)
    who = Annotated[Identity, Depends(trusted_identity)]

    @app.get('/api/v1/health', tags=['system'])
    def health():
        return {'status': 'ok'}

    @app.get('/api/v1/quota', tags=['quota'])
    def quota(identity: who):
        return {'success': True, 'quota': app.state.quotas.balance(identity)}

    @app.post('/api/ads/start', tags=['ads'])
    def ad_start(body: AdStartRequest, identity: who):
        return {'success': True, 'data': app.state.ads.start(identity)}

    @app.post('/api/ads/complete', tags=['ads'])
    def ad_complete(body: AdCompleteRequest, identity: who):
        return {'success': True, 'data': app.state.ads.complete(identity, body.session_id)}

    @app.get('/api/share/profile', tags=['share'])
    def share_profile(identity: who):
        return {'success': True, 'data': app.state.sharing.profile(identity)}

    @app.post('/api/share/log', tags=['share'])
    def share_log(body: ShareLogRequest, identity: who):
        return {'success': True, 'data': app.state.sharing.record(identity, body.inviter, body.source, body.entry_id)}

    @app.get('/api/share/my-invitees', tags=['share'])
    def share_invitees(identity: who, before: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
        return {'success': True, 'data': app.state.sharing.invitees(identity, before, limit)}

    @app.post('/api/v1/parse', tags=['parse'])
    def parse(body: ParseRequest, identity: who):
        status, response = app.state.quotas.parse(
            identity, body.request_id, body.url, parser or parse_one_url)
        return JSONResponse(status_code=status, content=response)

    @app.post('/api/v1/parse/batch', tags=['parse'], include_in_schema=False)
    def batch_disabled(identity: who):
        raise HTTPException(410, detail='批量接口已关闭，请按 request_id 逐次请求单链接接口')

    @app.get('/api/v1/platform', tags=['parse'])
    def platform(url: str, identity: who):
        url = clean_url(url)
        detected = detect_platform(url)
        if not detected:
            raise HTTPException(400, detail='不支持的平台域名')
        return {'success': True, 'platform': detected, 'url': url}

    add_download_routes(app)
    return app


app = create_app()
