import hashlib
import re
import time
import urllib.parse
import uuid
from typing import Any, Optional

import aiohttp

from astrbot.api import logger


REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}

WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
]

_wbi_cache: dict[str, Any] = {"key": "", "time": 0.0}


def parse_cookie_string(cookie: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value
    return result


def build_headers(cookies: Optional[dict[str, str]] = None) -> dict[str, str]:
    headers = dict(HEADERS)
    cookie_dict = dict(cookies or {})
    cookie_dict.setdefault("buvid3", f"{uuid.uuid4()}infoc")
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if v)
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _extract_wbi_key_part(url: str) -> str:
    filename = url.rsplit("/", 1)[-1]
    return filename.split(".", 1)[0]


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in WBI_MIXIN_KEY_ENC_TAB)[:32]


async def get_wbi_mixin_key(cookies: Optional[dict[str, str]] = None) -> str:
    now = time.time()
    if _wbi_cache["key"] and now - float(_wbi_cache["time"]) < 3600:
        return str(_wbi_cache["key"])

    url = "https://api.bilibili.com/x/web-interface/nav"
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, headers=build_headers(cookies)) as resp:
                data = await resp.json()
                wbi_img = (data.get("data") or {}).get("wbi_img") or {}
                img_key = _extract_wbi_key_part(wbi_img.get("img_url", ""))
                sub_key = _extract_wbi_key_part(wbi_img.get("sub_url", ""))
                if img_key and sub_key:
                    key = _mixin_key(img_key, sub_key)
                    _wbi_cache.update({"key": key, "time": now})
                    return key
    except Exception as exc:
        logger.warning(f"[B站视频总结] 获取 WBI key 失败: {exc}")
    return ""


async def sign_wbi_params(
    params: dict[str, Any],
    cookies: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    mixin_key = await get_wbi_mixin_key(cookies)
    if not mixin_key:
        return params

    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    filtered = {
        k: re.sub(r"[!'()*]", "", str(v))
        for k, v in signed.items()
        if v is not None
    }
    query = urllib.parse.urlencode(filtered)
    filtered["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return filtered


async def resolve_short_url(url: str) -> str:
    if "b23.tv/" not in (url or ""):
        return url
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, allow_redirects=True, headers=HEADERS) as resp:
                return str(resp.url)
    except Exception as exc:
        logger.warning(f"[B站视频总结] 解析短链失败: {exc}")
        return url


async def get_video_info(
    bvid: str,
    cookies: Optional[dict[str, str]] = None,
) -> Optional[dict[str, Any]]:
    url = "https://api.bilibili.com/x/web-interface/view"
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params={"bvid": bvid}, headers=build_headers(cookies)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    logger.warning(f"[B站视频总结] 获取视频信息失败: {data.get('message')}")
                    return None
                d = data.get("data") or {}
                owner = d.get("owner") or {}
                stat = d.get("stat") or {}
                return {
                    "bvid": d.get("bvid", bvid),
                    "title": d.get("title", ""),
                    "pic": d.get("pic", ""),
                    "desc": d.get("desc", ""),
                    "pubdate": d.get("pubdate", 0),
                    "owner_name": owner.get("name", "未知"),
                    "owner_mid": str(owner.get("mid", "")),
                    "view": stat.get("view", 0),
                    "danmaku": stat.get("danmaku", 0),
                    "like": stat.get("like", 0),
                }
    except Exception as exc:
        logger.warning(f"[B站视频总结] 获取视频信息异常: {exc}")
        return None


async def get_up_info(
    mid: str,
    cookies: Optional[dict[str, str]] = None,
) -> Optional[dict[str, str]]:
    params = await sign_wbi_params({"mid": mid}, cookies)
    url = "https://api.bilibili.com/x/space/wbi/acc/info"
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params, headers=build_headers(cookies)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return None
                info = data.get("data") or {}
                return {
                    "mid": str(info.get("mid", mid)),
                    "name": info.get("name", f"UP主_{mid}"),
                    "face": info.get("face", ""),
                }
    except Exception as exc:
        logger.warning(f"[B站视频总结] 获取 UP 信息异常: {exc}")
        return None


async def search_up_by_name(
    keyword: str,
    cookies: Optional[dict[str, str]] = None,
) -> Optional[dict[str, str]]:
    params = {
        "search_type": "bili_user",
        "keyword": keyword,
        "page": 1,
        "order": "fans",
        "order_sort": 0,
    }
    signed = await sign_wbi_params(params, cookies)
    url = "https://api.bilibili.com/x/web-interface/wbi/search/type"
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params=signed, headers=build_headers(cookies)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return None
                results = (data.get("data") or {}).get("result") or []
                if not results:
                    return None
                first = results[0]
                name = str(first.get("uname", "未知"))
                name = name.replace('<em class="keyword">', "").replace("</em>", "")
                return {"mid": str(first.get("mid", "")), "name": name}
    except Exception as exc:
        logger.warning(f"[B站视频总结] 搜索 UP 异常: {exc}")
        return None


async def get_latest_videos(
    mid: str,
    count: int = 1,
    cookies: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    params = await sign_wbi_params(
        {"mid": mid, "ps": count, "pn": 1, "order": "pubdate"},
        cookies,
    )
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params, headers=build_headers(cookies)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    logger.warning(f"[B站视频总结] 获取投稿失败: {data.get('message')}")
                    return []
                vlist = ((data.get("data") or {}).get("list") or {}).get("vlist") or []
                return [
                    {
                        "bvid": item.get("bvid", ""),
                        "title": item.get("title", ""),
                        "pubdate": item.get("created", 0),
                        "pic": item.get("pic", ""),
                        "description": item.get("description", ""),
                    }
                    for item in vlist[:count]
                ]
    except Exception as exc:
        logger.warning(f"[B站视频总结] 获取投稿异常: {exc}")
        return []
