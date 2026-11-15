"""Trace the extracted cells and build the miifont family (9 weights x upright/italic).

Each glyph is reduced to its stroke centreline, then re-stroked with a round
"pen" whose width depends on the weight. Regular uses the measured width of
the original handwriting. Italics are a 12 degree slant of the upright outlines.
"""
import json
import math
import os

import cv2
import numpy as np
import potrace
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

FAMILY = "miifont"
VERSION = "1.000"
UPM = 1000
UP = 4             # upscale factor before tracing
SIDE = 45          # side bearing in font units
CAP = 700          # cap height in font units
ITALIC_ANGLE = 12  # degrees
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELLS = os.path.join(ROOT, "build", "cells")
FONTS = os.path.join(ROOT, "fonts")

# (weight class, name, stroke width as a multiple of the measured pen width)
WEIGHTS = [
    (100, "Thin", 0.45),
    (200, "ExtraLight", 0.6),
    (300, "Light", 0.8),
    (400, "Regular", 1.0),
    (500, "Medium", 1.25),
    (600, "SemiBold", 1.55),
    (700, "Bold", 1.9),
    (800, "ExtraBold", 2.3),
    (900, "Black", 2.8),
]

meta = json.load(open(os.path.join(CELLS, "meta.json"), encoding="utf-8"))


def ink_mask(key):
    img = cv2.imread(os.path.join(CELLS, f"{key}.png"))
    b, g, r = [c.astype(int) for c in cv2.split(img)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=UP, fy=UP, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (5, 5), 0)
    mask = big < 120
    # Drop the light-blue guide lines and labels.
    blue = cv2.resize((((b - r) > 30) & (gray > 110)).astype(np.uint8), None, fx=UP, fy=UP,
                      interpolation=cv2.INTER_NEAREST).astype(bool)
    mask &= ~blue
    m = (mask * 255).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros_like(m)
    H, W = m.shape
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 10 * UP * UP:  # noise specks
            continue
        if (x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2) and a < 400 * UP:
            continue  # remnants of the cell border
        keep[lab == i] = 255
    return keep > 0


def trace(mask):
    # potracer treats low values as ink, so pass the inverted mask.
    return potrace.Bitmap(~mask).trace(turdsize=8 * UP, alphamax=1.0,
                                       opticurve=True, opttolerance=0.3)


# --- analyse every glyph: skeleton + distance field ---------------------------
glyphs = {}
widths_px = []
for key, m in meta.items():
    mask = ink_mask(key)
    if not mask.any():
        print("empty cell:", m["char"])
        continue
    pad = 40 * UP  # room for heavier weights to grow
    mask = np.pad(mask, pad)
    skel = cv2.ximgproc.thinning((mask * 255).astype(np.uint8)) > 0
    dist_in = cv2.distanceTransform((mask * 255).astype(np.uint8), cv2.DIST_L2, 5)
    widths_px.extend((dist_in[skel] * 2).tolist())
    # Distance from the centreline, thresholded per weight later.
    dist = cv2.distanceTransform((~skel * 255).astype(np.uint8), cv2.DIST_L2, 5)
    ys, xs = np.nonzero(mask)
    glyphs[key] = dict(dist=dist, base=m["baseline"] * UP + pad, top=ys.min())

caps = [glyphs[f"{ord(c):04x}"] for c in "EFHIKLMNTXZ"]
cap_px = np.median([g["base"] - g["top"] for g in caps])
S = CAP / cap_px  # font units per upscaled pixel
regular_w = round(float(np.median(widths_px)) * S)
print(f"scale {S:.2f} units/px, measured Regular stroke {regular_w} units")
WEIGHTS = [(c, n, round(regular_w * k)) for c, n, k in WEIGHTS]


def outline(g, stroke_units):
    """Re-stroke a glyph's centreline; returns (RecordingPen, advance width)."""
    r = stroke_units / 2 / S
    mask = g["dist"] <= r
    ys, xs = np.nonzero(mask)
    xmin, xmax = xs.min(), xs.max()
    f = lambda p: (round((p.x - xmin) * S + SIDE), round((g["base"] - p.y) * S))
    rp = RecordingPen()
    pen = ReverseContourPen(rp)  # y-flip reversed winding; restore CFF orientation
    for curve in trace(mask):
        pen.moveTo(f(curve.start_point))
        for seg in curve.segments:
            if seg.is_corner:
                pen.lineTo(f(seg.c)); pen.lineTo(f(seg.end_point))
            else:
                pen.curveTo(f(seg.c1), f(seg.c2), f(seg.end_point))
        pen.closePath()
    return rp, round((xmax - xmin) * S + 2 * SIDE)


def notdef(pen):
    pen.moveTo((50, 0)); pen.lineTo((450, 0)); pen.lineTo((450, 700)); pen.lineTo((50, 700)); pen.closePath()
    pen.moveTo((100, 50)); pen.lineTo((100, 650)); pen.lineTo((400, 650)); pen.lineTo((400, 50)); pen.closePath()


def save(weight_class, weight_name, stroke, italic, recs, widths, order, cmap):
    ribbi = weight_name in ("Regular", "Bold")
    style = {("Regular", False): "Regular", ("Regular", True): "Italic",
             ("Bold", False): "Bold", ("Bold", True): "Bold Italic"}.get((weight_name, italic))
    typo_sub = (weight_name if not italic else
                ("Italic" if weight_name == "Regular" else f"{weight_name} Italic"))
    ps_style = typo_sub.replace(" ", "")
    file_style = ps_style
    family = FAMILY if ribbi else f"{FAMILY} {weight_name}"
    subfamily = style if ribbi else ("Italic" if italic else "Regular")
    slant = math.tan(math.radians(ITALIC_ANGLE)) if italic else 0.0
    asc, desc = 900 + stroke // 2, -280 - stroke // 2

    for otf in (True, False):
        fb = FontBuilder(UPM, isTTF=not otf)
        fb.setupGlyphOrder(order)
        fb.setupCharacterMap(cmap)

        def draw_into(pen, gname):
            # Slant around the x-height midpoint so italics don't drift right.
            tp = TransformPen(pen, (1, 0, slant, 1, -slant * 240, 0))
            if gname == ".notdef":
                notdef(tp)
            elif gname in recs:
                recs[gname].replay(tp)

        if otf:
            cs = {}
            for gname in order:
                pen = T2CharStringPen(widths[gname], None)
                draw_into(pen, gname)
                cs[gname] = pen.getCharString()
            fb.setupCFF(f"{FAMILY}-{ps_style}", {"FullName": f"{FAMILY} {typo_sub}"}, cs, {})
        else:
            tt = {}
            for gname in order:
                pen = TTGlyphPen(None)
                draw_into(Cu2QuPen(ReverseContourPen(pen), max_err=1.0), gname)
                tt[gname] = pen.glyph()
            fb.setupGlyf(tt)
        fb.setupHorizontalMetrics({gname: (widths[gname], 0) for gname in order})
        fb.setupHorizontalHeader(ascent=asc, descent=desc,
                                 caretSlopeRise=1000 if italic else 1,
                                 caretSlopeRun=round(1000 * slant) if italic else 0)
        names = {
            "familyName": family,
            "styleName": subfamily,
            "uniqueFontIdentifier": f"{VERSION};{FAMILY}-{ps_style}",
            "fullName": f"{FAMILY} {typo_sub}",
            "psName": f"{FAMILY}-{ps_style}",
            "version": f"Version {VERSION}",
        }
        if not ribbi:
            names["typographicFamily"] = FAMILY
            names["typographicSubfamily"] = typo_sub
        fb.setupNameTable(names)
        fs = (0x01 if italic else 0) | (0x20 if weight_name == "Bold" else 0)
        if fs == 0:
            fs = 0x40  # REGULAR: upright and not Bold
        fs |= 0x80  # USE_TYPO_METRICS
        fb.setupOS2(version=4, usWeightClass=weight_class, fsSelection=fs,
                    sTypoAscender=asc, sTypoDescender=desc, sTypoLineGap=100,
                    usWinAscent=asc + 60, usWinDescent=-desc + 60,
                    sxHeight=480, sCapHeight=CAP, achVendID="MIIF")
        fb.setupPost(italicAngle=-ITALIC_ANGLE if italic else 0)
        fb.font["head"].macStyle = (0x01 if weight_name == "Bold" else 0) | (0x02 if italic else 0)

        ext = "otf" if otf else "ttf"
        os.makedirs(os.path.join(FONTS, ext), exist_ok=True)
        path = os.path.join(FONTS, ext, f"{FAMILY}-{file_style}.{ext}")
        fb.save(path)

        # Fix left side bearings to the real outline xMin, recalc bboxes.
        f = TTFont(path)
        gs = f.getGlyphSet()
        for gname in order:
            bp = BoundsPen(gs); gs[gname].draw(bp)
            f["hmtx"][gname] = (widths[gname], round(bp.bounds[0]) if bp.bounds else 0)
        f.save(path)
        if not otf:
            os.makedirs(os.path.join(FONTS, "woff2"), exist_ok=True)
            f = TTFont(path); f.flavor = "woff2"
            f.save(os.path.join(FONTS, "woff2", f"{FAMILY}-{file_style}.woff2"))
    print("built", f"{FAMILY}-{file_style}")


for weight_class, weight_name, stroke in WEIGHTS:
    order = [".notdef", "space"]
    cmap = {32: "space", 0xA0: "space"}
    widths = {".notdef": 500, "space": 260 + stroke}
    recs = {}
    for key, g in glyphs.items():
        ch = meta[key]["char"]
        name = f"uni{ord(ch):04X}"
        recs[name], widths[name] = outline(g, stroke)
        order.append(name)
        cmap[ord(ch)] = name
    for italic in (False, True):
        save(weight_class, weight_name, stroke, italic, recs, widths, order, cmap)
