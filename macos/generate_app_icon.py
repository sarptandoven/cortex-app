#!/usr/bin/env python3
from __future__ import annotations

import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "Assets"
ICONSET = ASSETS / "AppIcon.iconset"
ICNS = ASSETS / "AppIcon.icns"
SOURCE = ASSETS / "AppIcon.source.png"

SLOTS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    return cx + radius * math.cos(radians), cy + radius * math.sin(radians)


def draw_shutter_mark(draw: ImageDraw.ImageDraw, size: int) -> None:
    cx = cy = size / 2
    outer = size * 0.295
    inner = size * 0.106
    gap = 5.5
    skew = 24
    blade = "#111111"

    for index in range(6):
        start = -92 + index * 60
        end = start + 60
        points = [
            polar(cx, cy, outer, start + gap),
            polar(cx, cy, outer, end - gap),
            polar(cx, cy, inner, end - gap - skew),
            polar(cx, cy, inner, start + gap - skew),
        ]
        draw.polygon(points, fill=blade)

    draw.ellipse(
        (
            cx - inner * 0.74,
            cy - inner * 0.74,
            cx + inner * 0.74,
            cy + inner * 0.74,
        ),
        fill="#f8f5ef",
    )


def make_base(size: int = 1024) -> Image.Image:
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    background_mask = rounded_rect_mask(size, int(224 * scale))

    background = Image.new("RGBA", (size, size), "#f8f5ef")
    background.putalpha(background_mask)
    image.alpha_composite(background)

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (
            int(38 * scale),
            int(48 * scale),
            int(986 * scale),
            int(1000 * scale),
        ),
        radius=int(218 * scale),
        fill=(0, 0, 0, 28),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(22 * scale)))
    image.alpha_composite(shadow)
    image.alpha_composite(background)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (
            int(22 * scale),
            int(22 * scale),
            int(1002 * scale),
            int(1002 * scale),
        ),
        radius=int(218 * scale),
        outline=(18, 18, 18, 18),
        width=max(1, int(3 * scale)),
    )
    draw_shutter_mark(draw, size)
    return image


def save_rgb_icon(base: Image.Image, path: Path, size: int) -> None:
    resized = base.resize((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (size, size), "#f8f5ef")
    canvas.paste(resized, mask=resized.getchannel("A"))
    canvas.save(path)


def write_icns() -> None:
    members = [
        ("icp4", ICONSET / "icon_16x16.png"),
        ("icp5", ICONSET / "icon_32x32.png"),
        ("icp6", ICONSET / "icon_32x32@2x.png"),
        ("ic07", ICONSET / "icon_128x128.png"),
        ("ic08", ICONSET / "icon_256x256.png"),
        ("ic09", ICONSET / "icon_512x512.png"),
        ("ic10", ICONSET / "icon_512x512@2x.png"),
    ]
    chunks = []
    total_length = 8
    for code, path in members:
        data = path.read_bytes()
        chunk = code.encode("ascii") + struct.pack(">I", len(data) + 8) + data
        chunks.append(chunk)
        total_length += len(chunk)
    ICNS.write_bytes(b"icns" + struct.pack(">I", total_length) + b"".join(chunks))


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    ICONSET.mkdir(parents=True, exist_ok=True)
    base = make_base()
    base.save(SOURCE)
    for filename, size in SLOTS:
        save_rgb_icon(base, ICONSET / filename, size)
    write_icns()
    print(ICNS)


if __name__ == "__main__":
    main()
