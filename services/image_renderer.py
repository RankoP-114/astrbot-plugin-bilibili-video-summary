import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_WINDOWS_FONT_DIRS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts",
]
if os.environ.get("LOCALAPPDATA"):
    _WINDOWS_FONT_DIRS.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")


DEFAULT_FONT_CANDIDATES = [
    "/AstrBot/data/plugin_data/astrbot_plugin_bililens/fonts/HiraginoSansGB.ttc",
    "/AstrBot/data/plugin_data/astrbot_plugin_bililens/fonts/NotoSansCJK-Regular.ttc",
    "/AstrBot/data/plugin_data/astrbot_plugin_bililens/fonts/NotoSansCJKsc-Regular.otf",
    "/AstrBot/data/plugin_data/astrbot_plugin_bililens/fonts/SourceHanSansCN-Regular.otf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/opentype/adobe-source-han-sans/SourceHanSansCN-Regular.otf",
    "/usr/share/fonts/source-han-sans/SourceHanSansCN-Regular.otf",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansCN-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/TTF/wqy-microhei.ttc",
    "/usr/share/fonts/TTF/wqy-zenhei.ttc",
    "/usr/share/fonts/TTF/NotoSansCJK-Regular.ttc",
] + [
    str(font_dir / font_name)
    for font_dir in _WINDOWS_FONT_DIRS
    for font_name in (
        "msyh.ttc",
        "msyh.ttf",
        "simsun.ttc",
        "simhei.ttf",
        "Deng.ttf",
        "msjh.ttc",
        "mingliu.ttc",
        "simkai.ttf",
    )
]
CLOSING_PUNCTUATION = set("，。！？；：、,.!?;:)]）】》")


def render_markdown_card(
    markdown: str,
    output_path: str,
    width: int = 1800,
    font_path: str = "",
    scale: int = 2,
    columns: int = 2,
) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    scale = max(1, min(int(scale or 1), 3))
    canvas_width = width * scale
    columns = max(1, min(int(columns or 1), 3))
    fonts = _load_fonts(font_path, scale)
    ops, height = _layout(markdown, canvas_width, fonts, scale, columns)

    image = Image.new("RGB", (canvas_width, height), "#101418")
    draw = ImageDraw.Draw(image)
    _draw_background(draw, canvas_width, height)
    for op in ops:
        kind = op["kind"]
        if kind == "text":
            draw.text(op["xy"], op["text"], font=op["font"], fill=op["fill"])
        elif kind == "rect":
            draw.rounded_rectangle(op["box"], radius=op["radius"], fill=op["fill"], outline=op.get("outline"))
        elif kind == "line":
            draw.line(op["points"], fill=op["fill"], width=op["width"])

    image.save(output, format="PNG", optimize=True)
    return str(output)


def _load_fonts(font_path: str, scale: int = 1) -> dict[str, ImageFont.FreeTypeFont]:
    candidates = [font_path] if font_path else []
    candidates.extend(DEFAULT_FONT_CANDIDATES)
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            return {
                "title": ImageFont.truetype(candidate, 46 * scale),
                "h2": ImageFont.truetype(candidate, 34 * scale),
                "body": ImageFont.truetype(candidate, 28 * scale),
                "muted": ImageFont.truetype(candidate, 24 * scale),
            }
        except OSError:
            continue
    return {
        "title": ImageFont.load_default(),
        "h2": ImageFont.load_default(),
        "body": ImageFont.load_default(),
        "muted": ImageFont.load_default(),
    }


def _layout(markdown: str, width: int, fonts: dict[str, ImageFont.FreeTypeFont], scale: int, columns: int):
    margin = _px(54, scale)
    content_width = width - margin * 2
    y = _px(46, scale)
    ops: list[dict] = []
    dummy = Image.new("RGB", (width, _px(100, scale)))
    draw = ImageDraw.Draw(dummy)

    title = "B站视频总结"
    lines = [line.rstrip() for line in (markdown or "").splitlines()]
    if lines and lines[0].startswith("# "):
        title = _clean_inline(lines.pop(0)[2:].strip()) or title
    sections = _parse_sections(lines)

    title_lines = _wrap(draw, title, fonts["title"], content_width - _px(64, scale))
    ops.append(
        {
            "kind": "rect",
            "box": (margin, y, width - margin, y + _px(132, scale) + len(title_lines) * _px(52, scale)),
            "radius": _px(26, scale),
            "fill": "#18202a",
        }
    )
    y += _px(34, scale)
    for line in title_lines:
        ops.append({"kind": "text", "xy": (margin + _px(34, scale), y), "text": line, "font": fonts["title"], "fill": "#f4f7fb"})
        y += _px(56, scale)
    ops.append(
        {
            "kind": "line",
            "points": (margin + _px(34, scale), y + _px(6, scale), margin + _px(250, scale), y + _px(6, scale)),
            "fill": "#34d399",
            "width": _px(5, scale),
        }
    )
    y += _px(62, scale)

    gap = _px(24, scale)
    min_column_width = _px(520, scale)
    while columns > 1 and (content_width - gap * (columns - 1)) // columns < min_column_width:
        columns -= 1
    card_w = (content_width - gap * (columns - 1)) // columns

    for row_start in range(0, len(sections), columns):
        row_sections = sections[row_start : row_start + columns]
        row_layouts = []
        row_height = 0
        for index, section in enumerate(row_sections):
            x = margin + index * (card_w + gap)
            section_ops, section_height = _layout_section(section, x, y, card_w, draw, fonts, scale)
            row_layouts.append((x, section_ops, section_height))
            row_height = max(row_height, section_height)

        for x, section_ops, section_height in row_layouts:
            ops.append(
                {
                    "kind": "rect",
                    "box": (x, y, x + card_w, y + row_height),
                    "radius": _px(18, scale),
                    "fill": "#151b22",
                    "outline": "#263241",
                }
            )
            ops.extend(section_ops)
        y += row_height + gap

    y += _px(44, scale)
    ops.append({"kind": "text", "xy": (margin, y), "text": "Generated by B站视频总结", "font": fonts["muted"], "fill": "#66768a"})
    y += _px(64, scale)
    return ops, max(y, _px(360, scale))


def _parse_sections(lines: list[str]) -> list[dict]:
    sections: list[dict] = []
    current: dict = {"title": "", "lines": []}

    def flush() -> None:
        if current["title"] or any(line.strip() for line in current["lines"]):
            sections.append({"title": current["title"], "lines": list(current["lines"])})

    for raw in lines:
        if raw.startswith("## "):
            flush()
            current = {"title": _clean_inline(raw[3:].strip()), "lines": []}
            continue
        current["lines"].append(raw)

    flush()
    if not sections:
        sections.append({"title": "", "lines": ["暂无内容"]})
    return sections


def _layout_section(
    section: dict,
    x: int,
    y: int,
    width: int,
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.FreeTypeFont],
    scale: int,
) -> tuple[list[dict], int]:
    ops: list[dict] = []
    padding_x = _px(30, scale)
    padding_y = _px(26, scale)
    text_width = width - padding_x * 2
    cursor_y = y + padding_y

    title = str(section.get("title", ""))
    if title:
        for line in _wrap(draw, title, fonts["h2"], text_width):
            ops.append({"kind": "text", "xy": (x + padding_x, cursor_y), "text": line, "font": fonts["h2"], "fill": "#7dd3fc"})
            cursor_y += _px(42, scale)
        cursor_y += _px(14, scale)

    for raw in section.get("lines", []):
        raw = str(raw)
        if not raw.strip():
            cursor_y += _px(12, scale)
            continue

        text, font, fill = _format_line(raw, fonts)
        for line in _wrap(draw, text, font, text_width):
            ops.append({"kind": "text", "xy": (x + padding_x, cursor_y), "text": line, "font": font, "fill": fill})
            cursor_y += _px(38, scale) if font == fonts["muted"] else _px(42, scale)
        cursor_y += _px(8, scale)

    height = max(cursor_y - y + padding_y - _px(8, scale), _px(118, scale))
    return ops, height


def _format_line(raw: str, fonts: dict[str, ImageFont.FreeTypeFont]) -> tuple[str, ImageFont.FreeTypeFont, str]:
    fill = "#dbe4ee"
    font = fonts["body"]
    text = raw.strip()
    prefix = ""
    if text.startswith(">"):
        prefix = "│ "
        text = text.lstrip("> ").strip()
        fill = "#a7b7c8"
        font = fonts["muted"]
    elif text.startswith(("- ", "* ")):
        prefix = "• "
        text = text[2:].strip()
    elif re.match(r"^\d+\.\s+", text):
        number, text = text.split(".", 1)
        prefix = f"{number}. "
        text = text.strip()
    return prefix + _clean_inline(text), font, fill


def _px(value: int, scale: int) -> int:
    return int(value * scale)


def _draw_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    for i in range(height):
        ratio = i / max(height, 1)
        r = int(16 + 9 * ratio)
        g = int(20 + 15 * ratio)
        b = int(24 + 21 * ratio)
        draw.line((0, i, width, i), fill=(r, g, b))


def _clean_inline(text: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", text)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("⏱", "时间")
    value = value.replace("<br>", " ")
    return value.strip()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if _text_width(draw, candidate, font) <= max_width or not current or char in CLOSING_PUNCTUATION:
            current = candidate
            continue
        lines.append(current.rstrip())
        current = char.lstrip()
    if current:
        lines.append(current.rstrip())
    return lines


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]
