import asyncio
from copy import deepcopy
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, quote, urlsplit

import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.main import create_app
from app.identity import Identity, trusted_identity
from app.proxy import CHUNK_SIZE, MediaStreamResponse, proxy_media, proxy_result, validate_target
from app.settings import Settings

SOURCE = 'https://cdn.example/v.mp4?token=a%2Fb%26c&x=1+2#part'
PUBLIC_DNS = [(2, 1, 6, '', ('93.184.216.34', 443))]


def upstream(status=200, headers=None, chunks=(b'abc', b'def')):
    result = Mock(status_code=status, headers=headers or {'Content-Type': 'video/mp4', 'Content-Length': '6'})
    result.raw.stream.return_value = iter(chunks)
    return result


@pytest.fixture
def relay():
    app = FastAPI()
    app.add_api_route('/proxy', proxy_media, methods=['GET', 'HEAD'])
    with patch('app.proxy.requests.Session') as constructor, patch('app.proxy.socket.getaddrinfo', return_value=PUBLIC_DNS):
        session = constructor.return_value
        with TestClient(app) as client:
            yield client, session


def test_url_encoding_and_cached_response_unchanged():
    original = {'success': True, 'data': {'video_url': SOURCE, 'backup_video_urls': [SOURCE], 'image_urls': ['https://cdn.example/a.jpg']}}
    before = deepcopy(original)
    wrapped = proxy_result(original, 'https://download.example/')
    url = wrapped['data']['video_url']
    assert url == 'https://download.example/proxy?url=' + quote(SOURCE, safe='')
    assert parse_qs(urlsplit(url).query)['url'] == [SOURCE]
    assert wrapped['data']['backup_video_urls'] == [url]
    assert original == before
    assert proxy_result({'success': False}, 'https://download.example') == {'success': False}


def test_parse_route_keeps_source_for_cloud_transfer():
    original = {'success': True, 'data': {'video_url': SOURCE}, 'quota': {'remaining': 4}}
    with patch('app.main.Quotas') as quota:
        quota.return_value.parse.return_value = (200, original)
        app = create_app(Settings('unused', frozenset({'wxTest'}), proxy_base_url='https://download.example'))
        app.dependency_overrides[trusted_identity] = lambda: Identity('wxTest', 'test')
        with TestClient(app) as client:
            payload = {'request_id': 'proxy-test-request-001', 'url': 'https://v.douyin.com/test'}
            first = client.post('/api/v1/parse', json=payload).json()
            second = client.post('/api/v1/parse', json=payload).json()
    assert first == second
    assert first['data']['video_url'] == SOURCE
    assert first['quota'] == original['quota']
    assert original['data']['video_url'] == SOURCE


def test_full_download_and_single_decoding(relay):
    client, session = relay
    response = upstream()
    session.request.return_value = response
    source = SOURCE.split('#')[0]
    result = client.get('/proxy', params={'url': source})
    assert result.status_code == 200 and result.content == b'abcdef'
    assert result.headers['content-type'] == 'video/mp4'
    assert result.headers['content-length'] == '6'
    args, kwargs = session.request.call_args
    assert args == ('GET', source)
    assert kwargs['stream'] and not kwargs['allow_redirects']
    assert kwargs['headers']['Accept-Encoding'] == 'identity'
    assert 'Mozilla' in kwargs['headers']['User-Agent']
    response.raw.stream.assert_called_once_with(CHUNK_SIZE, decode_content=False)
    response.close.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize('status,headers,body', [
    (206, {'Content-Type': 'video/mp4', 'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'Accept-Ranges': 'bytes'}, b'def'),
    (416, {'Content-Range': 'bytes */6', 'Content-Length': '0'}, b''),
    (403, {'Content-Type': 'text/plain', 'Content-Length': '6'}, b'denied'),
])
def test_ranges_and_upstream_status(relay, status, headers, body):
    client, session = relay
    session.request.return_value = upstream(status, headers, (body,))
    result = client.get('/proxy', params={'url': 'https://cdn.example/v.mp4'}, headers={'Range': 'bytes=3-5', 'If-Range': '"abc"'})
    assert result.status_code == status and result.content == body
    for key, value in headers.items():
        assert result.headers[key] == value
    sent = session.request.call_args.kwargs['headers']
    assert sent['Range'] == 'bytes=3-5' and sent['If-Range'] == '"abc"'


def test_redirect_and_bilibili_referer(relay):
    client, session = relay
    redirect = upstream(302, {'Location': 'https://v.bilivideo.com/final.mp4'})
    final = upstream()
    session.request.side_effect = [redirect, final]
    result = client.get('/proxy', params={'url': 'https://cdn.example/start'})
    assert result.content == b'abcdef'
    assert session.request.call_count == 2
    assert session.request.call_args.kwargs['headers']['Referer'] == 'https://www.bilibili.com/'
    redirect.close.assert_called_once()


def test_redirect_to_private_target_is_blocked(relay):
    client, session = relay
    session.request.return_value = upstream(302, {'Location': 'http://127.0.0.1/private'})
    with patch('app.proxy.socket.getaddrinfo', side_effect=[PUBLIC_DNS, [(2, 1, 6, '', ('127.0.0.1', 80))]]):
        assert client.get('/proxy', params={'url': 'https://cdn.example/start'}).status_code == 400
    assert session.request.call_count == 1
    session.close.assert_called_once()


@pytest.mark.parametrize('url', ['file:///tmp/video', 'https://a:b@cdn.example/v', 'https://cdn.example/a\nb'])
def test_invalid_url_rejected(url):
    with pytest.raises(Exception) as exc:
        validate_target(url)
    assert exc.value.status_code == 400


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
def test_private_addresses_rejected(address):
    with patch('app.proxy.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 80))]):
        with pytest.raises(Exception) as exc:
            validate_target('http://cdn.example/v')
    assert exc.value.status_code == 400


@pytest.mark.parametrize('error,code', [(requests.Timeout(), 504), (requests.ConnectionError(), 502)])
def test_connection_errors_close_session(relay, error, code):
    client, session = relay
    session.request.side_effect = error
    assert client.get('/proxy', params={'url': 'https://cdn.example/v'}).status_code == code
    session.close.assert_called_once()


def test_head_does_not_read_video(relay):
    client, session = relay
    response = upstream()
    session.request.return_value = response
    result = client.head('/proxy', params={'url': 'https://cdn.example/v'})
    assert result.status_code == 200 and result.content == b''
    assert result.headers['content-length'] == '6'
    response.raw.stream.assert_not_called()
    response.close.assert_called_once()


def test_stream_is_lazy_and_cleanup_on_client_disconnect():
    response, session = upstream(), Mock()
    seen = []
    def chunks(*args, **kwargs):
        for value in (b'first', b'second', b'third'):
            seen.append(value)
            yield value
    response.raw.stream.side_effect = chunks
    stream = MediaStreamResponse(response, session)
    assert seen == []
    async def run():
        async def receive():
            await asyncio.sleep(100)
        async def send(message):
            if message['type'] == 'http.response.body':
                assert message['body'] == b'first'
                raise OSError('client disconnected')
        with pytest.raises(Exception):
            await stream({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    asyncio.run(run())
    assert seen == [b'first']
    response.close.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize('base', ['', 'https://private.example'])
def test_private_ingress_returns_original_cached_video_urls(base):
    original = {'success': True, 'data': {'video_url': SOURCE, 'backup_video_urls': [SOURCE]}, 'quota': {'remaining': 4}}
    with patch('app.main.Quotas') as quota:
        quota.return_value.parse.return_value = (200, deepcopy(original))
        app = create_app(Settings('unused', frozenset({'wxTest'}), proxy_base_url=base))
        app.dependency_overrides[trusted_identity] = lambda: Identity('wxTest', 'test')
        with TestClient(app, base_url='http://internal-service:8080') as client:
            payload = {'request_id': 'restore-direct-request-001', 'url': 'https://v.douyin.com/test'}
            assert client.post('/api/v1/parse', json=payload).json() == original
            assert client.post('/api/v1/parse', json=payload).json() == original
            assert client.get('/proxy', params={'url': SOURCE}).status_code == 404


def test_proxy_is_opt_in_even_with_existing_domain_env(monkeypatch):
    monkeypatch.setenv('WECHAT_PRIVATE_INGRESS_ONLY', 'true')
    monkeypatch.setenv('DATABASE_URL', 'mysql+pymysql://unused')
    monkeypatch.setenv('WECHAT_ALLOWED_APPIDS', 'wxTest')
    monkeypatch.setenv('PROXY_BASE_URL', 'https://private.example')
    monkeypatch.delenv('MEDIA_PROXY_ENABLED', raising=False)
    assert Settings.from_env().media_proxy_enabled is False
    monkeypatch.setenv('MEDIA_PROXY_ENABLED', 'true')
    with pytest.raises(RuntimeError, match='公网代理已停用'):
        Settings.from_env()
    monkeypatch.setenv('PROXY_BASE_URL', '')
    with pytest.raises(RuntimeError, match='公网代理已停用'):
        Settings.from_env()
