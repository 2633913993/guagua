"""最右公开详情 API 优先、分享页兜底；只返回当前帖子的媒体。"""
import re
import ast
import json
import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlencode, unquote

import requests

from .common import MOBILE_HEADERS, PC_HEADERS, TIMEOUT, session, fetch_page, get_final_url, detect_platform, first_nonempty_string, dedupe
from .share_page import page_states, media_urls, image_urls

_LOG = logging.getLogger(__name__)

_MARKERS = ('window.APP_INITIAL_STATE', 'window.__INITIAL_STATE__', 'window.__NEXT_DATA__')


def _post(state: dict, target: Optional[str] = None) -> Optional[dict]:
    # PC 与移动版的 post 包装层级可能不同，只沿详情容器查找。
    queue = [(state, 0)]
    while queue:
        root, depth = queue.pop(0)
        if not isinstance(root, dict):
            continue
        identifier = root.get('id') or root.get('pid') or root.get('post_id') or root.get('postId')
        if any(key in root for key in ('imgs', 'images', 'videos', 'video')):
            if not target or identifier is None or str(identifier) == target:
                return root
        if depth < 6:
            for key in ('post', 'data', 'postDetail', 'postInfo', 'post_data', 'detail', 'props', 'pageProps', 'initialState', 'state'):
                if isinstance(root.get(key), dict):
                    queue.append((root[key], depth + 1))
    return None


def _states(html: str) -> list:
    states = page_states(html, _MARKERS)
    # 兼容无 window 前缀、JSON.parse 单引号串和 URI 编码的状态串。
    for match in re.finditer(r'(?:(?:window|self)\s*\.\s*)?(?:APP_INITIAL_STATE|__INITIAL_STATE__)\s*=', html):
        rest = html[match.end():].lstrip()
        try:
            encoded = False
            if rest.startswith('JSON.parse('):
                rest = rest[len('JSON.parse('):].lstrip()
            if rest.startswith('decodeURIComponent('):
                rest = rest[len('decodeURIComponent('):].lstrip()
                encoded = True
            if rest.startswith(("'", '"')):
                token = re.match(r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")", rest, re.S)
                if not token:
                    continue
                raw = ast.literal_eval(token.group(0))
                data = json.loads(unquote(raw) if encoded else raw)
            else:
                # JSONDecoder 只读取当前赋值，不跨越后面的脚本寻找对象。
                data, _ = json.JSONDecoder().raw_decode(rest)
            if isinstance(data, dict):
                states.append((data, 'APP_INITIAL_STATE / JSON'))
        except (ValueError, TypeError, SyntaxError):
            continue
    return states


def _video_urls(record, actual_url: str) -> list:
    urls = media_urls(record, actual_url)
    if isinstance(record, dict):
        for key in ('videoUrl', 'video_url', 'play_addr', 'play_urls', 'play', 'download'):
            urls.extend(media_urls(record.get(key), actual_url))
    return dedupe(urls)


def _post_result(post: dict, actual_url: str, source: str) -> Optional[dict]:
    post_id = str(post.get('id') or post.get('pid') or post.get('post_id') or post.get('postId') or '')
    result = {
        'platform': '最右', 'title': first_nonempty_string([post.get('content'), post.get('text'), post.get('title')], '最右作品'),
        'type': 'unknown', 'video_url': None, 'image_urls': [], 'source': source,
        'post_id': post_id, 'watermark_free': None,
    }
    images = post.get('imgs') or post.get('images') or []
    videos = post.get('videos') or {}
    records = []
    if isinstance(videos, dict):
        ids = [str(image.get('id')) for image in images if isinstance(image, dict)]
        records = [videos[key] for key in ids if key in videos]
        if not records:
            records = list(videos.values())
    elif isinstance(videos, list):
        records = videos
    if isinstance(post.get('video'), dict):
        records = [post['video']] + records
    playable = []
    for record in records:
        urls = _video_urls(record, actual_url)
        if urls:
            playable.append(urls)
    if len(playable) > 1:
        unique = {record[0] for record in playable}
        if len(unique) > 1:
            raise RuntimeError('当前最右帖子包含多个视频，现有单视频结果格式暂不支持完整导出。')
    if playable:
        urls = dedupe(url for record in playable for url in record)
        result.update(type='video', video_url=urls[0], backup_video_urls=urls[1:])
        covers = image_urls(images, actual_url)
        result['cover'] = covers[0] if covers else ''
        return result
    # 视频结构存在但没有可用地址时，不把它的封面当成图片成功。
    if records:
        return None
    urls = image_urls(images, actual_url)
    if urls:
        result.update(type='image', image_urls=urls)
        return result
    return None


def parse_zuiyou_page(html: str, actual_url: str, diagnostics: Optional[dict] = None) -> Optional[dict]:
    query = parse_qs(urlparse(actual_url).query)
    target = (query.get('pid') or query.get('post_id') or [None])[0]
    states = _states(html)
    details = diagnostics if diagnostics is not None else {}
    details.update(state_count=len(states), post_found=False, target=target)
    for state, source in states:
        if not isinstance(state, dict):
            continue
        post = _post(state, target)
        if not post:
            continue
        details['post_found'] = True
        result = _post_result(post, actual_url, source)
        if result:
            return result
    return None


def _parse_zuiyou_pages(final_url: str, share_url: Optional[str] = None) -> dict:
    candidates = list(dict.fromkeys(url for url in (final_url, share_url) if url))
    # 已有 pid 的 PC 链接可直接读取官方完整地址，不依赖短链二次展开。
    for url in list(candidates):
        query = parse_qs(urlparse(url).query)
        pid = (query.get('pid') or query.get('post_id') or [None])[0]
        if pid and pid.isdigit():
            for host in ('share.xiaochuankeji.cn', 'share.izuiyou.com'):
                canonical = 'https://' + host + '/hybrid/share/post?' + urlencode({'pid': pid})
                if canonical not in candidates:
                    candidates.append(canonical)
    last_error = '最右公开分享页没有返回当前帖子媒体，可能是作品不可见、链接失效或页面结构变化。'
    page_error = None
    for url in candidates:
        for headers in (MOBILE_HEADERS, PC_HEADERS):
            try:
                html, actual_url, _ = fetch_page(url, headers)
            except requests.exceptions.SSLError:
                last_error = '最右分享域名 HTTPS/TLS 连接失败，请检查网络或从 App 重新复制分享链接。'
                break
            except requests.RequestException:
                last_error = '最右分享页请求失败，请检查网络或稍后重试。'
                break
            diagnostics = {}
            result = parse_zuiyou_page(html, actual_url, diagnostics)
            if result:
                return result
            if diagnostics.get('state_count', 0) == 0:
                last_error = '最右返回了页面，但未找到帖子数据，可能是应用唤起页或访问提示页。'
            elif not diagnostics.get('post_found'):
                last_error = '最右页面中没有找到与当前 pid 匹配的帖子详情。'
            else:
                last_error = '最右已识别当前帖子，但页面没有提供可用的视频或图片地址。'
            page_error = last_error
            _LOG.warning('最右分享页未命中: url=%s, diagnostics=%s', actual_url, diagnostics)
    raise RuntimeError(page_error or last_error)


# Endpoint/request schema verified against 5ime/video_spider at e171ffac7973:
# app/Parsers/IzuiyouParser.php. Implemented with this project's HTTP and result helpers.
_DETAIL_API = 'https://share.xiaochuankeji.cn/planck/share/post/detail_h5'


def _post_id(url: str) -> Optional[str]:
    if detect_platform(url) != 'zuiyou':
        return None
    query = parse_qs(urlparse(url).query)
    value = (query.get('pid') or query.get('post_id') or [None])[0]
    if value is None:
        return None
    if not re.fullmatch(r'[0-9]+', value) or int(value) <= 0:
        raise ValueError('最右帖子编号 pid 必须为正整数。')
    return str(int(value))


def _api_result(pid: str) -> dict:
    headers = {**PC_HEADERS, 'Referer': 'https://share.izuiyou.com/', 'Content-Type': 'application/json'}
    try:
        response = session.post(_DETAIL_API, json={'h_av': '5.2.13.011', 'pid': int(pid)},
                                headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.SSLError as exc:
        raise RuntimeError('最右公开详情接口 HTTPS/TLS 连接失败。') from exc
    except requests.RequestException as exc:
        raise RuntimeError('最右公开详情接口请求失败，请稍后重试。') from exc
    except ValueError as exc:
        raise RuntimeError('最右公开详情接口未返回有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('最右公开详情接口返回格式不正确。')
    if payload.get('ret') not in (None, 1, '1'):
        raise RuntimeError('最右公开详情接口没有返回成功结果，作品可能已删除或不可公开访问。')
    data = payload.get('data')
    post = data.get('post') if isinstance(data, dict) else None
    if not isinstance(post, dict) or not post:
        raise RuntimeError('最右公开详情接口缺少 data.post 作品数据。')
    returned_id = post.get('id') or post.get('pid') or post.get('post_id') or post.get('postId')
    if returned_id is not None and str(returned_id) != pid:
        raise RuntimeError('最右公开详情接口返回的帖子编号与分享链接不一致。')
    canonical = 'https://share.xiaochuankeji.cn/hybrid/share/post?' + urlencode({'pid': pid})
    result = _post_result(post, canonical, '最右公开详情 API')
    if result is None:
        raise RuntimeError('最右公开详情接口已返回帖子，但没有可用的视频或图片地址。')
    result['post_id'] = pid
    return result


def parse_zuiyou(final_url: str, share_url: Optional[str] = None) -> dict:
    urls = list(dict.fromkeys(url for url in (final_url, share_url) if url))
    pid = next((value for url in urls if (value := _post_id(url)) is not None), None)
    if pid is None:
        # 移动端短链必须展开到含 pid 的公开分享页，不能把短码当帖子编号。
        for url in list(urls):
            if detect_platform(url) != 'zuiyou':
                continue
            try:
                expanded = get_final_url(url, headers=MOBILE_HEADERS)
            except requests.RequestException:
                continue
            if expanded not in urls:
                urls.append(expanded)
            pid = _post_id(expanded)
            if pid:
                break
    api_error = None
    if pid:
        try:
            return _api_result(pid)
        except RuntimeError as exc:
            api_error = str(exc)
    # API 受限或旧分享页没有 pid 时，保留现有公开页面解析兼容性。
    try:
        page_url = next((url for url in urls if _post_id(url)), final_url)
        return _parse_zuiyou_pages(page_url, share_url)
    except RuntimeError as exc:
        if api_error:
            raise RuntimeError(api_error + ' 页面兜底：' + str(exc)) from exc
        raise
