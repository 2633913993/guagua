"""Stream external files into CloudBase's COS bucket using bounded multipart I/O."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import mimetypes
import os
from pathlib import PurePosixPath
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .transfer_http import TransferError, open_source, source_session
from .wechat_storage import cached_temporary_credentials as temporary_credentials, encoded_metadata, create_cos_client

logger = logging.getLogger('uvicorn.error.transfer')
PART_SIZE = 4 * 1024 * 1024
READ_SIZE = 64 * 1024
DEFAULT_STORAGE_ENV = 'prod-d3gk2acox7105dd30'
DEFAULT_COS_REGION = 'ap-shanghai'
DEFAULT_COS_BUCKET = '7072-prod-d3gk2acox7105dd30-1385311964'


@dataclass(frozen=True)
class TransferSettings:
    enabled: bool = True
    env: str = DEFAULT_STORAGE_ENV
    region: str = DEFAULT_COS_REGION
    bucket: str = DEFAULT_COS_BUCKET
    max_bytes: int = 512 * 1024 * 1024
    deadline: int = 300
    multipart_threshold: int = 8 * 1024 * 1024
    cache_seconds: int = 300

    @classmethod
    def from_env(cls):
        # Internal platform credentials replace API keys and static COS secrets.
        # The obsolete CLOUD_DOWNLOAD_ENABLED switch is deliberately ignored.
        config = cls(
            env=os.getenv('CLOUD_STORAGE_ENV') or DEFAULT_STORAGE_ENV,
            region=os.getenv('COS_REGION') or DEFAULT_COS_REGION,
            bucket=os.getenv('COS_BUCKET') or DEFAULT_COS_BUCKET,
            max_bytes=int(os.getenv('DOWNLOAD_MAX_BYTES', str(512 * 1024 * 1024))),
            deadline=int(os.getenv('DOWNLOAD_DEADLINE_SECONDS', '300')),
            multipart_threshold=int(os.getenv('DOWNLOAD_MULTIPART_THRESHOLD_BYTES', str(8 * 1024 * 1024))),
            cache_seconds=int(os.getenv('DOWNLOAD_CACHE_SECONDS', '300')),
        )
        if (not re.fullmatch(r'[a-zA-Z0-9-]+', config.env) or
                not re.fullmatch(r'ap-[a-z0-9-]+', config.region) or
                not re.fullmatch(r'[a-z0-9-]+-\d+', config.bucket)):
            raise RuntimeError('云存储环境、地域或存储桶配置无效')
        if not (1 <= config.max_bytes <= 2 * 1024**3 and 30 <= config.deadline <= 600):
            raise RuntimeError('转存大小须为 1 字节至 2GiB，任务时限须为 30 至 600 秒')
        if not (PART_SIZE <= config.multipart_threshold <= 128 * 1024 * 1024 and 0 <= config.cache_seconds <= 1800):
            raise RuntimeError('分片阈值须为 4 至 128MiB，转存缓存须为 0 至 1800 秒')
        return config


def file_type(headers, url, head):
    mime = headers.get('Content-Type', '').split(';')[0].strip().lower()
    # Prefer binary signatures; some CDNs incorrectly label video as JSON.
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', '.gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    if head[4:8] == b'ftyp':
        if b'avif' in head[8:64] or b'avis' in head[8:64]:
            return 'image/avif', '.avif'
        if any(brand in head[8:64] for brand in (b'heic', b'heix', b'hevc', b'hevx')):
            return 'image/heic', '.heic'
        if head[8:12] == b'qt  ':
            return 'video/quicktime', '.mov'
        return 'video/mp4', '.mp4'
    if head.lstrip(b'\xef\xbb\xbf \r\n\t').startswith((b'<', b'{', b'[')) or mime in ('text/html', 'application/json'):
        raise TransferError('下载地址返回网页或错误信息，请先解析分享链接后重试')
    if head.startswith(b'#EXTM3U') or 'mpegurl' in mime or urlsplit(url).path.lower().endswith('.m3u8'):
        raise TransferError('视频播放列表暂不支持直接转存，请使用视频文件直链')
    ext = mimetypes.guess_extension(mime) or PurePosixPath(urlsplit(url).path).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', ext or ''):
        ext = '.bin'
    return mime or 'application/octet-stream', '.jpg' if ext == '.jpe' else ext


class SourceBody:
    """Non-rewindable SDK body; at most READ_SIZE bytes returned by each read."""
    def __init__(self, chunks, length, limit, check):
        self.chunks = iter(chunks)
        self.length, self.limit, self.check = length, limit, check
        self.pending, self.total, self.eof = b'', 0, False

    def __len__(self):
        return self.length

    def read(self, size=READ_SIZE):
        self.check()
        if size == 0:
            return b''
        count = READ_SIZE if size is None or size < 0 else min(size, READ_SIZE)
        while not self.pending and not self.eof:
            self.pending = next(self.chunks, None)
            if self.pending is None:
                self.pending, self.eof = b'', True
        value, self.pending = self.pending[:count], self.pending[count:]
        self.total += len(value)
        if self.total > self.limit or self.total > self.length:
            raise TransferError('文件超过转存大小限制或声明的文件大小')
        if not value and self.total != self.length:
            raise TransferError('文件源传输中断，下载内容不完整')
        return value

    def verify_complete(self):
        if self.total != self.length or self.read(1):
            raise TransferError('文件源传输中断，下载内容不完整')


class CloudStorage:
    def __init__(self, settings, client=None, identity=None):
        self.settings, self.identity = settings, identity
        self._client, self._injected = client, client is not None
        self._expires_at = 0

    @property
    def client(self):
        if not self._injected and (self._client is None or time.time() + 60 >= self._expires_at):
            credentials = temporary_credentials()
            previous = self._client
            self._client = create_cos_client(self.settings, credentials)
            self._expires_at = credentials.expires_at
            if previous is not None:
                previous._session.close()
        return self._client

    def metadata(self, key):
        meta = encoded_metadata(self.settings.bucket, key, self.identity)
        file_id = 'cloud://%s.%s/%s' % (self.settings.env, self.settings.bucket, key)
        return file_id, meta

    def close(self):
        if self._client is not None and not self._injected:
            self._client._session.close()

    def upload(self, response, source_url, check_deadline):
        encoding = response.headers.get('Content-Encoding', 'identity').lower()
        if encoding not in ('', 'identity'):
            raise TransferError('文件源返回了不支持的压缩传输格式')
        declared = response.headers.get('Content-Length')
        if declared is not None:
            try:
                declared = int(declared)
            except ValueError:
                raise TransferError('文件源返回了无效的文件大小') from None
            if declared <= 0 or declared > self.settings.max_bytes:
                raise TransferError('文件为空或超过转存大小限制')
        stream = response.iter_content(chunk_size=READ_SIZE)
        first = next((chunk for chunk in stream if chunk), b'')
        if not first:
            raise TransferError('下载文件为空')
        mime, ext = file_type(response.headers, source_url, first[:512])
        # COS Key is relative to the bucket, never bucket-name/img/...
        inferred_mime = (mime if mime.startswith(('image/', 'video/'))
                         else mimetypes.guess_type('file' + ext)[0] or '')
        prefix = ('img' if inferred_mime.startswith('image/') else
                  'video' if inferred_mime.startswith('video/') else 'downloads')
        key = '%s/%s%s' % (prefix, uuid4().hex, ext)
        check_deadline()
        file_id, meta = self.metadata(key)
        args = dict(Bucket=self.settings.bucket, Key=key)
        check_deadline()
        if declared is not None and declared < self.settings.multipart_threshold:
            from itertools import chain
            body = SourceBody(chain((first,), stream), declared, self.settings.max_bytes, check_deadline)
            try:
                # Known-length sources go directly into COS SDK as a read stream.
                self.client.put_object(**args, Body=body, ContentLength=str(declared),
                    ContentType=mime, Metadata={'x-cos-meta-fileid': meta}, EnableMD5=False)
                body.verify_complete()
            except Exception:
                # A successful PUT followed by an integrity error must not leave
                # a truncated/inconsistent object available to the mini-program.
                try:
                    self.client.delete_object(**args)
                except Exception:
                    logger.warning('COS object cleanup failed; lifecycle cleanup is required')
                raise
            return {'fileID': file_id, 'contentType': mime, 'extension': ext, 'size': body.total}
        client = self.client
        upload_id = client.create_multipart_upload(
            **args, ContentType=mime, Metadata={'x-cos-meta-fileid': meta})['UploadId']
        parts, total = [], 0
        buffer = bytearray()
        pending = None

        def upload_part(number, data):
            check_deadline()
            result = client.upload_part(**args, UploadId=upload_id,
                        PartNumber=number, Body=data, EnableMD5=True)
            return {'PartNumber': number, 'ETag': result['ETag']}

        def send_part(pool):
            nonlocal pending
            check_deadline()
            if pending is not None:
                parts.append(pending.result())
            # One uploading part plus one filling buffer. No unbounded queue and
            # no concurrent use of the same SDK session by multiple workers.
            data = bytes(buffer)
            buffer.clear()
            pending = pool.submit(upload_part, len(parts) + 1, data)

        try:
            from itertools import chain
            # Wait for the upload worker before aborting or completing the job.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='cos-part') as pool:
                for chunk in chain((first,), stream):
                    check_deadline()
                    if pending is not None and pending.done():
                        pending.result()  # Surface upload failure before reading more.
                    total += len(chunk)
                    if total > self.settings.max_bytes or (declared is not None and total > declared):
                        raise TransferError('文件超过转存大小限制或声明的文件大小')
                    buffer.extend(chunk)
                    if len(buffer) >= PART_SIZE:
                        send_part(pool)
                if declared is not None and total != declared:
                    raise TransferError('文件源传输中断，下载内容不完整')
                if buffer:
                    send_part(pool)
                if pending is not None:
                    parts.append(pending.result())
            check_deadline()
            client.complete_multipart_upload(**args, UploadId=upload_id,
                                             MultipartUpload={'Part': parts})
        except Exception:
            try:
                client.abort_multipart_upload(**args, UploadId=upload_id)
            except Exception:
                logger.warning('Multipart cleanup failed; configure COS lifecycle for incomplete parts')
            raise
        return {'fileID': file_id, 'contentType': mime, 'extension': ext, 'size': total}


def transfer_file(url, settings, storage=None, identity=None):
    started = time.monotonic()
    deadline = started + settings.deadline
    def check():
        if time.monotonic() > deadline:
            raise TransferError('下载转存超时，请重新提交')
    target = storage or CloudStorage(settings, identity=identity)
    try:
        # Resolve internal credentials before opening the external file stream.
        target.client
        auth_finished = time.monotonic()
        check()
        with source_session() as session:
            response, final_url = open_source(session, url)
            source_opened = time.monotonic()
            with response:
                check()
                result = target.upload(response, final_url, check)
                finished = time.monotonic()
                logger.info('Transfer complete size=%d auth_ms=%d source_open_ms=%d stream_ms=%d total_ms=%d',
                            result['size'], (auth_finished-started)*1000,
                            (source_opened-auth_finished)*1000, (finished-source_opened)*1000,
                            (finished-started)*1000)
                return result
    finally:
        target.close()
