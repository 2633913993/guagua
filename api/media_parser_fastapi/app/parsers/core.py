"""统一解析入口：负责平台识别、短链展开和路由。"""
import requests
from .common import *
from .douyin import parse_douyin
from .xiaohongshu import parse_xiaohongshu
from .kuaishou import parse_kuaishou
from .bilibili import parse_bilibili
from .zuiyou import parse_zuiyou
from .pipix import parse_pipix
from .jianying import parse_jianying
# =========================
# 统一解析
# =========================

def parse_one_url(share_url: str) -> dict:
    share_url = clean_url(share_url)
    platform = detect_platform(share_url)

    if not platform:
        raise ValueError(f"不支持的平台域名：{normalize_host(share_url)}")

    headers = PC_HEADERS if platform in {"kuaishou", "bilibili"} else MOBILE_HEADERS

    # 短链展开不是强依赖：某些网络环境下 xhslink.cn 的跳转请求会失败，
    # 但小红书解析器本身可以直接尝试分享 URL，因此这里保留 share_url 作为回退入口。
    redirect_error = None
    try:
        final_url = get_final_url(share_url, headers=headers)
    except requests.RequestException as exc:
        redirect_error = exc
        final_url = share_url

    # 有些平台重定向后换了域名，重新判断一次。
    final_platform = detect_platform(final_url) or platform

    if final_platform == "douyin":
        if redirect_error:
            raise RuntimeError(f"抖音短链展开失败：{redirect_error}")
        result = parse_douyin(final_url)
    elif final_platform == "xiaohongshu":
        result = parse_xiaohongshu(final_url, share_url=share_url)
    elif final_platform == "kuaishou":
        if redirect_error:
            raise RuntimeError(f"快手短链展开失败：{redirect_error}")
        result = parse_kuaishou(final_url, share_url=share_url)
    elif final_platform == "bilibili":
        # 完整 BV/av 链接无需依赖预展开成功；解析器也能处理 b23.tv。
        result = parse_bilibili(final_url, share_url=share_url)
    elif final_platform == "zuiyou":
        result = parse_zuiyou(final_url, share_url=share_url)
    elif final_platform == "pipix":
        result = parse_pipix(final_url, share_url=share_url)
    elif final_platform == "jianying":
        result = parse_jianying(final_url, share_url=share_url)
    else:
        raise ValueError(f"重定向后的平台不受支持：{normalize_host(final_url)}")

    result["share_url"] = share_url
    result["final_url"] = final_url
    return result
