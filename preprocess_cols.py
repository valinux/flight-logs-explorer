#!/usr/bin/env python3
"""Preprocess scanned pages: locate the contact-text region, detect all text
column bands (2 for directory pages, 4 for address-book pages), enhance and
2x upscale each column crop into cols/ for OCR."""
import sys
from pathlib import Path

from PIL import Image, ImageOps

PAGES_DIR = Path("pages")
OUT_DIR = Path("cols")
DARK = 160


def clusters(rows, tol):
    out, cur = [], [rows[0]]
    for r in rows[1:]:
        if r - cur[-1] <= tol:
            cur.append(r)
        else:
            out.append(cur)
            cur = [r]
    out.append(cur)
    return out


def process(page):
    src = PAGES_DIR / f"page-{page:03d}.png"
    im = Image.open(src).convert("L")
    W, H = im.size
    mask = im.point(lambda p: 255 if p < DARK else 0)
    px = mask.load()

    # ---- row projection (sampled) ----
    row_dark = [0] * H
    for y in range(H):
        c = 0
        for x in range(0, W, 2):
            if px[x, y]:
                c += 1
        row_dark[y] = c * 2

    # cluster ink rows (tol 25px bridges line gaps). Major clusters (>=100
    # rows) are the candidates; photo/graphic bands are much denser than text
    # on the same page, so keep major clusters whose mean ink is within 1.3x
    # of the lightest major cluster.
    ink_rows = [y for y in range(H) if row_dark[y] > 0]
    if not ink_rows:
        return 0
    row_clusters = clusters(ink_rows, 25)

    def mean_ink(cl):
        return sum(row_dark[y] for y in cl) / len(cl)

    major = [cl for cl in row_clusters if len(cl) >= 100]
    if not major:
        major = [max(row_clusters, key=len)]
    # the contact list is the largest cluster; companion clusters must be of
    # similar darkness (photos/graphics are much denser or much lighter)
    anchor = max(major, key=len)
    am = mean_ink(anchor)
    text_clusters = [cl for cl in major if 0.75 * am <= mean_ink(cl) <= 1.35 * am]
    if anchor not in text_clusters:
        text_clusters.append(anchor)
    y0 = max(0, min(cl[0] for cl in text_clusters) - 6)
    y1 = min(H, max(cl[-1] for cl in text_clusters) + 6)
    nrows = y1 - y0

    # ---- column projection over text-like rows only ----
    text_ink = sorted(row_dark[y] for y in range(y0, y1) if row_dark[y] > 0)
    med = text_ink[len(text_ink) // 2] if text_ink else 1
    p75 = text_ink[3 * len(text_ink) // 4] if text_ink else 1
    row_cap = max(3 * med, p75)  # skip dense graphic-strip rows

    col_dark = [0] * W
    for y in range(y0, y1):
        if row_dark[y] > row_cap:
            continue
        for x in range(0, W, 2):
            if px[x, y]:
                col_dark[x] += 2

    t = max(3, int(0.012 * nrows))
    # runs of text columns, merging only tiny gaps (intra-column noise)
    bands = []
    x = 0
    while x < W:
        if col_dark[x] > t:
            start = x
            gap = 0
            x += 1
            while x < W and (col_dark[x] > t or gap < 6):
                gap = 0 if col_dark[x] > t else gap + 1
                x += 1
            bands.append((start, x - gap if gap else x))
        else:
            x += 1
    bands = [(a, b) for a, b in bands if b - a >= 80]

    # recursively split overly wide bands at their quietest interior channel
    # (dense scans fill real gutters with speckle, hiding them above)
    smooth = [0] * W
    for i in range(W):
        smooth[i] = sum(col_dark[max(0, i - 5):i + 6])

    def split_band(a, b, depth=0):
        if b - a <= 550 or depth >= 3:
            return [(a, b)]
        lo = a + int((b - a) * 0.22)
        hi = b - int((b - a) * 0.22)
        xmin = min(range(lo, hi), key=lambda i: smooth[i])
        if smooth[xmin] <= 5 * t:
            return split_band(a, xmin, depth + 1) + split_band(xmin, b, depth + 1)
        return [(a, b)]

    split = []
    for a, b in bands:
        split.extend(split_band(a, b))
    bands = split[:5]
    if not bands:
        return 0

    if len(bands) == 1:
        tags = [""]
    elif len(bands) == 2:
        tags = ["L", "R"]
    else:
        tags = [str(i + 1) for i in range(len(bands))]

    n = 0
    for tag, (cx0, cx1) in zip(tags, bands):
        crop = im.crop((max(0, cx0 - 10), max(0, y0 - 4),
                        min(W, cx1 + 10), min(H, y1 + 4)))
        if crop.width < 100 or crop.height < 60:
            continue
        crop = ImageOps.autocontrast(crop, cutoff=1)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        crop.save(OUT_DIR / f"page-{page:03d}{tag}.png")
        n += 1
    return n


def main():
    OUT_DIR.mkdir(exist_ok=True)
    pages = [int(a) for a in sys.argv[1:]] or list(range(1, 96))
    total = 0
    for p in pages:
        total += process(p)
    print(f"processed {len(pages)} pages -> {total} column crops")


if __name__ == "__main__":
    main()
