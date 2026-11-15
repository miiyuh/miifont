"""Generate the printable Jawi handwriting templates (HTML + PDF) and layout.json.

Every cell is one glyph to write: a letter in one of its contextual forms
(tunggal/isolated, awal/initial, tengah/medial, akhir/final). Sides that join
a neighbouring letter carry a tick on the baseline; the joining stroke should
run along the baseline up to that tick. Forms with long tails get double-width
cells. Each cell has a faint guide letter to trace or follow, in one of two
styles: Naskh (Noto Naskh Arabic) or Thuluth (Layla Thuluth). Both styles share
one layout, recorded in layout.json so the scans can be cut up later. The first
page is instructions only and isn't scanned.
"""
import base64
import json
import os
import subprocess

import uharfbuzz as hb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "templates", "jawi")
FONT_DIR = os.path.join(OUT, "fonts")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

ZWJ = "\u200d"
BLUE = "#8ec5e6"     # guides: dropped as "blue" when the scan is processed
LABEL = "#5aa9d6"    # labels: darker, still dropped as "blue"
SHADE = "#e6f3fb"    # no-write zone past a join tick
GHOST = "#cfe6f5"    # faint guide letter: light enough to write over, dropped when scanned
LATIN = "Segoe UI, sans-serif"

# Guide-letter styles. `alif` = target alif height in mm (sets the letter size).
# `missing` = letters the font lacks: (stand-in base letter, note about the dots).
STYLES = {
    "naskh": dict(title="Naskh", family="miifont-guide-naskh", file="NotoNaskhArabic-Regular.ttf",
                  credit="Noto Naskh Arabic (OFL)", alif=15.0, missing={}),
    "thuluth": dict(title="Thuluth", family="miifont-guide-thuluth", file="LaylaThuluthRegular.ttf",
                    credit="Layla Thuluth, Mohammed Isam (OFL)", alif=15.0, missing={
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
# letters whose isolated and final forms have long tails -> double-width cells
WIDE = set("بتثفڤسشصضقكݢنيئڽ")

# form -> (Malay name, joins on the right?, joins on the left?)
# In right-to-left text the right side connects to the previous letter, the left to the next.
FORMS = {"isol": ("tunggal", False, False), "init": ("awal", False, True),
         "medi": ("tengah", True, True), "fina": ("akhir", True, False)}
FORMS_FOR = {"D": ["isol", "init", "medi", "fina"], "R": ["isol", "fina"], "U": ["isol"], "C": ["medi"]}

# Page geometry (mm)
PW, PH = 210, 297
SQ = 8
SQUARES = [(8, 8), (PW - 8 - SQ, 8), (8, 281), (PW - 8 - SQ, 281)]  # top-left corners
COLS, ROWS = 7, 6
UW, GX = 22, 2          # unit cell width, gap
CH, GY = 40, 2.5        # cell height, gap
X0 = (PW - (COLS * UW + (COLS - 1) * GX)) / 2
Y0 = 24
# inside a cell (mm from the cell top)
BAND = 7.5              # label band
ASC, MID, BASE, DESC = 12, 21.5, 28, 36.5
TICK = 3.0              # join tick inset from the cell edge


# --- cells and pages -------------------------------------------------------------
def make_cells():
    out = []
    for ch, name, jt in LETTERS:
        for form in FORMS_FOR[jt]:
            wide = ch in WIDE and form in ("isol", "fina")
            out.append(dict(char=ch, name=name, form=form, units=2 if wide else 1, optional=False))
    for ch, name, _ in OPTIONAL:
        out.append(dict(char=ch, name=name, form="isol", units=1, optional=True))
    for i, c in enumerate(out, 1):
        c["id"] = i
    return out


def paginate(cells):
    """Flow cells into rows of COLS units (right to left), pulling a narrow cell
    forward when a wide one doesn't fit the end of a row."""
    pages, page, row, used = [], [], 0, 0
    queue = list(cells)
    while queue:
        free = COLS - used
        pick = next((i for i, c in enumerate(queue[:4]) if c["units"] <= free), None)
        if pick is None:
            row, used = row + 1, 0
            if row == ROWS:
                pages.append(page); page, row = [], 0
            continue
        c = queue.pop(pick)
        c["slot"] = (row, used)
        used += c["units"]
        page.append(c)
        if used == COLS:
            row, used = row + 1, 0
            if row == ROWS:
                pages.append(page); page, row = [], 0
    if page:
        pages.append(page)
    return pages


def place(pages):
    """Give every cell its page position (mm)."""
    for p, cells in enumerate(pages, 1):
        for c in cells:
            row, start = c.pop("slot")
            w = c["units"] * UW + (c["units"] - 1) * GX
            # right to left: unit 0 is the rightmost column
            c.update(page=p, x=round(X0 + (COLS - start - c["units"]) * (UW + GX), 3),
                     y=round(Y0 + row * (CH + GY), 3), w=w, h=CH,
                     join_right=FORMS[c["form"]][1], join_left=FORMS[c["form"]][2])


def sample(ch, form):
    """Text that shows the wanted contextual form when shaped."""
    if ch == "ـ":
        return ch
    return {"isol": ch, "init": ch + ZWJ, "medi": ZWJ + ch + ZWJ, "fina": ZWJ + ch}[form]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


# --- measuring guide letters -------------------------------------------------------
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


def ghost_svg(c, w, style, shaper, size):
    """Faint guide letter on the baseline, plus faint joining strokes out to the ticks."""
    _, jr, jl = FORMS[c["form"]]
    ch, note = c["char"], None
    if ch in style["missing"]:
        ch, note = style["missing"][ch]
    k_top = shaper.ink("ـ")[2] * size          # kashida thickness in this style
    ky = BASE - k_top / 2
    s = []
    if ch == "ـ":
        s.append(f'<line x1="{TICK}" y1="{ky:.3f}" x2="{w - TICK}" y2="{ky:.3f}" stroke="{GHOST}" stroke-width="{k_top:.3f}"/>')
        return s, size
    text = sample(ch, c["form"])
    left, right, top, bottom = shaper.ink(text)
    # fit inside the writing area (between the ticks), shrinking only if needed
    room_w = w - 2 * TICK - 1.5
    fit = min(size, room_w / (right - left), (BASE - BAND - 0.6) / max(top, 1e-3),
              (CH - 0.8 - BASE) / max(-bottom, 1e-3))
    x = w / 2 - (left + right) / 2 * fit                 # ink centred in the cell
    if jr:
        s.append(f'<line x1="{x + right * fit - 0.6:.3f}" y1="{ky:.3f}" x2="{w - TICK}" y2="{ky:.3f}" '
                 f'stroke="{GHOST}" stroke-width="{k_top:.3f}"/>')
    if jl:
        s.append(f'<line x1="{TICK}" y1="{ky:.3f}" x2="{x + left * fit + 0.6:.3f}" y2="{ky:.3f}" '
                 f'stroke="{GHOST}" stroke-width="{k_top:.3f}"/>')
    s.append(f'<text x="{x:.3f}" y="{BASE}" direction="ltr" text-anchor="start" font-family="{style["family"]}" '
             f'font-size="{fit:.3f}" fill="{GHOST}">{esc(text)}</text>')
    if note:
        s.append(f'<text x="{w / 2}" y="{CH - 1.2}" text-anchor="middle" font-family="{LATIN}" '
                 f'font-size="1.9" font-weight="bold" fill="{LABEL}">{esc(note)}</text>')
    return s, fit


# --- drawing -----------------------------------------------------------------------
def cell_svg(c, x, y, w, style, shaper, size, ink=None):
    """One cell at (x, y). `ink` = extra SVG drawn on top (the instructions page example)."""
    _, jr, jl = FORMS[c["form"]]
    s = [f'<g transform="translate({x:.3f},{y:.3f})">']
    if jr:
        s.append(f'<rect x="{w - TICK}" y="{BAND}" width="{TICK}" height="{CH - BAND}" fill="{SHADE}"/>')
    if jl:
        s.append(f'<rect x="0" y="{BAND}" width="{TICK}" height="{CH - BAND}" fill="{SHADE}"/>')
    ghost, fit = ghost_svg(c, w, style, shaper, size)
    s += ghost
    s.append(f'<rect x="0" y="0" width="{w}" height="{CH}" rx="1.2" fill="none" stroke="{BLUE}" stroke-width="0.35"/>')
    s.append(f'<line x1="0" y1="{BAND}" x2="{w}" y2="{BAND}" stroke="{BLUE}" stroke-width="0.2"/>')
    for gy in (ASC, MID, DESC):
        s.append(f'<line x1="1" y1="{gy}" x2="{w - 1}" y2="{gy}" stroke="{BLUE}" stroke-width="0.2" stroke-dasharray="0.8 0.8"/>')
    s.append(f'<line x1="0.6" y1="{BASE}" x2="{w - 0.6}" y2="{BASE}" stroke="{BLUE}" stroke-width="0.35"/>')
    for side, on in (("r", jr), ("l", jl)):
        if on:
            tx = w - TICK if side == "r" else TICK
            s.append(f'<line x1="{tx}" y1="{BASE - 2.5}" x2="{tx}" y2="{BASE + 2.5}" stroke="{BLUE}" stroke-width="0.55"/>')
    # label band: id + form on the left, the wanted shape on the right (in the guide style)
    form = "pilihan" if c["optional"] else "sambungan" if c["char"] == "ـ" else FORMS[c["form"]][0]
    s.append(f'<text font-family="{LATIN}" font-size="1.85" fill="{LABEL}">'
             f'<tspan x="1.2" y="3.0">{c["id"]:03d}</tspan>'
             f'<tspan x="1.2" y="5.6" font-weight="bold">{esc(form)}</tspan></text>')
    s.append(f'<text x="{w - 5}" y="5.6" text-anchor="middle" font-family="Noto Naskh Arabic, {style["family"]}" '
             f'font-size="5.2" fill="{LABEL}">{esc(sample(c["char"], c["form"]))}</text>')
    if ink:
        s.append(ink)
    s.append("</g>")
    return "\n".join(s), fit


def header(page, n_pages, style):
    s = [f'<rect x="{x}" y="{y}" width="{SQ}" height="{SQ}" fill="#000"/>' for x, y in SQUARES]
    for i in range(3):  # page code, 3 bits MSB first: filled = 1
        bit = (page >> (2 - i)) & 1
        x = PW / 2 - 13 + i * 10
        s.append(f'<rect x="{x}" y="9" width="5" height="5" '
                 + ('fill="#000"/>' if bit else f'fill="none" stroke="{BLUE}" stroke-width="0.4"/>'))
    s.append(f'<text x="{PW/2}" y="19" text-anchor="middle" font-family="{LATIN}" font-size="2.8" fill="#999">'
             f'miifont jawi · {style["title"].lower()} · halaman {page} / {n_pages} · pen hitam · '
             f'sambung di garis dasar hingga ke tanda |</text>')
    return s


def svg_open():
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW}mm" height="{PH}mm" viewBox="0 0 {PW} {PH}">'


def page_svg(page, n_pages, cells, style, shaper, size, shrunk):
    s = [svg_open()] + header(page, n_pages, style)
    for c in cells:
        svg, fit = cell_svg(c, c["x"], c["y"], c["w"], style, shaper, size)
        if fit < size - 0.01:
            shrunk.append((c["id"], c["char"], c["form"], round(fit / size, 2)))
        s.append(svg)
    s.append("</svg>")
    return "\n".join(s)


def instructions_svg(n_pages, n_cells, style, shaper, size):
    s = [svg_open()]
    t = lambda x, y, txt, sz=3.6, weight="normal", fill="#222": s.append(
        f'<text x="{x}" y="{y}" font-family="{LATIN}" font-size="{sz}" font-weight="{weight}" '
        f'fill="{fill}">{esc(txt)}</text>')
    t(20, 32, f"miifont · templat jawi · {style['title']}", 9, "bold")
    t(20, 40, f"{n_cells} petak di {n_pages} halaman · {n_cells} cells on {n_pages} pages · "
              f"huruf panduan: {style['credit']}", 3.4, fill="#777")
    steps = [
        ("Cetak pada A4, skala 100% (jangan 'fit to page').",
         "Print on A4 at 100% scale (not 'fit to page')."),
        ("Guna pen hitam, mata sederhana. Satu huruf dalam setiap petak.",
         "Use a black, medium-tip pen. One letter per cell."),
        ("Huruf pudar ialah panduan: surih, atau tulis gaya sendiri di atasnya. Ia tidak akan diimbas.",
         "The faint letter is a guide: trace it, or write your own way over it. It won't be scanned."),
        ("Duduk di atas garis dasar (garis penuh). Garis putus-putus: tinggi badan huruf, alif, ekor.",
         "Sit on the baseline (solid line). Dashed lines: body height, alif height, tail depth."),
        ("Bila ada tanda |, sambung garisan di garis dasar tepat hingga ke tanda itu, jangan lebih.",
         "Where there is a | tick, run the joining stroke along the baseline right up to it, no further."),
        ("Saiz huruf yang sama di semua petak. Titik dan hamzah ditulis seperti biasa.",
         "Keep the same letter size in every cell. Write dots and hamzah as you normally do."),
        ("Petak 'pilihan' (angka Arab) boleh dibiarkan kosong.",
         "Cells marked 'pilihan' (Arabic digits) are optional."),
        ("Imbas setiap halaman pada 300 dpi, berwarna, seluruh halaman termasuk empat segi empat hitam.",
         "Scan every page at 300 dpi in colour, the whole page including all four black squares."),
        ("Halaman ini tidak perlu diimbas.", "This page doesn't need to be scanned."),
    ]
    y = 54
    for i, (ms, en) in enumerate(steps, 1):
        t(20, y, f"{i}.", 3.8, "bold")
        t(27, y, ms, 3.8)
        t(27, y + 5, en, 3.3, fill="#777")
        y += 13
    # worked example: final, medial and initial ba, "written" over the guides
    t(20, y + 6, "Contoh · example", 4.5, "bold")
    ex_y, x = y + 12, 20
    for form, units in (("fina", 2), ("medi", 1), ("init", 1)):
        c = dict(id=0, char="ب", name="ba", form=form, optional=False)
        w = units * UW + (units - 1) * GX
        ghost, fit = ghost_svg(c, w, style, shaper, size)
        ink = "\n".join(g.replace(GHOST, "#111") for g in ghost)
        svg, _ = cell_svg(c, x, ex_y, w, style, shaper, size, ink=ink)
        s.append(svg)
        x += w + 6
    t(x + 2, ex_y + 14, "garisan sambungan berhenti", 3.2, fill="#555")
    t(x + 2, ex_y + 19, "tepat di tanda |", 3.2, fill="#555")
    t(x + 2, ex_y + 25, "joining strokes stop", 3.0, fill="#888")
    t(x + 2, ex_y + 29.5, "exactly at the | tick", 3.0, fill="#888")
    s.append("</svg>")
    return "\n".join(s)


def build_style(key, pages, n_cells):
    style = STYLES[key]
    font_path = os.path.join(FONT_DIR, style["file"])
    shaper = Shaper(font_path)
    size = style["alif"] / shaper.ink("ا")[2]   # font size (mm) that makes alif `alif` mm tall
    shrunk = []
    svgs = [instructions_svg(len(pages), n_cells, style, shaper, size)]
    svgs += [page_svg(i + 1, len(pages), pc, style, shaper, size, shrunk) for i, pc in enumerate(pages)]
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
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", "file:///" + html_path.replace("\\", "/")],
                   check=True, capture_output=True)
    print(f"{style['title']}: guide size {size:.1f}mm -> {pdf}")
    if shrunk:
        print("  shrunk to fit:", ", ".join(f"{i:03d} {ch} {f} ({r:.0%})" for i, ch, f, r in shrunk))


def main():
    cells = make_cells()
    pages = paginate(cells)
    place(pages)
    for key in STYLES:
        build_style(key, pages, len(cells))
    layout = dict(
        note="Scanned pages are numbered 1..pages (the instructions sheet is not scanned). "
             "All positions are millimetres on A4; cell guides are relative to the cell top. "
             "Both guide styles (naskh, thuluth) share this layout.",
        page_mm=[PW, PH], square_mm=SQ,
        square_centres_mm=[[x + SQ / 2, y + SQ / 2] for x, y in SQUARES],
        cell_guides_mm=dict(label_band=BAND, ascender=ASC, middle=MID, baseline=BASE,
                            descender=DESC, join_tick_inset=TICK),
        pages=len(pages), cells=cells)
    json.dump(layout, open(os.path.join(OUT, "layout.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


main()
