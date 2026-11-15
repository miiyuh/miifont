"""One-page Thuluth test sheet: a short Jawi sentence written with a broad nib.

The sentence is split into lines that fit the page. Each line gets two rows:
one with the faint guide words to trace, then an empty row to write it
freehand. Guides, labels and the dot ladder are light blue and drop out when
the scan is processed. Writes HTML, PDF and a layout.json (row boxes in mm).
"""
import base64
import json
import os
import subprocess
import tempfile

from jawi_template import (B, BLUE, CHROME, FONT_DIR, GHOST, LABEL, LATIN, MARGIN, OUT, PH, PW, SQ,
                           SQUARES, STYLES, L, R, Shaper, T, esc, svg_open)

NIB = 3.0                     # calligraphy nib width (mm)
STYLE = STYLES["thuluth"]
DOTS = STYLE["dots"]          # alif height in nib dots
SLACK = 1.05                  # spare width for handwriting (rows are full width, so less is needed)
SENTENCE = "ساي سوك منوليس جاوي"
RUMI = "saya suka menulis jawi"
LINES = ["ساي سوك", "منوليس جاوي"]   # how the sentence is split; each must fit one row

BAND = 6.0                    # label band at the top of each row
LADDER = 6.0                  # width reserved for the dot ladder at the right edge
GAP = 3.0                     # gap between rows
TOP = T + 16                  # below the header
BOTTOM = B - SQ - 2           # above the bottom squares


def ladder(x_right, base):
    """Column of nib-width rhombus dots up to the alif height."""
    s, h, wd = [], NIB, NIB * 1.15
    cx = x_right - 0.3 - wd / 2
    for i in range(DOTS):
        y1 = base - i * h
        xo = (wd * 0.12) if i % 2 else -(wd * 0.12)
        s.append(f'<path d="M{cx + xo:.2f},{y1:.2f} l{wd / 2:.2f},{-h / 2:.2f} l{-wd / 2:.2f},{-h / 2:.2f} '
                 f'l{-wd / 2:.2f},{h / 2:.2f} z" fill="{GHOST}"/>')
    return s


def main():
    font_path = os.path.join(FONT_DIR, STYLE["file"])
    shaper = Shaper(font_path)
    assert "".join(LINES).replace(" ", "") == SENTENCE.replace(" ", "")
    alif = DOTS * NIB
    size = alif / shaper.ink("ا")[2]                  # font size (mm) giving that alif height
    inks = [shaper.ink(line) for line in LINES]
    top = max(i[2] for i in inks) * size
    bottom = -min(i[3] for i in inks) * size
    width = R - L - LADDER - 2
    for line, (lft, rgt, _, _) in zip(LINES, inks):
        need = (rgt - lft) * size * SLACK
        if need > width:
            raise SystemExit(f"'{line}' needs {need:.0f} mm, row has {width:.0f} mm: split it further")

    n_rows = 2 * len(LINES)
    row_h = (BOTTOM - TOP - (n_rows - 1) * GAP) / n_rows
    spare = row_h - BAND - top - bottom
    if spare < 4:
        raise SystemExit(f"rows too short by {4 - spare:.1f} mm")
    base = BAND + top + spare * 0.55                 # baseline, from the row top
    guides = dict(ascender=round(base - alif, 2), middle=round(base - round(DOTS * 0.4) * NIB, 2),
                  baseline=round(base, 2), descender=round(base + bottom, 2))

    s = [svg_open()]
    s += [f'<rect x="{x:.2f}" y="{y:.2f}" width="{SQ}" height="{SQ}" fill="#000"/>' for x, y in SQUARES]
    s.append(f'<text x="{PW / 2}" y="{T + 5:.2f}" text-anchor="middle" font-family="{LATIN}" font-size="3.4" '
             f'font-weight="bold" fill="#666">miifont jawi · ujian thuluth · pen {NIB:.1f} mm</text>')
    s.append(f'<text x="{PW / 2}" y="{T + 10:.2f}" text-anchor="middle" font-family="{LATIN}" font-size="2.6" '
             f'fill="#999">"{RUMI}" · surih baris pudar, kemudian tulis sendiri di bawahnya · '
             f'cetak 100% · imbas 300 dpi berwarna</text>')

    rows = []
    for i, (line, (lft, rgt, _, _)) in enumerate(zip(LINES, inks)):
        for trace in (True, False):
            y = TOP + len(rows) * (row_h + GAP)
            by = y + base
            s.append(f'<rect x="{L:.2f}" y="{y:.2f}" width="{R - L:.2f}" height="{row_h:.2f}" rx="1.2" '
                     f'fill="none" stroke="{BLUE}" stroke-width="0.35"/>')
            s.append(f'<line x1="{L:.2f}" y1="{y + BAND:.2f}" x2="{R:.2f}" y2="{y + BAND:.2f}" '
                     f'stroke="{BLUE}" stroke-width="0.2"/>')
            for k in ("ascender", "middle", "descender"):
                gy = y + guides[k]
                s.append(f'<line x1="{L + 1:.2f}" y1="{gy:.2f}" x2="{R - LADDER:.2f}" y2="{gy:.2f}" '
                         f'stroke="{BLUE}" stroke-width="0.2" stroke-dasharray="0.8 0.8"/>')
            s.append(f'<line x1="{L + 0.6:.2f}" y1="{by:.2f}" x2="{R - 0.6:.2f}" y2="{by:.2f}" '
                     f'stroke="{BLUE}" stroke-width="0.35"/>')
            s += ladder(R, by)
            label = f"{len(rows) + 1} · {'surih' if trace else 'tulis sendiri'} · baris {i + 1}"
            s.append(f'<text x="{L + 1.5:.2f}" y="{y + 4.2:.2f}" font-family="{LATIN}" font-size="2.4" '
                     f'font-weight="bold" fill="{LABEL}">{esc(label)}</text>')
            s.append(f'<text x="{R - 2:.2f}" y="{y + 4.8:.2f}" text-anchor="end" font-family="Noto Naskh Arabic" '
                     f'font-size="4.6" fill="{LABEL}">{esc(line)}</text>')
            if trace:
                x = R - LADDER - 3 - rgt * size              # right-aligned, as Jawi starts on the right
                s.append(f'<text x="{x:.3f}" y="{by:.3f}" direction="ltr" text-anchor="start" '
                         f'font-family="{STYLE["family"]}" font-size="{size:.3f}" fill="{GHOST}">{esc(line)}</text>')
            rows.append(dict(row=len(rows) + 1, line=i + 1, text=line, trace=trace,
                             x=round(L, 3), y=round(y, 3), w=round(R - L, 3), h=round(row_h, 3)))
    s.append("</svg>")

    b64 = lambda f: base64.b64encode(open(os.path.join(FONT_DIR, f), "rb").read()).decode()
    css = (f"@font-face{{font-family:'{STYLE['family']}';src:url(data:font/ttf;base64,{b64(STYLE['file'])})}}"
           f"@font-face{{font-family:'Noto Naskh Arabic';src:url(data:font/ttf;base64,{b64(STYLES['naskh']['file'])})}}"
           "@page{size:A4;margin:0}html,body{margin:0;padding:0}svg{display:block}")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>miifont jawi test · Thuluth</title>"
            f"<style>{css}</style></head><body>\n" + "\n".join(s) + "\n</body></html>")
    out = os.path.join(OUT, "test")
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, "miifont-jawi-test-thuluth.html")
    open(html_path, "w", encoding="utf-8").write(html)
    pdf = html_path[:-5] + ".pdf"
    if os.path.exists(pdf):
        os.remove(pdf)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", f"--print-to-pdf={pdf}",
                        "file:///" + html_path.replace("\\", "/")],
                       check=True, capture_output=True, timeout=180)
    if not os.path.exists(pdf):
        raise SystemExit(f"Chrome did not write {pdf}")
    json.dump(dict(note="Single page; positions in mm on A4, row guides relative to the row top.",
                   style="thuluth", nib_mm=NIB, alif_dots=DOTS, sentence=SENTENCE, rumi=RUMI,
                   page_mm=[PW, PH], margin_mm=MARGIN, square_mm=SQ,
                   square_centres_mm=[[round(x + SQ / 2, 3), round(y + SQ / 2, 3)] for x, y in SQUARES],
                   row_guides_mm=dict(label_band=BAND, **guides), rows=rows),
              open(os.path.join(out, "layout.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"alif {alif:.0f} mm, {n_rows} rows of {row_h:.1f} mm, widest line "
          f"{max((r - l) * size for l, r, _, _ in inks):.0f} mm of {width:.0f} -> {pdf}")


main()
