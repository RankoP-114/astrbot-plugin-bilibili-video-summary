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


def render_markdown_card(
    markdown: str,
    output_path: str,
    width: int = 1400,
    font_path: str = "",
) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fonts = _load_fonts(font_path)
    ops, height = _layout(markdown, width, fonts)

    image = Image.new("RGB", (width, height), "#101418")
    draw = ImageDraw.Draw(image)
    _draw_background(draw, width, height)
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


def _load_fonts(font_path: str) -> dict[str, ImageFont.FreeTypeFont]:
    candidates = [font_path] if font_path else []
    candidates.extend(DEFAULT_FONT_CANDIDATES)
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            return {
                "title": ImageFont.truetype(candidate, 46),
                "h2": ImageFont.truetype(candidate, 34),
                "body": ImageFont.truetype(candidate, 28),
                "muted": ImageFont.truetype(candidate, 24),
            }
        except OSError:
            continue
    return {
        "title": ImageFont.load_default(),
        "h2": ImageFont.load_default(),
        "body": ImageFont.load_default(),
        "muted": ImageFont.load_default(),
    }


def _layout(markdown: str, width: int, fonts: dict[str, ImageFont.FreeTypeFont]):
    margin = 54
    content_width = width - margin * 2
    card_x = margin
    card_w = content_width
    y = 46
    ops: list[dict] = []
    dummy = Image.new("RGB", (width, 100))
    draw = ImageDraw.Draw(dummy)

    title = "B站视频总结"
    lines = [line.rstrip() for line in (markdown or "").splitlines()]
    if lines and lines[0].startswith("# "):
        title = _clean_inline(lines.pop(0)[2:].strip()) or title

    title_lines = _wrap(draw, title, fonts["title"], content_width - 64)
    ops.append({"kind": "rect", "box": (margin, y, width - margin, y + 132 + len(title_lines) * 52), "radius": 26, "fill": "#18202a"})
    y += 34
    for line in title_lines:
        ops.append({"kind": "text", "xy": (margin + 34, y), "text": line, "font": fonts["title"], "fill": "#f4f7fb"})
        y += 56
    ops.append({"kind": "line", "points": (margin + 34, y + 6, margin + 250, y + 6), "fill": "#34d399", "width": 5})
    y += 62

    section_top = None
    section_items: list[dict] = []

    def flush_section() -> None:
        nonlocal section_top, section_items
        if section_top is None:
            return
        bottom = y + 22
        ops.insert(
            len(ops) - len(section_items),
            {"kind": "rect", "box": (card_x, section_top, card_x + card_w, bottom), "radius": 18, "fill": "#151b22", "outline": "#263241"},
        )
        section_top = None
        section_items = []

    for raw in lines:
        if not raw.strip():
            y += 14
            continue

        if raw.startswith("## "):
            flush_section()
            section_top = y
            text = _clean_inline(raw[3:].strip())
            y += 28
            op = {"kind": "text", "xy": (margin + 34, y), "text": text, "font": fonts["h2"], "fill": "#7dd3fc"}
            ops.append(op)
            section_items.append(op)
            y += 54
            continue

        if section_top is None:
            section_top = y
            y += 28

        indent = 34
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

        text = prefix + _clean_inline(text)
        wrapped = _wrap(draw, text, font, content_width - 92)
        for line in wrapped:
            op = {"kind": "text", "xy": (margin + indent, y), "text": line, "font": font, "fill": fill}
            ops.append(op)
            section_items.append(op)
            y += 38 if font == fonts["muted"] else 42
        y += 8

    flush_section()
    y += 44
    ops.append({"kind": "text", "xy": (margin, y), "text": "Generated by B站视频总结", "font": fonts["muted"], "fill": "#66768a"})
    y += 64
    return ops, max(y, 360)


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
        if _text_width(draw, candidate, font) <= max_width or not current:
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
