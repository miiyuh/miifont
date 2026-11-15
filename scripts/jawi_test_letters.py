"""Thuluth test sheet with only the letter forms one sentence needs.

Uses the same cells as the full Jawi template (one letter form per cell, walls
and lanes on joining sides, faint guide letter, dot ladder), sized for a NIB mm
pen. layout-letters.json also records which cells spell each word, so the
scanned letters can be joined back into the sentence.
"""
import json
import os

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
        joins_next = JOINS[ch] == "D" and i < len(word) - 1
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
    svgs = [jt.page_svg(i + 1, len(pages), pc, STYLE, shaper, g, shrunk, title=f"thuluth letter test, {jt.NIB:.1f} mm nib")
            for i, pc in enumerate(pages)]
    # The sentence, small, under the grid on the last page, for reference.
    y = max(c["y"] + c["h"] for c in pages[-1])
    if y + 20 < jt.GRID_BOTTOM:
        note = (f'<text x="{jt.PW / 2}" y="{y + 8:.2f}" text-anchor="middle" font-family="{jt.LATIN}" '
                f'font-size="3" fill="#999">{RUMI}</text>'
                f'<text x="{jt.PW / 2}" y="{y + 18:.2f}" text-anchor="middle" '
                f'font-family="Noto Naskh Arabic" font-size="7" fill="{jt.LABEL}">{jt.esc(SENTENCE)}</text>')
        svgs[-1] = svgs[-1].replace("</svg>", note + "</svg>")

    out = os.path.join(jt.OUT, "test")
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, "miifont-jawi-test-letters-thuluth.html")
    open(html_path, "w", encoding="utf-8").write(jt.html_doc("miifont jawi letter test, Thuluth", svgs, STYLE))
    pdf = html_path[:-5] + ".pdf"
    jt.print_pdf(html_path, pdf)
    layout = jt.layout_dict(KEY, STYLE, g, pages, cells, sentence=SENTENCE, rumi=RUMI,
                            words=[[index[cf] for cf in w] for w in words])
    json.dump(layout, open(os.path.join(out, "layout-letters.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"{len(cells)} cells, {g['unit']:.1f} x {g['ch']:.1f} mm ({g['cols']} x {g['rows']} per page), "
          f"{len(pages)} page(s) -> {pdf}")
    print("words:", " | ".join(" ".join(f"{ch}:{form}#{index[(ch, form)]}" for ch, form in w) for w in words))
    if shrunk:
        print("  shrunk to fit:", shrunk)


main()
