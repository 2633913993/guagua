# -*- coding: utf-8 -*-
"""
拆分自 main_fixed.py：通用 HTTP / URL / JSON / HTML 工具。

多平台公开分享链接解析器
支持：
- 抖音 / iesdouyin
- 小红书 / RedNote
- 快手
- Bilibili
- 最右
- 皮皮虾
- 剪映

说明：
1. 只解析公开分享页能提供的数据，不登录、不伪造 Cookie、不调用需要账号权限的接口。
2. 小红书分享链接最好保留 xsec_token；快手优先使用“分享/复制链接”得到的官方分享链接。
3. 页面结构经常变化，所以代码不再只依赖某一个 JS 变量名。
4. 可选安装 Playwright 作为 JS 渲染兜底：
   pip install playwright
   playwright install chromium
"""

import html as html_lib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse

import requests


# =========================
# 基础配置
# =========================

TIMEOUT = 20
MAX_HTML = 15 * 1024 * 1024  # 防止异常页面无限膨胀

session = requests.Session()

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

MOBILE_HEADERS = {
    **BASE_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1"
    ),
}

PC_HEADERS = {
    **BASE_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
}


# 兼容用户提供的抖音解析函数所使用的小写变量名
mobile_headers = MOBILE_HEADERS
pc_headers = PC_HEADERS


# =========================
# 通用 URL / HTTP 工具
# =========================

def clean_url(url: str) -> str:
    """去除复制链接末尾经常出现的中文/英文标点。"""
    url = url.strip().strip("<>\"'`")
    while url and url[-1] in "，。！？；：、）》）】》」』\"'.,!?;:)>]}":
        url = url[:-1]
    return url


def extract_urls(text: str) -> list[str]:
    """
    从整段分享文案中提取所有 URL。
    旧代码只取第一条 URL，导致一次粘贴多条链接时后续链接直接丢失。
    """
    if not text:
        return []

    # URL 中允许存在 query 参数，所以只以空白作为大分隔。
    candidates = re.findall(r"https?://[^\s<>\"']+", text, flags=re.I)
    result = []

    for item in candidates:
        item = clean_url(item)
        try:
            parsed = urlparse(item)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                result.append(item)
        except ValueError:
            continue

    # 去重但保持顺序
    return list(dict.fromkeys(result))


def extract_url(text: str) -> Optional[str]:
    """兼容原 API：返回第一条链接。"""
    urls = extract_urls(text)
    return urls[0] if urls else None


def normalize_host(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def detect_platform(url: str) -> Optional[str]:
    host = normalize_host(url)

    if host == "douyin.com" or host.endswith(".douyin.com") or \
       host == "iesdouyin.com" or host.endswith(".iesdouyin.com"):
        return "douyin"

    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com") or \
       host == "rednote.com" or host.endswith(".rednote.com") or \
       host == "xhslink.com" or host.endswith(".xhslink.com") or \
       host == "xhslink.cn" or host.endswith(".xhslink.cn"):
        return "xiaohongshu"

    if host == "kuaishou.com" or host.endswith(".kuaishou.com") or \
       host == "kwai.com" or host.endswith(".kwai.com") or \
       host == "v.kuaishou.com":
        return "kuaishou"

    if host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv":
        return "bilibili"

    if host == "izuiyou.com" or host.endswith(".izuiyou.com") or host == "share.xiaochuankeji.cn":
        return "zuiyou"

    if host == "pipix.com" or host.endswith(".pipix.com") or host == "pipixia.com" or host.endswith(".pipixia.com"):
        return "pipix"

    if host == "lv.ulikecam.com":
        return "jianying"

    return None


def get_final_url(share_url: str, headers: Optional[dict] = None) -> str:
    """
    跟随公开 HTTP 重定向。
    这里不能把分享页直接替换掉：某些平台的数据只存在分享响应上下文，
    所以后面解析时会同时尝试 share_url 和 final_url。
    """
    resp = session.get(
        share_url,
        headers=headers or PC_HEADERS,
        allow_redirects=True,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.url


def fetch_page(url: str, headers: Optional[dict] = None) -> tuple[str, str, dict]:
    """
    获取页面，返回：
    html/text, 最终 URL, 响应头
    """
    resp = session.get(
        url,
        headers=headers or PC_HEADERS,
        allow_redirects=True,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    content = resp.content
    if len(content) > MAX_HTML:
        content = content[:MAX_HTML]

    # requests 自动猜编码经常足够；若页面自报编码也尊重它。
    resp.encoding = resp.encoding or "utf-8"
    text = content.decode(resp.encoding, errors="replace")

    return text, resp.url, dict(resp.headers)


def safe_json_loads(raw: str) -> Any:
    """
    尽量把页面脚本中的 JSON-like 数据还原成 Python 对象。
    不使用 eval。
    """
    raw = html_lib.unescape(raw).strip()

    # 页面 SSR 常见 undefined；只替换独立 token，避免修改字符串内容。
    raw = re.sub(r"(?<![\w$])undefined(?![\w$])", "null", raw)

    # 少数页面末尾会有 ;。
    raw = raw.rstrip(" ;\t\r\n")

    # BOM
    raw = raw.lstrip("\ufeff")

    return json.loads(raw)


def extract_balanced_json(text: str, start_pos: int) -> Optional[str]:
    """
    从 start_pos 开始寻找第一个 { 或 [，然后按括号深度解析。
    与 r'{.*?}</script>' 相比，不会被嵌套对象中的 } 干扰。
    """
    n = len(text)
    i = start_pos

    while i < n and text[i] not in "[{":
        i += 1

    if i >= n:
        return None

    opener = text[i]
    closer = "}" if opener == "{" else "]"

    depth = 0
    in_string = False
    escaped = False

    for j in range(i, n):
        ch = text[j]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[i:j + 1]

    return None


def extract_state(text: str, markers: Iterable[str]) -> tuple[Any, Optional[str]]:
    """
    尝试解析：
    - window.__INITIAL_STATE__
    - window.__APOLLO_STATE__
    - window._ROUTER_DATA
    - 页面其它常见 state 容器
    """
    # 顺序很重要：优先精确 marker。
    for marker in markers:
        pos = text.find(marker)
        while pos != -1:
            blob = extract_balanced_json(text, pos + len(marker))
            if blob:
                try:
                    return safe_json_loads(blob), marker
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            pos = text.find(marker, pos + 1)

    return None, None


def recursive_find_all(obj: Any, target_keys: set[str], max_results: int = 1000) -> list[Any]:
    """递归收集所有目标 key 的值。"""
    found: list[Any] = []

    def walk(node: Any):
        if len(found) >= max_results:
            return

        if isinstance(node, dict):
            for key, value in node.items():
                if str(key) in target_keys:
                    found.append(value)
                    if len(found) >= max_results:
                        return
                walk(value)
                if len(found) >= max_results:
                    return

        elif isinstance(node, list):
            for item in node:
                walk(item)
                if len(found) >= max_results:
                    return

    walk(obj)
    return found


def recursive_find_first(obj: Any, target_keys: set[str]) -> Any:
    values = recursive_find_all(obj, target_keys, max_results=1)
    return values[0] if values else None


def iter_nodes(obj: Any) -> Iterable[Any]:
    """深度遍历全部 dict/list 节点。"""
    yield obj

    if isinstance(obj, dict):
        for value in obj.values():
            yield from iter_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_nodes(item)


def normalize_media_url(url: Any, base_url: str = "") -> Optional[str]:
    if not isinstance(url, str):
        return None

    url = html_lib.unescape(url).strip()
    if not url:
        return None

    # JSON/HTML 转义
    url = url.replace("\\/", "/")

    if url.startswith("//"):
        url = "https:" + url

    if url.startswith("/") and base_url:
        parsed = urlparse(base_url)
        url = f"{parsed.scheme}://{parsed.netloc}{url}"

    # 只接受真正的 http(s) 资源 URL
    if url.startswith("http://") or url.startswith("https://"):
        return url

    return None


def dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def first_nonempty_string(values: Iterable[Any], default: str) -> str:
    for value in values:
        if isinstance(value, str):
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value
    return default


def title_from_html(html: str) -> Optional[str]:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
    ]

    for pat in patterns:
        m = re.search(pat, html, flags=re.I | re.S)
        if m:
            value = html_lib.unescape(m.group(1))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value

    return None


def html_media_fallback(html: str, base_url: str, want_video: bool = True) -> tuple[list[str], list[str]]:
    """
    通用 HTML 兜底：
    - og:video / video / source
    - og:image / img
    """
    videos: list[str] = []
    images: list[str] = []

    if want_video:
        video_patterns = [
            r'<meta[^>]+property=["\']og:video(?::url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<video[^>]+src=["\']([^"\']+)["\']',
            r'<source[^>]+src=["\']([^"\']+)["\']',
        ]
        for pat in video_patterns:
            for m in re.finditer(pat, html, flags=re.I | re.S):
                url = normalize_media_url(m.group(1), base_url)
                if url and url not in videos:
                    videos.append(url)

    image_patterns = [
        r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ]

    for pat in image_patterns:
        for m in re.finditer(pat, html, flags=re.I | re.S):
            url = normalize_media_url(m.group(1), base_url)
            if url and url not in images:
                images.append(url)

    return videos, images


def try_playwright(url: str) -> Optional[str]:
    """
    可选 JS 渲染兜底。
    默认不强制安装 Playwright。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=PC_HEADERS["User-Agent"],
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            content = page.content()
            browser.close()
            return content
    except Exception:
        return None
