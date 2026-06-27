#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parents[2]
OUT = WORKSPACE_ROOT / "outputs" / "doppl-icon-options"
ICON_DIR = OUT / "icons"
ICONSET = ROOT / "Assets" / "AppIcon.iconset"
ICNS = ROOT / "Assets" / "AppIcon.icns"

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


@dataclass(frozen=True)
class IconSpec:
    number: int
    slug: str
    name: str
    idea: str
    palette: tuple[str, str, str, str]
    draw: Callable[[ImageDraw.ImageDraw, float, tuple[str, str, str, str]], None]

    @property
    def filename(self) -> str:
        return f"{self.number:02d}-{self.slug}.png"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ar, ag, ab = hex_to_rgb(a)
    br, bg, bb = hex_to_rgb(b)
    return (
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def base_icon(palette: tuple[str, str, str, str], size: int = 1024) -> Image.Image:
    bg1, bg2, _, _ = palette
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            nx = x / max(1, size - 1)
            ny = y / max(1, size - 1)
            t = min(1, max(0, 0.62 * nx + 0.44 * ny))
            glow = max(0, 1 - math.hypot(nx - 0.25, ny - 0.20) * 1.35)
            r, g, b = mix(bg1, bg2, t)
            gr, gg, gb = mix("#ffffff", bg1, 0.72)
            px[x, y] = (
                min(255, int(r + gr * glow * 0.20)),
                min(255, int(g + gg * glow * 0.18)),
                min(255, int(b + gb * glow * 0.16)),
                255,
            )
    img.alpha_composite(grad)
    img.putalpha(rounded_mask(size, 226))

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((44, 54, 980, 992), radius=216, fill=(0, 0, 0, 72))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))

    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    rd.rounded_rectangle((30, 30, 994, 994), radius=210, outline=(255, 255, 255, 55), width=9)
    rd.rounded_rectangle((0, 0, 1023, 1023), radius=226, outline=(0, 0, 0, 38), width=2)
    img.alpha_composite(rim)
    return img


def line(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], fill, width: int) -> None:
    draw.line((a[0], a[1], b[0], b[1]), fill=fill, width=width, joint="curve")


def circle(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill, outline=None, width=1) -> None:
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=outline, width=width)


def rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width=1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_graph(draw, s, p):
    _, _, a, b = p
    pts = [(330, 330), (655, 260), (720, 660), (385, 670), (525, 505)]
    for i, j, w in [(0, 1, 34), (1, 2, 32), (2, 3, 30), (3, 0, 28), (4, 0, 22), (4, 2, 22)]:
        line(draw, pts[i], pts[j], (*hex_to_rgb(b), 150), w)
    for idx, (x, y) in enumerate(pts):
        circle(draw, x, y, [94, 82, 92, 72, 54][idx], "#fff8e7" if idx % 2 == 0 else a)


def draw_mirror(draw, s, p):
    _, _, a, b = p
    rounded(draw, (220, 260, 496, 760), 136, (255, 248, 231, 235))
    rounded(draw, (528, 260, 804, 760), 136, (*hex_to_rgb(a), 235))
    circle(draw, 360, 390, 82, p[3])
    circle(draw, 666, 390, 82, "#fff8e7")
    line(draw, (500, 330), (524, 720), (255, 255, 255, 95), 12)


def draw_vault(draw, s, p):
    _, _, a, b = p
    rounded(draw, (248, 302, 776, 752), 112, (255, 248, 231, 236))
    rounded(draw, (310, 400, 714, 690), 62, p[1])
    circle(draw, 512, 548, 96, a)
    for deg in range(0, 360, 45):
        r = math.radians(deg)
        line(draw, (512, 548), (512 + math.cos(r) * 68, 548 + math.sin(r) * 68), "#fff8e7", 13)
    rounded(draw, (360, 250, 664, 408), 88, None, b, 36)


def draw_compass(draw, s, p):
    _, _, a, b = p
    circle(draw, 512, 512, 292, (255, 248, 231, 235))
    circle(draw, 512, 512, 210, p[1])
    draw.polygon([(512, 236), (610, 528), (512, 786), (416, 528)], fill=a)
    draw.polygon([(512, 306), (560, 528), (512, 686), (464, 528)], fill="#fff8e7")
    circle(draw, 512, 512, 38, b)


def draw_orb(draw, s, p):
    _, _, a, b = p
    for r, alpha in [(320, 38), (254, 58), (188, 78)]:
        circle(draw, 512, 512, r, (*hex_to_rgb(a), alpha), outline=None)
    circle(draw, 512, 512, 150, "#fff8e7")
    circle(draw, 512, 512, 78, b)
    for angle in [20, 140, 260]:
        rad = math.radians(angle)
        circle(draw, 512 + math.cos(rad) * 270, 512 + math.sin(rad) * 270, 50, a)


def draw_neural_path(draw, s, p):
    _, _, a, b = p
    coords = [(255, 690), (355, 436), (540, 322), (714, 420), (775, 675)]
    for i in range(len(coords) - 1):
        line(draw, coords[i], coords[i + 1], (*hex_to_rgb("#fff8e7"), 145), 44)
    for x, y in coords:
        circle(draw, x, y, 64, a)
        circle(draw, x, y, 31, "#fff8e7")
    line(draw, (360, 690), (640, 690), b, 24)


def draw_loop(draw, s, p):
    _, _, a, b = p
    draw.arc((240, 258, 784, 784), 28, 318, fill="#fff8e7", width=64)
    draw.polygon([(755, 370), (835, 320), (818, 418)], fill="#fff8e7")
    draw.arc((320, 340, 704, 724), 208, 150, fill=a, width=42)
    circle(draw, 512, 512, 84, b)


def draw_capsule(draw, s, p):
    _, _, a, b = p
    rounded(draw, (300, 210, 724, 814), 212, "#fff8e7")
    rounded(draw, (340, 250, 684, 774), 172, p[0])
    line(draw, (340, 512), (684, 512), (*hex_to_rgb(a), 220), 30)
    circle(draw, 512, 512, 104, b)
    circle(draw, 512, 512, 50, "#fff8e7")


def draw_prism(draw, s, p):
    _, _, a, b = p
    draw.polygon([(512, 190), (780, 400), (680, 800), (344, 800), (244, 400)], fill=(255, 248, 231, 230))
    draw.polygon([(512, 190), (780, 400), (512, 520), (244, 400)], fill=a)
    draw.polygon([(512, 520), (780, 400), (680, 800)], fill=b)
    draw.polygon([(512, 520), (344, 800), (244, 400)], fill=p[1])
    line(draw, (512, 190), (512, 520), (255, 255, 255, 140), 12)


def draw_stack(draw, s, p):
    _, _, a, b = p
    for i, y in enumerate([270, 385, 500, 615]):
        fill = ["#fff8e7", a, "#fff8e7", b][i]
        rounded(draw, (270 + i * 16, y, 754 - i * 16, y + 112), 42, fill)
        line(draw, (350, y + 56), (674, y + 56), (*hex_to_rgb(p[0]), 95), 12)


def draw_constellation(draw, s, p):
    _, _, a, b = p
    pts = [(282, 300), (512, 232), (742, 322), (685, 602), (482, 744), (290, 612)]
    for i, pt in enumerate(pts):
        line(draw, pt, pts[(i + 1) % len(pts)], (*hex_to_rgb("#fff8e7"), 138), 20)
    line(draw, pts[0], pts[3], (*hex_to_rgb(a), 150), 18)
    line(draw, pts[1], pts[4], (*hex_to_rgb(b), 150), 18)
    for i, (x, y) in enumerate(pts):
        circle(draw, x, y, 52 if i % 2 else 66, [a, "#fff8e7", b][i % 3])


def draw_self_ring(draw, s, p):
    _, _, a, b = p
    circle(draw, 512, 512, 280, None, "#fff8e7", 56)
    circle(draw, 512, 406, 92, a)
    rounded(draw, (330, 530, 694, 742), 130, "#fff8e7")
    circle(draw, 512, 512, 186, None, b, 28)


def draw_time_fold(draw, s, p):
    _, _, a, b = p
    draw.arc((240, 236, 784, 788), 92, 448, fill="#fff8e7", width=52)
    draw.arc((332, 326, 692, 692), -86, 270, fill=a, width=42)
    line(draw, (512, 512), (512, 326), b, 24)
    line(draw, (512, 512), (650, 590), b, 24)
    circle(draw, 512, 512, 52, "#fff8e7")


def draw_shield_node(draw, s, p):
    _, _, a, b = p
    draw.polygon([(512, 190), (760, 300), (710, 654), (512, 828), (314, 654), (264, 300)], fill="#fff8e7")
    draw.polygon([(512, 260), (690, 342), (650, 622), (512, 748), (374, 622), (334, 342)], fill=p[0])
    circle(draw, 512, 510, 88, a)
    line(draw, (512, 510), (512, 650), b, 30)


def draw_daily(draw, s, p):
    _, _, a, b = p
    circle(draw, 512, 512, 286, "#fff8e7")
    for idx, ang in enumerate([45, 135, 225, 315]):
        r = math.radians(ang)
        circle(draw, 512 + math.cos(r) * 190, 512 + math.sin(r) * 190, 58, [a, b, p[1], a][idx])
    draw.arc((322, 322, 702, 702), 25, 332, fill=p[0], width=34)
    circle(draw, 512, 512, 70, b)


def draw_tool_mesh(draw, s, p):
    _, _, a, b = p
    rounded(draw, (224, 250, 440, 466), 54, "#fff8e7")
    rounded(draw, (584, 250, 800, 466), 54, a)
    rounded(draw, (404, 558, 620, 774), 54, b)
    line(draw, (440, 358), (584, 358), "#fff8e7", 28)
    line(draw, (512, 466), (512, 558), "#fff8e7", 28)
    line(draw, (332, 466), (438, 608), (*hex_to_rgb(a), 185), 24)
    line(draw, (692, 466), (586, 608), (*hex_to_rgb(b), 185), 24)


def draw_trace(draw, s, p):
    _, _, a, b = p
    circle(draw, 512, 352, 104, "#fff8e7")
    rounded(draw, (314, 498, 710, 792), 150, "#fff8e7")
    for x, y in [(326, 300), (704, 280), (742, 682), (256, 684)]:
        circle(draw, x, y, 48, a if x < 512 else b)
        line(draw, (512, 512), (x, y), (*hex_to_rgb(p[1]), 135), 18)


def draw_infinity(draw, s, p):
    _, _, a, b = p
    draw.arc((190, 330, 548, 690), 122, 418, fill="#fff8e7", width=62)
    draw.arc((476, 330, 834, 690), -58, 238, fill=a, width=62)
    circle(draw, 370, 510, 52, b)
    circle(draw, 654, 510, 52, "#fff8e7")


def draw_beacon(draw, s, p):
    _, _, a, b = p
    for r, alpha in [(330, 35), (250, 58), (170, 80)]:
        circle(draw, 512, 540, r, (*hex_to_rgb(a), alpha))
    draw.polygon([(512, 220), (674, 760), (350, 760)], fill="#fff8e7")
    draw.polygon([(512, 322), (596, 690), (428, 690)], fill=p[0])
    circle(draw, 512, 520, 58, b)


def draw_core(draw, s, p):
    _, _, a, b = p
    circle(draw, 512, 512, 260, "#fff8e7")
    circle(draw, 512, 512, 188, p[0])
    circle(draw, 512, 512, 112, a)
    circle(draw, 512, 512, 42, "#fff8e7")
    for ang in range(0, 360, 60):
        r = math.radians(ang)
        line(draw, (512, 512), (512 + math.cos(r) * 250, 512 + math.sin(r) * 250), (*hex_to_rgb(b), 175), 12)


PALETTES = [
    ("#0d332c", "#0d4965", "#7ee1c3", "#d8ff8f"),
    ("#111827", "#2e1065", "#f0abfc", "#67e8f9"),
    ("#132318", "#41401d", "#f5d06f", "#86efac"),
    ("#172033", "#314f5a", "#b7e4ff", "#f7f1d5"),
    ("#121212", "#354f52", "#cad2c5", "#84a98c"),
    ("#1d2530", "#5a2b51", "#ffcfdf", "#99f6e4"),
    ("#163024", "#572d1d", "#fbbf24", "#a7f3d0"),
    ("#0e2637", "#294243", "#93c5fd", "#fde68a"),
    ("#202124", "#5b4d7a", "#c4b5fd", "#fef3c7"),
    ("#11201d", "#334155", "#a7f3d0", "#f8fafc"),
]


SPECS = [
    IconSpec(1, "memory-graph", "Memory Graph", "Personal memory as a living graph.", PALETTES[0], draw_graph),
    IconSpec(2, "you-but-ai", "You, But AI", "A mirrored self that agents can act from.", PALETTES[1], draw_mirror),
    IconSpec(3, "private-vault", "Private Vault", "Local-first memory under user control.", PALETTES[2], draw_vault),
    IconSpec(4, "agent-compass", "Agent Compass", "Judgment and direction for connected agents.", PALETTES[3], draw_compass),
    IconSpec(5, "context-orb", "Context Orb", "Context packed into a reusable core.", PALETTES[5], draw_orb),
    IconSpec(6, "neural-path", "Neural Path", "How your decisions connect over time.", PALETTES[6], draw_neural_path),
    IconSpec(7, "daily-loop", "Daily Loop", "Capture, Review, Reuse, Return.", PALETTES[7], draw_loop),
    IconSpec(8, "local-capsule", "Local Capsule", "A portable private memory layer.", PALETTES[4], draw_capsule),
    IconSpec(9, "decision-prism", "Decision Prism", "Preferences turned into clear choices.", PALETTES[8], draw_prism),
    IconSpec(10, "recall-stack", "Recall Stack", "Layered context always ready to reuse.", PALETTES[9], draw_stack),
    IconSpec(11, "mcp-constellation", "MCP Constellation", "Connected tools around one memory source.", PALETTES[0], draw_constellation),
    IconSpec(12, "self-ring", "Self Ring", "AI as an extension of the user.", PALETTES[1], draw_self_ring),
    IconSpec(13, "time-fold", "Time Fold", "Putting more hours into the same day.", PALETTES[6], draw_time_fold),
    IconSpec(14, "trust-shield", "Trust Shield", "Permissioned memory and action controls.", PALETTES[2], draw_shield_node),
    IconSpec(15, "review-rhythm", "Review Rhythm", "A daily cockpit for what matters.", PALETTES[7], draw_daily),
    IconSpec(16, "tool-mesh", "Tool Mesh", "Agents, apps, and MCP tools connected.", PALETTES[3], draw_tool_mesh),
    IconSpec(17, "human-trace", "Human Trace", "The memory layer for how you work.", PALETTES[5], draw_trace),
    IconSpec(18, "infinite-day", "Infinite Day", "Forty-eight hours in a 24-hour day.", PALETTES[8], draw_infinity),
    IconSpec(19, "context-beacon", "Context Beacon", "A signal agents can orient around.", PALETTES[6], draw_beacon),
    IconSpec(20, "cortex-core", "Cortex Core", "The operating model at the center.", PALETTES[0], draw_core),
]


def render_icon(spec: IconSpec, size: int = 1024) -> Image.Image:
    img = base_icon(spec.palette, size=size)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    spec.draw(draw, size / 1024, spec.palette)
    overlay = overlay.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    img.alpha_composite(overlay)
    return img


def make_contact_sheet() -> Path:
    cell_w, cell_h = 280, 330
    cols, rows = 5, 4
    margin = 44
    title_h = 84
    sheet = Image.new("RGB", (cols * cell_w + margin * 2, rows * cell_h + margin * 2 + title_h), "#f6f5ef")
    d = ImageDraw.Draw(sheet)
    title_font = font(36, bold=True)
    label_font = font(18, bold=True)
    small_font = font(13)
    d.text((margin, 28), "Doppl App Icon Directions", fill="#17201d", font=title_font)
    d.text((margin, 66), "Pick a number. I will convert that direction into the App Store package.", fill="#4b5563", font=small_font)
    for spec in SPECS:
        idx = spec.number - 1
        col = idx % cols
        row = idx // cols
        x = margin + col * cell_w
        y = margin + title_h + row * cell_h
        d.rounded_rectangle((x, y, x + cell_w - 18, y + cell_h - 18), radius=22, fill="#ffffff", outline="#e5e1d5")
        icon = Image.open(ICON_DIR / spec.filename).resize((188, 188), Image.Resampling.LANCZOS)
        sheet.paste(icon.convert("RGB"), (x + 36, y + 24))
        d.text((x + 24, y + 224), f"{spec.number:02d}. {spec.name}", fill="#17201d", font=label_font)
        wrapped = spec.idea if len(spec.idea) <= 43 else spec.idea[:40].rstrip() + "..."
        d.text((x + 24, y + 254), wrapped, fill="#667085", font=small_font)
    path = OUT / "doppl-icon-options-contact-sheet.png"
    sheet.save(path, quality=95)
    return path


def generate() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for spec in SPECS:
        path = ICON_DIR / spec.filename
        render_icon(spec).save(path)
        manifest.append(
            {
                "number": spec.number,
                "name": spec.name,
                "slug": spec.slug,
                "idea": spec.idea,
                "png": str(path),
            }
        )
    contact = make_contact_sheet()
    (OUT / "manifest.json").write_text(json.dumps({"contact_sheet": str(contact), "icons": manifest}, indent=2) + "\n")
    print(contact)


def apply_choice(number: int) -> None:
    spec = next((item for item in SPECS if item.number == number), None)
    if spec is None:
        raise SystemExit(f"Unknown icon number: {number}")
    source = ICON_DIR / spec.filename
    if not source.exists():
        generate()
    ICONSET.mkdir(parents=True, exist_ok=True)
    base = Image.open(source).convert("RGBA")
    for filename, size in SLOTS:
        base.resize((size, size), Image.Resampling.LANCZOS).save(ICONSET / filename)
    if ICNS.exists():
        ICNS.unlink()
    subprocess.run(["iconutil", "--convert", "icns", "--output", str(ICNS), str(ICONSET)], check=True)
    print(ICNS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", type=int, help="Apply one generated option as macOS Assets/AppIcon.icns")
    args = parser.parse_args()
    generate()
    if args.apply:
        apply_choice(args.apply)


if __name__ == "__main__":
    main()
