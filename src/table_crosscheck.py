"""表格交叉校验层：find_tables() 单元格作为「第二意见」核对提取结果。

设计动机（docs/memory.md #31）：提取层 30 个坑里 2/3 源于 PDF 渲染变体（列边界、
span 顺序、标签定位），错误此前只能靠下一批 PDF 踩中后才被发现。本模块用与主提取
完全独立的表格识别路径（PyMuPDF find_tables，项目既有依赖）交叉核对，不一致即产出
warning——把「静默出错」变成「当场显形」。

核对项（V1，都是历史上真踩过的坑）：
- C1 总价 ≈ 单价 × 主数量（±1 或 0.5% 容差）        → #1/#2/#13/#23/#29 类错位
- C2 商品编码：提取到的编码须能在表格单元格中找到     → #29 幻影商品、整条漏提
- C3 合同协议号与单元格一致                          → 表头错位
- C4 件数/毛重/净重与单元格一致（数值比较）           → #8 类
- C5 两侧镜像：项号集合对称 + 同项号价格字段对称      → #26/#32 类单侧漏提/错位

只报 warning，不参与 pass/fail 判定，不改变比对结果。
"""
import re

import pymupdf

PRICE_NUM_RE = re.compile(r"^\d[\d,]*\.?\d*$")
CODE_RE = re.compile(r"\b(\d{9,10})\b")
QTY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(千克|件|个|套|张|只|台|根|卷|包|箱)")
HEADER_LABELS = {
    "quantity": "件数",
    "gross_weight": ("毛重", "毛重（千克）", "毛重(千克)"),
    "net_weight": ("净重", "净重（千克）", "净重(千克)"),
}


def _num(s):
    """'1,234.56' -> 1234.56，非数字返回 None"""
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _cell_texts(page_info):
    """单页所有表格单元格文本（带边框用线条策略，无边框回退 text 策略）"""
    texts = []
    try:
        doc = pymupdf.open(stream=page_info.pdf_bytes, filetype="pdf")
        page = doc[page_info.page_index]
    except Exception:
        return texts
    for strategy in ("lines_strict", "text"):
        try:
            tabs = page.find_tables(strategy=strategy)
        except Exception:
            tabs = None
        for t in (tabs.tables if tabs else []):
            for row in t.extract():
                for cell in row:
                    if cell and cell.strip() and cell.strip() not in texts:
                        texts.append(cell.strip())
        if texts:
            break  # 有产出即停，不混用两种策略避免重复
    return texts


def crosscheck_extraction(customs_pages, pre_pages, extracted):
    """交叉核对提取结果与表格单元格，返回 warning 列表。

    warning 结构: {source: "crosscheck", check: "C1..C4", page: int, message: str}
    page 为 doc 内页序（customs 页在前，pre 页在后，与 PageInfo.page_index 一致）。
    """
    warnings = []

    cells_by_page = []
    for pages in (customs_pages, pre_pages):
        for p in pages:
            cells_by_page.append((p, _cell_texts(p)))
    all_cells = " \n ".join(t for _, ts in cells_by_page for t in ts)

    # ---- C2: 商品编码须能在「本侧」单元格或页面文本中找到（幻影/漏提/跨侧污染） ----
    # V1 把两侧单元格合并成一个池：本侧提取错的编码只要在另一侧出现就能蒙混
    # （#33）。V2 按侧分池；但 find_tables 对部分预录单格式抓不到编码列
    # （20260909001 实测 4/4 编码不在本侧单元格），故再兜底本侧页面原始文本
    # （词边界匹配）——幻影/截断编码（#29 的 2026090400、#23 残留的 732399000）
    # 在本侧长数字串中间取不到词边界，仍会报警，真值则放行。
    for side, items_key, pages in (("报关单", "customs_items", customs_pages),
                                   ("预录单", "pre_items", pre_pages)):
        side_cells = " \n ".join(t for p, ts in cells_by_page if p in list(pages)
                                 for t in ts)
        side_text = "\n".join(getattr(p, "text", "") or "" for p in pages)
        for it in extracted.get(items_key, []):
            code = str(it.get("product_code") or "")
            item_no = it.get("item_no", "?")
            if not code:
                continue
            if re.search(rf"\b{re.escape(code)}\b", side_cells) or \
               re.search(rf"\b{re.escape(code)}\b", side_text):
                continue
            warnings.append({
                "source": "crosscheck", "check": "C2",
                "message": f"{side}项号{item_no} 商品编码{code}未在本侧表格单元格或页面文本中出现（疑幻影或跨侧污染）",
            })

    # ---- C1: 总价 ≈ 单价 × 某一数量 ----
    # quantity_unit 可能含多个数量（法定第一数量"6677千克"+成交数量"1712件"），
    # 单价对应的是成交数量而非法定数量，必须对所有数量候选尝试配对，
    # 任一匹配即通过——首个数量不匹配不代表错位（20260904008 误报教训）
    for side, items_key in (("报关单", "customs_items"), ("预录单", "pre_items")):
        for it in extracted.get(items_key, []):
            item_no = it.get("item_no", "?")
            unit_p, total_p = _num(it.get("unit_price")), _num(it.get("total_price"))
            if unit_p is None or total_p is None or total_p <= 0:
                continue
            qty_cands = [_num(m.group(1)) for m in
                         QTY_RE.finditer(str(it.get("quantity_unit") or ""))]
            qty_cands = [q for q in qty_cands if q]
            if not qty_cands:
                continue
            if any(abs(unit_p * q - total_p) <= max(1.0, total_p * 0.005) for q in qty_cands):
                continue
            warnings.append({
                "source": "crosscheck", "check": "C1",
                "message": f"{side}项号{item_no} 总价{total_p:g} 无法由单价{unit_p:g}×任一数量得出（{', '.join(f'{q:g}' for q in qty_cands)}），疑字段错位",
            })

    # ---- C3: 合同协议号 ----
    for side, header_key in (("报关单", "customs_header"), ("预录单", "pre_header")):
        contract = str(extracted.get(header_key, {}).get("contract_no") or "")
        if contract and contract not in all_cells:
            warnings.append({
                "source": "crosscheck", "check": "C3",
                "message": f"{side}合同协议号{contract}未在任何表格单元格中出现（疑提取错位）",
            })

    # ---- C4: 件数/毛重/净重（数值比较，标签与值同单元格或相邻单元格） ----
    for side, header_key in (("报关单", "customs_header"), ("预录单", "pre_header")):
        header = extracted.get(header_key, {})
        cell_nums = {_num(t) for t in re.split(r"\s+", all_cells) if PRICE_NUM_RE.match(t)}
        cell_nums.discard(None)
        for field, labels in HEADER_LABELS.items():
            val = _num(header.get(field))
            if val is None or not cell_nums:
                continue
            if any(abs(val - n) < 1e-6 for n in cell_nums):
                continue
            # 核对单单元格里标签与值常在同一个 cell（"毛重（千克）\n5093"），再按 cell 前缀找一遍
            label = labels if isinstance(labels, str) else labels[0]
            if any(label in t and str(int(val)) in t for t in all_cells.split("\n")):
                continue
            warnings.append({
                "source": "crosscheck", "check": "C4",
                "message": f"{side}{label}={val:g}未在任何表格单元格数值中出现（疑提取错位或报关单侧未填）",
            })

    # ---- C5: 两侧镜像——项号集合对称 + 同项号价格字段对称（#33） ----
    # 动机：提取层的安全网只判"结果非空"，单侧整条漏提/字段被吞（#26 丢 12 条、
    # #32 单商品续页价格被吞进数量列）在单侧视角下完全合法，只有拿另一侧做
    # 镜像才显形。C1 对"无价格"的 item 直接跳过，恰好放过了这类错——C5 补位。
    def _has_price(it):
        return _num(it.get("unit_price")) is not None or _num(it.get("total_price")) is not None

    cus_map = {str(it.get("item_no") or "").strip(): it
               for it in extracted.get("customs_items", [])
               if str(it.get("item_no") or "").strip()}
    pre_map = {str(it.get("item_no") or "").strip(): it
               for it in extracted.get("pre_items", [])
               if str(it.get("item_no") or "").strip()}

    def _no_sort_key(no):
        try:
            return (0, int(no), "")
        except ValueError:
            return (1, 0, no)

    for no in sorted(set(cus_map) | set(pre_map), key=_no_sort_key):
        in_c, in_p = no in cus_map, no in pre_map
        if in_c and not in_p:
            warnings.append({
                "source": "crosscheck", "check": "C5",
                "message": f"项号{no}仅报关单侧存在，预录单侧缺失（疑预录单漏提）",
            })
        elif in_p and not in_c:
            warnings.append({
                "source": "crosscheck", "check": "C5",
                "message": f"项号{no}仅预录单侧存在，报关单侧缺失（疑幻影商品或报关单漏提）",
            })
        else:
            c_price, p_price = _has_price(cus_map[no]), _has_price(pre_map[no])
            if c_price and not p_price:
                warnings.append({
                    "source": "crosscheck", "check": "C5",
                    "message": f"预录单项号{no}价格字段缺失，报关单侧有单价/总价（疑漏提或错位）",
                })
            elif p_price and not c_price:
                warnings.append({
                    "source": "crosscheck", "check": "C5",
                    "message": f"报关单项号{no}价格字段缺失，预录单侧有单价/总价（疑漏提或错位）",
                })

    return warnings
