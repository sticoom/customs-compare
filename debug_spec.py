"""
Debug script: investigate the product_name / spec_model boundary issue
for the third PDF pair.

Shows ALL spans in the first item slot (between header bottom line and
first separator), along with their column assignments.
"""

import fitz
import re

PDF_PATH = r"C:\Users\13676\Desktop\飞书下载\EIJYD202604090208-预录单.pdf"

# ── open PDF ──────────────────────────────────────────────────────────────
doc = fitz.open(PDF_PATH)
page = doc[0]

# ═══════════════════════════════════════════════════════════════════════════
# 1. Extract ALL spans on page 0
# ═══════════════════════════════════════════════════════════════════════════
blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

all_spans = []
for blk in blocks:
    if blk["type"] != 0:
        continue
    for line in blk["lines"]:
        for sp in line["spans"]:
            text = sp["text"].strip()
            if text:
                bbox = sp["bbox"]
                all_spans.append({
                    "text": text,
                    "x": bbox[0],
                    "y": bbox[1],
                    "x1": bbox[2],
                    "y1": bbox[3],
                    "size": sp["size"],
                    "font": sp["font"],
                    "color": sp["color"],
                })

all_spans.sort(key=lambda s: (s["y"], s["x"]))

# ═══════════════════════════════════════════════════════════════════════════
# 2. Find header row (containing "项号")
# ═══════════════════════════════════════════════════════════════════════════
header_keywords = {
    "项号": "item_no",
    "商品编号": "product_code",
    "商品名称": "product_name",
    "规格型号": "product_name",
    "数量": "quantity",
    "单价": "price",
    "总价": "price",
    "币制": "price",
    "原产国": "origin_country",
    "目的国": "dest_country",
    "境内货源地": "source",
    "征免": "duty",
}

header_y = None
for s in all_spans:
    if "项号" in s["text"] or s["text"].startswith("商品"):
        header_y = s["y"]
        break

print(f"Header row y = {header_y}")

# Collect header spans
header_spans = [s for s in all_spans if abs(s["y"] - header_y) < 5]
print("=" * 120)
print("  HEADER SPANS (table column headers)")
print("=" * 120)
for s in header_spans:
    print(f"  y={s['y']:7.1f}  x={s['x']:7.1f}  x1={s['x1']:7.1f}  \"{s['text']}\"")

# ═══════════════════════════════════════════════════════════════════════════
# 3. Build column positions from header
# ═══════════════════════════════════════════════════════════════════════════
col_positions = {}
for s in header_spans:
    for keyword, col_id in header_keywords.items():
        if keyword in s["text"]:
            if col_id not in col_positions:
                col_positions[col_id] = s["x"]
            break

# Handle "项号商品编号" combined header
if "product_code" not in col_positions and "item_no" in col_positions:
    for s in header_spans:
        if "商品编号" in s["text"] and "项号" not in s["text"]:
            col_positions["product_code"] = s["x"]
            break
    if "product_code" not in col_positions:
        for s in header_spans:
            if "项号" in s["text"] and "商品编号" in s["text"]:
                data_spans_header = [sp for sp in all_spans if sp["y"] > header_y + 5]
                for ds in data_spans_header:
                    if re.match(r"^\d{8,10}$", ds["text"].strip()):
                        col_positions["product_code"] = ds["x"]
                        break
                if "product_code" not in col_positions:
                    col_positions["product_code"] = s["x"] + 30
                break

print(f"\n  Column positions:")
for col_id, x in sorted(col_positions.items(), key=lambda c: c[1]):
    print(f"    {col_id:20s}  x = {x:.1f}")

# ═══════════════════════════════════════════════════════════════════════════
# 4. Build column boundaries
# ═══════════════════════════════════════════════════════════════════════════
sorted_cols = sorted(col_positions.items(), key=lambda c: c[1])

col_boundaries = []
for i, (col_id, x_center) in enumerate(sorted_cols):
    x_start = x_center - 15
    if i + 1 < len(sorted_cols):
        x_end = sorted_cols[i + 1][1] - 5
    else:
        x_end = 900
    col_boundaries.append((col_id, x_start, x_end))

print(f"\n  Column boundaries:")
for col_id, x_start, x_end in col_boundaries:
    width = x_end - x_start
    print(f"    {col_id:20s}  x=[{x_start:7.1f} .. {x_end:7.1f})  width={width:.1f}")

def get_col_id(x):
    for col_id, x_start, x_end in col_boundaries:
        if x_start <= x < x_end:
            return col_id
    return None

# ═══════════════════════════════════════════════════════════════════════════
# 5. Extract horizontal lines - show ALL with widths
# ═══════════════════════════════════════════════════════════════════════════
drawings = page.get_drawings()
h_lines = []

for d in drawings:
    for item in d.get("items", []):
        if item[0] == "l":
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) < 1.0:
                h_lines.append({
                    "y": p1.y,
                    "x_start": min(p1.x, p2.x),
                    "x_end": max(p1.x, p2.x),
                })
        elif item[0] == "re":
            rect = item[1]
            if rect.height < 2.0 and rect.width > 50:
                h_lines.append({
                    "y": rect.y0,
                    "x_start": rect.x0,
                    "x_end": rect.x1,
                })

h_lines.sort(key=lambda l: l["y"])

# De-duplicate
unique_ys = []
seen_ys = set()
for l in h_lines:
    y_key = round(l["y"], 0)
    if y_key not in seen_ys:
        seen_ys.add(y_key)
        unique_ys.append(l)
h_lines = sorted(unique_ys, key=lambda l: l["y"])

print(f"\n  All horizontal lines on page ({len(h_lines)}):")
for l in h_lines:
    width = l["x_end"] - l["x_start"]
    marker = ""
    if l["y"] > header_y and width > 100:
        marker = " <<< TABLE LINE (width > 100)"
    elif l["y"] > header_y and width <= 100:
        marker = f" <<< NARROW (width={width:.0f}, excluded by width>100 filter)"
    print(f"    y={l['y']:8.2f}  x=[{l['x_start']:7.1f} .. {l['x_end']:7.1f}]  width={width:6.1f}{marker}")

# ═══════════════════════════════════════════════════════════════════════════
# 6. Show what the parser ACTUALLY does (row_slots = None, fallback path)
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 120}")
print(f"  KEY FINDING: row_slots is None (only 1 table line found), so parser falls back")
print(f"  to item-number Y-coordinate grouping (the old logic)")
print(f"{'=' * 120}")

# footer detection
footer_keywords = ["特殊关系确认", "申报单位", "报关人员", "兹申明", "自报自缴", "自缴自报"]
footer_y = 9999
for s in all_spans:
    for kw in footer_keywords:
        if kw in s["text"]:
            if s["y"] < footer_y:
                footer_y = s["y"]
            break

print(f"\n  Footer y = {footer_y}")

# Data spans
data_spans = [s for s in all_spans if s["y"] > header_y + 5 and s["y"] < footer_y - 2]

# Find item numbers
item_x_start = col_positions.get("item_no", 0) - 15
if "product_code" in col_positions:
    item_x_end = col_positions["product_code"] - 2
else:
    item_x_end = col_positions.get("item_no", 0) + 30

print(f"\n  item_no x range: [{item_x_start:.1f} .. {item_x_end:.1f})")

item_start_ys = []
for s in data_spans:
    text = s["text"].strip()
    if item_x_start <= s["x"] < item_x_end and re.match(r"^\d{1,3}$", text):
        item_start_ys.append(s["y"])

print(f"\n  Item number Y-positions: {item_start_ys}")

# Now simulate the fallback logic for each item
print(f"\n{'=' * 120}")
print(f"  FALLBACK LOGIC: each item spans from its start_y to next item's start_y")
print(f"{'=' * 120}")

for idx, start_y in enumerate(item_start_ys):
    end_y = item_start_ys[idx + 1] if idx + 1 < len(item_start_ys) else 9999

    print(f"\n  --- Item {idx + 1}: y range [{start_y:.1f} .. {end_y:.1f}) ---")

    # The parser uses: start_y - 2 <= s["y"] < end_y - 2
    item_spans = [s for s in data_spans if start_y - 2 <= s["y"] < end_y - 2]

    # Check for "above" spans (data before first item number)
    # This only applies when has_data_above_first is True
    first_item_y = item_start_ys[0]
    has_data_above_first = any(
        s["y"] < first_item_y - 2 and s["y"] > header_y + 3
        for s in data_spans
        if get_col_id(s["x"]) and get_col_id(s["x"]) != "item_no"
    )

    above_spans = []
    if has_data_above_first:
        if idx == 0:
            upper_bound = header_y + 3
        else:
            upper_bound = (item_start_ys[idx - 1] + start_y) / 2
        for s in data_spans:
            if upper_bound <= s["y"] < start_y - 2:
                col = get_col_id(s["x"])
                if col and col != "item_no":
                    above_spans.append(s)

    all_item_spans = sorted(item_spans + above_spans, key=lambda s: (s["y"], s["x"]))

    print(f"    has_data_above_first = {has_data_above_first}")
    if above_spans:
        print(f"    above_spans count = {len(above_spans)}")

    print(f"\n    {'Y':>8}  {'X':>8}  {'X1':>8}  {'Col_Assignment':>25}  Text")
    print(f"    {'-'*8}  {'-'*8}  {'-'*8}  {'-'*25}  {'-'*40}")

    cols = {}
    for s in all_item_spans:
        col = get_col_id(s["x"])
        if col:
            stext = s["text"].strip()
            effective_col = col
            if col == "item_no" and re.match(r"^\d{6,}$", stext):
                effective_col = "product_code"
                col = "product_code"
            elif col == "product_code" and not re.match(r"^\d{6,}$", stext):
                effective_col = "product_name"
                col = "product_name"
            elif col is None:
                effective_col = "** NO COLUMN **"
            else:
                effective_col = col

            if col not in cols:
                cols[col] = []
            cols[col].append(s["text"])

            marker = ""
            if s in above_spans:
                marker = " [ABOVE]"

            print(f"    {s['y']:8.1f}  {s['x']:8.1f}  {s['x1']:8.1f}  {effective_col:>25}  \"{s['text']}\"{marker}")
        else:
            marker = ""
            if s in above_spans:
                marker = " [ABOVE]"
            print(f"    {s['y']:8.1f}  {s['x']:8.1f}  {s['x1']:8.1f}  {'** NO COLUMN **':>25}  \"{s['text']}\"{marker}")

    # Show parsed result
    if cols:
        product_name = (cols.get("product_name") or [""])[0]
        spec_model = " ".join(cols.get("product_name", [])[1:])
        print(f"\n    Parsed: product_name = \"{product_name}\"")
        print(f"            spec_model   = \"{spec_model}\"")

# ═══════════════════════════════════════════════════════════════════════════
# 7. Show ALL data spans between header_y and first item number Y
#    These are the spans that might get incorrectly included via above_spans
# ═══════════════════════════════════════════════════════════════════════════
if item_start_ys:
    first_item_y = item_start_ys[0]
    print(f"\n{'=' * 120}")
    print(f"  SPANS BETWEEN HEADER (y={header_y:.1f}) AND FIRST ITEM NUMBER (y={first_item_y:.1f})")
    print(f"  These are the spans that have no item number but are data rows")
    print(f"{'=' * 120}")

    between_spans = [s for s in data_spans if header_y + 3 < s["y"] < first_item_y - 2]
    for s in between_spans:
        col = get_col_id(s["x"])
        col_str = str(col) if col else "** NO COLUMN **"
        print(f"    y={s['y']:8.1f}  x={s['x']:8.1f}  x1={s['x1']:8.1f}  col={col_str:>20}  \"{s['text']}\"")

# ═══════════════════════════════════════════════════════════════════════════
# 8. ALSO: show ALL spans in the full item data area with y positions
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 120}")
print(f"  ALL SPANS IN ITEM DATA AREA (header_y={header_y:.1f} to footer_y={footer_y:.1f})")
print(f"  Grouped by approximate Y position")
print(f"{'=' * 120}")

prev_y = None
for s in data_spans:
    col = get_col_id(s["x"])
    col_str = str(col) if col else "NONE"
    if prev_y is None or abs(s["y"] - prev_y) > 5:
        print(f"\n  >> y = {s['y']:.1f}")
        prev_y = s["y"]
    print(f"      x={s['x']:7.1f}  col={col_str:>15}  \"{s['text']}\"")

doc.close()
print("\nDone.")
