"""Generate the printable Jawi handwriting template (HTML + PDF) and its layout.json.

Every cell is one glyph to write: a letter in one of its contextual forms
(tunggal/isolated, awal/initial, tengah/medial, akhir/final). Sides that join
a neighbouring letter carry a tick on the baseline; the joining stroke should
run along the baseline up to that tick. Forms with long tails get double-width
cells. layout.json records every cell's position in millimetres so the scans
can be cut up later. The first page is instructions only and isn't scanned.
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "templates", "jawi")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

ZWJ = "\u200d"
BLUE = "#8ec5e6"     # guides: dropped as "blue" when the scan is processed
LABEL = "#5aa9d6"    # labels: darker, still dropped as "blue"
SHADE = "#e6f3fb"    # no-write zone past a join tick
ARABIC = "Noto Naskh Arabic"
LATIN = "Segoe UI, sans-serif"

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


def sample(ch, form):
    """Text that shows the wanted contextual form when shaped."""
    if ch == "ـ":
        return ch
    return {"isol": ch, "init": ch + ZWJ, "medi": ZWJ + ch + ZWJ, "fina": ZWJ + ch}[form]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def cell_svg(c, x, y, w, example=None):
    """One cell at (x, y), width w. `example` = black text drawn in, for the instructions page."""
    _, jr, jl = FORMS[c["form"]]
    s = [f'<g transform="translate({x:.3f},{y:.3f})">']
    if jr:
        s.append(f'<rect x="{w - TICK}" y="{BAND}" width="{TICK}" height="{CH - BAND}" fill="{SHADE}"/>')
    if jl:
        s.append(f'<rect x="0" y="{BAND}" width="{TICK}" height="{CH - BAND}" fill="{SHADE}"/>')
    s.append(f'<rect x="0" y="0" width="{w}" height="{CH}" rx="1.2" fill="none" stroke="{BLUE}" stroke-width="0.35"/>')
    s.append(f'<line x1="0" y1="{BAND}" x2="{w}" y2="{BAND}" stroke="{BLUE}" stroke-width="0.2"/>')
    for gy in (ASC, MID, DESC):
        s.append(f'<line x1="1" y1="{gy}" x2="{w - 1}" y2="{gy}" stroke="{BLUE}" stroke-width="0.2" stroke-dasharray="0.8 0.8"/>')
    s.append(f'<line x1="0.6" y1="{BASE}" x2="{w - 0.6}" y2="{BASE}" stroke="{BLUE}" stroke-width="0.35"/>')
    for side, on in (("r", jr), ("l", jl)):
        if on:
            tx = w - TICK if side == "r" else TICK
            s.append(f'<line x1="{tx}" y1="{BASE - 2.5}" x2="{tx}" y2="{BASE + 2.5}" stroke="{BLUE}" stroke-width="0.55"/>')
    # label band: id + name + form on the left, the wanted shape on the right
    form = "pilihan" if c["optional"] else "sambungan" if c["char"] == "ـ" else FORMS[c["form"]][0]
    s.append(f'<text font-family="{LATIN}" font-size="1.85" fill="{LABEL}">'
             f'<tspan x="1.2" y="3.0">{c["id"]:03d}</tspan>'
             f'<tspan x="1.2" y="5.6" font-weight="bold">{esc(form)}</tspan></text>')
    s.append(f'<text x="{w - 5}" y="5.6" text-anchor="middle" font-family="{ARABIC}" '
             f'font-size="5.2" fill="{LABEL}">{esc(sample(c["char"], c["form"]))}</text>')
    if example:
        s.append(example)
    s.append("</g>")
    return "\n".join(s)


def header(page, n_pages):
    s = [f'<rect x="{x}" y="{y}" width="{SQ}" height="{SQ}" fill="#000"/>' for x, y in SQUARES]
    for i in range(3):  # page code, 3 bits MSB first: filled = 1
        bit = (page >> (2 - i)) & 1
        x = PW / 2 - 13 + i * 10
        s.append(f'<rect x="{x}" y="9" width="5" height="5" '
                 + ('fill="#000"/>' if bit else f'fill="none" stroke="{BLUE}" stroke-width="0.4"/>'))
    s.append(f'<text x="{PW/2}" y="19" text-anchor="middle" font-family="{LATIN}" font-size="2.8" fill="#999">'
             f'miifont jawi · halaman {page} / {n_pages} · pen hitam · sambung di garis dasar hingga ke tanda |</text>')
    return s


def page_svg(page, n_pages, cells):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW}mm" height="{PH}mm" viewBox="0 0 {PW} {PH}">']
    s += header(page, n_pages)
    for c in cells:
        row, start = c["slot"]
        w = c["units"] * UW + (c["units"] - 1) * GX
        # right to left: unit 0 is the rightmost column
        x = X0 + (COLS - start - c["units"]) * (UW + GX)
        y = Y0 + row * (CH + GY)
        c.update(page=page, x=round(x, 3), y=round(y, 3), w=w, h=CH,
                 join_right=FORMS[c["form"]][1], join_left=FORMS[c["form"]][2])
        del c["slot"]
        s.append(cell_svg(c, x, y, w))
    s.append("</svg>")
    return "\n".join(s)


def instructions_svg(n_pages, n_cells):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW}mm" height="{PH}mm" viewBox="0 0 {PW} {PH}">']
    t = lambda x, y, txt, size=3.6, weight="normal", fill="#222", anchor="start": s.append(
        f'<text x="{x}" y="{y}" font-family="{LATIN}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{esc(txt)}</text>')
    t(20, 32, "miifont · templat jawi", 9, "bold")
    t(20, 40, f"{n_cells} petak di {n_pages} halaman · {n_cells} cells on {n_pages} pages", 3.6, fill="#777")
    steps = [
        ("Cetak pada A4, skala 100% (jangan 'fit to page').",
         "Print on A4 at 100% scale (not 'fit to page')."),
        ("Guna pen hitam, mata sederhana. Satu huruf dalam setiap petak.",
         "Use a black, medium-tip pen. One letter per cell."),
        ("Tulis bentuk yang ditunjukkan di penjuru kanan: tunggal, awal, tengah atau akhir.",
         "Write the shape shown in the corner: isolated, initial, medial or final."),
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
    # worked example: medial ba, initial ba, final ba, filled in
    t(20, y + 6, "Contoh · example", 4.5, "bold")
    ex_y = y + 12
    demos = [
        (dict(id=0, char="ب", name="ba", form="fina", optional=False), 2, "ـب"),
        (dict(id=0, char="ب", name="ba", form="medi", optional=False), 1, "ـبـ"),
        (dict(id=0, char="ب", name="ba", form="init", optional=False), 1, "بـ"),
    ]
    x = 20
    for c, units, txt in demos:
        w = units * UW + (units - 1) * GX
        # Draw the example letter, then a baseline stroke out to each join tick.
        _, jr, jl = FORMS[c["form"]]
        ink = [f'<text x="{w / 2}" y="{BASE}" text-anchor="middle" font-family="{ARABIC}" font-size="17" fill="#111">{txt}</text>']
        if jr:
            ink.append(f'<line x1="{w / 2 + 2}" y1="{BASE - 0.35}" x2="{w - TICK}" y2="{BASE - 0.35}" stroke="#111" stroke-width="0.9" stroke-linecap="round"/>')
        if jl:
            ink.append(f'<line x1="{TICK}" y1="{BASE - 0.35}" x2="{w / 2 - 2}" y2="{BASE - 0.35}" stroke="#111" stroke-width="0.9" stroke-linecap="round"/>')
        s.append(cell_svg(c, x, ex_y, w, example="\n".join(ink)))
        x += w + 6
    t(x + 2, ex_y + 14, "garisan sambungan berhenti", 3.2, fill="#555")
    t(x + 2, ex_y + 19, "tepat di tanda |", 3.2, fill="#555")
    t(x + 2, ex_y + 25, "joining strokes stop", 3.0, fill="#888")
    t(x + 2, ex_y + 29.5, "exactly at the | tick", 3.0, fill="#888")
    s.append("</svg>")
    return "\n".join(s)


def main():
    os.makedirs(OUT, exist_ok=True)
    cells = make_cells()
    pages = paginate(cells)
    svgs = [instructions_svg(len(pages), len(cells))]
    svgs += [page_svg(i + 1, len(pages), pc) for i, pc in enumerate(pages)]
    html = ("<!doctype html><html><head><meta charset='utf-8'><title>miifont jawi template</title>"
            "<style>@page{size:A4;margin:0}html,body{margin:0;padding:0}"
            "svg{display:block;page-break-after:always}</style></head><body>\n"
            + "\n".join(svgs) + "\n</body></html>")
    html_path = os.path.join(OUT, "miifont-jawi-template.html")
    open(html_path, "w", encoding="utf-8").write(html)
    layout = dict(
        note="Scanned pages are numbered 1..pages (the instructions sheet is not scanned). "
             "All positions are millimetres on A4; cell guides are relative to the cell top.",
        page_mm=[PW, PH], square_mm=SQ,
        square_centres_mm=[[x + SQ / 2, y + SQ / 2] for x, y in SQUARES],
        cell_guides_mm=dict(label_band=BAND, ascender=ASC, middle=MID, baseline=BASE,
                            descender=DESC, join_tick_inset=TICK),
        pages=len(pages), cells=cells)
    json.dump(layout, open(os.path.join(OUT, "layout.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    pdf = os.path.join(OUT, "miifont-jawi-template.pdf")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", "file:///" + html_path.replace("\\", "/")],
                   check=True, capture_output=True)
    print(f"{len(cells)} cells on {len(pages)} pages (+ instructions) -> {pdf}")


main()
