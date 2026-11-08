"""快手公开分享页解析：优先移动端 INIT_STATE，兼容 PC/Apollo 与图集。"""
from .common import *
from urllib.parse import parse_qs
# =========================
# 快手
# =========================

def _collect_kuaishou_video_urls(data: Any, base_url: str) -> list[str]:
    data = _kuaishou_media_scope(data)
    urls = []

    # 常见结构：mainMvUrls / playUrl / representation
    for value in recursive_find_all(
        data,
        {"mainMvUrls", "playUrl", "playUrls", "play_url", "videoUrl", "video_url", "representation", "representations"},
        max_results=100,
    ):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    u = normalize_media_url(item, base_url)
                    if u:
                        urls.append(u)
                elif isinstance(item, dict):
                    for key in ("url", "playUrl", "src", "play_url"):
                        u = normalize_media_url(item.get(key), base_url)
                        if u:
                            urls.append(u)

        elif isinstance(value, str):
            u = normalize_media_url(value, base_url)
            if u:
                urls.append(u)

        elif isinstance(value, dict):
            for key in ("url", "playUrl", "src", "play_url"):
                u = normalize_media_url(value.get(key), base_url)
                if u:
                    urls.append(u)

    # 再补一层：明确出现 mp4 / m3u8 的 URL
    for node in iter_nodes(data):
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str):
                    low = value.lower()
                    if ("mp4" in low or ".m3u8" in low) and value.startswith(("http://", "https://")):
                        urls.append(value)

    return dedupe(urls)


def _kuaishou_cdn_hosts(value: Any) -> list[str]:
    """从 atlas.cdnList / cdn 中提取 CDN 主机名。"""
    hosts: list[str] = []

    def add(item: Any):
        if isinstance(item, str):
            item = item.strip()
            if not item:
                return
            if item.startswith(("http://", "https://")):
                host = normalize_host(item)
                if host:
                    hosts.append(host)
            else:
                # cdnList 正常是 p2.a.yximgs.com 这种纯 host。
                hosts.append(item.strip("/"))
        elif isinstance(item, dict):
            for key in ("cdn", "host", "url"):
                if item.get(key):
                    add(item.get(key))
                    break

    if isinstance(value, list):
        for item in value:
            add(item)
    else:
        add(value)

    # 仅保留看起来像域名的值，避免把其它字符串当 CDN。
    result = []
    for host in hosts:
        if re.fullmatch(r"[A-Za-z0-9.-]+", host) and "." in host:
            result.append(host)
    return dedupe(result)


def _kuaishou_atlas_paths(atlas: dict, base_url: str) -> tuple[list[str], list[str]]:
    """
    解析快手图集 atlas 结构。

    当前公开数据常见结构：
        atlas: {
            cdnList: [{"cdn": "p2.a.yximgs.com"}, ...],
            list: ["/ufile/atlas/..._0.webp", ...]
        }

    返回 (可直接访问的 URL, 相对路径)。
    """
    if not isinstance(atlas, dict):
        return [], []

    raw_list = atlas.get("list") or atlas.get("imageList") or atlas.get("images")
    if not isinstance(raw_list, list):
        return [], []

    cdns = _kuaishou_cdn_hosts(atlas.get("cdnList") or atlas.get("cdn"))
    direct_urls: list[str] = []
    paths: list[str] = []

    for item in raw_list:
        if isinstance(item, dict):
            value = item.get("url") or item.get("src") or item.get("path")
        else:
            value = item

        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue

        # atlas.list 通常是 /ufile/atlas/... 相对路径。不要先用 base_url
        # 把它变成 c.kuaishou.com/path，否则会丢掉真正的图片 CDN。
        if value.startswith("//"):
            direct_urls.append("https:" + value)
            continue
        if value.startswith("/"):
            paths.append(value)
            if cdns:
                # CDN 是同一张图的镜像，不能按 CDN 数量重复展示图片。
                direct_urls.append(f"https://{cdns[0]}{value}")
            continue

        u = normalize_media_url(value, base_url)
        if u:
            direct_urls.append(u)
            continue

    return dedupe(direct_urls), dedupe(paths)


def _collect_kuaishou_images(data: Any, base_url: str) -> list[str]:
    """
    提取快手图文/图集原图。

    与旧版最大的区别：快手图集并不是 imageUrls 数组，而是：
        atlas.cdnList + atlas.list
    其中 list 是 /ufile/atlas/... 的相对路径，必须与图片 CDN 组合。
    """
    urls: list[str] = []

    # 1) 优先处理 atlas 对象。
    # 如果页面同时存在推荐流/封面 imageUrls，不能和当前作品的 atlas 混在一起。
    atlas_urls: list[str] = []
    for atlas in recursive_find_all(data, {"atlas"}, max_results=100):
        if isinstance(atlas, dict):
            direct, _ = _kuaishou_atlas_paths(atlas, base_url)
            atlas_urls.extend(direct)

    atlas_urls = dedupe(atlas_urls)
    if atlas_urls:
        return atlas_urls

    # 2) 保留旧结构 imageUrls/images/photoList 兼容。
    for value in recursive_find_all(
        data,
        {"imageUrls", "images", "photoList"},
        max_results=100,
    ):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    u = normalize_media_url(item, base_url)
                    if u:
                        urls.append(u)
                elif isinstance(item, dict):
                    for key in ("url", "cdnUrl", "src", "path"):
                        u = normalize_media_url(item.get(key), base_url)
                        if u:
                            urls.append(u)
        elif isinstance(value, dict):
            for key in ("url", "cdnUrl", "src", "path"):
                u = normalize_media_url(value.get(key), base_url)
                if u:
                    urls.append(u)

    # 3) 最后兜底：任何明确出现 /ufile/atlas/ 的绝对 URL。
    for node in iter_nodes(data):
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str) and "/ufile/atlas/" in value:
                    u = normalize_media_url(value, base_url)
                    if u:
                        urls.append(u)

    return dedupe(urls)


def _probe_kuaishou_image_url(url: str) -> bool:
    """轻量验证快手图集 URL 是否返回图片。"""
    headers = {
        **PC_HEADERS,
        "Referer": "https://www.kuaishou.com/",
        "Range": "bytes=0-0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        resp = session.get(
            url,
            headers=headers,
            allow_redirects=True,
            stream=True,
            timeout=8,
        )
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


def _extract_kuaishou_browser_images(url: str) -> Optional[dict]:
    """
    快手图集的浏览器兜底。

    当前快手图集公开分享页的静态 INIT_STATE 可能为空，实际图像在 JS 执行后
    进入 DOM；浏览器模式只读取当前作品 swiper/player 容器中的 /ufile/atlas/ 图像，
    不读取账号 Cookie/LocalStorage。
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
            page.wait_for_timeout(3500)

            result = page.evaluate("""() => {
                const getCandidates = (root) => {
                    const out = [];
                    const add = (value) => {
                        if (typeof value !== 'string') return;
                        value = value.trim();
                        if (!value || !value.includes('/ufile/atlas/')) return;
                        out.push(value);
                    };

                    const nodes = root ? root.querySelectorAll('img, source, a, [data-src], [data-url]') : [];
                    for (const el of nodes) {
                        add(el.currentSrc);
                        add(el.src);
                        add(el.getAttribute('src'));
                        add(el.getAttribute('data-src'));
                        add(el.getAttribute('data-url'));
                        add(el.getAttribute('href'));
                        const srcset = el.getAttribute('srcset');
                        if (srcset) {
                            for (const part of srcset.split(',')) add(part.trim().split(/\\s+/)[0]);
                        }
                    }

                    // 处理背景图。
                    if (root) {
                        for (const el of root.querySelectorAll('*')) {
                            const bg = getComputedStyle(el).backgroundImage || '';
                            const marker = 'url(';
                            const start = bg.indexOf(marker);
                            if (start >= 0) {
                                let value = bg.slice(start + marker.length).trim();
                                if (value.endsWith(')')) value = value.slice(0, -1).trim();
                                value = value.replace(/^['\"]|['\"]$/g, '');
                                add(value);
                            }
                        }
                    }
                    return [...new Set(out)];
                };

                // 当前作品优先：active slide -> 同一个 swiper-wrapper 的全部 slides。
                const active = document.querySelector('.swiper-slide-active .player')
                    || document.querySelector('.swiper-slide-active');
                let root = active;
                const wrapper = active ? active.closest('.swiper-wrapper') : null;
                if (wrapper) root = wrapper;

                let urls = getCandidates(root);

                // 不扫描全页和全局 performance 资源，以免混入推荐作品图集。

                return {
                    title: document.title || '',
                    images: [...new Set(urls)]
                };
            }""")

            browser.close()
            if not result or not result.get("images"):
                return None

            return result
    except Exception:
        return None


# 移动端新分享页使用 INIT_STATE（没有前后的双下划线）。
_KUAISHOU_STATE_MARKERS = (
    "window.INIT_STATE", "window.__INIT_STATE__", "window.__APOLLO_STATE__",
    "window.__INITIAL_STATE__", "window.pageData", "window.__NEXT_DATA__",
)


def _kuaishou_media_scope(data: Any) -> Any:
    """排除配乐、头像、推荐内容；展开 manifest 中嵌套的 JSON 文本。"""
    ignored = {"soundTrack", "music", "audio", "user", "author", "userProfile",
               "recommend", "recommendList", "recommendFeeds", "relatedPhotos"}
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ignored:
                continue
            if key == "manifest" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    continue
            result[key] = _kuaishou_media_scope(value)
        return result
    if isinstance(data, list):
        return [_kuaishou_media_scope(value) for value in data]
    return data


def _kuaishou_photo(data: Any, actual_url: str) -> Optional[dict]:
    """像抖音解析一样，先定位当前作品，再提取媒体，而非扫描整个页面。"""
    parsed = urlparse(actual_url)
    query = parse_qs(parsed.query)
    target_ids = set(query.get("photoId", []) + query.get("shareObjectId", []))
    match = re.search(r"/(?:short-video|photo|long-video)/([\w-]+)", parsed.path)
    if match:
        target_ids.add(match.group(1))
    candidates = []
    for node in iter_nodes(_kuaishou_media_scope(data)):
        if not isinstance(node, dict):
            continue
        for key in ("photo", "photoDetail", "photoInfo", "currentPhoto"):
            photo = node.get(key)
            if isinstance(photo, dict):
                candidates.append(photo)
        if ("photoId" in node or "id" in node) and any(
            key in node for key in ("mainMvUrls", "atlas", "manifest", "playUrl", "videoUrl")
        ):
            candidates.append(node)
    if not candidates:
        return None
    for photo in candidates:
        ids = {str(photo.get(key, "")) for key in ("photoId", "id", "eid")}
        ids.update(parse_qs(str(photo.get("share_info", ""))).get("photoId", []))
        if ids & target_ids:
            return photo
    # 页面只有一个作品时可安全回退；多个未匹配作品时不猜测。
    unique = {id(photo): photo for photo in candidates}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def _kuaishou_result(data: Any, actual_url: str, title: str, source: str) -> Optional[dict]:
    photo = _kuaishou_photo(data, actual_url)
    if photo is None:
        # 兼容旧的单作品裸对象；多作品容器不能整体扫描取首条。
        if not isinstance(data, dict) or not any(
            key in data for key in ("mainMvUrls", "atlas", "playUrl", "videoUrl", "manifest", "imageUrls")
        ):
            return None
        photo = data
    media = _kuaishou_media_scope(photo)
    result = {
        "platform": "快手",
        "title": first_nonempty_string([photo.get(key) for key in ("caption", "shareTitle", "title", "desc")] + [title], "快手作品"),
        "type": "unknown", "video_url": None, "image_urls": [], "source": source,
    }
    # 图集可能同时有配乐/动态预览，atlas 才是作品主体。
    atlas_urls = []
    for atlas in recursive_find_all(media, {"atlas"}, max_results=30):
        if isinstance(atlas, dict):
            direct, _ = _kuaishou_atlas_paths(atlas, actual_url)
            atlas_urls.extend(direct)
    if atlas_urls:
        result.update(type="image", image_urls=dedupe(atlas_urls))
        return result
    # mainMvUrls 是分享页明确提供的视频播放地址，应先于 manifest 通用扫描。
    videos = _collect_kuaishou_video_urls({"mainMvUrls": media.get("mainMvUrls", [])}, actual_url)
    videos = videos or _collect_kuaishou_video_urls(media, actual_url)
    if videos:
        result.update(type="video", video_url=videos[0])
        return result
    images = _collect_kuaishou_images(media, actual_url)
    if images:
        result.update(type="image", image_urls=images)
        return result
    return None


def parse_kuaishou_page(html: str, actual_url: str) -> Optional[dict]:
    title = title_from_html(html) or "快手作品"
    # 一个页面可能同时带有空 Apollo 和有效 INIT_STATE，必须逐一尝试。
    for marker in _KUAISHOU_STATE_MARKERS:
        pos = html.find(marker)
        while pos != -1:
            blob = extract_balanced_json(html, pos + len(marker))
            if blob:
                try:
                    result = _kuaishou_result(safe_json_loads(blob), actual_url, title, marker)
                    if result:
                        return result
                except (ValueError, TypeError):
                    pass
            pos = html.find(marker, pos + len(marker))
    # 兼容 JSON script，不执行页面 JavaScript。
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.I | re.S):
        if not re.search(r"(?:application/(?:ld\+)?json|__NEXT_DATA__)", match.group(1), re.I):
            continue
        try:
            result = _kuaishou_result(json.loads(match.group(2)), actual_url, title, "JSON script")
            if result:
                return result
        except (ValueError, TypeError):
            continue
    videos, _ = html_media_fallback(html, actual_url, want_video=True)
    if videos:
        return {"platform": "快手", "title": title, "type": "video", "video_url": videos[0],
                "image_urls": [], "source": "HTML video"}
    # og:image 往往只是封面；不能把封面当成成功解析出的图集。
    return None


def parse_kuaishou(final_url: str, share_url: Optional[str] = None) -> dict:
    candidates = []
    for u in (share_url, final_url):
        if u and u not in candidates:
            candidates.append(u)

    last_error = (
        "快手公开页没有返回可识别的 Apollo/SSR 媒体字段。"
        "注意：不要把最终 /short-video/... 直链当成唯一输入，"
        "优先使用快手“分享/复制链接”产生的官方分享链接。"
    )

    # PC 分享页经常仅返回应用壳；移动端响应才携带 photo/mainMvUrls。
    for request_headers in (MOBILE_HEADERS, PC_HEADERS):
        for url in candidates:
            try:
                html, actual_url, headers = fetch_page(url, request_headers)
                content_type = str(headers.get("content-type", headers.get("Content-Type", ""))).lower()
                if "json" in content_type and html.lstrip().startswith(("{", "[")):
                    try:
                        result = _kuaishou_result(json.loads(html), actual_url, "快手作品", "JSON response")
                    except (ValueError, TypeError):
                        result = None
                else:
                    result = parse_kuaishou_page(html, actual_url)
                if result:
                    return result
                last_error = "快手公开页未提供当前作品的媒体信息，可能是链接失效、作品不可见或访问受限。"
            except requests.RequestException as exc:
                last_error = f"快手 HTTP 请求失败：{exc}"

    # 浏览器渲染兜底：普通视频页继续走 HTML；图集页重点读取公开 DOM 中的 /ufile/atlas/。
    rendered = try_playwright(share_url or final_url)
    if rendered:
        result = parse_kuaishou_page(rendered, share_url or final_url)
        if result:
            result["source"] = "Playwright/" + result.get("source", "HTML")
            return result

    # 专门的图集 DOM 路线。静态 INIT_STATE 为空时，只有执行 JS 后才能拿到图片。
    browser_result = _extract_kuaishou_browser_images(share_url or final_url)
    if browser_result:
        urls = dedupe(browser_result.get("images", []))
        if urls:
            return {
                "platform": "快手",
                "title": browser_result.get("title") or "快手作品",
                "type": "image",
                "video_url": None,
                "image_urls": urls,
                "source": "Playwright/atlas DOM",
            }

    raise RuntimeError(last_error)
