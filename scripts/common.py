"""Shared glyph analysis for the static and variable builds.

Loads the extracted cells, cleans the ink, finds each glyph's stroke
centreline and works out the font scale and the measured pen width.
"""
import json
import os

import cv2
import numpy as np
import potrace

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
    glyphs[key] = dict(dist=dist, skel=skel, base=m["baseline"] * UP + pad, top=ys.min())

caps = [glyphs[f"{ord(c):04x}"] for c in "EFHIKLMNTXZ"]
cap_px = np.median([g["base"] - g["top"] for g in caps])
S = CAP / cap_px  # font units per upscaled pixel
regular_w = round(float(np.median(widths_px)) * S)
print(f"scale {S:.2f} units/px, measured Regular stroke {regular_w} units")
WEIGHTS = [(c, n, round(regular_w * k)) for c, n, k in WEIGHTS]
