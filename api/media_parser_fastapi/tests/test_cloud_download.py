import asyncio
from dataclasses import replace
import json
import socket
from unittest.mock import Mock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.cloud_transfer import CloudStorage, PART_SIZE, READ_SIZE, TransferSettings
from app.download_tasks import DatabaseTasks, DownloadTasks
from app.identity import Identity, trusted_identity
from app.main import create_app
from app.settings import Settings
from app.transfer_http import (TransferError, PublicHTTPSConnection, open_source,
                               validate_url, resolve_public, source_headers)

WHO = Identity('wxTest', 'a')
CONFIG = TransferSettings(enabled=True, env='env-test', region='ap-shanghai', bucket='bucket-123')
URL = 'https://cdn.example/file.mp4?token=a%2Fb%26c&x=1+2'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://example.com/a', 'http://127.0.0.1/a',
    'http://10.0.0.1/a', 'http://192.168.1.1/a', 'http://169.254.169.254/a', 'http://[::1]/a',
    'http://[::ffff:127.0.0.1]/a', 'http://localhost/a', 'http://a.local/a',
    'http://user:password@example.com/a', 'http://example.com:8080/a', 'http://example.com/\na'])
def test_reject_unsafe_source(url):
    with pytest.raises(TransferError):
        validate_url(url)


def test_dns_rebinding_and_mixed_answers_rejected():
    def answer(ip):
        return (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))
    for ips in [['127.0.0.1'], ['93.184.216.34', '169.254.169.254'], ['100.100.100.200']]:
        with patch('app.transfer_http.socket.getaddrinfo', return_value=[answer(ip) for ip in ips]):
            with pytest.raises(TransferError):
                resolve_public('cdn.example', 443)
    conn = PublicHTTPSConnection('cdn.example', 443, timeout=5)
    with patch('app.transfer_http.resolve_public', return_value=['93.184.216.34']), patch('app.transfer_http.create_connection') as connect:
        conn._new_conn()
        assert connect.call_args.args[0] == ('93.184.216.34', 443)
        assert conn.host == 'cdn.example'


def test_redirects_checked_and_signed_query_preserved():
    session = Mock()
    first = Mock(status_code=302, headers={'Location': 'http://169.254.169.254/latest'})
    session.get.return_value = first
    with pytest.raises(TransferError):
        open_source(session, URL)
    assert session.get.call_count == 1
    assert session.get.call_args.args[0] == URL
    assert session.get.call_args.kwargs['stream'] is True
    assert session.get.call_args.kwargs['allow_redirects'] is False
    first.close.assert_called_once()
    final = Mock(status_code=200)
    session.get.side_effect = [Mock(status_code=302, headers={'Location': '/ok.mp4'}), final]
    assert open_source(session, URL) == (final, 'https://cdn.example/ok.mp4')
    assert 'iPhone' in source_headers(URL)['User-Agent']
    assert source_headers('https://a.bilivideo.com/a')['Referer'] == 'https://www.bilibili.com/'


class Body:
    headers = {'Content-Type': 'video/mp4'}
    def __init__(self, size):
        self.size, self.read = size, 0
    def iter_content(self, chunk_size):
        assert chunk_size == READ_SIZE
        first = b'\x00\x00\x00\x18ftypisom'
        while self.read < self.size:
            count = min(chunk_size, self.size - self.read)
            self.read += count
            yield (first + b'x' * count)[:count]
    @property
    def content(self):
        raise AssertionError('must not buffer full body')


def storage_fixture():
    client = Mock()
    client.create_multipart_upload.return_value = {'UploadId': 'upload-test'}
    client.upload_part.return_value = {'ETag': 'etag-test'}
    storage = CloudStorage(CONFIG, client)
    storage.metadata = lambda key: ('cloud://env-test.bucket-123/' + key, 'real-meta')
    return storage, client


def test_multipart_upload_streams_before_download_finishes():
    body = Body(PART_SIZE * 2 + 123)
    storage, client = storage_fixture()
    sizes, read = [], []
    def part(**kwargs):
        sizes.append(len(kwargs['Body']))
        read.append(body.read)
        return {'ETag': 'etag-' + str(kwargs['PartNumber'])}
    client.upload_part.side_effect = part
    result = storage.upload(body, URL, lambda: None)
    assert sizes == [PART_SIZE, PART_SIZE, 123]
    assert read[0] < body.size
    assert result['size'] == body.size and result['fileID'].startswith('cloud://')
    assert result['extension'] == '.mp4'
    assert client.create_multipart_upload.call_args.kwargs['Key'].startswith('video/')
    assert client.create_multipart_upload.call_args.kwargs['Metadata'] == {'x-cos-meta-fileid': 'real-meta'}
    assert client.complete_multipart_upload.call_args.kwargs['MultipartUpload']['Part'][2]['PartNumber'] == 3
    client.abort_multipart_upload.assert_not_called()


def test_abort_on_failure_and_unknown_length_limit():
    storage, client = storage_fixture()
    client.upload_part.side_effect = RuntimeError('secret must not leak')
    with pytest.raises(RuntimeError):
        storage.upload(Body(PART_SIZE + 1), URL, lambda: None)
    client.abort_multipart_upload.assert_called_once()
    client.complete_multipart_upload.assert_not_called()
    storage, client = storage_fixture()
    storage.settings = replace(CONFIG, max_bytes=100)
    with pytest.raises(TransferError, match='超过'):
        storage.upload(Body(101), URL, lambda: None)
    client.abort_multipart_upload.assert_called_once()


@pytest.fixture
def jobs():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE download_tasks (task_id TEXT PRIMARY KEY, owner_key TEXT, '
            'active_owner_key TEXT UNIQUE, status TEXT, result_json TEXT, '
            'created_at DATETIME, updated_at DATETIME, expires_at DATETIME)'))
    def transfer(url, settings, identity=None):
        assert identity == WHO
        return dict(fileID='cloud://env.bucket/downloads/file.mp4', contentType='video/mp4', size=2000000, extension='.mp4')
    manager = DownloadTasks(CONFIG, engine=engine, transfer=transfer)
    yield manager, engine
    engine.dispose()


def test_submit_is_queued_and_status_shared_between_instances(jobs):
    manager, engine = jobs
    background = BackgroundTasks()
    result = manager.submit(URL, WHO, background)
    assert result['code'] == 0
    assert manager.status(result['task_id'], WHO) == {'status': 'processing'}
    with pytest.raises(HTTPException) as exc:
        manager.submit(URL, WHO, BackgroundTasks())
    assert exc.value.status_code == 429
    asyncio.run(background())
    other = DownloadTasks(CONFIG, engine=engine)
    status = other.status(result['task_id'], WHO)
    assert status['status'] == 'success' and status['size'] > 1024 * 1024
    with pytest.raises(HTTPException) as exc:
        other.status(result['task_id'], Identity('wxTest', 'b'))
    assert exc.value.status_code == 404
    manager.submit(URL, WHO, BackgroundTasks())  # active owner released


def test_failed_task_and_no_exception_secrets(jobs):
    manager, _ = jobs
    manager.transfer = Mock(side_effect=RuntimeError('secret-id'))
    background = BackgroundTasks()
    result = manager.submit(URL, WHO, background)
    asyncio.run(background())
    status = manager.status(result['task_id'], WHO)
    assert status['status'] == 'failed' and 'secret-id' not in status['msg']


def test_private_routes_require_identity_and_never_stream(jobs):
    manager, _ = jobs
    with patch('app.main.Quotas'):
        app = create_app(Settings('unused', frozenset({'wxTest'})))
        with TestClient(app) as client:
            app.state.download_tasks = manager
            assert client.post('/api/download/submit', json={'url': URL}).status_code == 401
            app.dependency_overrides[trusted_identity] = lambda: WHO
            task = client.post('/api/download/submit', json={'url': URL})
            assert task.status_code == 200 and len(task.content) < 1024
            state = client.get('/api/download/status', params={'task_id': task.json()['task_id']})
            assert state.json()['status'] == 'success'
            for path in ['/proxy', '/api/proxy-download']:
                assert client.get(path, params={'url': URL}).status_code == 404


def test_actual_cos_sdk_configuration_and_request_contract():
    pytest.importorskip('qcloud_cos')
    from app.wechat_storage import TemporaryCredentials, create_cos_client
    client = create_cos_client(CONFIG, TemporaryCredentials('fakeid', 'fakekey', 'token', 9999999999))
    with patch.object(client, 'send_request', return_value={'data': b'<InitiateMultipartUploadResult><UploadId>x</UploadId></InitiateMultipartUploadResult>', 'headers': {}}) as send:
        # Signature inspection uses the installed official SDK without network.
        import inspect
        assert 'MultipartUpload' in inspect.signature(client.complete_multipart_upload).parameters
        assert 'Body' in inspect.signature(client.upload_part).parameters
        assert 'Metadata' not in inspect.signature(client.create_multipart_upload).parameters  # **kwargs


@pytest.mark.parametrize('mime,payload,suffix,prefix', [
    ('image/jpeg', b'\xff\xd8\xffjpeg', '.jpg', 'img/'),
    ('video/mp4', b'\x00\x00\x00\x18ftypisomvideo', '.mp4', 'video/'),
    ('application/pdf', b'%PDF-1.7 file', '.pdf', 'downloads/'),
])
def test_upload_uses_bucket_relative_media_directories(mime, payload, suffix, prefix):
    storage, client = storage_fixture()
    response = Mock(headers={'Content-Type': mime})
    response.iter_content.return_value = iter((payload,))
    storage.settings = replace(CONFIG, bucket='7072-prod-d3gk2acox7105dd30-1385311964')
    seen = []
    def metadata(key):
        seen.append(key)
        return 'cloud://env.bucket/' + key, 'meta'
    storage.metadata = metadata
    result = storage.upload(response, 'https://cdn.example/file' + suffix, lambda: None)
    args = client.create_multipart_upload.call_args.kwargs
    assert args['Bucket'] == storage.settings.bucket
    assert args['Key'].startswith(prefix) and args['Key'].endswith(suffix)
    assert storage.settings.bucket not in args['Key']
    assert seen == [args['Key']]
    assert result['fileID'].endswith('/' + args['Key'])
    assert client.upload_part.call_args.kwargs['Key'] == args['Key']
    assert client.complete_multipart_upload.call_args.kwargs['Key'] == args['Key']


def test_storage_defaults_use_user_bucket_without_requiring_override(monkeypatch):
    monkeypatch.delenv('COS_BUCKET', raising=False)
    monkeypatch.delenv('CLOUD_STORAGE_ENV', raising=False)
    monkeypatch.delenv('COS_REGION', raising=False)
    monkeypatch.setenv('CLOUD_DOWNLOAD_ENABLED', 'false')
    settings = TransferSettings.from_env()
    assert settings.bucket == '7072-prod-d3gk2acox7105dd30-1385311964'
    assert settings.env == 'prod-d3gk2acox7105dd30'
    assert settings.region == 'ap-shanghai'
    assert settings.enabled is True  # obsolete false switch does not disable internal storage
    monkeypatch.setenv('COS_BUCKET', 'override-123')
    assert TransferSettings.from_env().bucket == 'override-123'
    monkeypatch.setenv('COS_REGION', 'ap-guangzhou')
    assert TransferSettings.from_env().region == 'ap-guangzhou'


def test_large_known_length_pipelines_download_during_upload():
    import threading
    first_upload = threading.Event()
    continued_download = threading.Event()
    size = PART_SIZE * 2 + 123
    class StreamingBody(Body):
        headers = {'Content-Type': 'video/mp4', 'Content-Length': str(size)}
        def iter_content(self, chunk_size):
            for chunk in super().iter_content(chunk_size):
                if self.read > PART_SIZE:
                    assert first_upload.wait(2), 'first upload did not start'
                    continued_download.set()
                yield chunk
    body = StreamingBody(size)
    storage, client = storage_fixture()
    sizes = []
    def upload(**kwargs):
        sizes.append(len(kwargs['Body']))
        if kwargs['PartNumber'] == 1:
            first_upload.set()
            assert continued_download.wait(2), 'download stalled behind the upload'
        return {'ETag': str(kwargs['PartNumber'])}
    client.upload_part.side_effect = upload
    result = storage.upload(body, URL, lambda: None)
    assert result['size'] == size
    assert sizes == [PART_SIZE, PART_SIZE, 123]
    client.put_object.assert_not_called()
    assert [p['PartNumber'] for p in client.complete_multipart_upload.call_args.kwargs['MultipartUpload']['Part']] == [1, 2, 3]


def test_large_truncated_length_aborts_instead_of_publishing():
    body = Body(PART_SIZE + 123)
    body.headers = {'Content-Type': 'video/mp4', 'Content-Length': str(PART_SIZE * 2)}
    storage, client = storage_fixture()
    with pytest.raises(TransferError, match='不完整'):
        storage.upload(body, URL, lambda: None)
    client.complete_multipart_upload.assert_not_called()
    client.abort_multipart_upload.assert_called_once()


def test_completed_transfer_reused_only_for_exact_owner_url_and_target(jobs):
    manager, engine = jobs
    background = BackgroundTasks()
    first = manager.submit(URL, WHO, background)
    asyncio.run(background())
    manager.transfer = Mock(side_effect=AssertionError('cached video should not transfer again'))
    second_background = BackgroundTasks()
    assert manager.submit(URL, WHO, second_background) == first
    assert not second_background.tasks
    other = DownloadTasks(CONFIG, engine=engine)
    assert other.submit(URL, WHO, BackgroundTasks()) == first, 'cache must work across service instances'
    assert manager.submit(URL, Identity('wxTest', 'another-owner'), BackgroundTasks()) != first
    assert manager.submit(URL + '&different-signature=1', WHO, BackgroundTasks()) != first
    # Release the test-only unstarted active task before checking the next miss.
    with engine.begin() as conn:
        conn.execute(text('UPDATE download_tasks SET active_owner_key=NULL'))
    manager.settings = replace(CONFIG, bucket='other-123')
    assert manager.submit(URL, WHO, BackgroundTasks()) != first


def test_cache_expired_disabled_or_failed_jobs_never_reused(jobs):
    from datetime import datetime, timedelta, timezone
    manager, engine = jobs
    bg = BackgroundTasks()
    first = manager.submit(URL, WHO, bg)
    asyncio.run(bg())
    with engine.begin() as conn:
        conn.execute(text('UPDATE download_tasks SET updated_at=:old'),
                     {'old': datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=301)})
    assert manager.submit(URL, WHO, BackgroundTasks()) != first
    with engine.begin() as conn:
        conn.execute(text('UPDATE download_tasks SET active_owner_key=NULL'))
    manager.settings = replace(CONFIG, cache_seconds=0)
    assert manager.submit(URL, WHO, BackgroundTasks()) != first
    with engine.begin() as conn:
        conn.execute(text('UPDATE download_tasks SET active_owner_key=NULL, status=\'FAILED\''))
    manager.settings = CONFIG
    assert manager.submit(URL, WHO, BackgroundTasks()) != first


def test_cache_failure_falls_back_to_fresh_transfer(jobs):
    manager, _ = jobs
    with patch.object(manager.store, 'reusable', side_effect=RuntimeError('database transient')):
        assert manager.submit(URL, WHO, BackgroundTasks())['code'] == 0
