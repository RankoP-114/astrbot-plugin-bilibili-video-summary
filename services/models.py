from dataclasses import dataclass, field
from typing import Any


@dataclass
class AudioMeta:
    file_path: str
    title: str
    duration: float = 0.0
    cover_url: str = ""
    video_id: str = ""
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    language: str = "zh"
    full_text: str = ""
    segments: list[TranscriptSegment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NoteResult:
    ok: bool
    content: str
