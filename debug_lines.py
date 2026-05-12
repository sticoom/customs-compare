"""
Debug script: extract horizontal lines/rectangles and text spans from
the item-data table area of a customs pre-recording PDF.

Goal: understand how black separator lines can help distinguish item boundaries.
"""

import fitz  # PyMuPDF

PDF_PATH = r"C:\Users\13676\Desktop\飞书下载\EIJYD202604090208-预录单.pdf"

# ── open PDF ──────────────────────────────────────────────────────────────
doc = fitz.open(PDF_PATH)
page = doc[0]

Y_MIN = 220
Y_MAX = 440

# ═══════════════════════════════════════════════════════════════════════════
# 1.  Extract drawing instructions (lines, rectangles, curves ...)
# ═══════════════════════════════════════════════════════════════════════════
drawings = page.get_drawings()

# Collect all horizontal-ish elements as (y, x0, x1, kind, color, thickness)
raw_segments = []

for d in drawings:
    items = d.get("items", [])
    color = d.get("color")       # tuple or None
    fill  = d.get("fill")
    width = d.get("width") or 0
    d_rect = d.get("rect")

    for item in items:
        kind_code = item[0]

        if kind_code == "l":  # line segment  (x0,y0) -> (x1,y1)
            _, p0, p1 = item
            x0, y0 = p0
            x1, y1 = p1
            if abs(y1 - y0) < 2:
                mid_y = (y0 + y1) / 2
                if Y_MIN <= mid_y <= Y_MAX:
                    raw_segments.append((
                        round(mid_y, 2),
                        round(min(x0, x1), 2),
                        round(max(x0, x1), 2),
                        "line",
                        color,
                        round(width, 2)
                    ))

        elif kind_code == "re":  # rectangle: ('re', Rect(...), fill_code)
            _, r, fill_code = item
            rx0, ry0, rx1, ry1 = r
            height = abs(ry1 - ry0)
            mid_y = (ry0 + ry1) / 2
            if Y_MIN <= mid_y <= Y_MAX:
                kind_label = "rect-bar" if height < 3 else "rect-tall"
                raw_segments.append((
                    round(mid_y, 2),
                    round(min(rx0, rx1), 2),
                    round(max(rx0, rx1), 2),
                    kind_label,
                    fill,   # thin bars typically use fill, not stroke
                    round(height, 2)
                ))

# ── Deduplicate (same y, x0, x1, type → keep one) ───────────────────────
seen = set()
deduped = []
for seg in raw_segments:
    key = (seg[0], seg[1], seg[2], seg[3])
    if key not in seen:
        seen.add(key)
        deduped.append(seg)

deduped.sort(key=lambda t: (t[0], t[1]))

# ═══════════════════════════════════════════════════════════════════════════
# Print section 1: horizontal drawing elements
# ═══════════════════════════════════════════════════════════════════════════
print("=" * 100)
print("  SECTION 1: HORIZONTAL DRAWING ELEMENTS  (y between {:.0f} and {:.0f})".format(Y_MIN, Y_MAX))
print("=" * 100)

print(f"{'Y':>8}  {'X-start':>8}  {'X-end':>8}  {'Thick':>6}  {'Type':<12}  Color/Fill")
print("-" * 100)
for (y, x0, x1, kind, col, thick) in deduped:
    cstr = str(col) if col else "None"
    print(f"{y:8.2f}  {x0:8.2f}  {x1:8.2f}  {thick:6.2f}  {kind:<12}  {cstr}")

print(f"\n  Total deduped horizontal elements: {len(deduped)}")

# ── Merge segments at same Y into full-width rows ────────────────────────
row_ys = []
for seg in deduped:
    y = seg[0]
    if not row_ys or abs(y - row_ys[-1]) > 1.5:
        row_ys.append(y)

print(f"\n  Unique row Y-positions ({len(row_ys)}):")
for i, y in enumerate(row_ys):
    segs_at_y = [s for s in deduped if abs(s[0] - y) < 1.5]
    xmin = min(s[1] for s in segs_at_y)
    xmax = max(s[2] for s in segs_at_y)
    colors = set(str(s[4]) for s in segs_at_y)
    types  = set(s[3] for s in segs_at_y)
    print(f"    Row {i}: y={y:8.2f}  x=[{xmin:.1f} .. {xmax:.1f}]  "
          f"types={types}  colors={colors}")

# ═══════════════════════════════════════════════════════════════════════════
# 2.  Text spans near each row Y-position
# ═══════════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 100)
print("  SECTION 2: TEXT SPANS NEAR EACH ROW-SEPARATOR Y-POSITION")
print("=" * 100)

blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

for i, uy in enumerate(row_ys):
    print(f"\n--- Row separator Y = {uy:.2f} ---")
    spans_near = []
    for blk in blocks:
        if blk["type"] != 0:
            continue
        for line in blk["lines"]:
            for sp in line["spans"]:
                sy0 = sp["bbox"][1]
                sy1 = sp["bbox"][3]
                sp_mid = (sy0 + sy1) / 2
                if abs(sp_mid - uy) < 8:
                    text = sp["text"].strip()
                    if text:
                        spans_near.append((sp["bbox"][0], text, sp["size"], sp["color"]))
    spans_near.sort(key=lambda t: t[0])
    for sx, txt, sz, col in spans_near:
        print(f"    x={sx:7.1f}  sz={sz:4.1f}  \"{txt}\"")

# ═══════════════════════════════════════════════════════════════════════════
# 3.  Full text dump in the Y range (broader context)
# ═══════════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 100)
print(f"  SECTION 3: ALL TEXT SPANS IN Y RANGE [{Y_MIN}, {Y_MAX}]  (sorted by y then x)")
print("=" * 100)

all_spans = []
for blk in blocks:
    if blk["type"] != 0:
        continue
    for line in blk["lines"]:
        for sp in line["spans"]:
            sy0 = sp["bbox"][1]
            sy1 = sp["bbox"][3]
            sp_mid = (sy0 + sy1) / 2
            if Y_MIN <= sp_mid <= Y_MAX:
                text = sp["text"].strip()
                if text:
                    all_spans.append((
                        sp_mid, sp["bbox"][0],
                        text, sp["size"], sp["color"],
                        sp["font"]
                    ))

all_spans.sort(key=lambda t: (t[0], t[1]))

prev_y = None
for sy, sx, txt, sz, col, font in all_spans:
    if prev_y is None or abs(sy - prev_y) > 3:
        print(f"\n  >> y = {sy:.1f}")
        prev_y = sy
    print(f"      x={sx:7.1f}  sz={sz:4.1f}  \"{txt}\"")

# ═══════════════════════════════════════════════════════════════════════════
# 4.  Full-page overview of all horizontal separator Y-positions
# ═══════════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 100)
print("  SECTION 4: ALL UNIQUE HORIZONTAL-LINE Y-POSITIONS (full page)")
print("=" * 100)

all_horiz_ys = set()
for d in drawings:
    items = d.get("items", [])
    for item in items:
        if item[0] == "l":
            _, p0, p1 = item
            y0, y1 = p0[1], p1[1]
            if abs(y1 - y0) < 2:
                all_horiz_ys.add(round((y0 + y1) / 2, 2))
        elif item[0] == "re":
            _, r, _ = item
            mid = (r[1] + r[3]) / 2
            if abs(r[3] - r[1]) < 3:  # thin horizontal bar
                all_horiz_ys.add(round(mid, 2))

sorted_ys = sorted(all_horiz_ys)
for y in sorted_ys:
    marker = "  <<< TARGET RANGE" if Y_MIN <= y <= Y_MAX else ""
    print(f"  y = {y:8.2f}{marker}")

doc.close()
print("\nDone.")
