import asyncio
import json
import uuid
import time
from pathlib import Path
from typing import Optional

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

from .services.bilibili import (
    get_latest_videos,
    get_up_info,
    get_video_info,
    parse_cookie_string,
    resolve_short_url,
    search_up_by_name,
)
from .services.bilibili_login import BilibiliLogin
from .services.models import NoteResult
from .services.note_service import NoteService
from .services.subscription import SubscriptionStore
from .services.url_parser import extract_bvid, extract_mid, extract_video_url, is_bilibili_video_url


PLUGIN_NAME = "astrbot_plugin_bilibili_video_summary"


@register(PLUGIN_NAME, "RankoKanzaki", "B站视频总结插件", "1.0.0")
class BilibiliVideoSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.data_dir = self._get_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.debug_mode = bool(self.config.get("debug_mode", False))
        self.bili_login = BilibiliLogin(str(self.data_dir))
        self.cookies = self._load_effective_cookies()
        self.note_service = NoteService(str(self.data_dir), self.config, self.cookies)
        self.subscriptions = SubscriptionStore(str(self.data_dir))
        self._auto_detect_enabled = bool(self.config.get("enable_auto_detect", False))
        self._running = False
        self._task: Optional[asyncio.Task] = None

        if bool(self.config.get("enable_auto_push", False)):
            self._running = True
            self._task = asyncio.create_task(self._scheduled_loop())

        logger.info(f"[B站视频总结] 插件已加载，B站登录状态: {self._login_status_text()}")
        self._debug("Debug 模式已启用")

    async def initialize(self):
        pass

    @filter.command("B站帮助", alias={"bili帮助", "B站总结帮助"})
    async def help_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(
            "B站视频总结\n"
            "用法:\n"
            "  /B站登录\n"
            "  /B站登出\n"
            "  /B站状态\n"
            "  /B站视频总结 <B站链接/BV号/b23短链>\n"
            "  /B站最新 <UP主UID/空间链接/昵称>\n"
            "  /B站订阅 <UP主UID/空间链接/昵称>\n"
            "  /B站取消订阅 <UP主UID/空间链接/昵称>\n"
            "  /B站订阅列表\n"
            "  /B站检查更新\n"
            "  /B站识别开关\n\n"
            f"当前 B站登录状态: {self._login_status_text()}\n"
            "ASR 可在配置里选择 bcut 或 openai_compatible。图片输出使用 Pillow 渲染，不依赖 wkhtmltopdf。"
        )

    @filter.command("B站登录", alias={"哔哩登录", "B站扫码登录", "bili_login"})
    async def bilibili_login_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event, admin_only=True)
        if denied:
            yield event.plain_result(denied)
            return
        if self.cookies.get("SESSDATA"):
            yield event.plain_result(f"B站已登录，来源: {self._login_status_text()}。如需重新登录，请先 /B站登出。")
            return

        yield event.plain_result("正在生成 B站登录二维码...")
        self._log_info("开始 B站扫码登录流程")
        qr_data = await self.bili_login.generate_qrcode()
        if not qr_data:
            self._log_warning("生成 B站登录二维码失败")
            yield event.plain_result("生成 B站登录二维码失败，请稍后重试。")
            return

        qr_url = qr_data.get("url", "")
        qrcode_key = qr_data.get("qrcode_key", "")
        if not qr_url or not qrcode_key:
            self._log_warning("B站登录二维码响应缺少 url 或 qrcode_key")
            yield event.plain_result("获取 B站登录二维码数据失败。")
            return

        qr_path = self.data_dir / f"bili_login_qr_{uuid.uuid4().hex[:8]}.png"
        try:
            import segno

            segno.make(qr_url).save(str(qr_path), scale=10, border=4)
        except ImportError:
            self._log_warning("缺少 segno，无法生成 B站登录二维码图片")
            yield event.plain_result("缺少 segno 依赖，请安装 requirements.txt 后重载插件。")
            return
        except Exception as exc:
            self._log_warning(f"生成 B站登录二维码图片失败: {exc}")
            yield event.plain_result(f"生成二维码图片失败: {exc}")
            return

        yield event.chain_result([
            Plain("请使用 B站 App 扫描二维码登录，二维码约 3 分钟内有效。\n"),
            Image.fromFileSystem(str(qr_path)),
        ])

        result = await self.bili_login.do_login_flow(qrcode_key, timeout=180)
        try:
            qr_path.unlink(missing_ok=True)
        except Exception:
            pass

        if result["status"] == "success":
            self.cookies = result["cookies"]
            self._cookie_source = "扫码登录"
            self._rebuild_note_service()
            self._log_info("B站扫码登录成功")
            yield event.plain_result("B站登录成功，后续请求会使用扫码获取的 Cookie。")
        elif result["status"] == "expired":
            self._log_warning("B站登录二维码已过期")
            yield event.plain_result("二维码已过期，请重新发送 /B站登录。")
        elif result["status"] == "timeout":
            self._log_warning("B站扫码登录超时")
            yield event.plain_result("登录超时，请重新发送 /B站登录。")
        else:
            self._log_warning(f"B站扫码登录失败: {result['status']}")
            yield event.plain_result("B站登录失败，请重新发送 /B站登录。")

    @filter.command("B站登出", alias={"哔哩登出", "bili_logout"})
    async def bilibili_logout_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        self.bili_login.logout()
        self.config["bilibili_cookie"] = ""
        self._save_config()
        self.cookies = {}
        self._cookie_source = "未登录"
        self._rebuild_note_service()
        self._log_info("B站登录状态已清除")
        yield event.plain_result("已清除 B站登录状态。")

    @filter.command("B站状态", alias={"B站登录状态", "bili_status"})
    async def bilibili_status_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(f"当前 B站登录状态: {self._login_status_text()}")

    @filter.command("B站识别开关", alias={"bili识别开关"})
    async def toggle_detect_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        self._auto_detect_enabled = not self._auto_detect_enabled
        self.config["enable_auto_detect"] = self._auto_detect_enabled
        self._save_config()
        yield event.plain_result(f"B站链接自动识别: {'已开启' if self._auto_detect_enabled else '已关闭'}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        if not self._auto_detect_enabled:
            return
        raw_text = self._event_text(event)
        if not raw_text or raw_text.strip().startswith("/"):
            return
        if not self._check_feature_access(event):
            return

        video_url = extract_video_url(raw_text)
        if not video_url:
            return

        bvid = await self._url_to_bvid(video_url)
        if not bvid:
            return

        self._debug(f"自动识别到 B站视频: {bvid}")
        info = await get_video_info(bvid, self.cookies)
        if not info:
            self._log_warning(f"自动识别获取视频信息失败: {bvid}")
            return

        chain = self._video_info_chain(info)
        yield event.chain_result(chain)
        self._debug(f"已发送自动识别视频信息: {bvid}")

        if bool(self.config.get("detect_auto_summary", False)):
            yield event.plain_result("正在生成视频总结，请稍候...")
            note = await self._generate_note(f"https://www.bilibili.com/video/{bvid}", event)
            result = await self._note_result(note)
            if isinstance(result, list):
                yield event.chain_result(result)
            else:
                yield event.plain_result(result)

    @filter.command("B站视频总结", alias={"B站总结", "bili总结", "视频总结"})
    async def summarize_cmd(self, event: AstrMessageEvent):
        if not self._check_access(event):
            yield event.plain_result("你没有权限使用此插件。")
            return

        text = self._event_text(event)
        video_url = extract_video_url(text)
        if not video_url or not is_bilibili_video_url(video_url):
            yield event.plain_result("请提供 B站视频链接、BV号或 b23.tv 短链。")
            return

        self._log_info(f"收到 B站总结请求: {video_url}")
        yield event.plain_result("正在生成视频总结，请稍候...")
        note = await self._generate_note(video_url, event)
        result = await self._note_result(note)
        if isinstance(result, list):
            yield event.chain_result(result)
        else:
            yield event.plain_result(result)

    @filter.command("B站最新", alias={"bili最新"})
    async def latest_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        args = self._args(event.message_str)
        if not args:
            yield event.plain_result("请提供 UP主 UID、空间链接或昵称。")
            return

        up = await self._resolve_up(args)
        if not up:
            yield event.plain_result("无法识别 UP 主。")
            return

        videos = await get_latest_videos(up["mid"], count=1, cookies=self.cookies)
        if not videos:
            yield event.plain_result("没有获取到该 UP 主的最新视频。")
            return

        video = videos[0]
        self._log_info(f"开始总结 UP 最新视频: mid={up['mid']}, bvid={video['bvid']}")
        yield event.plain_result(f"找到最新视频：{video['title']}\n正在生成总结...")
        note = await self._generate_note(f"https://www.bilibili.com/video/{video['bvid']}", event)
        result = await self._note_result(note)
        if isinstance(result, list):
            yield event.chain_result(result)
        else:
            yield event.plain_result(result)

    @filter.command("B站订阅", alias={"bili订阅"})
    async def subscribe_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        args = self._args(event.message_str)
        if not args:
            yield event.plain_result("请提供 UP主 UID、空间链接或昵称。")
            return

        origin = event.unified_msg_origin
        current = self.subscriptions.list_for(origin)
        max_subs = int(self.config.get("max_subscriptions", 20))
        if len(current) >= max_subs:
            yield event.plain_result(f"当前会话已达到最大订阅数 {max_subs}。")
            return

        up = await self._resolve_up(args)
        if not up:
            yield event.plain_result("无法识别 UP 主。")
            return

        latest = await get_latest_videos(up["mid"], count=1, cookies=self.cookies)
        last_bvid = latest[0]["bvid"] if latest else ""
        ok = self.subscriptions.add(origin, up["mid"], up["name"], last_bvid)
        if ok:
            self._log_info(f"新增订阅: origin={origin}, mid={up['mid']}, name={up['name']}")
            yield event.plain_result(f"已订阅 UP主【{up['name']}】(UID:{up['mid']})。")
        else:
            yield event.plain_result(f"已经订阅过 UP主【{up['name']}】。")

    @filter.command("B站取消订阅", alias={"bili取消订阅"})
    async def unsubscribe_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        args = self._args(event.message_str)
        if not args:
            yield event.plain_result("请提供 UP主 UID、空间链接或昵称。")
            return
        up = await self._resolve_up(args)
        mid = up["mid"] if up else extract_mid(args)
        if not mid:
            yield event.plain_result("无法识别 UP 主。")
            return
        ok = self.subscriptions.remove(event.unified_msg_origin, mid)
        if ok:
            self._log_info(f"取消订阅: origin={event.unified_msg_origin}, mid={mid}")
        yield event.plain_result("已取消订阅。" if ok else "没有找到该订阅。")

    @filter.command("B站订阅列表", alias={"bili订阅列表"})
    async def list_subscriptions_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        subs = self.subscriptions.list_for(event.unified_msg_origin)
        if not subs:
            yield event.plain_result("当前会话还没有订阅 UP 主。")
            return
        lines = ["当前订阅列表:"]
        for index, item in enumerate(subs, 1):
            lines.append(f"{index}. {item['name']} (UID:{item['mid']})")
        yield event.plain_result("\n".join(lines))

    @filter.command("B站检查更新", alias={"bili检查更新"})
    async def manual_check_cmd(self, event: AstrMessageEvent):
        denied = self._feature_denied_message(event)
        if denied:
            yield event.plain_result(denied)
            return
        subs = self.subscriptions.list_for(event.unified_msg_origin)
        if not subs:
            yield event.plain_result("当前会话还没有订阅 UP 主。")
            return

        yield event.plain_result(f"正在检查 {len(subs)} 个 UP 主...")
        self._log_info(f"手动检查订阅更新: origin={event.unified_msg_origin}, count={len(subs)}")
        found = 0
        failed = 0
        for up in subs:
            updated = await self._check_one_up(
                event.unified_msg_origin,
                up,
                event,
                include_failure_message=True,
            )
            if updated:
                if updated.get("success"):
                    found += 1
                else:
                    failed += 1
                message = updated["message"]
                if isinstance(message, list):
                    yield event.chain_result(message)
                else:
                    yield event.plain_result(message)
                if updated.get("success"):
                    self.subscriptions.update_last(
                        event.unified_msg_origin,
                        up["mid"],
                        updated["bvid"],
                    )
        if found or failed:
            yield event.plain_result(f"检查完成，成功推送 {found} 个新视频，{failed} 个总结失败未更新进度。")
        else:
            yield event.plain_result("检查完成，暂无新视频。")

    async def _generate_note(
        self,
        video_url: str,
        event: Optional[AstrMessageEvent] = None,
        origin: str = "",
    ) -> NoteResult:
        return await self.note_service.generate_note(
            video_url,
            lambda prompt: self._ask_llm(prompt, event=event, origin=origin),
        )

    async def _note_result(self, note: NoteResult):
        if not note.ok:
            return note.content
        if not bool(self.config.get("output_image", True)):
            return note.content
        try:
            image_path = await self.note_service.render_note(note.content)
            return [Image.fromFileSystem(image_path)]
        except Exception as exc:
            logger.warning(f"[B站视频总结] 图片渲染失败，回退文本: {exc}")
            return note.content

    async def _ask_llm(
        self,
        prompt: str,
        event: Optional[AstrMessageEvent] = None,
        origin: str = "",
    ) -> str:
        if str(self.config.get("llm_provider", "astrbot")) == "openai_compatible":
            return await self._ask_openai_compatible(prompt)

        umo = origin or (event.unified_msg_origin if event else "")
        try:
            if hasattr(self.context, "llm_generate") and umo:
                provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
                return getattr(resp, "completion_text", None) or str(resp)
        except Exception as exc:
            logger.warning(f"[B站视频总结] 新版 LLM 调用失败，尝试旧接口: {exc}")

        provider = self.context.get_using_provider()
        if not provider:
            raise RuntimeError("未配置 LLM Provider，请先在 AstrBot 中配置聊天模型。")
        resp = await provider.text_chat(prompt=prompt, session_id="bilibili_video_summary_plugin")
        return getattr(resp, "completion_text", None) or str(resp)

    async def _ask_openai_compatible(self, prompt: str) -> str:
        api_base = str(self.config.get("llm_api_base", "")).rstrip("/")
        api_key = str(self.config.get("llm_api_key", ""))
        model = str(self.config.get("llm_model", "gpt-4o-mini"))
        if not api_base or not api_key:
            raise RuntimeError("请先配置 llm_api_base 和 llm_api_key。")

        url = f"{api_base}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"LLM API 返回错误 HTTP {resp.status}: {body[:300]}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    async def _resolve_up(self, text: str) -> Optional[dict[str, str]]:
        mid = extract_mid(text)
        if not mid:
            return await search_up_by_name(text, self.cookies)
        info = await get_up_info(mid, self.cookies)
        return info or {"mid": mid, "name": f"UP主_{mid}"}

    async def _check_one_up(
        self,
        origin: str,
        up: dict,
        event: Optional[AstrMessageEvent] = None,
        include_failure_message: bool = False,
    ):
        latest = await get_latest_videos(up["mid"], count=1, cookies=self.cookies)
        if not latest:
            return None
        video = latest[0]
        latest_bvid = video["bvid"]
        if latest_bvid == up.get("last_bvid"):
            return None
        if not up.get("last_bvid"):
            self.subscriptions.update_last(origin, up["mid"], latest_bvid)
            return None

        note = await self._generate_note(
            f"https://www.bilibili.com/video/{latest_bvid}",
            event=event,
            origin=origin,
        )
        if not note.ok:
            self._log_warning(
                f"订阅新视频总结失败，保留 last_bvid 以便重试: origin={origin}, mid={up['mid']}, bvid={latest_bvid}"
            )
            if include_failure_message:
                return {
                    "success": False,
                    "bvid": latest_bvid,
                    "message": (
                        f"UP主【{up['name']}】发布了新视频：{video['title']}\n"
                        f"但总结生成失败，订阅进度未更新，后续可重试。\n{note.content}"
                    ),
                }
            return None
        header = f"UP主【{up['name']}】发布了新视频：{video['title']}\n"
        result = await self._note_result(note)
        if isinstance(result, list):
            message = [Plain(header)] + result
        else:
            message = header + "\n" + result
        return {"success": True, "bvid": latest_bvid, "message": message}

    async def _scheduled_loop(self):
        await asyncio.sleep(10)
        while self._running:
            try:
                for origin, subs in self.subscriptions.all().items():
                    for up in subs:
                        result = await self._check_one_up(origin, up, event=None)
                        if result and result.get("success"):
                            message = result["message"]
                            chain = message if isinstance(message, list) else [Plain(message)]
                            await self.context.send_message(origin, MessageChain(chain=chain))
                            self.subscriptions.update_last(origin, up["mid"], result["bvid"])
                            self._log_info(f"已推送订阅新视频: origin={origin}, mid={up['mid']}, bvid={result['bvid']}")
                        await asyncio.sleep(2)
            except Exception as exc:
                logger.error(f"[B站视频总结] 定时检查失败: {exc}", exc_info=True)
            await asyncio.sleep(int(self.config.get("check_interval_minutes", 600)) * 60)

    def _video_info_chain(self, info: dict) -> list:
        def fmt_num(value: int) -> str:
            return f"{value / 10000:.1f}万" if value >= 10000 else str(value)

        lines = [f"{info.get('title', '')}"]
        if bool(self.config.get("detect_show_uploader", True)):
            lines.append(f"UP主: {info.get('owner_name', '未知')}")
        if bool(self.config.get("detect_show_desc", True)) and info.get("desc"):
            desc = str(info["desc"])
            lines.append(f"简介: {desc[:120]}{'...' if len(desc) > 120 else ''}")
        if bool(self.config.get("detect_show_pubtime", True)) and info.get("pubdate"):
            lines.append(f"发布: {time.strftime('%Y-%m-%d %H:%M', time.localtime(info['pubdate']))}")
        if bool(self.config.get("detect_show_stats", True)):
            lines.append(
                f"{fmt_num(int(info.get('view', 0)))}播放  "
                f"{fmt_num(int(info.get('danmaku', 0)))}弹幕  "
                f"{fmt_num(int(info.get('like', 0)))}点赞"
            )
        lines.append(f"https://www.bilibili.com/video/{info.get('bvid')}")

        chain = []
        pic = str(info.get("pic") or "")
        if bool(self.config.get("detect_show_cover", True)) and pic:
            chain.append(Image.fromURL("https:" + pic if pic.startswith("//") else pic))
        chain.append(Plain("\n".join(lines)))
        return chain

    async def _url_to_bvid(self, video_url: str) -> str:
        resolved = await resolve_short_url(video_url)
        return extract_bvid(resolved or video_url) or ""

    def _check_access(self, event: AstrMessageEvent) -> bool:
        mode = str(self.config.get("access_mode", "blacklist"))
        group_list = {
            item.strip()
            for item in str(self.config.get("group_list", "")).split(",")
            if item.strip()
        }
        if mode == "all" or not group_list:
            return True
        candidate = self._event_group_or_session_id(event)
        hit = bool(candidate and candidate in group_list)
        return hit if mode == "whitelist" else not hit

    def _check_feature_access(self, event: AstrMessageEvent, admin_only: bool = False) -> bool:
        if not self._check_access(event):
            return False
        if self._is_astrbot_admin(event):
            return True
        if admin_only:
            return False
        return bool(self.config.get("allow_non_admin_commands", False))

    def _feature_denied_message(self, event: AstrMessageEvent, admin_only: bool = False) -> str:
        if not self._check_access(event):
            return "你没有权限使用此插件。"
        if self._check_feature_access(event, admin_only=admin_only):
            return ""
        if admin_only:
            return "仅 AstrBot 管理员可使用 B站登录。"
        return "仅 AstrBot 管理员可使用此功能。如需开放，请在配置中启用 allow_non_admin_commands。"

    @staticmethod
    def _is_astrbot_admin(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return str(getattr(event, "role", "")).lower() == "admin"

    @staticmethod
    def _event_group_or_session_id(event: AstrMessageEvent) -> str:
        for getter in ("get_group_id", "get_session_id"):
            try:
                value = getattr(event, getter)()
                if value:
                    return str(value)
            except Exception:
                pass
        try:
            group_id = getattr(getattr(event, "message_obj", None), "group_id", "")
            if group_id:
                return str(group_id)
        except Exception:
            pass
        return str(getattr(event, "session_id", "") or "")

    def _load_effective_cookies(self) -> dict[str, str]:
        config_cookies = parse_cookie_string(str(self.config.get("bilibili_cookie", "")))
        if config_cookies.get("SESSDATA"):
            self._cookie_source = "配置 bilibili_cookie"
            return config_cookies

        stored_cookies = self.bili_login.get_cookies()
        if stored_cookies.get("SESSDATA"):
            self._cookie_source = "扫码登录"
            return stored_cookies

        self._cookie_source = "未登录"
        return {}

    def _rebuild_note_service(self) -> None:
        self.note_service = NoteService(str(self.data_dir), self.config, self.cookies)

    def _login_status_text(self) -> str:
        if self.cookies.get("SESSDATA"):
            return f"已登录（{self._cookie_source}）"
        return "未登录"

    def _log_info(self, message: str) -> None:
        logger.info(f"[B站视频总结] {message}")

    def _log_warning(self, message: str) -> None:
        logger.warning(f"[B站视频总结] {message}")

    def _debug(self, message: str) -> None:
        if self.debug_mode:
            logger.info(f"[B站视频总结/DBG] {message}")

    @staticmethod
    def _args(message: str) -> str:
        parts = str(message or "").strip().split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _event_text(event: AstrMessageEvent) -> str:
        text_parts = [event.message_str or ""]
        try:
            message_obj = getattr(event, "message_obj", None)
            if message_obj:
                BilibiliVideoSummaryPlugin._collect_text_like(getattr(message_obj, "raw_message", None), text_parts)
                BilibiliVideoSummaryPlugin._collect_text_like(getattr(message_obj, "message", None), text_parts)

            for comp in event.get_messages() or []:
                if hasattr(comp, "text"):
                    text_parts.append(str(comp.text))
                else:
                    value = str(comp)
                    if "bilibili" in value.lower() or "b23.tv" in value.lower() or "BV" in value:
                        text_parts.append(value)
                BilibiliVideoSummaryPlugin._collect_text_like(getattr(comp, "raw", None), text_parts)
                BilibiliVideoSummaryPlugin._collect_text_like(getattr(comp, "data", None), text_parts)
        except Exception:
            pass
        return " ".join(text_parts)

    @staticmethod
    def _collect_text_like(value, output: list[str], depth: int = 0) -> None:
        if value is None or depth > 6:
            return
        if isinstance(value, str):
            if value:
                output.append(value)
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    BilibiliVideoSummaryPlugin._collect_text_like(json.loads(stripped), output, depth + 1)
                except Exception:
                    pass
            return
        if isinstance(value, dict):
            preferred_keys = (
                "qqdocurl",
                "url",
                "jumpUrl",
                "jump_url",
                "desc",
                "text",
                "content",
                "data",
                "meta",
                "prompt",
                "preview",
            )
            for key in preferred_keys:
                if key in value:
                    BilibiliVideoSummaryPlugin._collect_text_like(value[key], output, depth + 1)
            for item in value.values():
                BilibiliVideoSummaryPlugin._collect_text_like(item, output, depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                BilibiliVideoSummaryPlugin._collect_text_like(item, output, depth + 1)

    def _save_config(self) -> None:
        try:
            if hasattr(self.config, "save_config"):
                self.config.save_config()
        except Exception:
            pass

    @staticmethod
    def _get_data_dir() -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except Exception:
            try:
                from astrbot.api.star import StarTools

                return Path(str(StarTools.get_data_dir(PLUGIN_NAME)))
            except Exception:
                return Path("data") / PLUGIN_NAME

    async def terminate(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[B站视频总结] 插件已卸载")
