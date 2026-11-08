"""命令行入口，保留原脚本交互方式。"""
from .common import *
from .core import parse_one_url
def print_result(result: dict):
    print("\n" + "=" * 72)
    print(f"[{result['platform']}]")
    print(f"标题：{result['title']}")
    print(f"作品类型：{result['type']}")
    print(f"解析来源：{result.get('source', 'unknown')}")
    if result.get("platform", "").startswith("小红书"):
        status = result.get("watermark_free")
        if status is True:
            print("媒体源状态：原始/无水印源")
        elif status is False:
            print("媒体源状态：网页兜底源，不能保证无水印")
    print(f"分享链接：{result.get('share_url', '')}")
    print(f"最终链接：{result.get('final_url', '')}")

    if result["type"] == "video":
        print(f"视频地址：{result['video_url']}")
    elif result["type"] == "image":
        urls = result.get("image_urls", [])
        print(f"图片数量：{len(urls)}")
        for index, url in enumerate(urls, 1):
            print(f"图片 {index}: {url}")
    else:
        print("未能识别视频或图片媒体。")
    print("=" * 72)


def print_dependency_hint():
    print(
        "\n提示：如果某个平台在 requests 模式下经常返回空壳页面，"
        "可以安装浏览器兜底：\n"
        "  pip install playwright\n"
        "  playwright install chromium\n"
        "安装后程序会自动尝试 JS 渲染，不需要修改代码。\n"
    )


def main():
    print("请粘贴一个或多个分享链接（可包含标题文字，空格/换行分隔）。")
    print("粘贴完成后再按一次回车留空结束输入：")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break

        if not line.strip():
            break

        lines.append(line)

    user_input = "\n".join(lines)
    urls = extract_urls(user_input)

    if not urls:
        print("未找到有效的 http/https 链接。")
        return

    print(f"识别到 {len(urls)} 个链接。")

    for index, share_url in enumerate(urls, 1):
        print(f"\n>>> 正在解析第 {index}/{len(urls)} 个链接")
        print(f"原始链接：{share_url}")

        try:
            result = parse_one_url(share_url)
            print_result(result)
        except Exception as exc:
            print(f"解析失败：{exc}")
            print_dependency_hint()


if __name__ == "__main__":
    main()
