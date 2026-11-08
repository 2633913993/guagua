#!/usr/bin/env python3
"""终端解析快手国内分享链接，可选下载公开原视频候选源。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    raise SystemExit("缺少 requests，请运行：python3 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple requests")

from api.media_parser_fastapi.app.parsers.common import PC_HEADERS, extract_urls
from api.media_parser_fastapi.app.parsers.kuaishou import parse_kuaishou


def extract_share_url(text: str) -> str:
    for url in extract_urls(text):
        host = (urlparse(url).hostname or "").lower()
        if any(host == domain or host.endswith("." + domain)
               for domain in ("kuaishou.com", "chenzhongtech.com")):
            return url
    raise ValueError("没有找到快手国内分享链接，请粘贴快手“分享 → 复制链接”的完整文案。")


def resolve(text: str) -> dict:
    """可供 Python 后端直接调用，不依赖 FastAPI。"""
    share_url = extract_share_url(text)
    result = parse_kuaishou(share_url, share_url=share_url)
    if result.get("type") != "video":
        raise ValueError("这条分享链接是图集，当前终端工具只处理视频。")
    result["share_url"] = share_url
    return result


def _mp4_header(data: bytes) -> bool:
    # MP4 常见首盒为 ftyp，少数媒体会以 free/wide 开始；不接受 HTML/JSON 错误页。
    return len(data) >= 12 and data[4:8] in (b"ftyp", b"free", b"wide", b"mdat", b"moov")


def download_video(result: dict, output: Path) -> tuple[Path, dict]:
    output = Path(output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(result.get("title") or "快手视频"))
    title = title.strip(" .")[:60] or "快手视频"
    # 防止 Windows 保留文件名，跨平台可运行。
    if title.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                                      *[f"LPT{i}" for i in range(1, 10)]}:
        title = "快手_" + title
    candidates = result.get("video_candidates") or [
        {"url": result["video_url"], "field": "unknown", "watermark_free": result.get("watermark_free")}
    ]
    errors = []
    for candidate in candidates:
        url = candidate["url"]
        if urlparse(url).path.lower().endswith(".m3u8"):
            errors.append("该候选源是 HLS 播放列表，不能直接保存为 MP4")
            continue
        temporary = None
        destination = None
        try:
            # 独立会话，避免把解析页面的 Cookie 发给媒体 CDN。
            with requests.get(url, headers={
                "User-Agent": PC_HEADERS["User-Agent"],
                "Referer": "https://www.kuaishou.com/", "Accept-Encoding": "identity",
            }, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("媒体服务器没有返回完整文件")
                content_type = response.headers.get("Content-Type", "").lower()
                if any(kind in content_type for kind in ("text/", "json", "mpegurl", "image/")):
                    raise ValueError("媒体服务器返回了网页、图片或播放列表，未保存为 MP4")
                received = 0
                prefix = b""
                with tempfile.NamedTemporaryFile(dir=str(output), prefix=".kuaishou-", suffix=".part", delete=False) as stream:
                    temporary = Path(stream.name)
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        if len(prefix) < 16:
                            prefix = (prefix + chunk)[:16]
                            if len(prefix) >= 12 and not _mp4_header(prefix):
                                raise ValueError("响应不是 MP4 视频，可能是验证页或链接已过期")
                        stream.write(chunk)
                        received += len(chunk)
                if not _mp4_header(prefix):
                    raise ValueError("视频内容为空或文件头无效")
                expected = response.headers.get("Content-Length", "")
                if expected.isdigit() and received != int(expected):
                    raise ValueError("下载不完整，请重新解析后重试")

            # 独占创建目标，已有同名文件自动编号，避免覆盖用户文件。
            for index in range(10000):
                name = f"{title}{'_' + str(index) if index else ''}.mp4"
                path = output / name
                try:
                    with path.open("xb"):
                        pass
                except FileExistsError:
                    continue
                destination = path
                break
            if destination is None:
                raise OSError("同名文件过多，请更换输出目录")
            temporary.replace(destination)
            temporary = None
            return destination.resolve(), candidate
        except (requests.RequestException, OSError, ValueError) as exc:
            errors.append(str(exc))
            if destination is not None and destination.exists():
                destination.unlink()
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise RuntimeError("视频下载失败：" + "；".join(dict.fromkeys(errors)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="快手国内分享链接解析（Python 3.7+）")
    parser.add_argument("share", nargs="?", help="分享链接或整段分享文案；省略时终端输入")
    parser.add_argument("-d", "--download", action="store_true", help="下载视频到本地")
    parser.add_argument("-o", "--output", type=Path, default=Path(__file__).resolve().parent / "downloads",
                        help="下载目录，默认项目 downloads 目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于后端集成")
    args = parser.parse_args(argv)
    try:
        text = args.share
        if text is None:
            if not args.json:
                print("请粘贴快手分享链接或分享文案，然后按回车：", file=sys.stderr)
            text = input()
        result = resolve(text)
        if args.download:
            path, selected = download_video(result, args.output)
            result.update(local_file=str(path), video_url=selected["url"],
                          watermark_free=selected.get("watermark_free"), downloaded_source=selected.get("field"))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"标题：{result['title']}")
            print(f"解析来源：{result.get('source', '')}")
            print("媒体源状态：" + ("页面明确标记为无水印源" if result.get("watermark_free") is True
                                  else "网页播放源，未确认画面是否含水印"))
            print(f"视频地址：{result['video_url']}")
            if result.get("local_file"):
                print(f"已保存：{result['local_file']}")
        return 0
    except (ValueError, RuntimeError, requests.RequestException, OSError, EOFError) as exc:
        if args.json:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
