import asyncio
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from astrbot.api import logger

from .asr import BcutASR, OpenAICompatibleASR
from .bilibili import resolve_short_url
from .downloader import BilibiliDownloader
from .image_renderer import render_markdown_card
from .models import AudioMeta, NoteResult, TranscriptResult
from .prompt import build_prompt


class NoteService:
    def __init__(self, data_dir: str, config: dict, cookies: dict[str, str]):
        self.data_dir = Path(data_dir)
        self.audio_dir = self.data_dir / "audio"
        self.image_dir = self.data_dir / "images"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.cookies = cookies
        self.downloader = BilibiliDownloader(str(self.audio_dir), cookies)

    async def generate_note(
        self,
        video_url: str,
        ask_llm: Callable[[str], Awaitable[str]],
    ) -> NoteResult:
        audio_meta: Optional[AudioMeta] = None
        video_url = await resolve_short_url(video_url)
        try:
            logger.info(f"[B站视频总结] 开始获取字幕: {video_url}")
            transcript = await asyncio.to_thread(self.downloader.download_subtitles, video_url)
            if transcript and transcript.segments:
                logger.info(f"[B站视频总结] 已获取 B站字幕: segments={len(transcript.segments)}")
                audio_meta = self._audio_meta_from_transcript(video_url, transcript)
            else:
                logger.info("[B站视频总结] 未获取到字幕，开始下载音频并调用 ASR")
                quality = str(self.config.get("download_quality", "fast"))
                audio_meta = await asyncio.to_thread(self.downloader.download_audio, video_url, quality)
                transcript = await self._transcribe(audio_meta.file_path)

            if not transcript or not transcript.segments:
                return NoteResult(False, "无法获取视频内容：字幕和 ASR 均为空。")

            prompt = build_prompt(
                title=audio_meta.title,
                segments=transcript.segments,
                tags=audio_meta.tags,
                style=str(self.config.get("note_style", "professional")),
                enable_timestamps=bool(self.config.get("enable_timestamps", True)),
                enable_summary=bool(self.config.get("enable_ai_summary", True)),
            )
            markdown = await ask_llm(prompt)
            markdown = (markdown or "").strip()
            if not markdown:
                return NoteResult(False, "LLM 未返回总结内容。")

            max_len = int(self.config.get("max_note_length", 6000))
            if len(markdown) > max_len:
                markdown = markdown[:max_len].rstrip() + "\n\n...(内容过长，已截断)"
            return NoteResult(True, markdown)
        except Exception as exc:
            logger.error(f"[B站视频总结] 生成总结失败: {exc}", exc_info=True)
            return NoteResult(False, f"生成总结失败: {exc}")
        finally:
            if audio_meta:
                self._cleanup(audio_meta.file_path)
            self._cleanup_generated_files()

    async def render_note(self, markdown: str) -> str:
        output_path = self.image_dir / f"bililens_{int(asyncio.get_running_loop().time() * 1000)}.png"
        rendered = await asyncio.to_thread(
            render_markdown_card,
            markdown,
            str(output_path),
            int(self.config.get("image_width", 1400)),
            str(self.config.get("font_path", "")),
        )
        self._cleanup_generated_files()
        return rendered

    def _audio_meta_from_transcript(self, video_url: str, transcript: TranscriptResult) -> AudioMeta:
        raw = transcript.raw or {}
        tags = raw.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return AudioMeta(
            file_path="",
            title=str(raw.get("title") or raw.get("video_id") or video_url),
            duration=float(raw.get("duration") or 0),
            cover_url=str(raw.get("thumbnail") or ""),
            video_id=str(raw.get("video_id") or ""),
            tags=tags,
            raw=raw,
        )

    def _cleanup_generated_files(self) -> None:
        retention_hours = self._config_int("generated_retention_hours", 72)
        max_files = self._config_int("generated_max_files", 200)
        if retention_hours <= 0 and max_files <= 0:
            return
        max_age_seconds = retention_hours * 3600 if retention_hours > 0 else 0
        self._cleanup_dir(
            self.audio_dir,
            ("*.srt", "*.json3", "*.vtt", "*.m4a", "*.webm", "*.mp3"),
            max_age_seconds,
            max_files,
            exclude_names={"cookies.txt"},
        )
        self._cleanup_dir(self.image_dir, ("*.png",), max_age_seconds, max_files)

    @staticmethod
    def _cleanup_dir(
        directory: Path,
        patterns: tuple[str, ...],
        max_age_seconds: int,
        max_files: int,
        exclude_names: Optional[set[str]] = None,
    ) -> None:
        exclude_names = exclude_names or set()
        files: list[Path] = []
        for pattern in patterns:
            files.extend(path for path in directory.glob(pattern) if path.name not in exclude_names)
        if not files:
            return
        now = time.time()
        for path in files:
            try:
                if max_age_seconds > 0 and now - path.stat().st_mtime > max_age_seconds:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                    logger.warning(f"[B站视频总结] 清理过期文件失败 {path}: {exc}")

        remaining = [path for path in files if path.exists()]
        if max_files > 0 and len(remaining) > max_files:
            remaining.sort(key=lambda item: item.stat().st_mtime)
            for path in remaining[: len(remaining) - max_files]:
                try:
                    path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning(f"[B站视频总结] 清理超量文件失败 {path}: {exc}")

    def _config_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except Exception:
            return default

    async def _transcribe(self, audio_path: str) -> TranscriptResult:
        provider = str(self.config.get("asr_provider", "bcut"))
        timeout = int(self.config.get("asr_timeout_seconds", 600))
        logger.info(f"[B站视频总结] 开始 ASR 转写: provider={provider}")
        if provider == "openai_compatible":
            client = OpenAICompatibleASR(
                api_base=str(self.config.get("asr_api_base", "")),
                api_key=str(self.config.get("asr_api_key", "")),
                model=str(self.config.get("asr_model", "whisper-1")),
                endpoint=str(self.config.get("asr_endpoint", "/audio/transcriptions")),
                language=str(self.config.get("asr_language", "")),
                timeout_seconds=timeout,
            )
            return await client.transcribe(audio_path)
        return await BcutASR(timeout_seconds=timeout).transcribe(audio_path)

    @staticmethod
    def _cleanup(file_path: str) -> None:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception as exc:
            logger.warning(f"[B站视频总结] 清理临时音频失败: {exc}")
