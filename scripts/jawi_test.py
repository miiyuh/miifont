"""One-page Thuluth test sheet: a short Jawi sentence written with a broad nib.

The sentence is split into lines that fit the page. Each line gets two rows:
one with the faint guide words to trace, then an empty row to write it
freehand. Guides, labels and the dot ladder are light coral and drop out when
the scan is processed. Writes HTML, PDF and a layout.json (row boxes in mm).
"""
import json
import os

import jawi_template as jt

NIB = 3.0                     # calligraphy nib width (mm)
jt.NIB = NIB                  # the shared header reads the nib from the module
STYLE = jt.STYLES["thuluth"]
DOTS = STYLE["dots"]          # alif height in nib dots
SLACK = 1.05                  # spare width for handwriting (rows are full width, so less is needed)
SENTENCE = "ساي سوك منوليس جاوي"
RUMI = "saya suka menulis jawi"
LINES = ["ساي سوك", "منوليس جاوي"]   # how the sentence is split; each must fit one row

BAND = 6.5                    # label row at the top of each row
LADDER = 6.0                  # width reserved for the dot ladder at the right edge
GAP = 3.0                     # gap between rows
TOP = jt.GRID_TOP             # below the header
BOTTOM = jt.GRID_BOTTOM       # above the bottom marks


def ladder(x_right, base):
    """Column of nib-width rhombus dots up to the alif height."""
    s, h, wd = [], NIB, NIB * 1.15
    cx = x_right - 0.3 - wd / 2
    for i in range(DOTS):
        y1 = base - i * h
        xo = (wd * 0.12) if i % 2 else -(wd * 0.12)
        s.append(f'<path d="M{cx + xo:.2f},{y1:.2f} l{wd / 2:.2f},{-h / 2:.2f} l{-wd / 2:.2f},{-h / 2:.2f} '
                 f'l{-wd / 2:.2f},{h / 2:.2f} z" fill="{jt.GHOST}"/>')
    return s


def main():
    shaper = jt.Shaper(os.path.join(jt.FONT_DIR, STYLE["file"]))
    assert "".join(LINES).replace(" ", "") == SENTENCE.replace(" ", "")
    alif = DOTS * NIB
    size = alif / shaper.ink("ا")[2]                  # font size (mm) giving that alif height
    inks = [shaper.ink(line) for line in LINES]
    top = max(i[2] for i in inks) * size
    bottom = -min(i[3] for i in inks) * size
    L, R = jt.L, jt.R
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

    s = [jt.svg_open()] + jt.header(1, 1, STYLE, title=f"thuluth test, {NIB:.1f} mm nib")
    rows = []
    for i, (line, (lft, rgt, _, _)) in enumerate(zip(LINES, inks)):
        for trace in (True, False):
            y = TOP + len(rows) * (row_h + GAP)
            by = y + base
            s.append(f'<g transform="translate({L:.3f},{y:.3f})">')
            s += jt.brackets(R - L, row_h)
            for k in ("ascender", "middle", "descender"):
                s.append(f'<line x1="0.8" y1="{guides[k]}" x2="{R - L - LADDER:.2f}" y2="{guides[k]}" '
                         f'stroke="{jt.GUIDE}" stroke-width="0.18" stroke-dasharray="0.3 1.1" stroke-linecap="round"/>')
            s.append(f'<line x1="0.8" y1="{base:.2f}" x2="{R - L - 0.8:.2f}" y2="{base:.2f}" '
                     f'stroke="{jt.GUIDE}" stroke-width="0.3"/>')
            s.append(f'<line x1="1.4" y1="{BAND}" x2="{R - L - 1.4:.2f}" y2="{BAND}" stroke="{jt.GUIDE}" '
                     f'stroke-width="0.18" stroke-dasharray="0.3 0.9" stroke-linecap="round"/>')
            s.append(f'<text x="1.4" y="4.4" font-family="{jt.LATIN}" font-size="2.3" fill="{jt.LABEL}">'
                     f'<tspan font-weight="bold">{len(rows) + 1}</tspan>'
                     f'<tspan dx="1.2">{"surih" if trace else "tulis sendiri"}</tspan></text>')
            s.append(f'<text x="{R - L - 1.6:.2f}" y="5.0" text-anchor="end" font-family="Noto Naskh Arabic" '
                     f'font-size="4.4" fill="{jt.LABEL}">{jt.esc(line)}</text>')
            s.append("</g>")
            s += ladder(R - 0.4, by)
            if trace:
                x = R - LADDER - 3 - rgt * size              # right-aligned, as Jawi starts on the right
                s.append(f'<text x="{x:.3f}" y="{by:.3f}" direction="ltr" text-anchor="start" '
                         f'font-family="{STYLE["family"]}" font-size="{size:.3f}" fill="{jt.GHOST}">{jt.esc(line)}</text>')
            rows.append(dict(row=len(rows) + 1, line=i + 1, text=line, trace=trace,
                             x=round(L, 3), y=round(y, 3), w=round(R - L, 3), h=round(row_h, 3)))
    s.append("</svg>")

    out = os.path.join(jt.OUT, "test")
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, "miifont-jawi-test-thuluth.html")
    open(html_path, "w", encoding="utf-8").write(jt.html_doc("miifont jawi sentence test, Thuluth", ["\n".join(s)], STYLE))
    pdf = html_path[:-5] + ".pdf"
    jt.print_pdf(html_path, pdf)
    json.dump(dict(note="Single page; positions in mm on A4, row guides relative to the row top.",
                   version=2, style="thuluth", nib_mm=NIB, alif_dots=DOTS, sentence=SENTENCE, rumi=RUMI,
                   page_mm=[jt.PW, jt.PH], margin_mm=jt.MARGIN, mark_shape="plus",
                   mark_size_mm=[jt.MARK_W, jt.MARK_H],
                   mark_centres_mm=[[round(x, 3), round(y, 3)] for x, y in jt.MARKS],
                   row_guides_mm=dict(label_band=BAND, **guides), rows=rows),
              open(os.path.join(out, "layout.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"alif {alif:.0f} mm, {n_rows} rows of {row_h:.1f} mm, widest line "
          f"{max((r - l) * size for l, r, _, _ in inks):.0f} mm of {width:.0f} -> {pdf}")


main()
