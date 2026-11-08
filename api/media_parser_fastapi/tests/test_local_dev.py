"""Local runner must not turn client headers into real WeChat identities."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from local_dev import TEST_IDENTITY, create_local_app, local_identity

URL = 'mysql+pymysql://unused@127.0.0.1/guagua_test_local'


def request(peer='127.0.0.1', headers=()):
    return Request({'type': 'http', 'client': (peer, 1234), 'headers': list(headers)})


def test_local_identity_is_fixed_and_loopback_only():
    assert local_identity(request()) == TEST_IDENTITY
    with pytest.raises(HTTPException) as exc:
        local_identity(request('192.168.1.50'))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        local_identity(request(headers=((b'x-wx-openid', b'forged'),)))
    assert exc.value.status_code == 400


@pytest.mark.parametrize('url', [
    'mysql+pymysql://unused@remote.example/guagua_test_local',
    'mysql+pymysql://unused@127.0.0.1/production',
    'sqlite:///guagua_test_local',
])
def test_local_runner_rejects_nonlocal_or_business_database(url):
    with pytest.raises(RuntimeError):
        create_local_app(url)


def test_local_health_without_private_gateway_attestation(monkeypatch):
    monkeypatch.delenv('WECHAT_PRIVATE_INGRESS_ONLY', raising=False)
    with TestClient(create_local_app(URL)) as client:
        assert client.get('/api/v1/health').status_code == 200
        assert '/api/v1/quota' in client.get('/openapi.json').json()['paths']
    assert 'WECHAT_PRIVATE_INGRESS_ONLY' not in __import__('os').environ


def test_production_image_excludes_local_identity_entry():
    docker = Path(__file__).parents[1].joinpath('Dockerfile').read_text()
    assert '"--production"' in docker
    assert 'COPY local_dev' not in docker
