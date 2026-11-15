"""Build the "miifont Jawi Test" font from the filled Thuluth letter test sheet.

Reads scans/jawi-test-letters.jpg and templates/jawi/test/layout-letters.json:
lines the scan up on its corner squares, cuts out each cell, keeps only the
black ink and traces it as a filled outline (keeping the broad-nib thick/thin
contrast). Joined sides are cut at the join point so neighbouring letters
overlap slightly and connect. The font gets init/medi/fina/isol substitutions,
so typing the sentence picks the right forms. Also renders a preview PNG.
"""
import json
import os
import subprocess
import tempfile

import cv2
import numpy as np
import potrace
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN = os.path.join(ROOT, "scans", "jawi-test-letters.jpg")
LAYOUT = os.path.join(ROOT, "templates", "jawi", "test", "layout-letters.json")
OUT = os.path.join(ROOT, "fonts", "jawi-test")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

FAMILY = "miifont Jawi Test"
PS = "miifontJawiTest-Regular"
PX = 20                 # warped page resolution, px per mm
UPM = 1000
ALIF_UNITS = 700        # alif height in font units
OVERLAP = 0.35          # joined letters overlap by 2 x this (mm)
SIDE = 1.2              # mm side bearing on non-joining sides
JOIN_BAND = 0.7         # look for the joining stroke within this many nib widths of the baseline
KASHIDA = 1.0           # nib widths of plain joining stroke kept past the letter body


def corner_squares(gray, size_px):
    _, bw = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    n, _, stats, cent = cv2.connectedComponentsWithStats(bw)
    lo = (size_px * 0.75) ** 2
    pts = np.array([cent[i] for i in range(1, n)
                    if stats[i, cv2.CC_STAT_AREA] > lo
                    and 0.8 < stats[i, cv2.CC_STAT_WIDTH] / stats[i, cv2.CC_STAT_HEIGHT] < 1.25
                    and stats[i, cv2.CC_STAT_AREA] > 0.85 * stats[i, cv2.CC_STAT_WIDTH] * stats[i, cv2.CC_STAT_HEIGHT]])
    h, w = gray.shape
    return np.array([pts[np.argmin(((pts - t) ** 2).sum(1))] for t in [(0, 0), (w, 0), (0, h), (w, h)]],
                    np.float32)


def warp(lay):
    img = cv2.imread(SCAN)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    px_per_mm = img.shape[1] / lay["page_mm"][0]
    src = corner_squares(gray, lay["square_mm"] * px_per_mm)
    dst = np.array(lay["square_centres_mm"], np.float32) * PX
    H = cv2.getPerspectiveTransform(src, dst)
    W, Hh = (np.array(lay["page_mm"]) * PX).astype(int)
    return cv2.warpPerspective(img, H, (W, Hh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def ink(page):
    """Black ink only: dark, and not blue (guides, labels and ghost letters are blue)."""
    b, g, r = [page[..., i].astype(int) for i in range(3)]
    m = (r < 120) & (b - r < 45)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] > (0.6 * PX) ** 2      # drop specks smaller than ~0.6 mm
    return keep[lab]


def glyph(cell, mask, guides, nib, k):
    """Trace one cell. Returns (outline commands in font units, advance width)."""
    x0, y0 = cell["x"], cell["y"]
    top = guides["label_band"] + 0.6
    sub = mask[int((y0 + top) * PX):int((y0 + cell["h"] - 0.8) * PX),
               int((x0 + 0.8) * PX):int((x0 + cell["w"] - 0.8) * PX)].copy()
    ox, oy = x0 + 0.8, y0 + top                                     # sub-image origin in page mm
    base = y0 + guides["baseline"]
    tick = guides["join_tick_inset"]
    to_mm = lambda px, py: (ox + px / PX, oy + py / PX)
    # Joined sides: cut at the tick, then use where the stroke actually ends.
    band = slice(max(0, int((base - oy - JOIN_BAND * nib - nib) * PX)), int((base - oy + JOIN_BAND * nib) * PX))
    if cell["join_right"]:
        sub[:, int((x0 + cell["w"] - tick - ox) * PX):] = False
    if cell["join_left"]:
        sub[:, :int((x0 + tick - ox) * PX)] = False
    if not sub.any():
        raise SystemExit(f"cell {cell['id']} ({cell['char']} {cell['form']}) is empty")
    # The template asks for joining strokes right up to the ticks, which is longer
    # than in running writing. Shorten a run of plain baseline stroke to KASHIDA nibs.
    outside = sub.copy(); outside[band] = False
    body = np.nonzero(outside.any(0))[0]          # columns with more than a join stroke
    keep = int(KASHIDA * nib * PX)
    if len(body):
        if cell["join_right"]:
            sub[:, body.max() + keep:] = False
        if cell["join_left"]:
            sub[:, :max(0, body.min() - keep)] = False
    ys, xs = np.nonzero(sub)
    bxs = np.nonzero(sub[band].any(0))[0]
    # The right join edge sits inside the stroke, so it overlaps the next letter's
    # stroke. The left edge is never inside the ink: Windows drops parts (dots) of
    # the previous glyph when a glyph reaches left of its origin.
    if cell["join_right"]:
        right = ox + (bxs.max() if len(bxs) else xs.max()) / PX - 2 * OVERLAP
    else:
        right = ox + xs.max() / PX + SIDE
    if cell["join_left"]:
        left = ox + xs.min() / PX
    else:
        left = ox + xs.min() / PX - SIDE
    f = lambda p: (round((to_mm(p.x, p.y)[0] - left) * k), round((base - to_mm(p.x, p.y)[1]) * k))
    cmds = []
    for curve in potrace.Bitmap(~sub).trace(turdsize=int((0.5 * PX) ** 2), alphamax=1.0,
                                            opticurve=True, opttolerance=0.3):
        cmds.append(("moveTo", f(curve.start_point)))
        for seg in curve.segments:
            if seg.is_corner:
                cmds += [("lineTo", f(seg.c)), ("lineTo", f(seg.end_point))]
            else:
                cmds.append(("curveTo", f(seg.c1), f(seg.c2), f(seg.end_point)))
        cmds.append(("closePath",))
    return cmds, round((right - left) * k)


def draw(cmds):
    pen = TTGlyphPen(None)
    p = Cu2QuPen(pen, max_err=1.0)   # y-flip already gives TrueType (clockwise) winding
    for c in cmds:
        getattr(p, c[0])(*c[1:])
    return pen.glyph()


def main():
    lay = json.load(open(LAYOUT, encoding="utf-8"))
    page = warp(lay)
    mask = ink(page)
    guides, nib = lay["cell_guides_mm"], lay["nib_mm"]
    k = ALIF_UNITS / (lay["alif_dots"] * nib)                 # font units per mm
    os.makedirs(OUT, exist_ok=True)
    cv2.imwrite(os.path.join(ROOT, "build", "jawi-test-warped.png"), page)

    names, glyf, hmtx, by_char = [".notdef", "space"], {}, {}, {}
    pen = TTGlyphPen(None)
    pen.moveTo((60, 0)); pen.lineTo((440, 0)); pen.lineTo((440, 700)); pen.lineTo((60, 700)); pen.closePath()
    glyf[".notdef"], hmtx[".notdef"] = pen.glyph(), (500, 60)
    glyf["space"], hmtx["space"] = TTGlyphPen(None).glyph(), (round(4.5 * k), 0)
    for c in lay["cells"]:
        name = f"uni{ord(c['char']):04X}.{c['form']}"
        cmds, adv = glyph(c, mask, guides, nib, k)
        g = draw(cmds)
        names.append(name); glyf[name] = g
        by_char.setdefault(c["char"], {})[c["form"]] = name
        hmtx[name] = (adv, 0)
    # Default (cmap) glyph: the isolated form if written, else any form.
    cmap = {ord(" "): "space"}
    for ch, fs in by_char.items():
        cmap[ord(ch)] = fs.get("isol") or next(iter(fs.values()))

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyf)
    gl = fb.font["glyf"]
    fb.setupHorizontalMetrics({n: (hmtx[n][0], getattr(gl[n], "xMin", 0)) for n in names})
    ymax = max(getattr(gl[n], "yMax", 0) for n in names)
    ymin = min(getattr(gl[n], "yMin", 0) for n in names)
    asc, desc = max(ymax, 900), min(ymin, -400)
    fb.setupHorizontalHeader(ascent=asc, descent=desc)
    fb.setupNameTable({"familyName": FAMILY, "styleName": "Regular", "uniqueFontIdentifier": f"1.000;{PS}",
                       "fullName": f"{FAMILY} Regular", "psName": PS, "version": "Version 1.000"})
    fb.setupOS2(version=4, usWeightClass=400, fsSelection=0xC0, sTypoAscender=asc, sTypoDescender=desc,
                sTypoLineGap=0, usWinAscent=asc, usWinDescent=-desc, achVendID="MIIF", fsType=0)
    fb.setupPost()
    fea = ["languagesystem DFLT dflt;", "languagesystem arab dflt;"]
    for form in ("isol", "init", "medi", "fina"):
        subs = [f"    sub {cmap[ord(ch)]} by {fs[form]};" for ch, fs in by_char.items()
                if form in fs and fs[form] != cmap[ord(ch)]]
        if subs:
            fea += [f"feature {form} {{", *subs, f"}} {form};"]
    addOpenTypeFeaturesFromString(fb.font, "\n".join(fea))
    font = fb.font
    font["OS/2"].recalcUnicodeRanges(font)
    font["OS/2"].recalcCodePageRanges(font)
    path = os.path.join(OUT, "miifont-JawiTest.ttf")
    font.save(path)
    print(f"{len(names) - 2} glyphs -> {path}")
    preview(lay, page, path)


def preview(lay, page, font_path):
    """PNG: the sentence set in the font, next to the freehand word written under the grid."""
    cells = lay["cells"]
    y = max(c["y"] + c["h"] for c in cells) + 22                 # below the printed sentence
    crop = page[int(y * PX):int((lay["safe_area_mm"]["bottom"] - lay["square_mm"] - 1) * PX),
                int(lay["safe_area_mm"]["left"] * PX):int(lay["safe_area_mm"]["right"] * PX)]
    m = ink(crop)
    ys, xs = np.nonzero(m)
    freehand = None
    if len(xs):
        pad = 2 * PX
        crop = crop[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]
        freehand = os.path.join(OUT, "freehand.png")
        cv2.imwrite(freehand, np.where(ink(crop)[..., None], 0, 255).astype(np.uint8).repeat(3, 2))
    words = lay["sentence"].split()
    # Render from a renamed copy: Windows caches glyphs by font name, so a rebuilt
    # font with the same name can still render with the old outlines.
    import time
    from fontTools.ttLib import TTFont
    stamp = str(int(time.time()))
    tmp_font = TTFont(font_path)
    for rec in tmp_font["name"].names:
        if rec.nameID in (1, 3, 4, 6):
            rec.string = rec.toUnicode().replace("JawiTest", "JawiTest" + stamp).replace("Jawi Test", "Jawi Test " + stamp)
    fresh = os.path.join(ROOT, "build", f"jawi-test-{stamp}.ttf")
    tmp_font.save(fresh)
    furl = "file:///" + fresh.replace("\\", "/")
    fh = "" if not freehand else (
        f"<div class=cap>tulisan tangan · freehand</div><img src='file:///{freehand.replace(chr(92), '/')}' "
        f"style='height:{(crop.shape[0] / PX) * 3.2:.0f}px'>")
    html = f"""<!doctype html><meta charset=utf-8><style>
@font-face{{font-family:t;src:url('{furl}')}}
body{{margin:0;padding:40px 56px;background:#fff;font-family:Segoe UI,sans-serif;width:1100px}}
.cap{{color:#888;font-size:15px;margin:26px 0 6px}}
.t{{font-family:t;direction:rtl;text-align:right;color:#111;line-height:1.25}}
.row{{display:flex;gap:60px;align-items:flex-end;justify-content:flex-end}}
</style>
<div class=cap>miifont Jawi Test · {lay['rumi']}</div>
<div class=t style="font-size:96px">{lay['sentence']}</div>
<div class=cap>font · {words[2]}</div><div class=t style="font-size:{27 * 3.2 / 0.7:.0f}px">{words[2]}</div>
{fh}
<div class=cap>teks biasa · body text</div>
<div class=t style="font-size:36px">{' '.join([lay['sentence']] * 2)}</div>"""
    tmp = os.path.join(ROOT, "build", "jawi-test-preview.html")
    open(tmp, "w", encoding="utf-8").write(html)
    png = os.path.join(OUT, "preview.png")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}",
                        "--hide-scrollbars", f"--screenshot={png}", "--window-size=1212,760",
                        "--force-device-scale-factor=2",
                        "--allow-file-access-from-files", "file:///" + tmp.replace("\\", "/")],
                       check=True, capture_output=True, timeout=120)
    print("preview ->", png)


main()
