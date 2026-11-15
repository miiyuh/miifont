"""Build miifont[wght].ttf and miifont-Italic[wght].ttf (variable weight 100-900).

The static build traces a filled outline per weight, and those outlines can't
be interpolated. Here every glyph's centreline is turned into stroke paths, and
every straight piece of a path is drawn as its own round-ended "capsule"
contour. Capsules only change by their radius, so the Thin and Black masters
have identical point structure and any weight in between is exact. Overlaps
are left in place (flagged with OVERLAP_SIMPLE) and fill correctly with the
nonzero rule.
"""
import math
import os

import cv2
import numpy as np
from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, InstanceDescriptor, SourceDescriptor
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.builder import buildStatTable
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools import varLib

from common import CAP, FAMILY, FONTS, ITALIC_ANGLE, S, SIDE, UPM, VERSION, WEIGHTS, glyphs, meta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build", "variable")
OUT = os.path.join(FONTS, "variable")
STROKES = {c: w for c, _, w in WEIGHTS}           # weight class -> stroke width (units)
W_MIN, W_MAX = STROKES[100], STROKES[900]
SPUR = 0.5 * STROKES[400] / S                     # px: shorter dangling branches are thinning noise
EPS = 0.9                                         # px: polyline simplification tolerance
N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# --- skeleton -> stroke paths --------------------------------------------------
def skeleton_paths(skel):
    """Split a 1px skeleton into polylines (lists of (x, y) pixel coords)."""
    pts = set(zip(*np.nonzero(skel)))  # (y, x)
    nb = {p: [(p[0] + dy, p[1] + dx) for dy, dx in N8 if (p[0] + dy, p[1] + dx) in pts] for p in pts}
    junction = {p for p in pts if len(nb[p]) >= 3}
    # Merge touching junction pixels into one node each.
    node_of, nodes = {}, []
    for p in junction:
        if p in node_of:
            continue
        stack, members = [p], []
        node_of[p] = len(nodes)
        while stack:
            q = stack.pop(); members.append(q)
            for r in nb[q]:
                if r in junction and r not in node_of:
                    node_of[r] = len(nodes); stack.append(r)
        nodes.append(members)
    centre = [tuple(np.mean(m, axis=0)) for m in nodes]

    edges, seen = [], set()

    def walk(start, prev):
        path = [start]
        cur, last = start, prev
        while True:
            if cur in node_of:
                return path, node_of[cur]
            nxt = [r for r in nb[cur] if r != last and r not in seen and (r not in path[-3:])]
            # prefer stepping into a node, then 4-neighbours (smoother walk)
            nxt.sort(key=lambda r: (r not in node_of, abs(r[0] - cur[0]) + abs(r[1] - cur[1])))
            if not nxt:
                return path, None
            seen.add(cur)
            last, cur = cur, nxt[0]
            path.append(cur)

    # Edges leaving each node.
    for ni, members in enumerate(nodes):
        for m in members:
            for r in nb[m]:
                if r in node_of or r in seen:
                    continue
                path, end = walk(r, m)
                seen.update(path)
                edges.append(dict(pts=[centre[ni]] + path[:-1] + ([centre[end]] if end is not None else [path[-1]]),
                                  a=ni, b=end))
    # Edges between endpoints (no junction), and closed loops.
    rest = [p for p in pts if p not in seen and p not in node_of]
    ends = [p for p in rest if len(nb[p]) <= 1]
    for p in ends + rest:
        if p in seen:
            continue
        path, _ = walk(p, None)
        seen.update(path)
        closed = len(path) > 2 and path[0] in nb[path[-1]] and len(nb[p]) == 2
        if closed:
            path = path + [path[0]]
        edges.append(dict(pts=path, a=None, b=None))

    # Prune short dangling spurs attached to a junction.
    degree = {}
    for e in edges:
        for n in (e["a"], e["b"]):
            if n is not None:
                degree[n] = degree.get(n, 0) + 1
    kept = []
    for e in edges:
        dangling = (e["a"] is None) != (e["b"] is None)
        node = e["a"] if e["a"] is not None else e["b"]
        if dangling and len(e["pts"]) < SPUR and degree.get(node, 0) >= 3:
            degree[node] -= 1
            continue
        kept.append(e)
    paths = [[(x, y) for y, x in e["pts"]] for e in kept]
    # Isolated nodes (tiny blobs) become dots.
    used = {n for e in kept for n in (e["a"], e["b"]) if n is not None}
    paths += [[(c[1], c[0])] for i, c in enumerate(centre) if i not in used]
    return paths


def simplify(path):
    if len(path) < 3:
        return path
    a = np.array(path, np.float32)
    closed = np.allclose(a[0], a[-1])
    # light smoothing, endpoints fixed
    if len(a) > 5:
        k = np.ones(5) / 5
        sm = np.stack([np.convolve(np.pad(a[:, i], 2, mode="edge"), k, "valid") for i in (0, 1)], 1)
        sm[0], sm[-1] = a[0], a[-1]
        a = sm.astype(np.float32)
    s = cv2.approxPolyDP(a.reshape(-1, 1, 2), EPS, False).reshape(-1, 2)
    out = [tuple(p) for p in s]
    if closed and out[0] != out[-1]:
        out.append(out[0])
    return out


# --- capsule outlines ------------------------------------------------------------
def capsule(pen, p0, p1, r):
    """Clockwise (TrueType) round-ended segment p0->p1 of radius r, using quadratics."""
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = ((x1 - x0) / L, (y1 - y0) / L) if L > 1e-6 else (1.0, 0.0)
    nx, ny = -uy, ux
    rnd = lambda x, y: (round(x), round(y))
    k = r / math.cos(math.radians(22.5))

    def arc(cx, cy, a0):
        # half circle, clockwise from angle a0, four 45 degree quadratic pieces
        for i in range(4):
            am, a1 = a0 - math.radians(45 * i + 22.5), a0 - math.radians(45 * (i + 1))
            pen.qCurveTo(rnd(cx + k * math.cos(am), cy + k * math.sin(am)),
                         rnd(cx + r * math.cos(a1), cy + r * math.sin(a1)))

    an = math.atan2(ny, nx)
    pen.moveTo(rnd(x0 + nx * r, y0 + ny * r))
    pen.lineTo(rnd(x1 + nx * r, y1 + ny * r))
    arc(x1, y1, an)
    pen.lineTo(rnd(x0 - nx * r, y0 - ny * r))
    arc(x0, y0, an + math.pi)
    pen.closePath()


def glyph_paths(g):
    """Stroke paths in font units (relative to the skeleton's left edge), plus skeleton width."""
    paths = [simplify(p) for p in skeleton_paths(g["skel"])]
    xs = [x for p in paths for x, _ in p]
    x0 = min(xs)
    to_font = lambda p: [((x - x0) * S, (g["base"] - y) * S) for x, y in p]
    return [to_font(p) for p in paths], (max(xs) - x0) * S


def notdef(pen):
    pen.moveTo((50, 0)); pen.lineTo((50, 700)); pen.lineTo((450, 700)); pen.lineTo((450, 0)); pen.closePath()
    pen.moveTo((100, 50)); pen.lineTo((400, 50)); pen.lineTo((400, 650)); pen.lineTo((100, 650)); pen.closePath()


print("extracting stroke paths...")
order, cmap, gpaths = [".notdef", "space"], {32: "space", 0xA0: "space"}, {}
for key, g in glyphs.items():
    name = f"uni{ord(meta[key]['char']):04X}"
    gpaths[name] = glyph_paths(g)
    order.append(name)
    cmap[ord(meta[key]["char"])] = name
npts = sum(max(len(p) - 1, 1) for paths, _ in gpaths.values() for p in paths)
print(f"{len(gpaths)} glyphs, {npts} capsules")


def master(stroke, italic):
    r = stroke / 2
    slant = math.tan(math.radians(ITALIC_ANGLE)) if italic else 0.0
    tt, widths = {}, {".notdef": 500, "space": 260 + stroke}
    for gname in order:
        pen = TTGlyphPen(None)
        if gname == ".notdef":
            notdef(pen)
        elif gname in gpaths:
            paths, skel_w = gpaths[gname]
            dx = r + SIDE
            xf = lambda x, y: (x + dx + slant * (y - 240), y)
            for p in paths:
                segs = list(zip(p, p[1:])) or [(p[0], p[0])]
                for a, b in segs:
                    capsule(pen, xf(*a), xf(*b), r)
            widths[gname] = round(skel_w + 2 * r + 2 * SIDE)
        tt[gname] = pen.glyph()
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(tt)
    gl = fb.font["glyf"]
    fb.setupHorizontalMetrics({n: (widths[n], getattr(gl[n], "xMin", 0)) for n in order})
    asc, desc = 900 + W_MAX // 2, -280 - W_MAX // 2
    fb.setupHorizontalHeader(ascent=asc, descent=desc,
                             caretSlopeRise=1000 if italic else 1,
                             caretSlopeRun=round(1000 * slant) if italic else 0)
    style = "Italic" if italic else "Regular"
    fb.setupNameTable({"familyName": FAMILY, "styleName": style,
                       "uniqueFontIdentifier": f"{VERSION};{FAMILY}-{style}VF",
                       "fullName": f"{FAMILY} {style}", "psName": f"{FAMILY}-{style}",
                       "version": f"Version {VERSION}"})
    fb.setupOS2(version=4, usWeightClass=400, fsSelection=(0x01 if italic else 0x40) | 0x80,
                sTypoAscender=asc, sTypoDescender=desc, sTypoLineGap=100,
                usWinAscent=asc + 60, usWinDescent=-desc + 60,
                sxHeight=480, sCapHeight=CAP, achVendID="MIIF")
    fb.setupPost(italicAngle=-ITALIC_ANGLE if italic else 0)
    fb.font["head"].macStyle = 0x02 if italic else 0
    return fb.font


def build(italic):
    os.makedirs(BUILD, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    tag = "Italic" if italic else "Upright"
    ds = DesignSpaceDocument()
    ax = AxisDescriptor()
    ax.tag, ax.name, ax.minimum, ax.default, ax.maximum = "wght", "Weight", 100, 400, 900
    # user weight -> design value (stroke width), so named weights match the static fonts
    ax.map = sorted(STROKES.items())
    ds.addAxis(ax)
    for stroke in (W_MIN, STROKES[400], W_MAX):
        path = os.path.join(BUILD, f"master-{tag}-{stroke}.ttf")
        master(stroke, italic).save(path)
        src = SourceDescriptor(); src.path = path; src.location = {"Weight": stroke}
        ds.addSource(src)
    for cls, name, stroke in WEIGHTS:
        inst = InstanceDescriptor()
        style = name if not italic else ("Italic" if name == "Regular" else f"{name} Italic")
        inst.familyName, inst.styleName = FAMILY, style
        inst.location = {"Weight": stroke}
        inst.postScriptFontName = f"{FAMILY}-{style.replace(' ', '')}"
        ds.addInstance(inst)
    vf, _, _ = varLib.build(ds)
    # Capsules overlap on purpose; tell rasterisers.
    for gname in vf.getGlyphOrder():
        g = vf["glyf"][gname]
        if g.numberOfContours > 0:
            g.flags[0] |= 0x40  # OVERLAP_SIMPLE
    buildStatTable(vf, [
        dict(tag="wght", name="Weight", values=[
            # Regular is elidable and style-links to Bold
            dict(value=c, name=n, **({"flags": 0x2, "linkedValue": 700} if c == 400 else {}))
            for c, n, _ in WEIGHTS]),
        dict(tag="ital", name="Italic", values=[
            dict(value=1, name="Italic") if italic else
            dict(value=0, name="Roman", flags=0x2, linkedValue=1)]),
    ], elidedFallbackName="Regular")
    name = f"{FAMILY}-Italic[wght].ttf" if italic else f"{FAMILY}[wght].ttf"
    vf.save(os.path.join(OUT, name))
    vf.flavor = "woff2"
    vf.save(os.path.join(OUT, name.replace(".ttf", ".woff2")))
    print("built", name)


build(False)
build(True)
