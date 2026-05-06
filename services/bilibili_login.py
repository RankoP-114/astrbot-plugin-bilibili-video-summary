import asyncio
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import aiohttp

from astrbot.api import logger


QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


class BilibiliLogin:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_path = self.data_dir / "bili_cookies.json"
        self._cookies = self._load_cookies()

    def get_cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def is_logged_in(self) -> bool:
        return bool(self._cookies.get("SESSDATA"))

    async def generate_qrcode(self) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.get(QR_GENERATE_URL, headers=HEADERS) as resp:
                    data = await resp.json()
                    if resp.status != 200 or data.get("code") != 0:
                        logger.warning(f"[B站视频总结] 申请 B站二维码失败: HTTP {resp.status}, {data}")
                        return None
                    return data.get("data")
        except Exception as exc:
            logger.warning(f"[B站视频总结] 申请 B站二维码异常: {exc}")
            return None

    async def poll_login(self, qrcode_key: str) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.get(QR_POLL_URL, params={"qrcode_key": qrcode_key}, headers=HEADERS) as resp:
                    if resp.status != 200:
                        return {"status": "error", "cookies": {}}
                    data = await resp.json()
                    code = (data.get("data") or {}).get("code")
                    if code == 0:
                        url = (data.get("data") or {}).get("url", "")
                        cookies = self._parse_cookies_from_url(url)
                        for cookie in resp.cookies.values():
                            cookies[cookie.key] = cookie.value
                        if cookies.get("SESSDATA"):
                            self._save_cookies(cookies)
                            return {"status": "success", "cookies": cookies}
                        return {"status": "error", "cookies": {}}
                    if code == 86090:
                        return {"status": "scanned", "cookies": {}}
                    if code == 86038:
                        return {"status": "expired", "cookies": {}}
                    if code == 86101:
                        return {"status": "waiting", "cookies": {}}
                    return {"status": "error", "cookies": {}}
        except Exception as exc:
            logger.warning(f"[B站视频总结] 轮询 B站登录异常: {exc}")
            return {"status": "error", "cookies": {}}

    async def do_login_flow(self, qrcode_key: str, timeout: int = 180) -> dict:
        elapsed = 0
        interval = 3
        while elapsed < timeout:
            result = await self.poll_login(qrcode_key)
            if result["status"] in {"success", "expired", "error"}:
                return result
            await asyncio.sleep(interval)
            elapsed += interval
        return {"status": "timeout", "cookies": {}}

    def logout(self) -> None:
        self._cookies = {}
        try:
            if self.cookies_path.exists():
                os.remove(self.cookies_path)
        except OSError as exc:
            logger.warning(f"[B站视频总结] 删除 B站 Cookie 失败: {exc}")

    def _load_cookies(self) -> dict[str, str]:
        if not self.cookies_path.exists():
            return {}
        try:
            data = json.loads(self.cookies_path.read_text(encoding="utf-8"))
            if data.get("SESSDATA"):
                return {str(k): str(v) for k, v in data.items() if v}
        except Exception as exc:
            logger.warning(f"[B站视频总结] 加载 B站 Cookie 失败: {exc}")
        return {}

    def _save_cookies(self, cookies: dict[str, str]) -> None:
        self.cookies_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cookies = dict(cookies)

    @staticmethod
    def _parse_cookies_from_url(url: str) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if "?" not in url:
            return cookies
        query = url.split("?", 1)[1]
        for item in query.split("&"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key in {"SESSDATA", "bili_jct", "DedeUserID", "sid"}:
                cookies[key] = unquote(value)
        return cookies

