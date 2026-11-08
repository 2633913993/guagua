from dataclasses import replace
import time
from unittest.mock import Mock, patch

import pytest
import requests

from app.cloud_transfer import CloudStorage, SourceBody, TransferSettings, READ_SIZE, PART_SIZE
from app.identity import Identity
from app.transfer_http import TransferError
from app.wechat_storage import (TemporaryCredentials, encoded_metadata, internal_request,
                               temporary_credentials, create_cos_client)

WHO = Identity('wxTest', 'real-openid')
SETTINGS = TransferSettings()
AUTH = {'TmpSecretId': 'tmp-id', 'TmpSecretKey': 'tmp-secret', 'Token': 'tmp-token',
        'ExpiredTime': int(time.time()) + 3600}


def test_internal_auth_fixed_origin_and_no_proxy_or_redirects():
    with patch('app.wechat_storage.requests.Session') as constructor:
        session = constructor.return_value.__enter__.return_value
        response = session.request.return_value.__enter__.return_value
        response.status_code = 200
        response.json.return_value = AUTH
        credentials = temporary_credentials()
        assert credentials.secret_id == AUTH['TmpSecretId']
        assert session.trust_env is False
        args, kwargs = session.request.call_args
        assert args == ('GET', 'http://api.weixin.qq.com/_/cos/getauth')
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (3, 8)
        assert AUTH['TmpSecretKey'] not in repr(credentials)
        assert AUTH['Token'] not in repr(credentials)


@pytest.mark.parametrize('data', [
    {}, {**AUTH, 'Token': ''}, {**AUTH, 'ExpiredTime': time.time() - 10},
    {**AUTH, 'ExpiredTime': 'invalid'},
])
def test_invalid_or_expired_temporary_auth_rejected(data):
    with patch('app.wechat_storage.internal_request', return_value=data):
        with pytest.raises(TransferError):
            temporary_credentials()


def test_owner_encoded_metadata_is_distinct_from_file_id():
    key = 'img/file.jpg'
    response = {'errcode': 0, 'respdata': {'x_cos_meta_field_strs': ['encoded-file-metadata']}}
    with patch('app.wechat_storage.internal_request', return_value=response) as call:
        storage = CloudStorage(SETTINGS, client=Mock(), identity=WHO)
        file_id, meta = storage.metadata(key)
        assert file_id == 'cloud://prod-d3gk2acox7105dd30.7072-prod-d3gk2acox7105dd30-1385311964/img/file.jpg'
        assert meta == 'encoded-file-metadata' and meta != file_id
        assert call.call_args.args == ('POST', '/_/cos/metaid/encode', {
            'openid': WHO.openid, 'bucket': SETTINGS.bucket, 'paths': [key]})
    with pytest.raises(TransferError, match='可信身份'):
        encoded_metadata(SETTINGS.bucket, key, None)


@pytest.mark.parametrize('response', [
    {}, {'respdata': {}}, {'respdata': {'x_cos_meta_field_strs': []}},
    {'respdata': {'x_cos_meta_field_strs': ['cloud://not-encoded']}},
    {'respdata': {'x_cos_meta_field_strs': ['invalid\r\nheader']}},
])
def test_missing_metadata_never_silently_falls_back_to_fileid(response):
    with patch('app.wechat_storage.internal_request', return_value=response):
        with pytest.raises(TransferError):
            encoded_metadata(SETTINGS.bucket, 'video/a.mp4', WHO)


def test_internal_api_error_does_not_disclose_response_or_credentials():
    with patch('app.wechat_storage.requests.Session') as constructor:
        session = constructor.return_value.__enter__.return_value
        session.request.side_effect = requests.ConnectionError('secret-token')
        with pytest.raises(TransferError) as result:
            internal_request('GET', '/_/cos/getauth')
        assert 'secret-token' not in str(result.value)


def test_credentials_refreshed_before_next_sdk_operation():
    old = TemporaryCredentials('old-id', 'old-secret', 'old-token', int(time.time()) + 3600)
    fresh = TemporaryCredentials('new-id', 'new-secret', 'new-token', int(time.time()) + 3600)
    client1, client2 = Mock(), Mock()
    with patch('app.cloud_transfer.temporary_credentials', side_effect=[old, fresh]) as auth, \
         patch('app.cloud_transfer.create_cos_client', side_effect=[client1, client2]):
        storage = CloudStorage(SETTINGS, identity=WHO)
        assert storage.client is client1
        assert storage.client is client1
        assert auth.call_count == 1
        storage._expires_at = time.time() + 10
        assert storage.client is client2
        client1._session.close.assert_called_once()
        storage.close()
        client2._session.close.assert_called_once()


def test_known_length_download_is_streamed_directly_into_sdk():
    size = PART_SIZE + 123
    read = 0
    def chunks(chunk_size):
        nonlocal read
        assert chunk_size == READ_SIZE
        while read < size:
            count = min(chunk_size, size - read)
            chunk = (b'\x00\x00\x00\x18ftypisom' + b'x' * count)[:count]
            read += count
            yield chunk
    response = Mock(headers={'Content-Type': 'video/mp4', 'Content-Length': str(size)})
    response.iter_content.side_effect = chunks
    client = Mock()
    calls = []
    def put(**kwargs):
        assert read < size  # Upload starts before the source finishes downloading.
        assert isinstance(kwargs['Body'], SourceBody)
        assert kwargs['EnableMD5'] is False
        assert kwargs['Metadata'] == {'x-cos-meta-fileid': 'encoded'}
        assert kwargs['ContentLength'] == str(size)
        while True:
            data = kwargs['Body'].read(1024 * 1024)
            if not data: break
            assert len(data) <= READ_SIZE
            calls.append(len(data))
        return {'ETag': 'etag'}
    client.put_object.side_effect = put
    storage = CloudStorage(SETTINGS, client=client, identity=WHO)
    storage.metadata = lambda key: ('cloud://env.bucket/' + key, 'encoded')
    result = storage.upload(response, 'https://cdn.example/a.mp4', lambda: None)
    assert sum(calls) == size and result['size'] == size
    assert client.put_object.call_args.kwargs['Key'].startswith('video/')
    client.create_multipart_upload.assert_not_called()


def test_truncated_stream_never_reports_success():
    response = Mock(headers={'Content-Type': 'image/jpeg', 'Content-Length': '100'})
    response.iter_content.return_value = iter([b'\xff\xd8\xffa'])
    client = Mock()
    def put(**kwargs):
        while kwargs['Body'].read(65536): pass
    client.put_object.side_effect = put
    storage = CloudStorage(SETTINGS, client=client)
    storage.metadata = lambda key: ('cloud://env.bucket/' + key, 'encoded')
    with pytest.raises(TransferError, match='不完整'):
        storage.upload(response, 'https://cdn.example/a.jpg', lambda: None)
    client.delete_object.assert_called_once()


def test_internal_sdk_uses_temporary_token_and_no_stream_retries():
    pytest.importorskip('qcloud_cos')
    credential = TemporaryCredentials('tmp-id', 'tmp-key', 'tmp-token', int(time.time()) + 3600)
    client = create_cos_client(SETTINGS, credential)
    assert client._conf._token == 'tmp-token'
    assert client._retry == 0 and client._session.trust_env is False
    client._session.close()


def test_real_cos_sdk_reads_nonseekable_source_over_http():
    pytest.importorskip('qcloud_cos')
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import hashlib
    import threading
    from qcloud_cos import CosConfig, CosS3Client
    size = 2 * 1024 * 1024 + 17
    received = {}
    expected = hashlib.sha256()
    first = b'\xff\xd8\xff' + b'x' * (READ_SIZE - 3)
    def pieces():
        remaining, initial = size, True
        while remaining:
            chunk = first if initial else b'x' * min(READ_SIZE, remaining)
            initial = False
            remaining -= len(chunk)
            expected.update(chunk)
            yield chunk
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_PUT(self):
            remaining = int(self.headers['Content-Length'])
            digest = hashlib.sha256()
            while remaining:
                chunk = self.rfile.read(min(READ_SIZE, remaining))
                if not chunk: break
                digest.update(chunk)
                remaining -= len(chunk)
            received.update(remaining=remaining, digest=digest.digest(), headers=dict(self.headers))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.send_header('ETag', 'test-etag')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = CosS3Client(CosConfig(Region='ap-shanghai', SecretId='tmp-id', SecretKey='tmp-key',
        Token='tmp-token', Scheme='http', Domain='127.0.0.1:' + str(server.server_port)), retry=0)
    client._session.trust_env = False
    try:
        response = Mock(headers={'Content-Type':'image/jpeg','Content-Length':str(size)})
        response.iter_content.return_value = pieces()
        storage = CloudStorage(SETTINGS, client=client, identity=WHO)
        storage.metadata = lambda key: ('cloud://env.bucket/' + key, 'encoded-owner-metadata')
        result = storage.upload(response, 'https://cdn.example/img.jpg', lambda: None)
        assert result['size'] == size
        assert received['remaining'] == 0 and received['digest'] == expected.digest()
        headers = {key.lower(): value for key,value in received['headers'].items()}
        assert headers['x-cos-meta-fileid'] == 'encoded-owner-metadata'
        assert headers['x-cos-security-token'] == 'tmp-token'
    finally:
        client._session.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_internal_credentials_shared_between_jobs_and_expiry_refreshed():
    import app.wechat_storage as module
    credential = TemporaryCredentials('id', 'secret', 'token', int(time.time()) + 3600)
    expired = TemporaryCredentials('id', 'secret', 'token', int(time.time()) + 20)
    with patch.object(module, '_cached_credentials', None), patch.object(module, 'temporary_credentials', return_value=credential) as fetch:
        assert module.cached_temporary_credentials() is credential
        assert module.cached_temporary_credentials() is credential
        assert fetch.call_count == 1
        module._cached_credentials = expired
        assert module.cached_temporary_credentials() is credential
        assert fetch.call_count == 2
