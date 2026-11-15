"""Thuluth test sheet with only the letter forms one sentence needs.

Uses the same cells as the full Jawi template (one letter form per cell, join
ticks, faint guide letter, dot ladder), sized for a NIB mm pen. layout.json also
records which cells spell each word, so the scanned letters can be joined back
into the sentence.
"""
import base64
import json
import os
import subprocess
import tempfile

import jawi_template as jt

jt.NIB = 3.0                       # the shared drawing code reads the nib from the module
KEY = "thuluth"
STYLE = jt.STYLES[KEY]
SENTENCE = "ساي سوك منوليس جاوي"
RUMI = "saya suka menulis jawi"
NAMES = {ch: name for ch, name, _ in jt.LETTERS}
JOINS = {ch: jtype for ch, _, jtype in jt.LETTERS}


def forms(word):
    """Contextual form of each letter in a word, from the Jawi joining rules."""
    out = []
    for i, ch in enumerate(word):
        prev_joins = i > 0 and JOINS[word[i - 1]] == "D"
        next_exists = i < len(word) - 1
        joins_next = JOINS[ch] == "D" and next_exists
        out.append((ch, {(False, False): "isol", (False, True): "init",
                         (True, True): "medi", (True, False): "fina"}[(prev_joins, joins_next)]))
    return out


def main():
    words = [forms(w) for w in SENTENCE.split()]
    cells, index = [], {}
    for word in words:
        for ch, form in word:
            if (ch, form) not in index:
                cells.append(dict(char=ch, name=NAMES[ch], form=form, optional=False))
                index[(ch, form)] = len(cells)
    for i, c in enumerate(cells, 1):
        c["id"] = i

    shaper = jt.Shaper(os.path.join(jt.FONT_DIR, STYLE["file"]))
    g = jt.geometry(STYLE, shaper, cells)
    pages = jt.paginate(cells, g)
    shrunk = []
    svgs = [jt.page_svg(i + 1, len(pages), pc, STYLE, shaper, g, shrunk) for i, pc in enumerate(pages)]
    # The sentence, small, under the grid on the last page, for reference.
    y = max(c["y"] + c["h"] for c in pages[-1])
    if y + 20 < jt.GRID_BOTTOM:
        note = (f'<text x="{jt.PW / 2}" y="{y + 8:.2f}" text-anchor="middle" font-family="{jt.LATIN}" '
                f'font-size="3" fill="#999">ayat ujian · {RUMI}</text>'
                f'<text x="{jt.PW / 2}" y="{y + 18:.2f}" text-anchor="middle" '
                f'font-family="Noto Naskh Arabic" font-size="7" fill="{jt.LABEL}">{jt.esc(SENTENCE)}</text>')
        svgs[-1] = svgs[-1].replace("</svg>", note + "</svg>")

    b64 = lambda f: base64.b64encode(open(os.path.join(jt.FONT_DIR, f), "rb").read()).decode()
    css = (f"@font-face{{font-family:'{STYLE['family']}';src:url(data:font/ttf;base64,{b64(STYLE['file'])})}}"
           f"@font-face{{font-family:'Noto Naskh Arabic';src:url(data:font/ttf;base64,{b64(jt.STYLES['naskh']['file'])})}}"
           "@page{size:A4;margin:0}html,body{margin:0;padding:0}svg{display:block;page-break-after:always}")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>miifont jawi test letters · Thuluth</title>"
            f"<style>{css}</style></head><body>\n" + "\n".join(svgs) + "\n</body></html>")
    out = os.path.join(jt.OUT, "test")
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, "miifont-jawi-test-letters-thuluth.html")
    open(html_path, "w", encoding="utf-8").write(html)
    pdf = html_path[:-5] + ".pdf"
    if os.path.exists(pdf):
        os.remove(pdf)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([jt.CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", f"--print-to-pdf={pdf}",
                        "file:///" + html_path.replace("\\", "/")],
                       check=True, capture_output=True, timeout=180)
    if not os.path.exists(pdf):
        raise SystemExit(f"Chrome did not write {pdf}")
    json.dump(dict(
        note="Scanned pages are numbered 1..pages. Positions in mm on A4; cell guides relative to the cell top. "
             "`words` lists, right to left, the cell ids that spell each word of the sentence.",
        style=KEY, nib_mm=jt.NIB, alif_dots=STYLE["dots"], sentence=SENTENCE, rumi=RUMI,
        words=[[index[cf] for cf in w] for w in words],
        page_mm=[jt.PW, jt.PH], safe_area_mm=dict(left=jt.L, right=jt.R, top=jt.T, bottom=jt.B),
        square_mm=jt.SQ, square_centres_mm=[[round(x + jt.SQ / 2, 3), round(y + jt.SQ / 2, 3)] for x, y in jt.SQUARES],
        cell_guides_mm=dict(label_band=jt.BAND, ascender=g["asc"], middle=g["mid"], baseline=g["base"],
                            descender=g["desc"], join_tick_inset=jt.TICK),
        pages=len(pages), cells=cells),
        open(os.path.join(out, "layout-letters.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(cells)} cells, {g['unit']:.1f} x {g['ch']:.1f} mm ({g['cols']} x {g['rows']} per page), "
          f"{len(pages)} page(s) -> {pdf}")
    print("words:", " | ".join(" ".join(f"{ch}:{form}#{index[(ch, form)]}" for ch, form in w) for w in words))
    if shrunk:
        print("  shrunk to fit:", shrunk)


main()
