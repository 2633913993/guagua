"""抖音解析模块：核心解析逻辑保持原代码。"""
from .common import *
# =========================
# 抖音
# =========================

def parse_douyin(final_url):
    match = re.search(r'/(?:video|note)/(\d+)', final_url)
    aweme_id = match.group(1) if match else None

    resp = session.get(final_url, headers=mobile_headers, timeout=15)
    html = resp.text

    if not aweme_id:
        aweme_id_match = re.search(r'"aweme_id"\s*:\s*"(\d+)"', html)
        if aweme_id_match: aweme_id = aweme_id_match.group(1)

    match = re.search(r'window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>', html, re.DOTALL)
    if not match:
        raise Exception("抖音页面未找到数据结构")

    json_str = re.sub(r'\bundefined\b', 'null', match.group(1))
    data = json.loads(json_str)

    result = {"platform": "抖音", "title": "抖音作品", "type": "unknown", "video_url": None, "image_urls": []}

    def find_aweme(obj):
        if isinstance(obj, dict):
            if str(obj.get("aweme_id")) == str(aweme_id): return obj
            for v in obj.values():
                res = find_aweme(v)
                if res: return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_aweme(item)
                if res: return res
        return None

    aweme_info = find_aweme(data)
    if not aweme_info: raise Exception("未找到抖音作品详细信息")

    if "desc" in aweme_info: result["title"] = aweme_info["desc"]

    if "images" in aweme_info and aweme_info["images"]:
        result["type"] = "image"
        for img in aweme_info["images"]:
            url_list = img.get("url_list", [])
            if url_list: result["image_urls"].append(url_list[0])
    elif "video" in aweme_info and "play_addr" in aweme_info["video"]:
        result["type"] = "video"
        url_list = aweme_info["video"]["play_addr"].get("url_list", [])
        if url_list: result["video_url"] = url_list[0].replace("playwm", "play")

    return result
