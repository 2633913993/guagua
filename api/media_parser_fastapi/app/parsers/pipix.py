"""皮皮虾公开分享页解析。"""
import re
from typing import Optional

import requests

from .common import MOBILE_HEADERS, PC_HEADERS, fetch_page, iter_nodes, first_nonempty_string, dedupe
from .share_page import page_states, media_urls, image_urls

_MARKERS = ('window.__INITIAL_STATE__', 'window._ROUTER_DATA', 'window.__NEXT_DATA__')


def parse_pipix_page(html: str, actual_url: str) -> Optional[dict]:
    match = re.search(r'/(?:ppx/)?item/(\d+)', actual_url)
    target = match.group(1) if match else None
    for state, source in page_states(html, _MARKERS):
        if not isinstance(state, dict):
            continue
        detail = state.get('ppxItemDetail') or state.get('itemDetail') or state.get('data') or state
        if not isinstance(detail, dict):
            continue
        item = detail.get('item') or detail.get('item_info')
        if not isinstance(item, dict):
            continue
        item_id = str(item.get('item_id_str') or item.get('item_id') or '')
        if target and item_id and target != item_id:
            continue
        result = {
            'platform': '皮皮虾', 'title': first_nonempty_string([item.get('content'), item.get('title')], '皮皮虾作品'),
            'type': 'unknown', 'video_url': None, 'image_urls': [], 'source': source,
            'item_id': item_id, 'watermark_free': None,
        }
        video = item.get('video') or {}
        if not isinstance(video, dict):
            video = {}
        result['title'] = first_nonempty_string([item.get('content'), video.get('text'), video.get('title'), result['title']], '皮皮虾作品')
        covers = media_urls(video.get('cover_image') or item.get('cover'), actual_url)
        result['cover'] = covers[0] if covers else ''
        # 某些分享页在评论上下文中重复携带当前作品的原播放源。
        # 必须同时匹配字符串 item_id 和 video_id，不能获取评论自己的视频。
        videos = [video]
        if item_id and video.get('video_id'):
            for node in iter_nodes(state.get('ppxCellComment', {})):
                if not isinstance(node, dict) or str(node.get('item_id_str') or node.get('item_id') or '') != item_id:
                    continue
                duplicate = node.get('video')
                if isinstance(duplicate, dict) and duplicate.get('video_id') == video['video_id']:
                    videos.append(duplicate)
        urls = media_urls(item.get('origin_video_download'), actual_url)
        selected = None
        for key in ('video_high', 'video_mid', 'video_low', 'video_fallback', 'video_download'):
            if urls:
                break
            for candidate in videos:
                urls = media_urls(candidate.get(key), actual_url)
                if urls:
                    selected = candidate.get(key)
                    break
        if urls:
            result.update(type='video', video_url=urls[0], backup_video_urls=dedupe(urls[1:]),
                          duration=video.get('duration') or item.get('duration'))
            if isinstance(selected, dict):
                result['width'] = selected.get('width')
                result['height'] = selected.get('height')
            return result
        images = item.get('images') or item.get('image_list')
        if not images and isinstance(item.get('note'), dict):
            images = item['note'].get('images')
        urls = image_urls(images, actual_url)
        if urls:
            result.update(type='image', image_urls=urls)
            return result
    return None


def parse_pipix(final_url: str, share_url: Optional[str] = None) -> dict:
    candidates = list(dict.fromkeys(url for url in (final_url, share_url) if url))
    last_error = '皮皮虾公开页未返回当前作品的视频或图片，可能是链接失效、作品不可见或页面结构发生变化。'
    for url in candidates:
        for headers in (MOBILE_HEADERS, PC_HEADERS):
            try:
                html, actual_url, _ = fetch_page(url, headers)
            except requests.RequestException as exc:
                last_error = '皮皮虾分享页请求失败，请检查网络或稍后重试。'
                break
            result = parse_pipix_page(html, actual_url)
            if result:
                return result
    raise RuntimeError(last_error)
