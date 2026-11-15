"""Detect the template grid on each scan and save per-cell ink + baseline info."""
import json, os
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = [
    (os.path.join(ROOT, "scans", "page1.jpg"),
     list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?.,;:'\"()-&@")),
    (os.path.join(ROOT, "scans", "page2.jpg"),
     list("abcdefghijklmnopqrstuvwxyz#$%€£¥*+×÷=/\\_<>[]{}|~°")),
]
OUT = os.path.join(ROOT, "build", "cells")
os.makedirs(OUT, exist_ok=True)


def corner_squares(gray):
    """Return centers of the 4 big corner registration squares (TL, TR, BL, BR)."""
    _, bw = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw)
    big = [(stats[i, cv2.CC_STAT_AREA], cent[i]) for i in range(1, n)
           if stats[i, cv2.CC_STAT_AREA] > 2500
           and 0.8 < stats[i, cv2.CC_STAT_WIDTH] / stats[i, cv2.CC_STAT_HEIGHT] < 1.25]
    pts = np.array([c for _, c in big])
    h, w = gray.shape
    targets = [(0, 0), (w, 0), (0, h), (w, h)]
    return np.array([pts[np.argmin(((pts - t) ** 2).sum(1))] for t in targets], np.float32)


def main():
    meta = {}
    ref = None
    for path, chars in PAGES:
        img = cv2.imread(path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sq = corner_squares(gray)
        if ref is None:
            ref = sq
        M = cv2.getPerspectiveTransform(sq, ref)
        img = cv2.warpPerspective(img, M, (img.shape[1], img.shape[0]),
                                  flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        b, g, r = [c.astype(int) for c in cv2.split(img)]
        blue = ((b - r) > 25) & (b > 120)
        # Region between the top and bottom registration squares.
        x0, x1 = int(ref[0][0]) - 40, int(ref[1][0]) + 40
        y0, y1 = int(ref[0][1]) + 80, int(ref[2][1]) - 30
        # Regular grid (in page-1 reference frame): pitch 165.6 x 193 px.
        xs = sum([[int(136 + 165.6 * c), int(136 + 165.6 * c) + 138] for c in range(7)], [])
        ys = sum([[int(313 + 193 * r), int(313 + 193 * r) + 184] for r in range(7)], [])
        dbg = img.copy()
        for row in range(7):
            for col in range(7):
                ch = chars[row * 7 + col]
                cx0, cx1 = xs[col * 2] + 6, xs[col * 2 + 1] - 6
                cy0, cy1 = ys[row * 2] + 6, ys[row * 2 + 1] - 6
                cell = img[cy0:cy1, cx0:cx1]
                cb = blue[cy0:cy1, cx0:cx1]
                # Baseline = the solid guide: row with the most blue pixels, lower half of cell.
                prof = cb.sum(1).astype(float)
                hh = len(prof)
                lo = hh // 2
                base = lo + int(np.argmax(prof[lo:]))
                key = f"{ord(ch):04x}"
                cv2.imwrite(f"{OUT}/{key}.png", cell)
                meta[key] = {"char": ch, "baseline": base, "w": cell.shape[1], "h": hh}
                cv2.rectangle(dbg, (cx0, cy0), (cx1, cy1), (0, 0, 255), 1)
                cv2.line(dbg, (cx0, cy0 + base), (cx1, cy0 + base), (0, 200, 0), 1)
        cv2.imwrite(os.path.join(ROOT, "build", f"debug_page{PAGES.index((path, chars)) + 1}.png"), dbg)
    json.dump(meta, open(f"{OUT}/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


main()
