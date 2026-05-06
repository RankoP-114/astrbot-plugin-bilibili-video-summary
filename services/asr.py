import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import aiohttp

from astrbot.api import logger

from .models import TranscriptResult, TranscriptSegment


BCUT_BASE = "https://member.bilibili.com/x/bcut/rubick-interface"


class BcutASR:
    def __init__(self, timeout_seconds: int = 600, poll_interval: float = 1.5):
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval

    async def transcribe(self, file_path: str) -> TranscriptResult:
        audio = Path(file_path).read_bytes()
        if not audio:
            raise ValueError("音频文件为空")

        headers = {
            "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            resource = await self._create_resource(session, headers, audio)
            etags = await self._upload_parts(session, audio, resource)
            download_url = await self._complete_upload(session, headers, resource, etags)
            task_id = await self._create_task(session, headers, download_url)
            result = await self._wait_result(session, headers, task_id)
            return self._parse_result(result)

    async def _create_resource(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        audio: bytes,
    ) -> dict[str, Any]:
        payload = {
            "type": 2,
            "name": "audio.mp3",
            "size": len(audio),
            "ResourceFileType": "mp3",
            "model_id": "8",
        }
        async with session.post(f"{BCUT_BASE}/resource/create", json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"必剪申请上传失败: {data.get('message')}")
            return data["data"]

    async def _upload_parts(
        self,
        session: aiohttp.ClientSession,
        audio: bytes,
        resource: dict[str, Any],
    ) -> list[str]:
        per_size = int(resource["per_size"])
        urls = list(resource["upload_urls"])
        etags: list[str] = []
        for index, upload_url in enumerate(urls):
            start = index * per_size
            end = min((index + 1) * per_size, len(audio))
            async with session.put(
                upload_url,
                data=audio[start:end],
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                resp.raise_for_status()
                etags.append((resp.headers.get("Etag") or "").strip('"'))
        return etags

    async def _complete_upload(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        resource: dict[str, Any],
        etags: list[str],
    ) -> str:
        payload = {
            "InBossKey": resource["in_boss_key"],
            "ResourceId": resource["resource_id"],
            "Etags": ",".join(etags),
            "UploadId": resource["upload_id"],
            "model_id": "8",
        }
        async with session.post(f"{BCUT_BASE}/resource/create/complete", json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"必剪提交上传失败: {data.get('message')}")
            return data["data"]["download_url"]

    async def _create_task(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        download_url: str,
    ) -> str:
        async with session.post(
            f"{BCUT_BASE}/task",
            json={"resource": download_url, "model_id": "8"},
            headers=headers,
        ) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"必剪创建转写任务失败: {data.get('message')}")
            return data["data"]["task_id"]

    async def _wait_result(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        task_id: str,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        last_state = None
        while asyncio.get_running_loop().time() < deadline:
            async with session.get(
                f"{BCUT_BASE}/task/result",
                params={"model_id": 7, "task_id": task_id},
                headers=headers,
            ) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    raise RuntimeError(f"必剪查询转写失败: {data.get('message')}")
                payload = data["data"]
                last_state = payload.get("state")
                if last_state == 4:
                    return payload
                if last_state == 3:
                    raise RuntimeError("必剪转写任务失败")
            await asyncio.sleep(self.poll_interval)
        raise TimeoutError(f"必剪转写超时，最后状态: {last_state}")

    @staticmethod
    def _parse_result(payload: dict[str, Any]) -> TranscriptResult:
        result = json.loads(payload.get("result") or "{}")
        segments: list[TranscriptSegment] = []
        for item in result.get("utterances", []):
            text = (item.get("transcript") or "").strip()
            if not text:
                continue
            start = float(item.get("start_time") or 0) / 1000.0
            end = float(item.get("end_time") or 0) / 1000.0
            segments.append(TranscriptSegment(start=start, end=end, text=text))
        return TranscriptResult(
            language=result.get("language", "zh"),
            full_text=" ".join(seg.text for seg in segments),
            segments=segments,
            raw=result,
        )


class OpenAICompatibleASR:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        endpoint: str = "/audio/transcriptions",
        language: str = "",
        timeout_seconds: int = 600,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.language = language
        self.timeout_seconds = timeout_seconds

    async def transcribe(self, file_path: str) -> TranscriptResult:
        if not self.api_base or not self.api_key:
            raise ValueError("请先配置 ASR API Base 和 API Key")

        url = f"{self.api_base}{self.endpoint}"
        path = Path(file_path)
        file_bytes = path.read_bytes()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            status, body = await self._post_transcription(session, url, headers, path.name, file_bytes, True)
            if status >= 400:
                logger.warning(f"[B站视频总结] ASR 带时间戳参数失败，重试普通 verbose_json: HTTP {status}")
                status, body = await self._post_transcription(session, url, headers, path.name, file_bytes, False)
            if status >= 400:
                raise RuntimeError(f"ASR API HTTP {status}: {body[:300]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return _plain_text_result(body)
        return _parse_openai_asr_json(data)

    async def _post_transcription(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        filename: str,
        file_bytes: bytes,
        include_timestamps: bool,
    ) -> tuple[int, str]:
        form = aiohttp.FormData()
        form.add_field("model", self.model)
        form.add_field("response_format", "verbose_json")
        if self.language:
            form.add_field("language", self.language)
        if include_timestamps:
            form.add_field("timestamp_granularities[]", "segment")
        form.add_field(
            "file",
            file_bytes,
            filename=filename,
            content_type="audio/mpeg",
        )
        async with session.post(url, data=form, headers=headers) as resp:
            return resp.status, await resp.text()


def _parse_openai_asr_json(data: dict[str, Any]) -> TranscriptResult:
    segments: list[TranscriptSegment] = []
    for item in data.get("segments") or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=float(item.get("start") or 0),
                end=float(item.get("end") or 0),
                text=text,
            )
        )
    if segments:
        return TranscriptResult(
            language=data.get("language") or "unknown",
            full_text=" ".join(seg.text for seg in segments),
            segments=segments,
            raw=data,
        )
    return _plain_text_result(str(data.get("text") or ""))


def _plain_text_result(text: str) -> TranscriptResult:
    text = text.strip()
    if not text:
        return TranscriptResult(raw={"source": "openai_compatible"})
    return TranscriptResult(
        language="unknown",
        full_text=text,
        segments=[TranscriptSegment(start=0.0, end=0.0, text=text)],
        raw={"source": "openai_compatible"},
    )
