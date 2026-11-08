"""Bounded-memory HTTP media relay; never buffer or decompress the upstream body."""
from copy import deepcopy
from ipaddress import ip_address
import socket
from urllib.parse import quote, urljoin, urlsplit

import anyio
import requests
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from .parsers.common import PC_HEADERS

CHUNK_SIZE = 64 * 1024
REDIRECTS = {301, 302, 303, 307, 308}
RESPONSE_HEADERS = (
    'Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Range',
    'Content-Encoding', 'Content-Disposition', 'ETag', 'Last-Modified', 'Cache-Control',
)


def proxy_result(response, base_url):
    """Wrap at the API boundary, including old cached quota results, without mutation."""
    if not response.get('success') or not isinstance(response.get('data'), dict):
        return response
    result = deepcopy(response)
    data = result['data']

    def wrap(url):
        if isinstance(url, str) and url.startswith(('http://', 'https://')):
            return base_url.rstrip('/') + '/proxy?url=' + quote(url, safe='')
        return url

    if data.get('video_url'):
        data['video_url'] = wrap(data['video_url'])
    if isinstance(data.get('backup_video_urls'), list):
        data['backup_video_urls'] = [wrap(url) for url in data['backup_video_urls']]
    return result


def validate_target(url):
    # Query parameters have already been decoded by FastAPI. Never unquote again:
    # signed CDN URLs can contain literal %2F, %26, + and other escaped bytes.
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or
                parts.username is not None or parts.password is not None or
                parts.fragment or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError('invalid URL')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('non-public address')
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, detail='代理仅支持公网 HTTP/HTTPS 资源地址') from exc
    except OSError as exc:
        raise HTTPException(502, detail='视频源域名解析失败') from exc


def upstream_headers(url, request):
    headers = {'User-Agent': PC_HEADERS['User-Agent'], 'Accept': '*/*', 'Accept-Encoding': 'identity'}
    for name in ('Range', 'If-Range', 'If-None-Match', 'If-Modified-Since'):
        if request.headers.get(name):
            headers[name] = request.headers[name]
    host = urlsplit(url).hostname or ''
    if host == 'bilivideo.com' or host.endswith(('.bilivideo.com', '.bilivideo.cn', '.bilibili.com')):
        headers['Referer'] = 'https://www.bilibili.com/'
    return headers


class MediaStreamResponse(StreamingResponse):
    def __init__(self, upstream, session, head=False):
        self.upstream = upstream
        self.session = session
        # raw.stream(decode_content=False) preserves Content-Length even if the
        # server ignores Accept-Encoding: identity and sends compressed bytes.
        body = iter(()) if head else upstream.raw.stream(CHUNK_SIZE, decode_content=False)
        headers = {name: upstream.headers[name] for name in RESPONSE_HEADERS if name in upstream.headers}
        headers['X-Accel-Buffering'] = 'no'
        super().__init__(body, status_code=upstream.status_code, headers=headers)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also runs on disconnect, cancellation and failed upstream reads.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.upstream.close)
                await run_in_threadpool(self.session.close)


def proxy_media(request: Request, url: str):
    session = requests.Session()
    session.trust_env = False
    upstream = None
    try:
        target = url
        for hop in range(6):
            # Validate every redirect before making the next request.
            validate_target(target)
            upstream = session.request(
                request.method, target, headers=upstream_headers(target, request),
                stream=True, allow_redirects=False, timeout=(10, 60),
            )
            if upstream.status_code not in REDIRECTS:
                return MediaStreamResponse(upstream, session, head=request.method == 'HEAD')
            location = upstream.headers.get('Location')
            upstream.close()
            if not location or hop == 5:
                raise HTTPException(502, detail='视频源重定向异常或次数过多')
            target = urljoin(target, location)
    except Exception as exc:
        if upstream is not None:
            upstream.close()
        session.close()
        if isinstance(exc, requests.Timeout):
            raise HTTPException(504, detail='视频源连接超时') from exc
        if isinstance(exc, requests.RequestException):
            raise HTTPException(502, detail='视频源连接失败') from exc
        raise
