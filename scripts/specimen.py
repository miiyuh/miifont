"""Render specimen.png (every style) from the built fonts."""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS = ["Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black"]
SAMPLE = "The quick brown fox jumps over the lazy dog 0123"
CHARSET = [
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789 !?.,;:'\"()-&@ #$%€£¥*+×÷=/\\_<>[]{}|~°",
]


def font(style, size):
    return ImageFont.truetype(os.path.join(ROOT, "fonts", "otf", f"miifont-{style}.otf"), size)


label_font = ImageFont.load_default(16)
im = Image.new("RGB", (1500, 40 + len(CHARSET) * 80 + len(WEIGHTS) * 2 * 62 + 30), "white")
d = ImageDraw.Draw(im)
y = 80
for line in CHARSET:
    d.text((20, y), line, font=font("Regular", 54), fill="black", anchor="ls")
    y += 80
y += 10
for w in WEIGHTS:
    for italic in (False, True):
        style = w if not italic else ("Italic" if w == "Regular" else w + "Italic")
        d.text((20, y), style, font=label_font, fill=(120, 140, 170), anchor="ls")
        d.text((230, y), SAMPLE, font=font(style, 44), fill="black", anchor="ls")
        y += 62
im.save(os.path.join(ROOT, "specimen.png"))
