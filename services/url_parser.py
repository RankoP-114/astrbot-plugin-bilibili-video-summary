import re
from typing import Optional


BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")
VIDEO_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/[A-Za-z0-9/?=&_.%-]+"
)
SHORT_URL_PATTERN = re.compile(r"https?://b23\.tv/[A-Za-z0-9/?=&_.%-]+")


def extract_bvid(text: str) -> Optional[str]:
    if not text:
        return None
    match = BV_PATTERN.search(text)
    return match.group(1) if match else None


def extract_video_url(text: str) -> str:
    if not text:
        return ""

    match = VIDEO_URL_PATTERN.search(text)
    if match:
        return match.group(0).rstrip(">'\"")

    short = SHORT_URL_PATTERN.search(text)
    if short:
        return short.group(0).rstrip(">'\"")

    bvid = extract_bvid(text)
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"

    return ""


def extract_mid(text: str) -> Optional[str]:
    text = (text or "").strip()
    if text.isdigit():
        return text
    match = re.search(r"space\.bilibili\.com/(\d+)", text)
    return match.group(1) if match else None


def is_bilibili_video_url(url: str) -> bool:
    value = (url or "").lower()
    return "bilibili.com/video/" in value or "b23.tv/" in value or bool(extract_bvid(value))

