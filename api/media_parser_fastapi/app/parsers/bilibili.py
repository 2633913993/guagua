"""Bilibili 公开视频解析：BV / av / b23.tv，返回可直接播放的合流 MP4。"""
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .common import (
    PC_HEADERS, TIMEOUT, clean_url, extract_state, fetch_page, get_final_url,
    normalize_host, normalize_media_url, session,
)

_HEADERS = {**PC_HEADERS, "Referer": "https://www.bilibili.com/"}
_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
_PLAY_API = "https://api.bilibili.com/x/player/playurl"


def _video_reference(url: str) -> Optional[tuple[str, int]]:
    parsed = urlparse(url)
    host = normalize_host(url)
    if parsed.scheme not in ("http", "https") or not (host == "bilibili.com" or host.endswith(".bilibili.com")):
        return None
    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av[0-9]+)/?", parsed.path, re.I)
    if not match:
        return None
    raw_page = parse_qs(parsed.query).get("p", ["1"])[0]
    if not raw_page.isdigit() or int(raw_page) < 1:
        raise ValueError("Bilibili 分集参数 p 必须为大于 0 的整数。")
    identifier = match.group(1)
    identifier = ("BV" + identifier[2:]) if identifier[:2].lower() == "bv" else identifier.lower()
    return identifier, int(raw_page)


def _resolve_reference(final_url: str, share_url: Optional[str]) -> tuple[str, int]:
    # 原始链接优先，以免重定向丢失分集参数；b23.tv 则使用展开后的地址。
    for url in (share_url, final_url):
        if url:
            reference = _video_reference(url)
            if reference:
                return reference
    for url in (share_url, final_url):
        if url and normalize_host(url) == "b23.tv":
            try:
                expanded = get_final_url(url, headers=_HEADERS)
            except requests.RequestException as exc:
                raise RuntimeError("Bilibili 短链展开失败，请确认网络或使用完整 BV/av 链接。") from exc
            reference = _video_reference(expanded)
            if reference:
                return reference
    raise ValueError("目前支持 Bilibili /video/BV…、/video/av… 和 b23.tv 视频分享链接，暂不支持番剧、直播或动态。")


def _api_data(url: str, params: dict, headers: dict) -> dict:
    try:
        response = session.get(url, params=params, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError("Bilibili 公开接口请求失败，可能遇到网络错误或访问限制。") from exc
    except ValueError as exc:
        raise RuntimeError("Bilibili 接口未返回 JSON，可能是访问验证页。") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Bilibili 接口返回的数据格式不正确。")
    if payload.get("code") != 0:
        code = payload.get("code")
        if code == -404:
            raise ValueError("Bilibili 视频不存在或已删除。")
        if code in (-101, -403, -10403, -352, -412):
            raise RuntimeError("Bilibili 暂未向匿名访问提供此视频，请检查作品权限或稍后重试。")
        raise RuntimeError(f"Bilibili 接口返回错误（{code}）：{payload.get('message') or '未知错误'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Bilibili 接口没有返回视频数据。")
    return data


def _matches_video(video: Any, identifier: str) -> bool:
    if not isinstance(video, dict):
        return False
    if identifier.startswith("BV"):
        return video.get("bvid") == identifier
    return str(video.get("aid")) == identifier[2:]


def _check_public_video(video: dict) -> None:
    rights = video.get("rights") or {}
    paid = any(rights.get(key) in (True, 1, "1") for key in ("pay", "ugc_pay", "arc_pay"))
    exclusive = any(video.get(key) in (True, 1, "1") for key in (
        "is_upower_exclusive", "is_upower_preview", "is_chargeable_season",
    ))
    if paid or exclusive:
        raise RuntimeError("当前作品包含付费、充电专属或试看限制，仅支持公开完整视频。")


def _select_page(video: dict, page: int) -> dict:
    pages = video.get("pages")
    if isinstance(pages, list) and pages:
        for item in pages:
            if isinstance(item, dict) and str(item.get("page")) == str(page) and item.get("cid"):
                return item
        raise ValueError(f"Bilibili 视频没有第 {page} 个分集。")
    if page == 1 and video.get("cid"):
        return {"cid": video["cid"], "page": 1, "duration": video.get("duration")}
    raise ValueError("Bilibili 页面缺少该分集的 cid，无法获取播放地址。")


def _progressive_video(data: dict, page_url: str) -> Optional[dict]:
    if data.get("is_preview") in (True, 1, "1") or data.get("isPreview") in (True, 1, "1"):
        raise RuntimeError("Bilibili 仅返回试看内容，未提供公开完整视频。")
    segments = data.get("durl")
    if not isinstance(segments, list) or not segments:
        return None
    if len(segments) != 1:
        # 不能静默返回第一段，导致下载结果被截断。
        raise RuntimeError("Bilibili 返回了多段视频，当前接口暂不支持合并，请换一个公开视频。")
    segment = segments[0]
    if not isinstance(segment, dict):
        return None
    urls = [segment.get("url")] + (segment.get("backup_url") or [])
    urls = [normalize_media_url(url, page_url) for url in urls]
    urls = list(dict.fromkeys(url for url in urls if url))
    if not urls:
        return None
    # FLV 与纯 DASH 视频不适合现有前端 video 组件；只接受合流 MP4。
    format_name = str(data.get("format") or "").lower()
    if not format_name.startswith("mp4") and not urlparse(urls[0]).path.lower().endswith(".mp4"):
        return None
    quality = data.get("quality")
    descriptions = dict(zip(data.get("accept_quality") or [], data.get("accept_description") or []))
    return {
        "video_url": urls[0], "backup_video_urls": urls[1:],
        "quality": quality, "quality_description": descriptions.get(quality, ""),
        "format": format_name or "mp4",
    }


def parse_bilibili(final_url: str, share_url: Optional[str] = None) -> dict:
    identifier, page_number = _resolve_reference(clean_url(final_url), share_url)
    page_url = f"https://www.bilibili.com/video/{identifier}/"
    if page_number != 1:
        page_url += "?" + urlencode({"p": page_number})
    headers = {**_HEADERS, "Referer": page_url}
    html = ""
    video = None
    initial_state = None
    try:
        html, _, _ = fetch_page(page_url, headers)
        initial_state, _ = extract_state(html, ["window.__INITIAL_STATE__"])
        if isinstance(initial_state, dict):
            candidate = initial_state.get("videoData")
            if _matches_video(candidate, identifier):
                video = candidate
    except requests.RequestException:
        # 某些环境网页受限但公开详情接口仍可访问。
        pass
    if video is None:
        key, value = ("bvid", identifier) if identifier.startswith("BV") else ("aid", identifier[2:])
        video = _api_data(_VIEW_API, {key: value}, headers)
        if not _matches_video(video, identifier):
            raise RuntimeError("Bilibili 返回了不匹配的视频信息。")
    _check_public_video(video)
    selected = _select_page(video, page_number)
    cid = selected["cid"]

    # 页内播放信息可以省去一次 API 请求，但必须属于当前分集。
    playback = None
    source = "Bilibili playurl / MP4"
    state_cid = initial_state.get("cid") if isinstance(initial_state, dict) else None
    if str(state_cid) == str(cid):
        playinfo, _ = extract_state(html, ["window.__playinfo__"])
        if isinstance(playinfo, dict) and playinfo.get("code") == 0 and isinstance(playinfo.get("data"), dict):
            playback = _progressive_video(playinfo["data"], page_url)
            if playback:
                source = "window.__playinfo__ / MP4"
    if playback is None:
        params = {
            "bvid": video.get("bvid"), "aid": video.get("aid"), "cid": cid,
            "qn": 64, "fnval": 0, "fnver": 0, "fourk": 0,
            "platform": "html5", "high_quality": 1,
        }
        data = _api_data(_PLAY_API, {key: value for key, value in params.items() if value is not None}, headers)
        playback = _progressive_video(data, page_url)
        if playback is None:
            raise RuntimeError("Bilibili 未提供可直接播放的合流 MP4，可能只有音视频分离流或存在访问限制。")
    title = video.get("title") or "Bilibili 视频"
    if page_number != 1 and selected.get("part"):
        title += " - " + str(selected["part"])
    return {
        "platform": "Bilibili", "title": title, "type": "video", "image_urls": [],
        "source": source, "cover": normalize_media_url(video.get("pic"), page_url) or "",
        "bvid": video.get("bvid"), "aid": video.get("aid"), "cid": cid,
        "page": page_number, "duration": selected.get("duration", video.get("duration")),
        # CDN 地址会过期；保存的只是解析结果，不能作为永久下载地址。
        "request_headers": {"Referer": "https://www.bilibili.com/", "User-Agent": PC_HEADERS["User-Agent"]},
        **playback,
    }
