"""WeChat Cloud Run internal COS APIs; never accept an API origin from a client."""
from dataclasses import dataclass, field
import time
import threading

import requests

from .transfer_http import TransferError

INTERNAL_ORIGIN = 'http://api.weixin.qq.com'


@dataclass(frozen=True)
class TemporaryCredentials:
    secret_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: int


def internal_request(method, path, payload=None):
    # Cloud Run intercepts these fixed HTTP open-interface routes internally.
    # Never use the untrusted-source HTTP adapter for this platform endpoint.
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.request(method, INTERNAL_ORIGIN + path, json=payload,
                                 timeout=(3, 8), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise TransferError('微信云托管内部存储接口不可用，请检查服务的对象存储访问权限')
                data = response.json()
    except (requests.RequestException, ValueError):
        raise TransferError('无法访问微信云托管内部存储接口，请在微信云托管中运行此服务') from None
    if not isinstance(data, dict) or data.get('errcode', 0) != 0:
        code = data.get('errcode') if isinstance(data, dict) else None
        suffix = '（错误码 %s）' % code if isinstance(code, int) else ''
        raise TransferError('微信云托管内部存储接口调用失败' + suffix)
    return data


def temporary_credentials():
    data = internal_request('GET', '/_/cos/getauth')
    values = [data.get(key) for key in ('TmpSecretId', 'TmpSecretKey', 'Token')]
    try:
        expires_at = int(data.get('ExpiredTime', 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not all(isinstance(value, str) and value for value in values) or expires_at <= time.time() + 60:
        raise TransferError('微信云托管返回的临时存储凭证无效或已过期')
    return TemporaryCredentials(*values, expires_at)


_credentials_lock = threading.Lock()
_cached_credentials = None


def cached_temporary_credentials():
    """Reuse server credentials until the refresh window, never owner metadata."""
    global _cached_credentials
    with _credentials_lock:
        if _cached_credentials is None or _cached_credentials.expires_at <= time.time() + 60:
            _cached_credentials = temporary_credentials()
        return _cached_credentials


def encoded_metadata(bucket, key, identity):
    if identity is None or not identity.openid:
        raise TransferError('缺少微信可信身份，无法生成文件访问元数据')
    data = internal_request('POST', '/_/cos/metaid/encode', {
        'openid': identity.openid, 'bucket': bucket, 'paths': [key],
    })
    info = data.get('respdata')
    values = info.get('x_cos_meta_field_strs') if isinstance(info, dict) else None
    if (not isinstance(values, list) or len(values) != 1 or
            not isinstance(values[0], str) or not values[0] or
            values[0].startswith('cloud://') or any(c in values[0] for c in '\r\n')):
        raise TransferError('微信云托管返回的文件访问元数据无效')
    return values[0]


def create_cos_client(settings, credentials):
    from qcloud_cos import CosConfig, CosS3Client
    client = CosS3Client(CosConfig(
        Region=settings.region, SecretId=credentials.secret_id,
        SecretKey=credentials.secret_key, Token=credentials.token,
        Scheme='https', Timeout=60,
    ), retry=0)  # The source stream cannot be rewound after a partially sent PUT.
    client._session.trust_env = False
    return client
