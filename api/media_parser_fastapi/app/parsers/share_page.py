"""公开分享页的 JSON 与媒体地址读取工具；不执行页面脚本。"""
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import unquote

from .common import extract_balanced_json, normalize_media_url, dedupe


def page_states(html: str, markers: tuple) -> list:
    states = []
    for marker in markers:
        pos = html.find(marker)
        while pos >= 0:
            rest = html[pos + len(marker):].lstrip(' \t\r\n=')
            try:
                if rest.startswith('JSON.parse('):
                    raw, _ = json.JSONDecoder().raw_decode(rest[len('JSON.parse('):].lstrip())
                    data = json.loads(raw)
                else:
                    blob = extract_balanced_json(html, pos + len(marker))
                    data = json.loads(blob) if blob else None
                if isinstance(data, (dict, list)):
                    states.append((data, marker))
            except (ValueError, TypeError):
                pass
            pos = html.find(marker, pos + len(marker))
    for match in re.finditer(r'<script\b([^>]*)>(.*?)</script\s*>', html, re.I | re.S):
        attrs, raw = match.groups()
        if not re.search(r'(?:application/json|RENDER_DATA|__NEXT_DATA__)', attrs, re.I):
            continue
        for candidate in (raw.strip(), unquote(raw.strip()), html_lib.unescape(raw.strip())):
            try:
                data = json.loads(candidate)
                if isinstance(data, (dict, list)):
                    states.append((data, 'RENDER_DATA' if 'RENDER_DATA' in attrs else 'JSON script'))
                    break
            except (ValueError, TypeError):
                continue
    return states


def media_urls(value: Any, base_url: str) -> list:
    if isinstance(value, str):
        url = normalize_media_url(value, base_url)
        return [url] if url else []
    if isinstance(value, list):
        return dedupe(url for item in value for url in media_urls(item, base_url))
    if isinstance(value, dict):
        urls = []
        for key in ('origin', 'original', 'raw', 'url', 'urls', 'url_list', 'src', 'play_url', 'playUrl', 'backup_url'):
            if key in value:
                urls.extend(media_urls(value[key], base_url))
        return dedupe(urls)
    return []


def image_urls(images: Any, base_url: str) -> list:
    if not isinstance(images, list):
        return []
    result = []
    for image in images:
        urls = media_urls(image, base_url)
        if urls:
            result.append(urls[0])
    return dedupe(result)
