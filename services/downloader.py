import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import yt_dlp

from astrbot.api import logger

from .models import AudioMeta, TranscriptResult, TranscriptSegment


QUALITY_MAP = {
    "fast": "32",
    "medium": "64",
    "high": "128",
}


class BilibiliDownloader:
    def __init__(self, data_dir: str, cookies: Optional[dict[str, str]] = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = ""
        if cookies:
            self.cookies_file = str(self.data_dir / "cookies.txt")
            self._write_cookies_file(cookies)

    def _write_cookies_file(self, cookies: dict[str, str]) -> None:
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in cookies.items():
            if value:
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
        Path(self.cookies_file).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def download_audio(self, video_url: str, quality: str = "fast") -> AudioMeta:
        output_path = str(self.data_dir / "%(id)s.%(ext)s")
        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": output_path,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": QUALITY_MAP.get(quality, "32"),
                }
            ],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        if self.cookies_file and os.path.exists(self.cookies_file):
            opts["cookiefile"] = self.cookies_file

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            video_id = info.get("id") or "audio"
            audio_path = str(self.data_dir / f"{video_id}.mp3")
            tags = info.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            return AudioMeta(
                file_path=audio_path,
                title=info.get("title") or video_id,
                duration=float(info.get("duration") or 0),
                cover_url=info.get("thumbnail") or "",
                video_id=video_id,
                tags=tags,
                raw=info,
            )

    def download_subtitles(self, video_url: str) -> Optional[TranscriptResult]:
        langs = ["zh-Hans", "zh", "zh-CN", "ai-zh", "en", "en-US"]
        video_id = self._extract_bvid(video_url) or "subtitle"
        opts = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "srt/json3/best",
            "skip_download": True,
            "outtmpl": str(self.data_dir / f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        if self.cookies_file and os.path.exists(self.cookies_file):
            opts["cookiefile"] = self.cookies_file

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                requested = info.get("requested_subtitles") or {}
                if not requested:
                    return None

                lang = next((item for item in langs if item in requested), "")
                if not lang:
                    lang = next((item for item in requested if item != "danmaku"), "")
                if not lang:
                    return None

                sub_info = requested.get(lang) or {}
                ext = str(sub_info.get("ext", "srt")).lower()
                if sub_info.get("data"):
                    if ext == "json3":
                        return self._with_metadata(self._parse_json3_data(sub_info["data"], lang), info)
                    return self._with_metadata(self._parse_srt(str(sub_info["data"]), lang), info)

                sub_path = self.data_dir / f"{video_id}.{lang}.{ext}"
                if not sub_path.exists():
                    return None
                if ext == "json3":
                    return self._with_metadata(self._parse_json3(sub_path, lang), info)
                return self._with_metadata(self._parse_srt(sub_path.read_text(encoding="utf-8"), lang), info)
        except Exception as exc:
            logger.warning(f"[B站视频总结] 获取字幕失败: {exc}")
            return None

    @staticmethod
    def _extract_bvid(url: str) -> Optional[str]:
        match = re.search(r"(BV[0-9A-Za-z]+)", url or "")
        return match.group(1) if match else None

    @staticmethod
    def _with_metadata(result: Optional[TranscriptResult], info: dict) -> Optional[TranscriptResult]:
        if result is None:
            return None
        tags = info.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        result.raw.update(
            {
                "title": info.get("title") or "",
                "tags": tags,
                "thumbnail": info.get("thumbnail") or "",
                "duration": info.get("duration") or 0,
                "video_id": info.get("id") or "",
            }
        )
        return result

    @staticmethod
    def _parse_srt(content: str, language: str) -> Optional[TranscriptResult]:
        pattern = (
            r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
            r"(\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|$)"
        )
        segments: list[TranscriptSegment] = []
        for _, start, end, text in re.findall(pattern, content, re.DOTALL):
            text = re.sub(r"<[^>]+>", "", text).strip()
            if text:
                segments.append(TranscriptSegment(_srt_time(start), _srt_time(end), text))
        if not segments:
            return None
        return TranscriptResult(
            language=language,
            full_text=" ".join(seg.text for seg in segments),
            segments=segments,
            raw={"source": "bilibili_subtitle", "format": "srt"},
        )

    @staticmethod
    def _parse_json3(path: Path, language: str) -> Optional[TranscriptResult]:
        return BilibiliDownloader._parse_json3_data(path.read_text(encoding="utf-8"), language)

    @staticmethod
    def _parse_json3_data(content: Any, language: str) -> Optional[TranscriptResult]:
        if isinstance(content, (bytes, bytearray)):
            content = content.decode("utf-8", errors="ignore")
        if isinstance(content, str):
            data = json.loads(content)
        elif isinstance(content, dict):
            data = content
        else:
            return None
        segments: list[TranscriptSegment] = []
        for event in data.get("events", []):
            start_ms = float(event.get("tStartMs") or 0)
            duration_ms = float(event.get("dDurationMs") or 0)
            text = "".join(seg.get("utf8", "") for seg in event.get("segs", [])).strip()
            if text:
                segments.append(
                    TranscriptSegment(start_ms / 1000.0, (start_ms + duration_ms) / 1000.0, text)
                )
        for item in data.get("body", []):
            text = str(item.get("content") or "").strip()
            if text:
                start = float(item.get("from") or 0)
                end = float(item.get("to") or start)
                segments.append(TranscriptSegment(start, end, text))
        if not segments:
            return None
        return TranscriptResult(
            language=language,
            full_text=" ".join(seg.text for seg in segments),
            segments=segments,
            raw={"source": "bilibili_subtitle", "format": "json3"},
        )


def _srt_time(value: str) -> float:
    hh, mm, ss = value.replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)
