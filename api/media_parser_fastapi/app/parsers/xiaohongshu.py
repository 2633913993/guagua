"""小红书 / RedNote 解析模块：核心解析逻辑保持原代码。"""
from .common import *
# =========================
# 小红书 / RedNote
# =========================

XHS_VALID_IMAGE_FOLDERS = {"notes_pre_post", "spectrum", "comment", "feed"}
XHS_IMAGE_CDN_HOSTS = (
    "ci.xiaohongshu.com",
    "sns-img-bd.xhscdn.com",
    "sns-img-al.xhscdn.com",
    "sns-img-hw.xhscdn.com",
)


def _xhs_extract_original_path(url: str) -> Optional[str]:
    """从小红书展示图 URL 提取原图路径，保留 notes_pre_post/spectrum 等目录。"""
    if not isinstance(url, str):
        return None

    value = html_lib.unescape(url).strip()
    if not value:
        return None

    parsed = urlparse(value)
    path = parsed.path or ""
    segments = [seg for seg in path.split("/") if seg]
    if not segments:
        return None

    # 去掉文件变体：xxx!nd_dft_wlteh_webp_3 -> xxx
    segments[-1] = segments[-1].split("!", 1)[0]
    if not segments[-1]:
        return None

    # 2025/2026 新结构通常：/<timestamp>/<32hex>/<folder>/<image-id>。
    # 必须从有效 folder 开始保留完整路径，否则 notes_pre_post 图片会 404。
    for idx, seg in enumerate(segments):
        if seg in XHS_VALID_IMAGE_FOLDERS and idx + 1 < len(segments):
            tail = segments[idx:]
            if all(re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in tail):
                return "/".join(tail)

    # 旧结构没有 folder，最后一段就是 image id。
    token = segments[-1]
    if len(token) >= 16 and re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        return token

    return None


def _xhs_collect_raw_image_urls(node: dict, base_url: str) -> list[str]:
    """递归收集 imageList 项里所有可能的 CDN 图片源。"""
    urls: list[str] = []
    seen = set()

    def walk(value: Any, depth: int = 0):
        if depth > 5:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_l = str(key).lower()
                if key_l in {
                    "urldefault", "url", "urlpre", "originurl", "imageurl",
                    "url_default", "url_pre", "origin_url", "image_url", "src"
                }:
                    if isinstance(child, str):
                        u = normalize_media_url(child, base_url)
                        if u and u not in seen:
                            seen.add(u)
                            urls.append(u)
                # info / infoList / imageList 等内部对象继续展开。
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(node)
    return urls


def _xhs_image_candidates(node: dict, base_url: str) -> list[str]:
    """
    生成小红书图文原图候选地址。

    重要：不能简单取 URL 的最后一个 path segment。
    现代 XHS 图片可能带 /notes_pre_post/ 或 /spectrum/，该目录必须保留。
    """
    raw_urls = _xhs_collect_raw_image_urls(node, base_url)
    candidates: list[str] = []
    seen = set()

    def add(url: Optional[str]):
        if url and url not in seen:
            seen.add(url)
            candidates.append(url)

    # 先从原始展示 CDN URL 推导真正的原图路径。
    for raw in raw_urls:
        original_path = _xhs_extract_original_path(raw)
        if not original_path:
            continue

        # 2026 新规则下，原图直链首先尝试无 query 的 ci URL。
        # notes_pre_post 若追加 imageView2 可能得到 500，因此绝不强制追加。
        for host in XHS_IMAGE_CDN_HOSTS:
            add(f"https://{host}/{original_path}")

        # 非 notes_pre_post 的旧资源，再提供 PNG 变换候选作为兼容路径。
        if not original_path.startswith("notes_pre_post/"):
            add(f"https://ci.xiaohongshu.com/{original_path}?imageView2/2/w/format/png")
            add(f"https://sns-img-bd.xhscdn.com/{original_path}?imageView2/2/w/format/png")

    # 如果节点有单独 traceId/fileId，也只作为补充；traceId 可能本身为空。
    for key in ("traceId", "trace_id", "fileId", "file_id"):
        value = node.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        raw = value.strip().strip("/")
        # 有效 folder 的 traceId 直接保留；普通 token 则原样使用。
        if raw.count("/") >= 1:
            original_path = raw.split("?", 1)[0].split("#", 1)[0].split("!", 1)[0].strip("/")
        else:
            original_path = raw.split("?", 1)[0].split("#", 1)[0].split("!", 1)[0]
        if len(original_path.rsplit("/", 1)[-1]) < 16:
            continue
        for host in XHS_IMAGE_CDN_HOSTS:
            add(f"https://{host}/{original_path}")

    return candidates


def _probe_xhs_image_url(url: str) -> bool:
    """轻量验证图片 CDN URL 是否真的能返回图片，不下载完整文件。"""
    headers = {
        **MOBILE_HEADERS,
        "Referer": "https://www.xiaohongshu.com/",
        "Range": "bytes=0-0",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    try:
        resp = session.get(url, headers=headers, allow_redirects=True,
                           stream=True, timeout=8)
        try:
            if resp.status_code not in (200, 206):
                return False
            content_type = (resp.headers.get("Content-Type") or "").lower()
            return (
                content_type.startswith("image/")
                or content_type in {"application/octet-stream", "binary/octet-stream"}
            )
        finally:
            resp.close()
    except requests.RequestException:
        return False


def _xhs_image_urls_from_node(node: dict, base_url: str, verify: bool = True) -> list[str]:
    """一个 imageList 项只返回一张最终可访问原图。"""
    candidates = _xhs_image_candidates(node, base_url)
    if not candidates:
        return []

    if not verify:
        return candidates[:1]

    for candidate in candidates:
        if _probe_xhs_image_url(candidate):
            return [candidate]

    return []


def _xhs_origin_video_urls(data: Any) -> list[str]:
    """优先从 originVideoKey 生成公开在线播放原始视频地址。"""
    urls: list[str] = []

    for value in recursive_find_all(data, {"originVideoKey"}, max_results=50):
        if not isinstance(value, str):
            continue
        key = value.strip().strip('/')
        if not key:
            continue

        # 当前常见的无水印原始视频 CDN。
        for host in (
            "sns-video-bd.xhscdn.com",
            "sns-video-al.xhscdn.com",
        ):
            urls.append(f"https://{host}/{key}")

    return dedupe(urls)


def _xhs_master_video_urls(data: Any, base_url: str) -> list[str]:
    urls: list[str] = []

    for value in recursive_find_all(data, {"masterUrl"}, max_results=100):
        if isinstance(value, str):
            u = normalize_media_url(value, base_url)
            if u:
                urls.append(u)
        elif isinstance(value, dict):
            for key in ("masterUrl", "url"):
                u = normalize_media_url(value.get(key), base_url)
                if u:
                    urls.append(u)

    return dedupe(urls)


def _xhs_stream_backup_urls(data: Any, base_url: str) -> list[str]:
    urls: list[str] = []

    # 不依赖固定嵌套路径，兼容 media.stream 数组/对象。
    for value in recursive_find_all(data, {"stream"}, max_results=100):
        nodes = value if isinstance(value, list) else [value]
        for stream in nodes:
            if not isinstance(stream, dict):
                continue

            for key in ("masterUrl", "url"):
                u = normalize_media_url(stream.get(key), base_url)
                if u:
                    urls.append(u)

            backup = stream.get("backupUrls")
            if isinstance(backup, list):
                for item in backup:
                    if isinstance(item, str):
                        u = normalize_media_url(item, base_url)
                        if u:
                            urls.append(u)
                    elif isinstance(item, dict):
                        for key in ("url", "masterUrl"):
                            u = normalize_media_url(item.get(key), base_url)
                            if u:
                                urls.append(u)

    return dedupe(urls)


def _xhs_generic_video_urls(data: Any, base_url: str) -> list[str]:
    urls: list[str] = []

    for value in recursive_find_all(data, {"videoUrl", "video_url"}, max_results=100):
        if isinstance(value, str):
            u = normalize_media_url(value, base_url)
            if u:
                urls.append(u)
        elif isinstance(value, dict):
            for key in ("url", "videoUrl", "video_url"):
                u = normalize_media_url(value.get(key), base_url)
                if u:
                    urls.append(u)

    return dedupe(urls)


def _find_xhs_media(data: Any, base_url: str) -> tuple[Optional[str], list[str]]:
    """
    小红书媒体提取：严格按“原始/无水印源优先”的顺序选择，避免递归遍历
    先碰到 urlDefault / videoUrl 就把有水印资源当成最终结果。
    """
    # 1) 视频：只接受能够明确作为原始/无水印源的字段。
    # originVideoKey > masterUrl > stream/master/backupUrls。
    # 普通 videoUrl 已知可能是带水印展示地址，因此不作为成功结果返回。
    origin_videos = _xhs_origin_video_urls(data)
    master_videos = _xhs_master_video_urls(data, base_url)
    stream_videos = _xhs_stream_backup_urls(data, base_url)

    video_candidates = dedupe(
        origin_videos + master_videos + stream_videos
    )

    # 2) 图文：imageList 的 token 生成原图候选，且不再优先返回 urlDefault。
    image_candidates: list[str] = []
    for value in recursive_find_all(data, {"imageList"}, max_results=50):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    image_candidates.extend(_xhs_image_urls_from_node(item, base_url))

    image_candidates = dedupe(image_candidates)
    return (video_candidates[0] if video_candidates else None), image_candidates



def video_state_has_watermark_only(data: Any) -> bool:
    """判断页面是否只出现了常见的展示层 videoUrl，而没有公开原始视频字段。"""
    has_generic = bool(recursive_find_all(data, {"videoUrl", "video_url"}, max_results=1))
    has_original = bool(
        recursive_find_all(data, {"originVideoKey", "masterUrl"}, max_results=1)
        or recursive_find_all(data, {"stream"}, max_results=1)
    )
    return has_generic and not has_original


def parse_xiaohongshu(final_url: str, share_url: Optional[str] = None) -> dict:
    candidates = []
    for u in (share_url, final_url):
        if u and u not in candidates:
            candidates.append(u)

    last_error = "小红书页面未找到可解析数据"

    for url in candidates:
        try:
            html, actual_url, _ = fetch_page(url, MOBILE_HEADERS)

            data, marker = extract_state(
                html,
                [
                    "window.__INITIAL_STATE__",
                    "window.__ROUTER_STATE__",
                    "window.__APOLLO_STATE__",
                ],
            )

            result = {
                "platform": "小红书 / RedNote",
                "title": title_from_html(html) or "小红书作品",
                "type": "unknown",
                "video_url": None,
                "image_urls": [],
                "source": marker or "HTML",
                "watermark_free": None,
            }

            if data is not None:
                title_values = recursive_find_all(
                    data,
                    {"title", "desc", "displayTitle", "shareTitle"},
                    max_results=80,
                )
                result["title"] = first_nonempty_string(
                    title_values + [result["title"]],
                    "小红书作品",
                )

                video_url, image_urls = _find_xhs_media(data, actual_url)

                if video_url:
                    result["type"] = "video"
                    result["video_url"] = video_url
                    # 只要命中我们生成的原始视频 CDN，就明确标记为无水印源。
                    if ".xhscdn.com/" in video_url and (
                        "sns-video-bd.xhscdn.com/" in video_url
                        or "sns-video-al.xhscdn.com/" in video_url
                    ):
                        result["watermark_free"] = True
                        if origin_v := _xhs_origin_video_urls(data):
                            if video_url == origin_v[0]:
                                result["source"] = "originVideoKey"
                        if result["source"] == (marker or "HTML"):
                            result["source"] = "XHS original video CDN"
                    else:
                        result["watermark_free"] = False
                    return result

                if image_urls:
                    result["type"] = "image"
                    result["image_urls"] = image_urls
                    result["watermark_free"] = all(
                        ("ci.xiaohongshu.com/" in u)
                        or ("sns-img-bd.xhscdn.com/" in u)
                        or ("sns-img-al.xhscdn.com/" in u)
                        for u in image_urls
                    )
                    if result["watermark_free"]:
                        result["source"] = "XHS original image CDN"
                    return result

            # 不把 og:video / og:image 这种展示层资源作为小红书“无水印成功”结果。
            # 它们可能带水印；这里宁可明确失败，也不返回错误媒体。
            if video_state_has_watermark_only(data):
                last_error = (
                    "小红书只返回了可能带水印的 videoUrl，未找到 originVideoKey/masterUrl 等公开原始源；"
                    "请使用有效分享链接或在登录态下重试。"
                )
            else:
                last_error = (
                    "小红书返回了页面，但未发现公开原始媒体字段；"
                    "可能是风控页、页面结构已变化，或当前分享 token 已失效。"
                )

        except requests.RequestException as exc:
            last_error = f"HTTP 请求失败：{exc}"
        except Exception as exc:
            last_error = str(exc)

    # Playwright 兜底
    rendered = try_playwright(share_url or final_url)
    if rendered:
        data, marker = extract_state(
            rendered,
            [
                "window.__INITIAL_STATE__",
                "window.__ROUTER_STATE__",
                "window.__APOLLO_STATE__",
            ],
        )

        if data is not None:
            result = {
                "platform": "小红书 / RedNote",
                "title": title_from_html(rendered) or "小红书作品",
                "type": "unknown",
                "video_url": None,
                "image_urls": [],
                "source": "Playwright/" + str(marker),
                "watermark_free": None,
            }

            title_values = recursive_find_all(
                data,
                {"title", "desc", "displayTitle", "shareTitle"},
                max_results=80,
            )
            result["title"] = first_nonempty_string(
                title_values + [result["title"]],
                "小红书作品",
            )

            video_url, image_urls = _find_xhs_media(data, share_url or final_url)
            if video_url:
                result["type"] = "video"
                result["video_url"] = video_url
                result["watermark_free"] = (
                    "sns-video-bd.xhscdn.com/" in video_url
                    or "sns-video-al.xhscdn.com/" in video_url
                )
                if result["watermark_free"]:
                    result["source"] = "Playwright/originVideoKey or original video CDN"
                return result

            if image_urls:
                result["type"] = "image"
                result["image_urls"] = image_urls
                result["watermark_free"] = all(
                    ("ci.xiaohongshu.com/" in u)
                    or ("sns-img-bd.xhscdn.com/" in u)
                    or ("sns-img-al.xhscdn.com/" in u)
                    for u in image_urls
                )
                if result["watermark_free"]:
                    result["source"] = "Playwright/XHS original image CDN"
                return result

    raise RuntimeError(last_error)
