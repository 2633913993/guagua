"""剪映公开分享接口；请求校验格式来自官方分享页脚本。"""
import hashlib
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from .common import MOBILE_HEADERS, TIMEOUT, session, normalize_media_url, first_nonempty_string

_API_PATH = '/lv/v1/web/replicate/multi_get_templates'


class JianyingPreviewOnlyError(RuntimeError):
    """公开预览不能作为无水印解析结果。"""


def template_result(payload: dict, template_id: str) -> dict:
    data = payload.get('data')
    templates = data.get('templates', []) if isinstance(data, dict) else []
    for item in templates if isinstance(templates, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get('web_id') or item.get('id') or '') != template_id:
            continue
        if item.get('status') in (4, 5, 6, 7, 100):
            raise RuntimeError('剪映作品已失效或不可见。')
        purchase = item.get('purchase_info')
        if isinstance(purchase, dict) and purchase.get('need_purchase'):
            raise JianyingPreviewOnlyError('该剪映链接为付费模板，暂未获取到无水印视频源，本次不扣解析次数。')
        video_url = normalize_media_url(item.get('video_url'))
        if not video_url:
            raise RuntimeError('剪映公开分享未提供当前作品的视频地址。')
        return {
            'platform': '剪映', 'type': 'video', 'template_id': template_id,
            'title': first_nonempty_string([item.get('title'), item.get('short_title')], '剪映作品'),
            'video_url': video_url, 'image_urls': [],
            'cover': normalize_media_url(item.get('cover_url')) or '',
            'width': item.get('cover_width'), 'height': item.get('cover_height'),
            'duration': item.get('duration'), 'watermark_free': None,
            'source': 'jianying_public_share_api',
        }
    raise RuntimeError('剪映未返回当前作品，请确认链接有效或稍后重试。')


def parse_jianying(final_url: str, share_url: Optional[str] = None) -> dict:
    query = parse_qs(urlparse(final_url).query)
    if not query.get('template_id') and share_url:
        query = parse_qs(urlparse(share_url).query)
    template_id = query.get('template_id', [''])[0]
    if not template_id.isascii() or not template_id.isdigit():
        raise ValueError('剪映分享链接缺少有效的 template_id。')
    item_type = query.get('item_type', ['1'])[0]
    if not item_type.isascii() or not item_type.isdigit():
        raise ValueError('剪映分享链接的 item_type 无效。')
    timestamp = str(int(time.time()))
    # 官方网页使用的公开请求完整性校验，无需 Cookie 或账号凭据。
    signature = hashlib.md5(f'9e2c|{_API_PATH[-7:]}|1||{timestamp}||11ac'.encode()).hexdigest()
    headers = {**MOBILE_HEADERS, 'Referer': 'https://lv.ulikecam.com/',
               'sign': signature, 'pf': '1', 'sign-ver': '1', 'device-time': timestamp}
    try:
        response = session.post('https://lv-api.ulikecam.com' + _API_PATH,
                                json={'sdk_version': '100.0.0', 'id': [template_id],
                                      'scene': 'share', 'item_type': item_type},
                                headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError('剪映分享接口请求失败，请稍后重试。') from exc
    if not isinstance(payload, dict) or str(payload.get('ret')) != '0':
        raise RuntimeError('剪映分享接口暂未返回作品，请稍后重试。')
    return template_result(payload, template_id)
