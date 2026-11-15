"""Generate the printable Jawi handwriting templates (HTML + PDF) and their layouts.

Every cell is one glyph to write: a letter in one of its contextual forms
(tunggal/isolated, awal/initial, tengah/medial, akhir/final). Sides that join
a neighbouring letter carry a tick on the baseline; the joining stroke should
run along the baseline up to that tick. Each cell has a faint guide letter to
trace or follow, in Naskh (Noto Naskh Arabic) or Thuluth (Layla Thuluth), and a
faint ladder of pen-width dots showing the alif height.

Sizes are set for a broad calligraphy nib (NIB mm): letter heights follow the
traditional dot proportions, and cell sizes are worked out from the measured
guide letters. Everything sits inside the Canon PIXMA E470's recommended print
area for A4, so the pages print at 100% without clipping. Each style gets its
own layout.json so the scans can be cut up later. The first page is
instructions only and isn't scanned.
"""
import base64
import json
import math
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
BLUE = "#8ec5e6"     # guides: dropped as "blue" when the scan is processed
LABEL = "#5aa9d6"    # labels: darker, still dropped as "blue"
SHADE = "#e6f3fb"    # no-write zone past a join tick
GHOST = "#cfe6f5"    # faint guide letter and dot ladder: dropped when scanned
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
SQ = 8
SQUARES = [(L, T), (R - SQ, T), (L, B - SQ), (R - SQ, B - SQ)]    # top-left corners
GX, GY = 2.0, 2.5            # gaps between cells
GRID_TOP, GRID_BOTTOM = T + 16, B - SQ - 2
BAND = 7.5                   # label band at the top of each cell
TICK = 3.2                   # join tick inset from the cell edge


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
        need[c["id"]] = (right - left) * size * SLACK + 2 * TICK + 2.0
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
                mid=round(base - round(style["dots"] * 0.4) * NIB, 2), desc=round(desc, 2))


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


# --- drawing ----------------------------------------------------------------------------
def ghost_svg(c, w, style, shaper, g):
    """Faint guide letter on the baseline, plus faint joining strokes out to the ticks."""
    _, jr, jl = FORMS[c["form"]]
    size, base, ch_h = g["size"], g["base"], g["ch"]
    note = style["missing"].get(c["char"], (None, None))[1]
    k = shaper.ink("ـ")[2] * size              # kashida thickness in this style
    ky = base - k / 2
    s = []
    if c["char"] == "ـ":
        s.append(f'<line x1="{TICK}" y1="{ky:.3f}" x2="{w - TICK}" y2="{ky:.3f}" stroke="{GHOST}" stroke-width="{k:.3f}"/>')
        return s, size
    text = guide_text(c, style)
    left, right, top, bottom = shaper.ink(text)
    room_w = w - 2 * TICK - 1.5
    fit = min(size, room_w / (right - left), (base - BAND - 0.6) / max(top, 1e-3),
              (ch_h - 0.8 - base) / max(-bottom, 1e-3))
    x = w / 2 - (left + right) / 2 * fit                 # ink centred in the cell
    if jr:
        s.append(f'<line x1="{x + right * fit - 0.6:.3f}" y1="{ky:.3f}" x2="{w - TICK}" y2="{ky:.3f}" '
                 f'stroke="{GHOST}" stroke-width="{k:.3f}"/>')
    if jl:
        s.append(f'<line x1="{TICK}" y1="{ky:.3f}" x2="{x + left * fit + 0.6:.3f}" y2="{ky:.3f}" '
                 f'stroke="{GHOST}" stroke-width="{k:.3f}"/>')
    s.append(f'<text x="{x:.3f}" y="{base}" direction="ltr" text-anchor="start" font-family="{style["family"]}" '
             f'font-size="{fit:.3f}" fill="{GHOST}">{esc(text)}</text>')
    if note:
        s.append(f'<text x="{w / 2}" y="{ch_h - 1.0}" text-anchor="middle" font-family="{LATIN}" '
                 f'font-size="1.9" font-weight="bold" fill="{LABEL}">{esc(note)}</text>')
    return s, fit


def dot_ladder(style, g):
    """Column of nib-width rhombus dots up to the alif height, at the cell's left edge."""
    s, h, wd = [], NIB, NIB * 1.15
    cx = 0.25 + wd / 2
    for i in range(style["dots"]):
        y1 = g["base"] - i * h
        xo = (wd * 0.12) if i % 2 else -(wd * 0.12)     # slight zigzag, as dots are stacked by hand
        s.append(f'<path d="M{cx + xo:.2f},{y1:.2f} l{wd / 2:.2f},{-h / 2:.2f} l{-wd / 2:.2f},{-h / 2:.2f} '
                 f'l{-wd / 2:.2f},{h / 2:.2f} z" fill="{GHOST}"/>')
    return s


def cell_svg(c, x, y, w, style, shaper, g, ink=None):
    """One cell at (x, y). `ink` = extra SVG drawn on top (the instructions page example)."""
    _, jr, jl = FORMS[c["form"]]
    ch_h, base = g["ch"], g["base"]
    s = [f'<g transform="translate({x:.3f},{y:.3f})">']
    if jr:
        s.append(f'<rect x="{w - TICK}" y="{BAND}" width="{TICK}" height="{ch_h - BAND}" fill="{SHADE}"/>')
    if jl:
        s.append(f'<rect x="0" y="{BAND}" width="{TICK}" height="{ch_h - BAND}" fill="{SHADE}"/>')
    s += dot_ladder(style, g)
    ghost, fit = ghost_svg(c, w, style, shaper, g)
    s += ghost
    s.append(f'<rect x="0" y="0" width="{w:.3f}" height="{ch_h:.3f}" rx="1.2" fill="none" stroke="{BLUE}" stroke-width="0.35"/>')
    s.append(f'<line x1="0" y1="{BAND}" x2="{w:.3f}" y2="{BAND}" stroke="{BLUE}" stroke-width="0.2"/>')
    for gy in (g["asc"], g["mid"], g["desc"]):
        s.append(f'<line x1="3.2" y1="{gy}" x2="{w - 1:.3f}" y2="{gy}" stroke="{BLUE}" stroke-width="0.2" stroke-dasharray="0.8 0.8"/>')
    s.append(f'<line x1="0.6" y1="{base}" x2="{w - 0.6:.3f}" y2="{base}" stroke="{BLUE}" stroke-width="0.35"/>')
    for side, on in (("r", jr), ("l", jl)):
        if on:
            tx = w - TICK if side == "r" else TICK
            s.append(f'<line x1="{tx:.3f}" y1="{base - 2.5}" x2="{tx:.3f}" y2="{base + 2.5}" stroke="{BLUE}" stroke-width="0.55"/>')
    form = "pilihan" if c["optional"] else "sambungan" if c["char"] == "ـ" else FORMS[c["form"]][0]
    s.append(f'<text font-family="{LATIN}" font-size="1.85" fill="{LABEL}">'
             f'<tspan x="1.2" y="3.0">{c["id"]:03d}</tspan>'
             f'<tspan x="1.2" y="5.6" font-weight="bold">{esc(form)}</tspan></text>')
    s.append(f'<text x="{w - 5:.3f}" y="5.6" text-anchor="middle" font-family="Noto Naskh Arabic, {style["family"]}" '
             f'font-size="5.2" fill="{LABEL}">{esc(sample(c["char"], c["form"]))}</text>')
    if ink:
        s.append(ink)
    s.append("</g>")
    return "\n".join(s), fit


def svg_open():
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW}mm" height="{PH}mm" viewBox="0 0 {PW} {PH}">'


def header(page, n_pages, style):
    s = [f'<rect x="{x:.2f}" y="{y:.2f}" width="{SQ}" height="{SQ}" fill="#000"/>' for x, y in SQUARES]
    for i in range(3):  # page code, 3 bits MSB first: filled = 1
        bit = (page >> (2 - i)) & 1
        x = PW / 2 - 13 + i * 10
        s.append(f'<rect x="{x}" y="{T + 1.5:.2f}" width="5" height="5" '
                 + ('fill="#000"/>' if bit else f'fill="none" stroke="{BLUE}" stroke-width="0.4"/>'))
    s.append(f'<text x="{PW/2}" y="{T + 11.5:.2f}" text-anchor="middle" font-family="{LATIN}" font-size="2.8" fill="#999">'
             f'miifont jawi · {style["title"].lower()} · halaman {page} / {n_pages} · pen {NIB:.1f} mm · '
             f'sambung di garis dasar hingga ke tanda |</text>')
    return s


def page_svg(page, n_pages, cells, style, shaper, g, shrunk):
    s = [svg_open()] + header(page, n_pages, style)
    for c in cells:
        svg, fit = cell_svg(c, c["x"], c["y"], c["w"], style, shaper, g)
        if fit < g["size"] - 0.01:
            shrunk.append((c["id"], c["char"], c["form"], fit / g["size"]))
        s.append(svg)
    s.append("</svg>")
    return "\n".join(s)


def instructions_svg(n_pages, n_cells, style, shaper, g):
    s = [svg_open()]
    t = lambda x, y, txt, sz=3.5, weight="normal", fill="#222": s.append(
        f'<text x="{x}" y="{y}" font-family="{LATIN}" font-size="{sz}" font-weight="{weight}" '
        f'fill="{fill}">{esc(txt)}</text>')
    x0 = L + 12
    t(x0, 24, f"miifont · templat jawi · {style['title']}", 9, "bold")
    t(x0, 31.5, f"{n_cells} petak di {n_pages} halaman · pen kaligrafi {NIB:.1f} mm · "
                f"alif {style['dots']} titik ({g['alif']:.0f} mm) · huruf panduan: {style['credit']}", 3.2, fill="#777")
    steps = [
        ("Cetak pada A4, skala 100% / saiz sebenar ('Actual size'). Jangan 'fit to page'.",
         f"Print on A4 at 100% / actual size, not 'fit to page'. Everything fits the {PRINTER} print area."),
        (f"Guna pen kaligrafi {NIB:.1f} mm dengan dakwat hitam. Satu huruf dalam setiap petak.",
         f"Use a {NIB:.1f} mm calligraphy pen with black ink. One letter per cell."),
        ("Huruf pudar ialah panduan: surih, atau tulis gaya sendiri di atasnya. Ia tidak akan diimbas.",
         "The faint letter is a guide: trace it, or write your own way over it. It won't be scanned."),
        (f"Titik-titik di tepi kiri ialah ukuran: alif setinggi {style['dots']} titik pen.",
         f"The dots at the left edge are the scale: alif is {style['dots']} pen dots tall."),
        ("Duduk di atas garis dasar (garis penuh). Garis putus-putus: tinggi alif, badan huruf, ekor.",
         "Sit on the baseline (solid line). Dashed lines: alif height, body height, tail depth."),
        ("Bila ada tanda |, sambung garisan di garis dasar tepat hingga ke tanda itu, jangan lebih.",
         "Where there is a | tick, run the joining stroke along the baseline right up to it, no further."),
        ("Biar dakwat kering sebelum mengimbas. Petak 'pilihan' (angka Arab) boleh dibiarkan kosong.",
         "Let the ink dry before scanning. Cells marked 'pilihan' (Arabic digits) are optional."),
        ("Imbas setiap halaman pada 300 dpi, berwarna, seluruh halaman termasuk empat segi empat hitam.",
         "Scan every page at 300 dpi in colour, the whole page including all four black squares."),
        ("Halaman ini tidak perlu diimbas.", "This page doesn't need to be scanned."),
    ]
    y = 45
    for i, (ms, en) in enumerate(steps, 1):
        t(x0, y, f"{i}.", 3.6, "bold")
        t(x0 + 7, y, ms, 3.6)
        t(x0 + 7, y + 4.8, en, 3.1, fill="#777")
        y += 12.5
    t(x0, y + 5, "Contoh · example", 4.5, "bold")
    ex_y, x = y + 10, x0
    for form in ("fina", "medi", "init"):
        c = dict(id=0, char="ب", name="ba", form=form, optional=False)
        units = 2 if form == "fina" else 1
        w = units * g["unit"] + (units - 1) * GX
        ghost, _ = ghost_svg(c, w, style, shaper, g)
        ink = "\n".join(e.replace(GHOST, "#111") for e in ghost)
        svg, _ = cell_svg(c, x, ex_y, w, style, shaper, g, ink=ink)
        s.append(svg)
        x += w + 5
    if x + 40 < R:
        t(x + 2, ex_y + 14, "garisan sambungan berhenti", 3.2, fill="#555")
        t(x + 2, ex_y + 19, "tepat di tanda |", 3.2, fill="#555")
        t(x + 2, ex_y + 25, "joining strokes stop", 3.0, fill="#888")
        t(x + 2, ex_y + 29.5, "exactly at the | tick", 3.0, fill="#888")
    s.append("</svg>")
    return "\n".join(s)


def build_style(key):
    style = STYLES[key]
    font_path = os.path.join(FONT_DIR, style["file"])
    shaper = Shaper(font_path)
    cells = make_cells()
    g = geometry(style, shaper, cells)
    pages = paginate(cells, g)
    shrunk = []
    svgs = [instructions_svg(len(pages), len(cells), style, shaper, g)]
    svgs += [page_svg(i + 1, len(pages), pc, style, shaper, g, shrunk) for i, pc in enumerate(pages)]
    font_b64 = base64.b64encode(open(font_path, "rb").read()).decode()
    naskh_b64 = base64.b64encode(open(os.path.join(FONT_DIR, STYLES["naskh"]["file"]), "rb").read()).decode()
    css = (f"@font-face{{font-family:'{style['family']}';src:url(data:font/ttf;base64,{font_b64})}}"
           f"@font-face{{font-family:'Noto Naskh Arabic';src:url(data:font/ttf;base64,{naskh_b64})}}"
           "@page{size:A4;margin:0}html,body{margin:0;padding:0}svg{display:block;page-break-after:always}")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>miifont jawi template · {style['title']}</title>"
            f"<style>{css}</style></head><body>\n" + "\n".join(svgs) + "\n</body></html>")
    out = os.path.join(OUT, key)
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, f"miifont-jawi-{key}.html")
    open(html_path, "w", encoding="utf-8").write(html)
    pdf = os.path.join(out, f"miifont-jawi-{key}.pdf")
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
    layout = dict(
        note="Scanned pages are numbered 1..pages (the instructions sheet is not scanned). "
             "All positions are millimetres on A4; cell guides are relative to the cell top.",
        style=key, nib_mm=NIB, alif_dots=style["dots"], printer=PRINTER, page_mm=[PW, PH],
        safe_area_mm=dict(left=L, right=R, top=T, bottom=B), square_mm=SQ,
        square_centres_mm=[[round(x + SQ / 2, 3), round(y + SQ / 2, 3)] for x, y in SQUARES],
        cell_guides_mm=dict(label_band=BAND, ascender=g["asc"], middle=g["mid"], baseline=g["base"],
                            descender=g["desc"], join_tick_inset=TICK),
        pages=len(pages), cells=cells)
    json.dump(layout, open(os.path.join(out, "layout.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    wide = sum(c["units"] == 2 for c in cells)
    print(f"{style['title']}: alif {g['alif']:.0f} mm, cells {g['unit']:.1f} x {g['ch']:.1f} mm "
          f"({g['cols']} x {g['rows']} per page, {wide} wide), {len(pages)} pages -> {pdf}")
    if shrunk:
        print("  shrunk to fit:", ", ".join(f"{i:03d} {ch} {f} ({r:.0%})" for i, ch, f, r in shrunk))


def main():
    for key in STYLES:
        build_style(key)


main()
