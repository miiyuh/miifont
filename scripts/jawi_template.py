"""Generate the printable Jawi handwriting templates (HTML + PDF) and their layouts.

Every cell is one glyph to write: a letter in one of its contextual forms
(tunggal/isolated, awal/initial, tengah/medial, akhir/final). Each cell has a
faint guide letter to trace or follow, in Naskh (Noto Naskh Arabic) or Thuluth
(Layla Thuluth), and a faint ladder of pen-width dots showing the alif height.

The cells are strict about length so the letters join cleanly once typed:
- a joining side has a "wall" a short way past the guide letter, with a lane on
  the baseline showing how thick the joining stroke should be; the stroke runs
  along the lane and stops at the wall;
- a side that doesn't join has a limit line at the letter's expected width;
- everything past a wall or limit is hatched: no ink there.
The wall and limit positions go into layout.json, so the scans are cut exactly
where the strokes are meant to end.

Sizes are set for a broad calligraphy nib (NIB mm): letter heights follow the
traditional dot proportions, and cell sizes come from the measured guide
letters. Everything sits inside the Canon PIXMA E470's recommended print area
for A4, so the pages print at 100% without clipping. All guide colours are light
coral, which the scan processing drops, so only black ink is kept.
"""
import base64
import json
import os
import subprocess
import tempfile

import uharfbuzz as hb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "templates", "jawi")
FONT_DIR = os.path.join(OUT, "fonts")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

NIB = 2.0            # calligraphy nib width (mm); one dot (nuqta) is about one nib tall
SLACK = 1.12         # room for handwriting to run a little bigger than the guide

ZWJ = "\u200d"
# Palette: every guide colour has a high red channel, so the scan's black-ink
# filter drops it.
GUIDE = "#eb9c8a"    # lines, brackets, limits
LABEL = "#d9775f"    # labels, reference letters, walls and lanes
GHOST = "#fadcd3"    # faint guide letter and dot ladder
NOINK = "#f6c1b4"    # hatching over no-ink zones past a wall
LIMIT = "#fbe3dc"    # lighter hatching past a non-joining side's limit
INK = "#2b2b2b"      # header text (outside the cells, never part of a glyph)
LATIN = "Segoe UI, sans-serif"

# Guide-letter styles. `dots` = alif height in nib-width dots.
# `missing` = letters the font lacks: (stand-in base letter, note about the dots).
STYLES = {
    "naskh": dict(title="Naskh", family="miifont-guide-naskh", file="NotoNaskhArabic-Regular.ttf",
                  credit="Noto Naskh Arabic (OFL)", dots=5, missing={}),
    "thuluth": dict(title="Thuluth", family="miifont-guide-thuluth", file="LaylaThuluthRegular.ttf",
                    credit="Layla Thuluth, Mohammed Isam (OFL)", dots=9, missing={
                        "ڠ": ("ع", "+ 3 titik di atas"),
                        "ݢ": ("ك", "+ 1 titik di atas"),
                        "ڽ": ("ن", "3 titik, bukan 1"),
                        "ۏ": ("و", "+ 1 titik di atas"),
                    }),
}

# Jawi alphabet order (DBP). Joining type: D = dual, R = right-joining, U = none, C = join-causing.
LETTERS = [
    ("ا", "alif", "R"), ("أ", "alif hamzah atas", "R"), ("إ", "alif hamzah bawah", "R"),
    ("ب", "ba", "D"), ("ت", "ta", "D"), ("ة", "ta marbutah", "R"), ("ث", "sa", "D"),
    ("ج", "jim", "D"), ("چ", "ca", "D"), ("ح", "ha (ح)", "D"), ("خ", "kha", "D"),
    ("د", "dal", "R"), ("ذ", "zal", "R"), ("ر", "ra", "R"), ("ز", "zai", "R"),
    ("س", "sin", "D"), ("ش", "syin", "D"), ("ص", "sad", "D"), ("ض", "dad", "D"),
    ("ط", "ta (ط)", "D"), ("ظ", "za (ظ)", "D"), ("ع", "ain", "D"), ("غ", "ghain", "D"),
    ("ڠ", "nga", "D"), ("ف", "fa", "D"), ("ڤ", "pa", "D"), ("ق", "qaf", "D"),
    ("ك", "kaf", "D"), ("ݢ", "ga", "D"), ("ل", "lam", "D"), ("م", "mim", "D"),
    ("ن", "nun", "D"), ("و", "wau", "R"), ("ؤ", "wau hamzah", "R"), ("ۏ", "va", "R"),
    ("ه", "ha (ه)", "D"), ("ي", "ya", "D"), ("ئ", "ya hamzah", "D"), ("ڽ", "nya", "D"),
    ("ء", "hamzah", "U"), ("لا", "lam alif", "R"), ("ـ", "tatwil", "C"),
    ("،", "koma", "U"), ("؛", "koma bertitik", "U"), ("؟", "tanda soal", "U"),
]
OPTIONAL = [(d, f"angka {i}", "U") for i, d in enumerate("٠١٢٣٤٥٦٧٨٩")]

# form -> (Malay name, joins on the right?, joins on the left?)
# In right-to-left text the right side connects to the previous letter, the left to the next.
FORMS = {"isol": ("tunggal", False, False), "init": ("awal", False, True),
         "medi": ("tengah", True, True), "fina": ("akhir", True, False)}
FORMS_FOR = {"D": ["isol", "init", "medi", "fina"], "R": ["isol", "fina"], "U": ["isol"], "C": ["medi"]}

# --- page: A4 inside the Canon PIXMA E470's recommended print area -------------------
# Canon E470 manual (Print Area > Standard Sizes): printable A4 area 203.2 x 289.0 mm,
# margins 3.0 top, 3.4 left/right; the bottom 16.7 mm can affect quality/feed precision,
# so we stay above it. SAFETY adds a little more on every side.
PW, PH = 210, 297
PRINTER = "Canon PIXMA E470"
MARGIN = dict(top=3.0, bottom=16.7, left=3.4, right=3.4)
SAFETY = 1.6
L, R = MARGIN["left"] + SAFETY, PW - MARGIN["right"] - SAFETY     # usable x range
T, B = MARGIN["top"] + SAFETY, PH - MARGIN["bottom"] - SAFETY     # usable y range
# Registration marks: a bold plus sign in each corner.
MARK_W = MARK_H = 8.0
MARK_ARM = 2.4               # thickness of the plus sign's bars
MARK = MARK_H
MARKS = [(L + MARK_W / 2, T + MARK_H / 2), (R - MARK_W / 2, T + MARK_H / 2),
         (L + MARK_W / 2, B - MARK_H / 2), (R - MARK_W / 2, B - MARK_H / 2)]  # centres: TL, TR, BL, BR
INK_DARK = "#1c1916"
GX, GY = 2.4, 3.0            # gaps between cells
GRID_TOP, GRID_BOTTOM = T + 15, B - MARK - 2
BAND = 6.5                   # label row at the top of each cell
LANE = 1.5                   # joining lane length, in nibs, inside the wall
REACH = 0.8                  # wall distance past the guide letter's edge, in nibs
EDGE = 1.6                   # minimum hatched zone between a wall/limit and the cell edge


def sample(ch, form):
    """Text that shows the wanted contextual form when shaped."""
    if ch == "ـ":
        return ch
    return {"isol": ch, "init": ch + ZWJ, "medi": ZWJ + ch + ZWJ, "fina": ZWJ + ch}[form]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


class Shaper:
    def __init__(self, path):
        self.face = hb.Face(hb.Blob.from_file_path(path))
        self.font = hb.Font(self.face)
        self.upm = self.face.upem

    def ink(self, text):
        """Ink box of shaped text in em: (left, right, top, bottom), x from the text's left edge."""
        buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties()
        hb.shape(self.font, buf)
        x, xs, ys = 0, [], []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            e = self.font.get_glyph_extents(info.codepoint)
            if e and e.width:
                gx = x + pos.x_offset + e.x_bearing
                gy = pos.y_offset + e.y_bearing
                xs += [gx, gx + e.width]; ys += [gy, gy + e.height]
            x += pos.x_advance
        u = self.upm
        return min(xs) / u, max(xs) / u, max(ys) / u, min(ys) / u


# --- per-style geometry ---------------------------------------------------------------
def guide_text(c, style):
    ch = style["missing"].get(c["char"], (c["char"], None))[0]
    return sample(ch, c["form"])


def make_cells():
    out = []
    for ch, name, jt in LETTERS:
        for form in FORMS_FOR[jt]:
            out.append(dict(char=ch, name=name, form=form, optional=False))
    for ch, name, _ in OPTIONAL:
        out.append(dict(char=ch, name=name, form="isol", optional=True))
    for i, c in enumerate(out, 1):
        c["id"] = i
    return out


def geometry(style, shaper, cells):
    """Size the guide letters for the nib, then size cells to fit them."""
    alif_mm = style["dots"] * NIB
    size = alif_mm / shaper.ink("ا")[2]          # font size (mm) giving that alif height
    need, tops, bottoms = {}, [alif_mm], [0.0]
    for c in cells:
        left, right, top, bottom = shaper.ink(guide_text(c, style))
        need[c["id"]] = (right - left) * size * SLACK + 2 * (REACH * NIB + EDGE) + 1.0
        tops.append(top * size); bottoms.append(-bottom * size)
    widths = sorted(need.values())
    unit = widths[int(len(widths) * 0.8)]        # most cells fit one unit, the rest get two
    content_w = R - L
    cols = max(1, int((content_w + GX) // (unit + GX)))
    unit = (content_w - (cols - 1) * GX) / cols
    for c in cells:
        c["units"] = 1 if need[c["id"]] <= unit else 2
    above, below = max(tops) + 2.0, max(bottoms) + 2.5
    avail = GRID_BOTTOM - GRID_TOP + GY
    rows = max(1, int(avail // (BAND + above + below + GY)))
    # share the leftover page height out between the rows: more room above and below
    extra = avail / rows - GY - (BAND + above + below)
    above += extra * 0.55; below += extra * 0.45
    base = BAND + above
    desc = base + max(bottoms)
    ch = base + below
    return dict(size=size, alif=alif_mm, unit=unit, cols=cols, rows=rows, ch=ch,
                base=round(base, 2), asc=round(base - alif_mm, 2),
                mid=round(base - round(style["dots"] * 0.4) * NIB, 2), desc=round(desc, 2),
                kashida=shaper.ink("ـ")[2] * size)


def paginate(cells, g):
    """Flow cells into rows (right to left), pulling a narrow cell forward when a
    wide one doesn't fit the end of a row."""
    pages, page, row, used = [], [], 0, 0
    queue = list(cells)
    while queue:
        free = g["cols"] - used
        pick = next((i for i, c in enumerate(queue[:4]) if c["units"] <= free), None)
        if pick is None:
            row, used = row + 1, 0
            if row == g["rows"]:
                pages.append(page); page, row = [], 0
            continue
        c = queue.pop(pick)
        c["slot"] = (row, used)
        used += c["units"]
        page.append(c)
        if used == g["cols"]:
            row, used = row + 1, 0
            if row == g["rows"]:
                pages.append(page); page, row = [], 0
    if page:
        pages.append(page)
    for p, pc in enumerate(pages, 1):
        for c in pc:
            row, start = c.pop("slot")
            w = c["units"] * g["unit"] + (c["units"] - 1) * GX
            c.update(page=p, x=round(L + (g["cols"] - start - c["units"]) * (g["unit"] + GX), 3),
                     y=round(GRID_TOP + row * (g["ch"] + GY), 3), w=round(w, 3), h=round(g["ch"], 3),
                     join_right=FORMS[c["form"]][1], join_left=FORMS[c["form"]][2])
    return pages


def fit_guide(c, w, style, shaper, g):
    """Place the guide letter in a cell of width w and work out the walls and limits.

    Returns (text, font size, x of the text origin, guide dict). Guide x values are
    mm from the cell's left edge: `wall_*` on joining sides, `limit_*` on the others.
    """
    _, jr, jl = FORMS[c["form"]]
    size, base, ch_h = g["size"], g["base"], g["ch"]
    reach = REACH * NIB
    if c["char"] == "ـ":
        return None, size, 0.0, dict(wall_left=round(EDGE + reach, 3), wall_right=round(w - EDGE - reach, 3))
    text = guide_text(c, style)
    il, ir, top, bottom = shaper.ink(text)
    room = w - 2 * (EDGE + reach) - 0.5
    fit = min(size, room / (ir - il), (base - BAND - 0.6) / max(top, 1e-3),
              (ch_h - 0.8 - base) / max(-bottom, 1e-3))
    x = w / 2 - (il + ir) / 2 * fit                       # ink centred in the cell
    ink_l, ink_r = x + il * fit, x + ir * fit
    spare = (ink_r - ink_l) * (SLACK - 1) / 2 + 0.5 * NIB  # room to write a little bigger
    guide = {}
    if jr:
        guide["wall_right"] = round(min(ink_r + reach, w - EDGE), 3)
    else:
        guide["limit_right"] = round(min(ink_r + spare, w - EDGE), 3)
    if jl:
        guide["wall_left"] = round(max(ink_l - reach, EDGE), 3)
    else:
        guide["limit_left"] = round(max(ink_l - spare, EDGE), 3)
    guide["ink_left"], guide["ink_right"] = round(ink_l, 3), round(ink_r, 3)
    return text, fit, x, guide


# --- drawing ----------------------------------------------------------------------------
def defs():
    """Hatch patterns for the no-ink zones."""
    return (f'<defs>'
            f'<pattern id="noink" width="1.6" height="1.6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="1.6" height="1.6" fill="#fff"/><line x1="0" y1="0" x2="0" y2="1.6" stroke="{NOINK}" stroke-width="0.7"/></pattern>'
            f'<pattern id="limit" width="2.4" height="2.4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="2.4" height="2.4" fill="#fff"/><line x1="0" y1="0" x2="0" y2="2.4" stroke="{LIMIT}" stroke-width="0.6"/></pattern>'
            f'</defs>')


def dot_ladder(style, g, x_right):
    """Column of nib-width rhombus dots up to the alif height, ending at x_right."""
    s, h, wd = [], NIB, NIB * 1.15
    cx = x_right - wd / 2 - 0.2
    for i in range(style["dots"]):
        y1 = g["base"] - i * h
        xo = (wd * 0.12) if i % 2 else -(wd * 0.12)     # slight zigzag, as dots are stacked by hand
        s.append(f'<path d="M{cx + xo:.2f},{y1:.2f} l{wd / 2:.2f},{-h / 2:.2f} l{-wd / 2:.2f},{-h / 2:.2f} '
                 f'l{-wd / 2:.2f},{h / 2:.2f} z" fill="{GHOST}"/>')
    return s


def brackets(w, h, arm=3.0, sw=0.4):
    """Corner brackets instead of a closed box."""
    s = []
    for cx, cy, dx, dy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
        s.append(f'<path d="M{cx + dx * arm:.3f},{cy:.3f} L{cx:.3f},{cy:.3f} L{cx:.3f},{cy + dy * arm:.3f}" '
                 f'fill="none" stroke="{GUIDE}" stroke-width="{sw}" stroke-linecap="round"/>')
    return s


def cell_svg(c, x, y, w, style, shaper, g, ink=None):
    """One cell at (x, y). `ink` = extra SVG drawn on top (the instructions page example).

    Returns (svg, guide font size, guide dict)."""
    _, jr, jl = FORMS[c["form"]]
    ch_h, base, k = g["ch"], g["base"], g["kashida"]
    text, fit, tx, guide = fit_guide(c, w, style, shaper, g)
    s = [f'<g transform="translate({x:.3f},{y:.3f})">']
    top = BAND + 0.4
    # no-ink zones
    for side in ("left", "right"):
        if f"wall_{side}" in guide:
            gx, fill = guide[f"wall_{side}"], "url(#noink)"
        else:
            gx, fill = guide[f"limit_{side}"], "url(#limit)"
        x0, x1 = (0, gx) if side == "left" else (gx, w)
        s.append(f'<rect x="{x0:.3f}" y="{top:.3f}" width="{x1 - x0:.3f}" height="{ch_h - top:.3f}" fill="{fill}"/>')
    s += dot_ladder(style, g, w - 0.4)
    # guide letter, with its joining strokes drawn out to the walls
    ky = base - k / 2
    if text is None:   # tatwil: the joining stroke is the whole glyph
        s.append(f'<line x1="{guide["wall_left"]}" y1="{ky:.3f}" x2="{guide["wall_right"]}" y2="{ky:.3f}" '
                 f'stroke="{GHOST}" stroke-width="{k:.3f}"/>')
    else:
        if jr:
            s.append(f'<line x1="{guide["ink_right"] - 0.6:.3f}" y1="{ky:.3f}" x2="{guide["wall_right"]}" y2="{ky:.3f}" '
                     f'stroke="{GHOST}" stroke-width="{k:.3f}"/>')
        if jl:
            s.append(f'<line x1="{guide["wall_left"]}" y1="{ky:.3f}" x2="{guide["ink_left"] + 0.6:.3f}" y2="{ky:.3f}" '
                     f'stroke="{GHOST}" stroke-width="{k:.3f}"/>')
        s.append(f'<text x="{tx:.3f}" y="{base}" direction="ltr" text-anchor="start" font-family="{style["family"]}" '
                 f'font-size="{fit:.3f}" fill="{GHOST}">{esc(text)}</text>')
    # writing lines
    for gy in (g["asc"], g["mid"], g["desc"]):
        s.append(f'<line x1="0.8" y1="{gy}" x2="{w - 0.8:.3f}" y2="{gy}" stroke="{GUIDE}" stroke-width="0.18" '
                 f'stroke-dasharray="0.3 1.1" stroke-linecap="round"/>')
    s.append(f'<line x1="0.8" y1="{base}" x2="{w - 0.8:.3f}" y2="{base}" stroke="{GUIDE}" stroke-width="0.3"/>')
    # walls and joining lanes: the stroke runs inside the lane and stops at the wall
    for side, sign in (("left", 1), ("right", -1)):
        if f"wall_{side}" in guide:
            wx = guide[f"wall_{side}"]
            lane_end = wx + sign * LANE * NIB
            s.append(f'<line x1="{wx:.3f}" y1="{base - k - 2.2:.3f}" x2="{wx:.3f}" y2="{base + 2.2:.3f}" '
                     f'stroke="{LABEL}" stroke-width="0.6" stroke-linecap="round"/>')
            for ly in (base - k, base):
                s.append(f'<line x1="{wx:.3f}" y1="{ly:.3f}" x2="{lane_end:.3f}" y2="{ly:.3f}" '
                         f'stroke="{LABEL}" stroke-width="0.25"/>')
        else:
            lx = guide[f"limit_{side}"]
            s.append(f'<line x1="{lx:.3f}" y1="{top + 0.5:.3f}" x2="{lx:.3f}" y2="{ch_h - 0.5:.3f}" '
                     f'stroke="{GUIDE}" stroke-width="0.22" stroke-dasharray="1 0.8"/>')
    s += brackets(w, ch_h)
    # label row
    form = "pilihan" if c["optional"] else "sambungan" if c["char"] == "ـ" else FORMS[c["form"]][0]
    s.append(f'<text x="1.4" y="4.4" font-family="{LATIN}" font-size="2.3" fill="{LABEL}">'
             f'<tspan font-weight="bold">{c["id"]:03d}</tspan><tspan dx="1.2">{esc(form)}</tspan></text>')
    s.append(f'<text x="{w - 1.6:.3f}" y="5.0" text-anchor="end" font-family="Noto Naskh Arabic, {style["family"]}" '
             f'font-size="4.4" fill="{LABEL}">{esc(sample(c["char"], c["form"]))}</text>')
    s.append(f'<line x1="1.4" y1="{BAND}" x2="{w - 1.4:.3f}" y2="{BAND}" stroke="{GUIDE}" stroke-width="0.18" '
             f'stroke-dasharray="0.3 0.9" stroke-linecap="round"/>')
    note = style["missing"].get(c["char"], (None, None))[1]
    if note:
        s.append(f'<text x="{w / 2}" y="{ch_h - 1.2}" text-anchor="middle" font-family="{LATIN}" '
                 f'font-size="1.9" font-weight="bold" fill="{LABEL}">{esc(note)}</text>')
    if ink:
        s.append(ink)
    s.append("</g>")
    return "\n".join(s), fit, guide


def svg_open():
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW}mm" height="{PH}mm" viewBox="0 0 {PW} {PH}">'
            + defs())


def rhombus(cx, cy, w, h, fill="none", stroke=None, sw=0.35):
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return (f'<path d="M{cx - w / 2:.2f},{cy:.2f} L{cx:.2f},{cy - h / 2:.2f} L{cx + w / 2:.2f},{cy:.2f} '
            f'L{cx:.2f},{cy + h / 2:.2f} Z" fill="{fill}"{st}/>')


def plus(cx, cy, size, arm, fill):
    h, a = size / 2, arm / 2
    return (f'<path d="M{cx - a:.2f},{cy - h:.2f} h{arm:.2f} v{h - a:.2f} h{h - a:.2f} v{arm:.2f} h{-(h - a):.2f} '
            f'v{h - a:.2f} h{-arm:.2f} v{-(h - a):.2f} h{-(h - a):.2f} v{-arm:.2f} h{h - a:.2f} Z" fill="{fill}"/>')


def marks_svg():
    """Registration marks at the corners of the print area: a bold plus sign each."""
    return [plus(x, y, MARK_W, MARK_ARM, INK_DARK) for x, y in MARKS]


def header(page, n_pages, style, title=None):
    """`miifont jawi` and what the sheet is on the left, the page on the right."""
    s = marks_svg()
    x0, x1 = L + MARK_W + 3, R - MARK_W - 3
    what = title or f"{style['title'].lower()}, {NIB:.1f} mm nib"
    s.append(f'<text x="{x0:.2f}" y="{T + 5.2:.2f}" font-family="{LATIN}" font-size="3.8" fill="{INK}">'
             f'<tspan font-weight="600">miifont jawi</tspan>'
             f'<tspan dx="2.2" fill="#8a8580">{esc(what)}</tspan></text>')
    s.append(f'<text x="{x1:.2f}" y="{T + 5.2:.2f}" text-anchor="end" font-family="{LATIN}" font-size="3.8" '
             f'fill="#8a8580">page <tspan fill="{INK}" font-weight="600">{page}</tspan> of {n_pages}</text>')
    # the page number again as three small nuqtas (filled = 1), read when the scans are sorted
    for i in range(3):
        bit = (page >> (2 - i)) & 1
        cx = x1 - 8.6 + i * 4.3
        s.append(rhombus(cx, T + 9.6, 2.6, 2.2, fill=INK_DARK) if bit else
                 rhombus(cx, T + 9.6, 2.6, 2.2, stroke=GUIDE))
    return s


def page_svg(page, n_pages, cells, style, shaper, g, shrunk, title=None):
    s = [svg_open()] + header(page, n_pages, style, title)
    for c in cells:
        svg, fit, guide = cell_svg(c, c["x"], c["y"], c["w"], style, shaper, g)
        c["guide"] = guide
        if fit < g["size"] - 0.01:
            shrunk.append((c["id"], c["char"], c["form"], fit / g["size"]))
        s.append(svg)
    s.append("</svg>")
    return "\n".join(s)


def instructions_svg(n_pages, n_cells, style, shaper, g):
    s = [svg_open()] + marks_svg()
    t = lambda x, y, txt, sz=3.5, weight="normal", fill="#222": s.append(
        f'<text x="{x}" y="{y}" font-family="{LATIN}" font-size="{sz}" font-weight="{weight}" '
        f'fill="{fill}">{esc(txt)}</text>')
    x0 = L + 12
    s.append(f'<text x="{x0}" y="27" font-family="{LATIN}" font-size="9" font-weight="600" fill="{INK}">miifont jawi'
             f'<tspan dx="4" fill="#8a8580" font-weight="normal">{style["title"].lower()}</tspan></text>')
    t(x0, 35, f"{n_cells} cells on {n_pages} pages. A {NIB:.1f} mm nib and black ink. Alif is "
              f"{style['dots']} dots tall ({g['alif']:.0f} mm). Guide letters from {style['credit']}.", 3.2, fill="#77716b")
    steps = [
        ("Cetak A4 pada saiz sebenar (100%), bukan 'fit to page'.",
         "Print on A4 at actual size (100%), not 'fit to page'."),
        ("Satu huruf satu petak. Huruf pudar cuma panduan, ia tak diimbas.",
         "One letter per cell. The faint letter is only a guide; it isn't scanned."),
        ("Jangan ada dakwat di kawasan berbelang.",
         "Keep ink off the hatched areas."),
        ("Sisi bersambung: tarik garisan di lorong garis dasar dan berhenti di dinding.",
         "Joining side: run the stroke along the lane on the baseline and stop at the wall."),
        ("Sisi tak bersambung: jangan lepas garis putus-putus.",
         "Side that doesn't join: stay inside the dashed line."),
        (f"Duduk di garis dasar. Alif setinggi {style['dots']} titik di tepi petak.",
         f"Sit on the baseline. Alif is as tall as the {style['dots']} dots at the cell's edge."),
        ("Imbas 300 dpi, berwarna, seluruh halaman. Halaman ini tak perlu diimbas.",
         "Scan at 300 dpi in colour, the whole page. Skip this one."),
    ]
    y = 50
    for i, (ms, en) in enumerate(steps, 1):
        t(x0, y, f"{i}", 3.4, "600", LABEL)
        t(x0 + 6, y, ms, 3.3)
        t(x0 + 6, y + 4.6, en, 2.9, fill="#77716b")
        y += 12.4
    t(x0, y + 4, "Contoh", 4.2, "600")
    t(x0 + 15, y + 4, "example", 4.2, fill="#8a8580")
    ex_y, x = y + 9, x0
    for form in ("fina", "medi", "init"):
        c = dict(id=0, char="ب", name="ba", form=form, optional=False)
        units = 2 if form == "fina" else 1
        w = units * g["unit"] + (units - 1) * GX
        # the guide letter itself as the "written" example, in ink
        text, fit, tx, guide = fit_guide(c, w, style, shaper, g)
        k, base = g["kashida"], g["base"]
        ky = base - k / 2
        _, jr, jl = FORMS[form]
        ink = []
        if jr:
            ink.append(f'<line x1="{guide["ink_right"] - 0.6:.3f}" y1="{ky:.3f}" x2="{guide["wall_right"]}" '
                       f'y2="{ky:.3f}" stroke="#111" stroke-width="{k:.3f}"/>')
        if jl:
            ink.append(f'<line x1="{guide["wall_left"]}" y1="{ky:.3f}" x2="{guide["ink_left"] + 0.6:.3f}" '
                       f'y2="{ky:.3f}" stroke="#111" stroke-width="{k:.3f}"/>')
        ink.append(f'<text x="{tx:.3f}" y="{base}" direction="ltr" text-anchor="start" font-family="{style["family"]}" '
                   f'font-size="{fit:.3f}" fill="#111">{esc(text)}</text>')
        svg, _, _ = cell_svg(c, x, ex_y, w, style, shaper, g, ink="\n".join(ink))
        s.append(svg)
        x += w + 5
    if x + 40 < R:
        t(x + 2, ex_y + 14, "Berhenti di dinding.", 3.2, fill="#555")
        t(x + 2, ex_y + 19, "Stop at the wall.", 3.0, fill="#8a8580")
    s.append("</svg>")
    return "\n".join(s)


def print_pdf(html_path, pdf):
    if os.path.exists(pdf):
        os.remove(pdf)
    # A separate profile per run stops a second Chrome from handing off to the first and exiting.
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", f"--print-to-pdf={pdf}",
                        "file:///" + html_path.replace("\\", "/")],
                       check=True, capture_output=True, timeout=180)
    if not os.path.exists(pdf):
        raise SystemExit(f"Chrome did not write {pdf}")


def html_doc(title, svgs, style):
    b64 = lambda f: base64.b64encode(open(os.path.join(FONT_DIR, f), "rb").read()).decode()
    css = (f"@font-face{{font-family:'{style['family']}';src:url(data:font/ttf;base64,{b64(style['file'])})}}"
           f"@font-face{{font-family:'Noto Naskh Arabic';src:url(data:font/ttf;base64,{b64(STYLES['naskh']['file'])})}}"
           "@page{size:A4;margin:0}html,body{margin:0;padding:0}svg{display:block;page-break-after:always}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
            f"<style>{css}</style></head><body>\n" + "\n".join(svgs) + "\n</body></html>")


def layout_dict(key, style, g, pages, cells, **extra):
    return dict(
        note="Scanned pages are numbered 1..pages. All positions are millimetres on A4. Cell guide "
             "y values are from the cell top; each cell's `guide` x values are from the cell's left "
             "edge: `wall_*` is where a joining stroke must stop, `limit_*` the edge of a non-joining side.",
        version=2, style=key, nib_mm=NIB, alif_dots=style["dots"], printer=PRINTER, page_mm=[PW, PH],
        safe_area_mm=dict(left=L, right=R, top=T, bottom=B),
        mark_shape="plus", mark_size_mm=[MARK_W, MARK_H],
        mark_centres_mm=[[round(x, 3), round(y, 3)] for x, y in MARKS],
        cell_guides_mm=dict(label_band=BAND, ascender=g["asc"], middle=g["mid"], baseline=g["base"],
                            descender=g["desc"], kashida=round(g["kashida"], 3)),
        **extra, pages=len(pages), cells=cells)


def build_style(key):
    style = STYLES[key]
    shaper = Shaper(os.path.join(FONT_DIR, style["file"]))
    cells = make_cells()
    g = geometry(style, shaper, cells)
    pages = paginate(cells, g)
    shrunk = []
    svgs = [instructions_svg(len(pages), len(cells), style, shaper, g)]
    svgs += [page_svg(i + 1, len(pages), pc, style, shaper, g, shrunk) for i, pc in enumerate(pages)]
    out = os.path.join(OUT, key)
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, f"miifont-jawi-{key}.html")
    open(html_path, "w", encoding="utf-8").write(html_doc(f"miifont jawi template · {style['title']}", svgs, style))
    pdf = os.path.join(out, f"miifont-jawi-{key}.pdf")
    print_pdf(html_path, pdf)
    json.dump(layout_dict(key, style, g, pages, cells), open(os.path.join(out, "layout.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    wide = sum(c["units"] == 2 for c in cells)
    print(f"{style['title']}: alif {g['alif']:.0f} mm, cells {g['unit']:.1f} x {g['ch']:.1f} mm "
          f"({g['cols']} x {g['rows']} per page, {wide} wide), {len(pages)} pages -> {pdf}")
    if shrunk:
        print("  shrunk to fit:", ", ".join(f"{i:03d} {ch} {f} ({r:.0%})" for i, ch, f, r in shrunk))


def main():
    for key in STYLES:
        build_style(key)


if __name__ == "__main__":
    main()
