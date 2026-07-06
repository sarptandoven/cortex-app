#!/usr/bin/env python3
"""Generate Cortex's macOS app icon.

macOS (unlike iOS) does NOT round or mask app icons for you — the icon must ship as a rounded
"squircle" tile sitting on transparent padding, with its own soft contact shadow, exactly like every
other Mac app. The art is Cortex's aperture/shutter mark in ink on warm "Archive" paper.
"""
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

# The Archive palette.
PAPER = "#F7F4ED"
INK = "#2B2620"

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


def polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    return cx + radius * math.cos(radians), cy + radius * math.sin(radians)


def draw_shutter_mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, outer: float) -> None:
    inner = outer * 0.36
    gap = 5.5
    skew = 24
    for index in range(6):
        start = -92 + index * 60
        end = start + 60
        points = [
            polar(cx, cy, outer, start + gap),
            polar(cx, cy, outer, end - gap),
            polar(cx, cy, inner, end - gap - skew),
            polar(cx, cy, inner, start + gap - skew),
        ]
        draw.polygon(points, fill=INK)
    draw.ellipse(
        (cx - inner * 0.74, cy - inner * 0.74, cx + inner * 0.74, cy + inner * 0.74),
        fill=PAPER,
    )


def make_base(size: int = 1024) -> Image.Image:
    """A transparent canvas holding a rounded paper tile (with macOS-standard padding + shadow)
    and the shutter mark. Alpha OUTSIDE the tile stays transparent so the corners are truly round."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # macOS icon grid: the tile is ~82% of the canvas, centered, corners at the ~22.4% continuous
    # radius, leaving transparent padding all around (a touch more at the bottom for the shadow).
    pad = round(size * 0.090)
    left = top = pad
    right = bottom = size - pad
    tile_w = right - left
    radius = round(tile_w * 0.2237)

    # Soft contact shadow beneath the tile.
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (left, top + round(size * 0.012), right, bottom + round(size * 0.012)),
        radius=radius,
        fill=(20, 18, 16, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(round(size * 0.018)))
    image.alpha_composite(shadow)

    # The paper tile.
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    tdraw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=PAPER)
    # A hairline ink edge so the pale tile reads crisply on light desktops.
    tdraw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        outline=(43, 38, 32, 40),
        width=max(1, round(size * 0.002)),
    )
    image.alpha_composite(tile)

    draw = ImageDraw.Draw(image)
    draw_shutter_mark(draw, size / 2, size / 2, tile_w * 0.30)
    return image


def save_icon(base: Image.Image, path: Path, size: int) -> None:
    # Preserve alpha (RGBA) so the rounded corners and padding survive — the previous version
    # flattened onto an opaque paper canvas, which repainted the corners and made a hard square.
    resized = base.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(path)


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
        save_icon(base, ICONSET / filename, size)
    write_icns()
    print(ICNS)


if __name__ == "__main__":
    main()
