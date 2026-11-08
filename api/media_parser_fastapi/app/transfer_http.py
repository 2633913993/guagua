"""Public HTTP sources only; DNS is checked and pinned at socket creation."""
from ipaddress import ip_address
import socket
from urllib.parse import urljoin, urlsplit, urldefrag

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util.connection import create_connection

MOBILE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) '
             'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1')
REDIRECTS = {301, 302, 303, 307, 308}


class TransferError(Exception):
    """Only safe, user-readable messages (never credentials or signed URLs)."""


def public_ip(address):
    ip = ip_address(address)
    if getattr(ip, 'ipv4_mapped', None):
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def validate_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or
                parts.username is not None or parts.password is not None or
                any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url or
                '%' in parts.hostname or parts.port not in (None, 80, 443)):
            raise ValueError()
        host = parts.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError()
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and not public_ip(str(address)):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise TransferError('仅支持公网 HTTP/HTTPS 文件地址，禁止访问内网或元数据地址') from None
    return urldefrag(url)[0]


def resolve_public(host, port):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not public_ip(item[4][0]) for item in addresses):
            raise TransferError('目标域名解析到非公网地址，已拒绝下载')
        return list(dict.fromkeys(item[4][0] for item in addresses))
    except OSError:
        raise TransferError('文件源域名解析失败') from None


class PinnedConnection:
    def _new_conn(self):
        # Only the numeric address reaches create_connection. TLS SNI, Host and
        # certificate verification continue to use the original hostname.
        addresses = resolve_public(self.host, self.port)
        for address in addresses:
            try:
                return create_connection((address, self.port), self.timeout,
                                         source_address=self.source_address,
                                         socket_options=self.socket_options)
            except OSError:
                continue
        raise TransferError('文件源连接失败')


class PublicHTTPConnection(PinnedConnection, HTTPConnection):
    pass


class PublicHTTPSConnection(PinnedConnection, HTTPSConnection):
    pass


class PublicHTTPPool(HTTPConnectionPool):
    ConnectionCls = PublicHTTPConnection


class PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = PublicHTTPSConnection


class PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {'http': PublicHTTPPool, 'https': PublicHTTPSPool}


def source_headers(url):
    host = (urlsplit(url).hostname or '').lower()
    platforms = {
        'bilibili.com': 'https://www.bilibili.com/', 'bilivideo.com': 'https://www.bilibili.com/',
        'bilivideo.cn': 'https://www.bilibili.com/', 'douyin.com': 'https://www.douyin.com/',
        'douyinvod.com': 'https://www.douyin.com/', 'iesdouyin.com': 'https://www.douyin.com/',
        'byteimg.com': 'https://www.douyin.com/', 'douyinpic.com': 'https://www.douyin.com/',
        'kwaicdn.com': 'https://www.kuaishou.com/', 'kuaishou.com': 'https://www.kuaishou.com/',
        'gifshow.com': 'https://www.kuaishou.com/', 'ksapisrv.com': 'https://www.kuaishou.com/',
        'xiaohongshu.com': 'https://www.xiaohongshu.com/', 'xhscdn.com': 'https://www.xiaohongshu.com/',
        'xiaohongshu.com.cn': 'https://www.xiaohongshu.com/',
    }
    referer = next((value for domain, value in platforms.items()
                    if host == domain or host.endswith('.' + domain)),
                   urlsplit(url)._replace(path='/', query='', fragment='').geturl())
    return {'User-Agent': MOBILE_UA, 'Referer': referer, 'Accept': '*/*', 'Accept-Encoding': 'identity'}


def open_source(session, url):
    target = validate_url(url)
    for hop in range(6):
        # Automatic redirects would skip SSRF validation. Follow manually with
        # identical semantics, validating every hop before opening its socket.
        target = validate_url(target)
        response = session.get(target, headers=source_headers(target), stream=True,
                               allow_redirects=False, timeout=(10, 60))
        if response.status_code not in REDIRECTS:
            if response.status_code != 200:
                code = response.status_code
                response.close()
                raise TransferError('文件源拒绝下载或链接已失效（HTTP %s）' % code)
            return response, target
        location = response.headers.get('Location')
        response.close()
        if not location or hop == 5:
            raise TransferError('文件源重定向异常或次数过多')
        target = urljoin(target, location)


def source_session():
    session = requests.Session()
    session.trust_env = False  # Never route validated sources through env proxies.
    adapter = PublicAdapter(max_retries=0)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session
