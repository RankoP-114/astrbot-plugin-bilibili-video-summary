from .models import TranscriptSegment


STYLE_HINTS = {
    "concise": "用简洁模式输出，只保留核心观点、结论和最关键例子。",
    "detailed": "用详细模式输出，保留主要论证、例子、数据和上下文。",
    "professional": "用专业模式输出，结构化分析背景、核心论点、关键证据、风险和结论。",
}


def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_prompt(
    title: str,
    segments: list[TranscriptSegment],
    tags: list[str],
    style: str,
    enable_timestamps: bool,
    enable_summary: bool,
) -> str:
    segment_text = "\n".join(
        f"{format_time(seg.start)} - {seg.text.strip()}"
        for seg in segments
        if seg.text.strip()
    )
    timestamp_rule = (
        "每个主要章节标题或章节第一段后加入时间戳，格式为 `时间 mm:ss`。"
        if enable_timestamps
        else "不要额外加入时间戳。"
    )
    summary_rule = (
        "末尾添加 `## AI 总结`，用 3-5 句话给出整体结论。"
        if enable_summary
        else "末尾不需要单独添加 AI 总结。"
    )
    style_hint = STYLE_HINTS.get(style, STYLE_HINTS["professional"])
    tag_text = ", ".join(tags) if tags else "无"

    return f"""你是一个严谨的视频内容总结助手。请根据 B 站视频的字幕/转写内容生成中文 Markdown 总结。

视频标题：{title}
视频标签：{tag_text}

输出要求：
- 第一行必须是一级标题，格式为 `# {title}`。
- 使用 `##` 组织章节，每个章节聚焦一个主题。
- 保留事实、例子、数字、人物、观点转折和结论，不要编造视频里没有的信息。
- 如果转写有明显口误或语气词，可以自然清理。
- {timestamp_rule}
- {summary_rule}
- {style_hint}
- 只输出 Markdown 正文，不要包裹代码块。

视频分段如下：
---
{segment_text}
---
"""
