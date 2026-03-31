#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageColor, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _color(hex_color: str | None, opacity: int = 100) -> tuple[int, int, int, int]:
    if not hex_color or hex_color == "transparent":
        return (0, 0, 0, 0)
    r, g, b = ImageColor.getrgb(hex_color)
    a = max(0, min(255, int(255 * (opacity / 100))))
    return (r, g, b, a)


def _draw_centered_label(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], label: dict) -> None:
    text = label.get("text", "")
    if not text:
        return
    font_size = int(label.get("fontSize", 16))
    font = _font(font_size)

    x1, y1, x2, y2 = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=4)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    tx = x1 + ((x2 - x1) - tw) / 2
    ty = y1 + ((y2 - y1) - th) / 2
    draw.multiline_text((tx, ty), text, fill=(30, 30, 30, 255), font=font, align="center", spacing=4)


def _draw_dashed_line(draw: ImageDraw.ImageDraw, p1: tuple[float, float], p2: tuple[float, float], color: tuple[int, int, int, int], width: int = 2, dash: int = 8, gap: int = 5) -> None:
    x1, y1 = p1
    x2, y2 = p2
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return

    dx = (x2 - x1) / length
    dy = (y2 - y1) / length
    dist = 0.0
    while dist < length:
        start = dist
        end = min(dist + dash, length)
        sx = x1 + dx * start
        sy = y1 + dy * start
        ex = x1 + dx * end
        ey = y1 + dy * end
        draw.line((sx, sy, ex, ey), fill=color, width=width)
        dist += dash + gap


def _draw_arrowhead(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: tuple[int, int, int, int]) -> None:
    sx, sy = start
    ex, ey = end
    angle = math.atan2(ey - sy, ex - sx)
    size = 10
    left = (ex - size * math.cos(angle - math.pi / 6), ey - size * math.sin(angle - math.pi / 6))
    right = (ex - size * math.cos(angle + math.pi / 6), ey - size * math.sin(angle + math.pi / 6))
    draw.polygon([end, left, right], fill=color)


def _bounds(elements: Iterable[dict]) -> tuple[int, int]:
    max_x = 1200
    max_y = 900
    for el in elements:
        if el.get("type") == "cameraUpdate":
            max_x = max(max_x, int(el.get("width", 1200)))
            max_y = max(max_y, int(el.get("height", 900)))
            continue
        x = float(el.get("x", 0))
        y = float(el.get("y", 0))
        w = float(el.get("width", 0))
        h = float(el.get("height", 0))
        max_x = max(max_x, int(x + w + 40))
        max_y = max(max_y, int(y + h + 40))
    return max_x, max_y


def render(elements_path: Path, output_path: Path) -> None:
    elements = json.loads(elements_path.read_text(encoding="utf-8"))
    width, height = _bounds(elements)

    image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image, "RGBA")

    for el in elements:
        etype = el.get("type")
        if etype == "cameraUpdate":
            continue

        x = float(el.get("x", 0))
        y = float(el.get("y", 0))
        w = float(el.get("width", 0))
        h = float(el.get("height", 0))
        opacity = int(el.get("opacity", 100))

        stroke = _color(el.get("strokeColor", "#1e1e1e"), opacity)
        fill = _color(el.get("backgroundColor", "transparent"), opacity)
        stroke_width = int(el.get("strokeWidth", 2))

        if etype == "rectangle":
            radius = 14 if el.get("roundness") else 0
            draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=stroke, fill=fill, width=stroke_width)
            if "label" in el:
                _draw_centered_label(draw, (x, y, x + w, y + h), el["label"])

        elif etype == "ellipse":
            draw.ellipse((x, y, x + w, y + h), outline=stroke, fill=fill, width=stroke_width)
            if "label" in el:
                _draw_centered_label(draw, (x, y, x + w, y + h), el["label"])

        elif etype == "text":
            text = el.get("text", "")
            font_size = int(el.get("fontSize", 16))
            draw.multiline_text((x, y), text, fill=stroke, font=_font(font_size), spacing=4)

        elif etype == "arrow":
            points = el.get("points", [[0, 0], [w, h]])
            abs_points = [(x + float(px), y + float(py)) for px, py in points]
            is_dashed = el.get("strokeStyle") == "dashed"

            for i in range(len(abs_points) - 1):
                if is_dashed:
                    _draw_dashed_line(draw, abs_points[i], abs_points[i + 1], stroke, width=stroke_width)
                else:
                    draw.line((abs_points[i], abs_points[i + 1]), fill=stroke, width=stroke_width)

            if el.get("endArrowhead") == "arrow" and len(abs_points) >= 2:
                _draw_arrowhead(draw, abs_points[-2], abs_points[-1], stroke)

            label = el.get("label", {})
            text = label.get("text", "")
            if text and len(abs_points) >= 2:
                mx = (abs_points[0][0] + abs_points[-1][0]) / 2
                my = (abs_points[0][1] + abs_points[-1][1]) / 2 - 18
                font_size = int(label.get("fontSize", 14))
                draw.text((mx, my), text, fill=(30, 30, 30, 255), font=_font(font_size), anchor="mm")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Excalidraw elements JSON to PNG")
    parser.add_argument("--input", default="docs/architecture.excalidraw.elements.json", help="Path to elements JSON")
    parser.add_argument("--output", default="docs/architecture.png", help="Path to PNG output")
    args = parser.parse_args()

    render(Path(args.input), Path(args.output))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
