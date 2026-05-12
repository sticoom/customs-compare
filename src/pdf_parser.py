"""
PDF 解析器：文本提取 + 文档类型识别
"""
import fitz  # PyMuPDF
import re
from dataclasses import dataclass, field


@dataclass
class PageInfo:
    """单页 PDF 解析结果"""
    page_index: int
    text: str
    doc_type: str = ""  # customs_declaration / pre_recording / contract / packing_list / invoice / unknown
    pdf_bytes: bytes = b""  # 原始 PDF 字节（用于位置感知提取）


@dataclass
class ParsedPDF:
    """一个 PDF 文件的解析结果"""
    filename: str
    pages: list = field(default_factory=list)

    @property
    def customs_pages(self) -> list:
        return [p for p in self.pages if p.doc_type == "customs_declaration"]

    @property
    def pre_recording_pages(self) -> list:
        return [p for p in self.pages if p.doc_type == "pre_recording"]

    @property
    def contract_pages(self) -> list:
        return [p for p in self.pages if p.doc_type == "contract"]


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """从 PDF 字节中提取所有文本"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = []
    for page in doc:
        texts.append(page.get_text())
    doc.close()
    return "\n".join(texts)


def parse_pdf(pdf_bytes: bytes, filename: str = "") -> ParsedPDF:
    """
    解析 PDF 文件：逐页提取文本并识别文档类型
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parsed = ParsedPDF(filename=filename)

    for i, page in enumerate(doc):
        text = page.get_text()
        page_info = PageInfo(page_index=i, text=text, pdf_bytes=pdf_bytes)
        page_info.doc_type = identify_doc_type(text)
        parsed.pages.append(page_info)

    doc.close()

    # 后处理：根据第一个明确识别的主文档类型，推断续页
    # 找第一个明确识别为 pre_recording 或 customs_declaration 的页面
    primary_type = None
    for p in parsed.pages:
        if p.doc_type in ("pre_recording", "customs_declaration"):
            # 检查是否是"明确"识别的（含出境关别或出口口岸）
            has_exit_customs = bool(re.search(r"出境关别\s*\(?\d*\)?\s*\n?\s*[\u4e00-\u9fff]+海关", p.text))
            has_export_port = bool(re.search(r"出口口岸\s*\n?\s*[-\s]*\n", p.text)) or bool(re.search(r"出口口岸\s*-", p.text))
            if has_exit_customs or has_export_port:
                primary_type = p.doc_type
                break

    # 如果找到了主类型，将不明确的续页归入同一类型
    if primary_type:
        for p in parsed.pages:
            # 情况1：doc_type 已经是 customs/pre，但既没有出境关别也没有出口口岸 → 续页
            if p.doc_type in ("customs_declaration", "pre_recording"):
                has_exit_customs = bool(re.search(r"出境关别\s*\(?\d*\)?\s*\n?\s*[\u4e00-\u9fff]+海关", p.text))
                has_export_port = bool(re.search(r"出口口岸\s*\n?\s*[-\s]*\n", p.text)) or bool(re.search(r"出口口岸\s*-", p.text))
                if not has_exit_customs and not has_export_port:
                    p.doc_type = primary_type

            # 情况2：doc_type 为 unknown，但含商品项号数据（纯数字+10位编码模式）→ 续页
            elif p.doc_type == "unknown":
                # 检查是否有项号+商品编码的模式（如 "30\n3926909090"）
                if re.search(r"\n\d{1,3}\n\d{8,10}\n", p.text) or re.match(r"\d{1,3}\n\d{8,10}\n", p.text):
                    p.doc_type = primary_type

    return parsed


def identify_doc_type(text: str) -> str:
    """
    根据文本内容识别文档类型

    规则：
    - 报关单：含"中华人民共和国海关出口货物报关单" 且 出口口岸值为"-"
    - 预录单：含"出境关别" 且 值不为空
    - 合同页：含"合同" + "CONTRACT"
    - 装箱单：含"装箱单"
    - 发票：含"发票" + "INVOICE"
    """
    # 先检查报关单（必须在预录单之前，因为两者可能都含"出口货物报关单"）
    if "中华人民共和国海关出口货物报关单" in text or "海关出口货物报关单" in text:
        # 检查出口口岸是否为空 → 报关单
        export_port_match = re.search(r"出口口岸\s*\n?\s*[-\s]*\n", text)
        has_empty_export_port = bool(export_port_match) or re.search(r"出口口岸\s*-", text)

        # 检查出境关别是否不为空 → 预录单
        exit_customs_match = re.search(r"出境关别\s*\(?\d*\)?\s*\n?\s*[\u4e00-\u9fff]+海关", text)

        if exit_customs_match:
            return "pre_recording"
        elif has_empty_export_port:
            return "customs_declaration"
        # 如果都不明确，优先按"整合申报"/"仅供核对"判断
        if "仅供核对" in text or "整合申报" in text:
            return "pre_recording"
        return "customs_declaration"

    # 合同页
    if ("合同" in text or "CONTRACT" in text) and ("卖方" in text or "Sellers" in text or "Buyers" in text or "买方" in text):
        return "contract"

    # 装箱单
    if "装箱单" in text or "PACKING LIST" in text:
        return "packing_list"

    # 发票
    if ("发票" in text or "INVOICE" in text) and "合计" in text:
        return "invoice"

    return "unknown"


def parse_multiple_pdfs(pdf_files: list) -> list:
    """
    批量解析多个 PDF 文件
    pdf_files: [(filename, bytes), ...]
    返回: [ParsedPDF, ...]
    """
    results = []
    for filename, data in pdf_files:
        if isinstance(data, str):
            # 如果传入的是文件路径
            with open(data, "rb") as f:
                data = f.read()
        parsed = parse_pdf(data, filename)
        results.append(parsed)
    return results


def get_page_text_by_type(parsed_list: list, doc_type: str) -> str:
    """从解析结果中获取指定类型所有页面的合并文本"""
    texts = []
    for parsed in parsed_list:
        for page in parsed.pages:
            if page.doc_type == doc_type:
                texts.append(page.text)
    return "\n\n".join(texts)


def extract_spans_with_positions(page_info: PageInfo) -> list:
    """
    从页面中提取所有文本 span 及其位置信息
    返回: [{"text": str, "x": float, "y": float, "x1": float, "y1": float}, ...]
    """
    doc = fitz.open(stream=page_info.pdf_bytes, filetype="pdf")
    page = doc[page_info.page_index]
    spans = []

    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    bbox = span["bbox"]
                    spans.append({
                        "text": text,
                        "x": bbox[0],
                        "y": bbox[1],
                        "x1": bbox[2],
                        "y1": bbox[3],
                    })

    doc.close()
    return spans


def extract_horizontal_lines(page_info: PageInfo) -> list:
    """
    从 PDF 页面中提取水平线位置（用于表格行分隔）
    返回: [{"y": float, "x_start": float, "x_end": float}, ...] 按 y 排序
    """
    doc = fitz.open(stream=page_info.pdf_bytes, filetype="pdf")
    page = doc[page_info.page_index]
    lines = []

    drawings = page.get_drawings()
    for d in drawings:
        for item in d.get("items", []):
            # 线段: (line, p1, p2)
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                # 水平线：y 坐标接近
                if abs(p1.y - p2.y) < 1.0:
                    lines.append({
                        "y": p1.y,
                        "x_start": min(p1.x, p2.x),
                        "x_end": max(p1.x, p2.x),
                    })
            # 矩形（细长矩形也是线）
            elif item[0] == "re":
                rect = item[1]
                # 细长矩形（高度 < 2pt）视为水平线
                if rect.height < 2.0 and rect.width > 50:
                    lines.append({
                        "y": rect.y0,
                        "x_start": rect.x0,
                        "x_end": rect.x1,
                    })

    doc.close()
    lines.sort(key=lambda l: l["y"])
    return lines


def extract_pre_recording_fields_by_position(page_info: PageInfo) -> dict:
    """
    用位置感知方式从预录单中提取字段
    利用预录单的双列表格布局：标签行(y) → 值行(y+10~15)
    """
    spans = extract_spans_with_positions(page_info)
    fields = {}

    # 按 y 坐标分组（容差 5px）形成行
    rows = {}
    for s in spans:
        y_key = round(s["y"] / 5) * 5  # 5px 精度分组
        if y_key not in rows:
            rows[y_key] = []
        rows[y_key].append(s)

    # 按 y 排序
    sorted_y = sorted(rows.keys())

    # 构建行列表：每行是 [(text, x), ...]
    row_list = []
    for y in sorted_y:
        row_spans = sorted(rows[y], key=lambda s: s["x"])
        row_texts = []
        current_x = -1
        for s in row_spans:
            if current_x >= 0 and s["x"] - current_x > 20:
                # 同一行中有明显间隔，作为新单元格
                pass
            row_texts.append((s["text"], s["x"]))
            current_x = s["x1"]
        row_list.append((y, row_texts))

    # 构建 label → value 映射
    # 预录单的布局：label 行后面紧跟 value 行
    # 我们搜索已知的标签，然后在下一行对应 x 位置找值

    label_value_map = {}  # label_text → value_text

    # 方法：扫描所有 span，找到标签，然后在下方（y+8~20）相同 x 位置找值
    span_list = sorted(spans, key=lambda s: (s["y"], s["x"]))

    # 预录单标签 → 字段 ID 映射
    label_to_field = {
        "境内发货人": "sender_unit",
        "境外收货人": "buyer",
        "生产销售单位": "business_unit",
        "合同协议号": "contract_no",
        "出境关别": "exit_customs",
        "运输方式": "transport_mode",
        "监管方式": "trade_mode",
        "贸易国（地区）": "trade_country",
        "贸易国(地区)": "trade_country",
        "运抵国（地区）": "dest_country",
        "运抵国(地区)": "dest_country",
        "指运港": "dest_port",
        "离境口岸": "exit_port",
        "包装种类": "package_type",
        "件数": "quantity",
        "毛重(千克)": "gross_weight",
        "毛重（千克）": "gross_weight",
        "净重(千克)": "net_weight",
        "净重（千克）": "net_weight",
        "成交方式": "deal_mode",
        "征免性质": "duty_nature",
        "随附单证及编号": "attached_docs",
        "标记唛码及备注": "marks_remarks",
    }

    # 需要从标签行收集代码的字段（fixed 类型，值必须含括号代码）
    fields_need_inline_code = {
        "package_type", "trade_mode", "trade_country",
        "deal_mode", "duty_nature", "duty_exemption",
    }

    for span in span_list:
        text = span["text"]
        # 检查是否匹配已知标签
        # 只移除字母数字代码括号如 (0110)、(HKG)，保留中文括号如 (地区)、(千克)
        clean_text = re.sub(r"\([A-Za-z0-9]+\)", "", text).strip()
        # 从原始文本中提取标签内嵌的代码（如 "监管方式(0110)" → "(0110)"）
        embedded_codes = re.findall(r"\([A-Za-z0-9]+\)", text)
        label_inline_code = ""
        if embedded_codes:
            label_inline_code = embedded_codes[0]

        matched_field = None
        for label, fid in label_to_field.items():
            if label in clean_text or label == clean_text:
                matched_field = fid
                break

        if matched_field and matched_field not in fields:
            x_center = span["x"]
            y_label = span["y"]
            value = ""

            # 计算右边界：取同行右侧最近标签的 x - 5
            x_max = x_center + 160
            for other in span_list:
                if abs(other["y"] - y_label) < 3 and other["x"] > x_center + 20:
                    other_clean = re.sub(r"\(\d+\)", "", other["text"]).strip()  # 只移除数字代码
                    # 检查是否是已知标签
                    for lbl in label_to_field:
                        if lbl in other_clean:
                            if other["x"] - 5 < x_max:
                                x_max = other["x"] - 5
                            break

            x_min = x_center - 5

            # 找标签同行的代码 span（如 "(0110)"、"(HKG)"、"(3)" 等）
            # 预录单中这些代码在标签右侧同一行，是值的组成部分
            inline_code = ""
            for other in span_list:
                if abs(other["y"] - y_label) < 3 and other["x"] > x_center and other["x"] < x_max:
                    if re.match(r"^\([A-Za-z0-9]+\)$", other["text"].strip()):
                        if other["text"].strip() != text.strip():
                            inline_code = other["text"].strip()

            # 精确搜索：标签正下方 y+5~20
            candidates = []
            for other in span_list:
                if other["y"] > y_label + 3 and other["y"] < y_label + 20:
                    if other["x"] >= x_min and other["x"] < x_max:
                        other_clean = re.sub(r"\([A-Za-z0-9]+\)", "", other["text"]).strip()
                        # 跳过其他已知标签
                        is_label = False
                        for lbl in label_to_field:
                            if lbl in other_clean:
                                is_label = True
                                break
                        # 跳过纯代码（已在上面通过 inline_code 收集）
                        if re.match(r"^\([A-Za-z0-9]+\)$", other["text"].strip()):
                            continue
                        if not is_label and other_clean and other_clean != "-":
                            candidates.append((other["y"], other["x"], other["text"]))

            # 只取最靠近标签的那一行
            if candidates:
                candidates.sort(key=lambda c: (c[0], c[1]))
                first_y = candidates[0][0]
                value_parts = [c[2] for c in candidates if abs(c[0] - first_y) < 3]
                value = " ".join(value_parts)

            # 如果标签本身后面就跟着值（同行）
            if not value:
                label_text_only = re.sub(r"\(.*?\)", "", text).strip()
                remainder = text.replace(label_text_only, "").strip()
                if remainder:
                    value = remainder.strip("() ")

            # 拼上代码（仅 fixed 类型字段需要）
            # 代码来源有两个：标签同行右侧的独立代码 span，或标签文本内嵌的代码
            if matched_field in fields_need_inline_code:
                code = inline_code or label_inline_code
                if code and value:
                    value = f"{code}{value}"
                elif code and not value:
                    value = code

            fields[matched_field] = value

    # 后处理：清理常见格式问题
    for fid in fields:
        v = fields[fid]
        # 清理包装种类中的分割符
        v = v.replace("/ ", "/").replace(" /", "/").replace("  ", " ").strip()
        fields[fid] = v

    return fields


def extract_pre_recording_items_by_position(page_info: PageInfo) -> list:
    """
    用位置感知方式从预录单中提取商品明细
    动态检测列位置：从表头行读取各列的 x 坐标，不依赖固定值
    """
    spans = extract_spans_with_positions(page_info)

    # ---- 第一步：找到表头行并提取列位置 ----
    # 表头关键词 → 列 ID 映射
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

    # 查找含"项号"或"商品编号"的 span，确定表头行 y
    header_y = None
    for s in spans:
        if "项号" in s["text"] or s["text"].startswith("商品"):
            header_y = s["y"]
            break

    if header_y is None:
        return []

    # 收集表头行所有 span（y 容差 ±5）
    header_spans = [s for s in spans if abs(s["y"] - header_y) < 5]

    # 建立 列ID → x_center 映射
    col_positions = {}  # col_id → x_center
    for s in header_spans:
        for keyword, col_id in header_keywords.items():
            if keyword in s["text"]:
                # 避免重复设置（如"单价/总价/币制"只设一次 price）
                if col_id not in col_positions:
                    col_positions[col_id] = s["x"]
                break

    # 如果"商品编号"没有单独的列头，但"项号商品编号"合在一起
    if "product_code" not in col_positions and "item_no" in col_positions:
        for s in header_spans:
            if "商品编号" in s["text"] and "项号" not in s["text"]:
                col_positions["product_code"] = s["x"]
                break
        # 如果"项号商品编号"是一体的，从数据中推断商品编号的 x 位置
        if "product_code" not in col_positions:
            for s in header_spans:
                if "项号" in s["text"] and "商品编号" in s["text"]:
                    # 找数据中在 item_no 旁边的长数字（商品编码，8-10位）
                    data_spans_header = [sp for sp in spans if sp["y"] > header_y + 5]
                    for ds in data_spans_header:
                        if re.match(r"^\d{8,10}$", ds["text"].strip()):
                            col_positions["product_code"] = ds["x"]
                            break
                    # 如果找不到数据，用偏移估计
                    if "product_code" not in col_positions:
                        col_positions["product_code"] = s["x"] + 30
                    break

    # 如果"数量"和"单价/总价/币制"合在一个 span 里（续页常见），
    # 需要从数据中推断 price 列的位置
    if "price" not in col_positions and "quantity" in col_positions:
        # 找数据中看起来像价格的 span（纯数字带小数点，如 60.2900）
        data_spans_after_header = [sp for sp in spans if sp["y"] > header_y + 5]
        qty_x = col_positions["quantity"]
        # 价格通常在数量列的右侧，找比 quantity x 更大且看起来像价格的 span
        price_candidates = []
        for ds in data_spans_after_header:
            if re.match(r"^\d+\.\d{2,4}$", ds["text"].strip()):
                # 价格数字的 x 应该大于 quantity x，且在 origin_country 之前
                if ds["x"] > qty_x + 30:
                    price_candidates.append(ds["x"])
        if price_candidates:
            # 取中位数作为 price 列位置
            price_candidates.sort()
            col_positions["price"] = price_candidates[len(price_candidates) // 2]
        elif "origin_country" in col_positions:
            # 退而求其次：在 quantity 和 origin_country 之间取中点
            col_positions["price"] = (qty_x + col_positions["origin_country"]) / 2

    if "item_no" not in col_positions:
        return []

    # ---- 第二步：动态计算列边界 ----
    # 列边界 = [(col_id, x_start, x_end), ...]
    # x_start = 当前列的 x_center - 10
    # x_end = 下一列的 x_center - 5（或页面右边）
    sorted_cols = sorted(col_positions.items(), key=lambda c: c[1])

    col_boundaries = []
    for i, (col_id, x_center) in enumerate(sorted_cols):
        x_start = x_center - 15
        if i + 1 < len(sorted_cols):
            x_end = sorted_cols[i + 1][1] - 5
        else:
            x_end = 900  # 页面右边
        col_boundaries.append((col_id, x_start, x_end))

    def get_col_id(x):
        for col_id, x_start, x_end in col_boundaries:
            if x_start <= x < x_end:
                return col_id
        return None

    # ---- 第三步：提取数据行 ----
    data_spans = [s for s in spans if s["y"] > header_y + 5]
    if not data_spans:
        return []

    # 检测页脚位置（遇到以下文字停止提取）
    footer_keywords = ["特殊关系确认", "申报单位", "报关人员", "兹申明", "自报自缴", "自缴自报"]
    footer_y = 9999
    for s in data_spans:
        for kw in footer_keywords:
            if kw in s["text"]:
                if s["y"] < footer_y:
                    footer_y = s["y"]
                break

    # 过滤掉页脚区域
    data_spans = [s for s in data_spans if s["y"] < footer_y - 2]
    if not data_spans:
        return []

    # 找到所有项号的 y 位置（在 item_no 列范围内的数字）
    # 项号通常只有1-3位数字，商品编号有8-10位，用长度区分
    item_x_start = col_positions.get("item_no", 0) - 15
    # item_no 列右边界：确保不包含商品编号（8-10位数字）
    if "product_code" in col_positions:
        item_x_end = col_positions["product_code"] - 2
    else:
        item_x_end = col_positions.get("item_no", 0) + 30

    item_start_ys = []
    for s in data_spans:
        text = s["text"].strip()
        # 项号: 纯数字, 1-3位 (01, 02, 1, 2, 10, etc.)
        if item_x_start <= s["x"] < item_x_end and re.match(r"^\d{1,3}$", text):
            item_start_ys.append(s["y"])

    if not item_start_ys:
        return []

    # ---- 第四步：用水平线精确划分项目边界 ----
    # 提取 PDF 中的水平线
    h_lines = extract_horizontal_lines(page_info)

    # 找到表格区域内的水平线（在 header 下方、页脚上方）
    # 表格的水平线可能被分成多段短线（被垂直列线打断），所以宽度阈值不能太高
    # 策略：先按 y 坐标分组，如果同一 y 位置有多条线段且总跨度足够，视为表格行分隔线
    from collections import defaultdict
    y_groups = defaultdict(list)
    for l in h_lines:
        if l["y"] > header_y and l["y"] < footer_y and l["x_end"] - l["x_start"] > 5:
            y_key = round(l["y"], 0)
            y_groups[y_key].append(l)

    # 每组中，如果最左到最右的总跨度覆盖了表格宽度，视为表格行分隔线
    table_left = col_positions.get("item_no", 0) - 20
    table_right = max(x for x in col_positions.values()) + 30
    table_lines = []
    for y_key, segs in y_groups.items():
        min_x = min(s["x_start"] for s in segs)
        max_x = max(s["x_end"] for s in segs)
        total_span = max_x - min_x
        # 表格行分隔线应覆盖大部分表格宽度
        if total_span > (table_right - table_left) * 0.5:
            table_lines.append({"y": y_key, "x_start": min_x, "x_end": max_x})

    table_lines.sort(key=lambda l: l["y"])

    # 用水平线构建行槽位 (row slots)
    # 每个 slot = [line_top_y, line_bottom_y]
    if len(table_lines) >= 2:
        row_slots = []
        for i in range(len(table_lines) - 1):
            row_slots.append((table_lines[i]["y"], table_lines[i + 1]["y"]))
    else:
        # 没有水平线数据，退回到项号 y 坐标分组
        row_slots = None

    # 判断每个 slot 是否有数据（检查是否有非 item_no 列的文本）
    def slot_has_data(y_top, y_bottom):
        for s in data_spans:
            if y_top < s["y"] < y_bottom:
                col = get_col_id(s["x"])
                if col and col != "item_no":
                    return True
        return False

    # 按 item 分组
    items = []

    if row_slots:
        # 使用水平线精确分组
        # 找到每个项号所在的 slot
        item_slot_map = []  # [(item_no_text, slot_idx), ...]
        for s in data_spans:
            text = s["text"].strip()
            if item_x_start <= s["x"] < item_x_end and re.match(r"^\d{1,3}$", text):
                # 找到这个项号在哪个 slot
                for si, (y_top, y_bottom) in enumerate(row_slots):
                    if y_top < s["y"] < y_bottom:
                        item_slot_map.append((text, si))
                        break

        # 按 slot 分组
        processed_slots = set()
        for item_text, slot_idx in item_slot_map:
            if slot_idx in processed_slots:
                continue
            processed_slots.add(slot_idx)

            y_top, y_bottom = row_slots[slot_idx]
            item_spans = [s for s in data_spans if y_top < s["y"] < y_bottom]

            # 按 x 列分组
            cols = {}
            for s in item_spans:
                col = get_col_id(s["x"])
                if col:
                    stext = s["text"].strip()
                    # 修正列分配
                    if col == "item_no" and re.match(r"^\d{6,}$", stext):
                        col = "product_code"
                    elif col == "product_code" and not re.match(r"^\d{6,}$", stext):
                        col = "product_name"
                    if col not in cols:
                        cols[col] = []
                    cols[col].append(s["text"])

            item = {
                "item_no": str(int(cols.get("item_no", ["0"])[0])) if cols.get("item_no") and cols["item_no"][0].isdigit() else cols.get("item_no", [""])[0],
                "product_code": (cols.get("product_code") or [""])[0],
                "product_name": (cols.get("product_name") or [""])[0],
                "spec_model": " ".join(cols.get("product_name", [])[1:]),
                "quantity_unit": " / ".join(cols.get("quantity", [])),
                "unit_price": cols.get("price", [""])[0] if cols.get("price") else "",
                "total_price": cols.get("price", ["", ""])[1] if len(cols.get("price", [])) >= 2 else "",
                "currency": cols.get("price", ["", "", ""])[2] if len(cols.get("price", [])) >= 3 else "",
                "origin_country": " ".join(cols.get("origin_country", [])),
                "final_dest_country": " ".join(cols.get("dest_country", [])),
                "domestic_source": " ".join(cols.get("source", [])),
                "duty_exemption": " ".join(cols.get("duty", [])),
            }

            if "人民币" in item.get("currency", ""):
                item["currency"] = "人民币"

            items.append(item)
    else:
        # 退回到基于项号 y 坐标的分组（旧逻辑）
        first_item_y = item_start_ys[0]
        has_data_above_first = any(s["y"] < first_item_y - 2 and s["y"] > header_y + 3
                                   for s in data_spans
                                   if get_col_id(s["x"]) and get_col_id(s["x"]) != "item_no")

        for idx, start_y in enumerate(item_start_ys):
            end_y = item_start_ys[idx + 1] if idx + 1 < len(item_start_ys) else 9999
            main_spans = [s for s in data_spans if start_y - 2 <= s["y"] < end_y - 2]

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

            item_spans = sorted(main_spans + above_spans, key=lambda s: (s["y"], s["x"]))

            cols = {}
            for s in item_spans:
                col = get_col_id(s["x"])
                if col:
                    stext = s["text"].strip()
                    if col == "item_no" and re.match(r"^\d{6,}$", stext):
                        col = "product_code"
                    elif col == "product_code" and not re.match(r"^\d{6,}$", stext):
                        col = "product_name"
                    if col not in cols:
                        cols[col] = []
                    cols[col].append(s["text"])

            item = {
                "item_no": str(int(cols.get("item_no", ["0"])[0])) if cols.get("item_no") and cols["item_no"][0].isdigit() else cols.get("item_no", [""])[0],
                "product_code": (cols.get("product_code") or [""])[0],
                "product_name": (cols.get("product_name") or [""])[0],
                "spec_model": " ".join(cols.get("product_name", [])[1:]),
                "quantity_unit": " / ".join(cols.get("quantity", [])),
                "unit_price": cols.get("price", [""])[0] if cols.get("price") else "",
                "total_price": cols.get("price", ["", ""])[1] if len(cols.get("price", [])) >= 2 else "",
                "currency": cols.get("price", ["", "", ""])[2] if len(cols.get("price", [])) >= 3 else "",
                "origin_country": " ".join(cols.get("origin_country", [])),
                "final_dest_country": " ".join(cols.get("dest_country", [])),
                "domestic_source": " ".join(cols.get("source", [])),
                "duty_exemption": " ".join(cols.get("duty", [])),
            }

            if "人民币" in item.get("currency", ""):
                item["currency"] = "人民币"

            items.append(item)

    # 后处理：拆分货源地中合并的征免信息
    # 常见格式：
    #   "(44199)东莞(1)-照章征税" — 括号数字开头的货源地+征免
    #   "福州其他 (35019) 照章征税" — 城市名+代码+征免
    #   "东莞 (44199) 照章征税" — 城市名+代码+征免
    for item in items:
        src = item.get("domestic_source", "")
        duty = item.get("duty_exemption", "")

        # 货源地中混入了 "照章征税"（跨列导致）
        if src and ("照章" in src or "免税" in src):
            cleaned = re.sub(r"\(\d{4,6}\)", "", src)
            cleaned = re.sub(r"照章.*$", "", cleaned)
            cleaned = cleaned.strip()
            if cleaned:
                item["domestic_source"] = cleaned
            # 只有征免列为空或只有纯代码 "(1)" 时，才从货源地补入
            if "照章" in src:
                if not duty:
                    item["duty_exemption"] = "照章征税(1)"
                elif duty == "(1)":
                    item["duty_exemption"] = "照章征税(1)"

        # 不做盲目规范化 — 保留原始提取值，让比对引擎判断对错
        # 例如 "(1)-照章" ≠ "照章征税(1)"，应如实展示为不通过

        # 修货源地：清除区域代码如 (33079)、(44199) 和征免代码 (1)
        src = item.get("domestic_source", "")
        if src:
            src = re.sub(r"\(\d{4,6}\)", "", src)
            src = re.sub(r"\(1\)\s*$", "", src)
            src = src.strip()
            item["domestic_source"] = src

    return items
