"""
PDF 解析器：文本提取 + 文档类型识别
"""
import fitz  # PyMuPDF
import re
from dataclasses import dataclass, field

# 统一价格匹配：支持整数(5)、小数(25.28)、千分位(1,234.56)、多位小数(25.2800)
PRICE_RE = re.compile(r"^\d[\d,]*\.?\d*$")


def _is_empty_item(item: dict) -> bool:
    """检查商品项是否完全没有有效数据"""
    core_fields = ("product_code", "product_name", "quantity_unit",
                   "unit_price", "total_price")
    return not any(item.get(f) for f in core_fields)


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
            # "仅供核对"格式：虽然没有出境关别值，但已明确为预录单
            has_hedui = "仅供核对" in p.text or "核对单" in p.text
            if has_exit_customs or has_export_port or has_hedui:
                primary_type = p.doc_type
                break

    # fallback：新模板(如 20260625 录入单)首页 identify_doc_type 已正确判为 pre/customs，
    # 但既无"出境关别值"、也无"出口口岸"、也无"仅供核对"标记，上方强证据路径全部 miss，
    # primary_type 保持 None → 续页(只有商品、被判 customs)无法被纠正回主类型，污染报关单侧。
    # 此时信任 identify_doc_type 的判定：取首个 pre/customs 页作为 primary_type。
    # 详见 docs/memory.md #15。
    if primary_type is None:
        for p in parsed.pages:
            if p.doc_type in ("pre_recording", "customs_declaration"):
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
        # 0. 海关编号已签发（有合法值）→ 报关单（最可靠判据，优先级最高）
        #    预录单/核对单的海关编号为空，或后跟标签词（备案号/预录入编号/null），
        #    不会误命中。修复 20260625001 这类用"境内发货人/监管方式"标签排版
        #    的报关单被规则 3 误判为预录单的问题。详见 docs/memory.md #15。
        if re.search(r"海关编号[:：\s]*[0-9][0-9A-Z]{5,}", text):
            return "customs_declaration"

        # 1. 出境关别有值 → 预录单
        exit_customs_match = re.search(r"出境关别\s*\(?\d*\)?\s*\n?\s*[\u4e00-\u9fff]+海关", text)
        if exit_customs_match:
            return "pre_recording"

        # 2. "整合申报" / "仅供核对" → 预录单
        if "仅供核对" in text or "整合申报" in text:
            return "pre_recording"

        # 3. 预录单特有标签（境内发货人、监管方式、境外收货人）→ 预录单
        if "境内发货人" in text or "监管方式" in text or "境外收货人" in text:
            return "pre_recording"

        # 4. 报关单特有标签（经营单位）→ 报关单
        #    报关单也有"预录入编号"字段（空值），需通过标签区分
        if "经营单位" in text:
            return "customs_declaration"

        # 5. 预录入编号在项号之前 → 预录单（兜底，部分旧格式预录单只有此标识）
        pre_input_pos = text.find("预录入编号")
        xianghao_pos = text.find("项号")
        if pre_input_pos >= 0 and (xianghao_pos < 0 or pre_input_pos < xianghao_pos):
            return "pre_recording"

        # 6. 出口口岸为空 → 报关单
        export_port_match = re.search(r"出口口岸\s*\n?\s*[-\s]*\n", text)
        has_empty_export_port = bool(export_port_match) or re.search(r"出口口岸\s*-", text)
        if has_empty_export_port:
            return "customs_declaration"
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


def extract_customs_header_by_grid(page_info: PageInfo) -> dict:
    """
    网格排版报关单表头提取（标签行 + 值行，按 x 列上下对齐）。

    适用于 20260625001 这类新排版报关单：标签与值分行排列，同一字段的标签
    和值 x 对齐、y 相差约一行；空值省略不显示。文本正则会因标签值不紧贴、
    空值错位而失效，必须用坐标。详见 docs/memory.md #15。

    返回: {field_id: value}
    """
    # 新排版报关单用的是「境内发货人/监管方式/征免性质」这套标签（与预录单
    # pre_field 一致），而非老排版的「发货单位/经营单位」。
    LABEL_MAP = {
        "境内发货人": "sender_unit",
        "境外收货人": "buyer",
        "生产销售单位": "business_unit",
        "合同协议号": "contract_no",
        "包装种类": "package_type",
        "运输方式": "transport_mode",
        "监管方式": "trade_mode",
        "贸易国": "trade_country",
        "贸易国（地区）": "trade_country",
        "件数": "quantity",
        "毛重（千克）": "gross_weight",
        "毛重(千克)": "gross_weight",
        "净重（千克）": "net_weight",
        "净重(千克)": "net_weight",
        "成交方式": "deal_mode",
        "征免性质": "duty_nature",
        "运抵国（地区）": "dest_country",
        "运抵国(地区)": "dest_country",
        "指运港": "dest_port",
        "离境口岸": "exit_port",
        "出境关别": "exit_customs",
        "随附单证及编号": "attached_docs",
        "标记唛码及备注": "marks_remarks",
    }

    spans = extract_spans_with_positions(page_info)
    if not spans:
        return {}

    fields = {}
    for sp in spans:
        field_id = LABEL_MAP.get(sp["text"].strip())
        if not field_id or field_id in fields:
            continue
        x, y = sp["x"], sp["y"]
        # 在标签正下方（0 < dy < 14）找 x 最接近的非空 span 作为值
        best, best_dx = None, 18.0
        for cand in spans:
            dy = cand["y"] - y
            if 0 < dy < 14:
                dx = abs(cand["x"] - x)
                if dx < best_dx:
                    val = cand["text"].strip()
                    if val and val != "-":
                        best, best_dx = val, dx
        if best:
            fields[field_id] = best
    return fields


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
    用位置感知方式从预录单中提取字段。
    支持两种布局：
    1. 标准预录单：标签在上，值在下方 y+5~20
    2. "仅供核对用"格式：标签和值散布在页面各处，但值与标签在同一 x 列
    """
    spans = extract_spans_with_positions(page_info)
    fields = {}

    # 检测是否为"仅供核对用"格式（标签底部、值上方的乱序排版）
    is_hedui = "仅供核对" in page_info.text or "整合申报" in page_info.text
    # 无"仅供核对"标记的核对单变体（如 0060228GDM）：核心表头标签印在页面底部
    # (y>500)，而标准预录单的标签在顶部 (y<200)。详见 docs/memory.md #16。
    if not is_hedui:
        for _s in spans:
            if _s["text"].strip() in ("境内发货人", "境外收货人", "合同协议号") and _s["y"] > 500:
                is_hedui = True
                break

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

    def _is_known_label(clean_text):
        """检查文本是否匹配已知标签"""
        for lbl in label_to_field:
            if lbl in clean_text or lbl == clean_text:
                return True
        return False

    def _get_x_bounds(span, all_spans):
        """计算标签对应的 x 搜索范围"""
        x_center = span["x"]
        y_label = span["y"]

        if is_hedui:
            # "仅供核对用"格式：值在同一 x 列，宽度 ±25px
            return x_center - 10, x_center + 80

        # 标准格式：右边界取同行右侧最近标签
        x_max = x_center + 160
        for other in all_spans:
            if abs(other["y"] - y_label) < 3 and other["x"] > x_center + 20:
                other_clean = re.sub(r"\(\d+\)", "", other["text"]).strip()
                for lbl in label_to_field:
                    if lbl in other_clean:
                        if other["x"] - 5 < x_max:
                            x_max = other["x"] - 5
                        break
        return x_center - 5, x_max

    # "仅供核对用"格式的额外排除文本（非值字段标签/元数据）
    _hedui_noise_labels = {
        "预录入编号", "海关编号", "备案号", "申报日期", "出口日期",
        "提运单号", "运输工具名称及航次号", "许可证号", "杂费", "保费", "运费",
        "特殊关系确认", "价格影响确认", "支付特许权使用费确认",
        "公式定价确认", "暂定价格确认", "自报自缴", "水运中转",
        "申报单位", "电话", "报关人员证号", "报关人员",
        "兹申明", "申报单位（签章）", "海关批注及签章",
        "商品编号", "项号", "商品名称及规格型号", "数量及单位",
        "单价/总价/币制", "原产国(地区)", "原产国（地区）",
        "最终目的国(地区)", "最终目的国（地区）",
        "境内货源地", "征免",
        "中华人民共和国海关出口货物报关单", "页码/页数",
        "仅供核对用", "打印时间",
    }

    def _is_noise_span(text, field_id=None):
        """检查是否为非值文本（标签、元数据、噪声等）"""
        clean = re.sub(r"\([A-Za-z0-9]+\)", "", text).strip()
        if not clean:
            return True
        # 精确匹配噪声标签
        for noise in _hedui_noise_labels:
            if noise in clean or clean in noise:
                return True
        # 含中文冒号的行（如 "预录入编号："）
        if re.search(r"[：:]$", clean):
            return True
        # 页码格式
        if re.match(r"^\d+/\d+$", clean):
            return True
        # 纯数字编号格式（如海关编号 4403961BEF）— 但合同协议号本身就是纯数字
        if field_id != "contract_no" and re.match(r"^\d{8,}$", clean):
            return True
        # 条码
        if clean.startswith("*"):
            return True
        # 纯代码
        if re.match(r"^\([A-Za-z0-9]+\)$", text.strip()):
            return True
        return False

    def _find_value_by_column(label_span, all_spans, field_id):
        """
        "仅供核对用"格式专用：在同一 x 列中找最近的非标签 span 作为值。
        值可能在标签上方或下方，距离不固定（10px ~ 500px）。
        """
        x_center = label_span["x"]
        y_label = label_span["y"]

        # 动态计算 x 搜索范围：取该标签到右侧最近标签的中点
        # 如果没有右侧标签，使用 x_center + 20（收窄：核对单表头值相对标签
        # 的 x 缩进稳定在 10-12px，过宽会把相邻列值抓进来，如贸易国抓到毛重 366、
        # 征免性质抓到运抵国加拿大）。详见 docs/memory.md #16。
        x_max = x_center + 20
        y_label_approx = round(y_label / 3) * 3
        for other in all_spans:
            if abs(other["y"] - y_label) < 5 and other["x"] > x_center + 5:
                other_clean = re.sub(r"\([A-Za-z0-9]+\)", "", other["text"]).strip()
                if _is_known_label(other_clean):
                    mid = (x_center + other["x"]) / 2
                    if mid < x_max:
                        x_max = mid
        x_min = x_center - 5

        candidates = []
        for other in all_spans:
            if other is label_span:
                continue
            y_diff = other["y"] - y_label
            if abs(y_diff) < 2:
                continue
            if not (other["x"] >= x_min and other["x"] < x_max):
                continue

            other_text = other["text"].strip()

            # 跳过噪声
            if _is_noise_span(other_text, field_id):
                continue
            # 跳过已知标签
            other_clean = re.sub(r"\([A-Za-z0-9]+\)", "", other_text).strip()
            if _is_known_label(other_clean):
                continue

            candidates.append(other)

        if not candidates:
            # 如果精确列范围无结果，稍微扩大（x_center -10 到 x_center + 45）
            for other in all_spans:
                if other is label_span:
                    continue
                y_diff = other["y"] - y_label
                if abs(y_diff) < 2:
                    continue
                if not (other["x"] >= x_center - 10 and other["x"] < x_center + 45):
                    continue
                other_text = other["text"].strip()
                if _is_noise_span(other_text, field_id):
                    continue
                other_clean = re.sub(r"\([A-Za-z0-9]+\)", "", other_text).strip()
                if _is_known_label(other_clean):
                    continue
                candidates.append(other)

        if not candidates:
            return "", ""

        # 按绝对 y 距离排序，取最近的
        candidates.sort(key=lambda c: abs(c["y"] - y_label))

        # 取最靠近的同一行（y 容差 ±3px）的 span 组合为值
        closest = candidates[0]
        closest_y = closest["y"]
        same_row = [c for c in candidates if abs(c["y"] - closest_y) < 3]
        same_row.sort(key=lambda c: c["x"])

        # 组装值文本
        value_parts = []
        code_part = ""
        for c in same_row:
            txt = c["text"].strip()
            if re.match(r"^\([A-Za-z0-9]+\)$", txt):
                code_part = txt
            else:
                value_parts.append(txt)
        value = " ".join(value_parts)
        return value, code_part

    for span in span_list:
        text = span["text"]
        clean_text = re.sub(r"\([A-Za-z0-9]+\)", "", text).strip()
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
            inline_code = ""

            if is_hedui:
                # "仅供核对用"格式：x 列匹配
                value, col_code = _find_value_by_column(span, span_list, matched_field)
                if col_code:
                    inline_code = col_code
            else:
                # 标准格式
                x_min, x_max = _get_x_bounds(span, span_list)

                # 找标签同行的代码 span
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
                            if _is_known_label(other_clean):
                                continue
                            if re.match(r"^\([A-Za-z0-9]+\)$", other["text"].strip()):
                                continue
                            if other_clean and other_clean != "-":
                                candidates.append((other["y"], other["x"], other["text"]))

                # 扩大搜索 ±50px
                if not candidates:
                    for other in span_list:
                        y_diff = other["y"] - y_label
                        if abs(y_diff) > 3 and abs(y_diff) < 50:
                            if other["x"] >= x_min and other["x"] < x_max:
                                other_clean = re.sub(r"\([A-Za-z0-9]+\)", "", other["text"]).strip()
                                if _is_known_label(other_clean):
                                    continue
                                if re.match(r"^\([A-Za-z0-9]+\)$", other["text"].strip()):
                                    continue
                                if re.match(r"^\d+/\d+$", other_clean):
                                    continue
                                if other_clean.startswith("*"):
                                    continue
                                if other_clean and other_clean != "-":
                                    candidates.append((other["y"], other["x"], other["text"]))

                if candidates:
                    candidates.sort(key=lambda c: (c[0], c[1]))
                    first_y = candidates[0][0]
                    value_parts = [c[2] for c in candidates if abs(c[0] - first_y) < 3]
                    value = " ".join(value_parts)

                # 标签同行值
                if not value:
                    label_text_only = re.sub(r"\(.*?\)", "", text).strip()
                    remainder = text.replace(label_text_only, "").strip()
                    if remainder:
                        value = remainder.strip("() ")

            # 拼上代码（仅 fixed 类型字段需要）
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
        v = v.replace("/ ", "/").replace(" /", "/").replace("  ", " ").strip()
        fields[fid] = v

    return fields


def _extract_items_vertical_layout(spans, page_info):
    """
    从纵向列头布局的核对单中提取商品明细。
    纵向布局：所有商品表列头（项号、商品编号等）垂直排列在同一个 x 位置，
    数据在右侧相邻列中。
    """
    # 1. 检测纵向布局：查找已知的表格列头关键词
    vertical_header_keywords = [
        ("项号", "item_no"),
        ("商品编号", "product_code"),
        ("商品名称及规格型号", "product_name_spec"),
        ("数量及单位", "quantity"),
        ("单价/总价/币制", "price"),
        ("原产国(地区)", "origin_country"),
        ("原产国（地区）", "origin_country"),
        ("最终目的国(地区)", "dest_country"),
        ("最终目的国（地区）", "dest_country"),
        ("境内货源地", "source"),
        ("征免", "duty"),
    ]

    # Find all header spans
    header_entries = []  # (x, y, col_id, text)
    for s in spans:
        text = s["text"].strip()
        for keyword, col_id in vertical_header_keywords:
            if keyword in text:
                header_entries.append((s["x"], s["y"], s["y1"], col_id, text))
                break

    if len(header_entries) < 3:
        return []

    # Group by x position to find dominant x (vertical column)
    from collections import defaultdict
    x_groups = defaultdict(list)
    for x, y, y1, col_id, text in header_entries:
        rx = round(x, 0)
        x_groups[rx].append((x, y, y1, col_id, text))

    # Find the x position with most headers
    best_x = max(x_groups, key=lambda k: len(x_groups[k]))
    if len(x_groups[best_x]) < 3:
        return []

    header_x = best_x

    # Build header map: col_id → y (deduplicate, keep first match)
    header_map = {}
    for x, y, y1, col_id, text in x_groups[best_x]:
        if col_id not in header_map:
            header_map[col_id] = y

    # 2. Collect data spans to the right of headers
    x_min = header_x + 5
    x_max = header_x + 80

    # Header keyword texts to exclude from data
    header_texts = {"项号", "商品编号", "商品名称及规格型号", "数量及单位",
                    "单价/总价/币制", "原产国(地区)", "原产国（地区）",
                    "最终目的国(地区)", "最终目的国（地区）", "境内货源地", "征免"}

    noise_labels = {
        "预录入编号", "海关编号", "备案号", "申报日期", "出口日期",
        "提运单号", "运输工具名称及航次号", "许可证号",
        "标记唛码及备注", "中华人民共和国海关出口货物报关单",
        "页码/页数", "仅供核对用", "打印时间",
        "特殊关系确认", "价格影响确认", "支付特许权使用费确认",
        "公式定价确认", "暂定价格确认", "自报自缴", "水运中转",
        "申报单位", "报关人员", "兹申明", "海关批注及签章",
    }

    data_spans = []
    for s in spans:
        text = s["text"].strip()
        if not text:
            continue
        if not (x_min <= s["x"] <= x_max):
            continue
        # Skip header keywords
        if any(text == ht for ht in header_texts):
            continue
        if any(kw in text for _, kw in vertical_header_keywords if kw not in ("征免",)):
            continue
        # Skip noise
        if any(noise in text for noise in noise_labels):
            continue

        y_center = (s["y"] + s["y1"]) / 2
        data_spans.append({
            "text": text,
            "x": s["x"],
            "y": s["y"],
            "y1": s["y1"],
            "y_center": y_center,
        })

    # 3. Assign each data span to closest header by y distance
    col_data = defaultdict(list)
    for ds in data_spans:
        best_col = None
        best_dist = 100
        for col_id, header_y in header_map.items():
            dist = abs(ds["y_center"] - header_y)
            if dist < best_dist:
                best_dist = dist
                best_col = col_id
        if best_col:
            col_data[best_col].append(ds)

    # 4. Find item numbers to determine item boundaries
    item_nos = []
    for ds in sorted(col_data.get("item_no", []), key=lambda d: d["y_center"]):
        text = ds["text"].strip()
        if re.match(r"^\d{1,3}$", text):
            item_nos.append({"no": str(int(text)), "y": ds["y_center"]})

    if not item_nos:
        return []

    # For now handle as single item group (vertical layout typically has 1 item per page)
    items = []

    # Process product_code: handle merged "8304000000置物架" format
    product_code = ""
    product_name = ""
    for ds in sorted(col_data.get("product_code", []), key=lambda d: d["y_center"]):
        text = ds["text"].strip()
        m = re.match(r"^(\d{8,10})(.+)$", text)
        if m:
            product_code = m.group(1)
            product_name = m.group(2).strip()
        elif re.match(r"^\d{8,10}$", text):
            product_code = text
        else:
            product_name = text

    # Process product_name_spec
    spec_model = ""
    for ds in sorted(col_data.get("product_name_spec", []), key=lambda d: d["y_center"]):
        text = ds["text"].strip()
        if "|" in text:
            spec_model = text
        elif not product_name:
            product_name = text

    # Process quantity: collect all quantity+unit pairs
    qty_parts = []
    for ds in sorted(col_data.get("quantity", []), key=lambda d: d["x"]):
        text = ds["text"].strip()
        if re.match(r"\d+", text):
            qty_parts.append(text)
    quantity_unit = " / ".join(qty_parts)

    # Process price: separate numbers (unit_price, total_price) from text (currency)
    price_numbers = []
    price_text = []
    for ds in sorted(col_data.get("price", []), key=lambda d: d["x"]):
        text = ds["text"].strip()
        if PRICE_RE.match(text):
            price_numbers.append(text)
        else:
            price_text.append(text)

    unit_price = price_numbers[0] if len(price_numbers) >= 1 else ""
    total_price = price_numbers[1] if len(price_numbers) >= 2 else ""
    currency = " ".join(price_text)
    if "人民币" in currency:
        currency = "人民币"

    # Process origin_country
    origin_parts = []
    for ds in sorted(col_data.get("origin_country", []), key=lambda d: d["x"]):
        origin_parts.append(ds["text"].strip())
    origin_country = "".join(origin_parts)

    # Process dest_country
    dest_parts = []
    for ds in sorted(col_data.get("dest_country", []), key=lambda d: d["x"]):
        dest_parts.append(ds["text"].strip())
    dest_country = "".join(dest_parts)

    # Process source and duty — may be merged in a single span
    source_raw = " ".join(ds["text"].strip() for ds in sorted(col_data.get("source", []), key=lambda d: d["x"]))
    duty_raw = " ".join(ds["text"].strip() for ds in sorted(col_data.get("duty", []), key=lambda d: d["x"]))

    domestic_source = ""
    duty_exemption = ""

    # Check if duty column contains merged source+duty text like "(33079)金华照章征税"
    if duty_raw and re.search(r"[（(]\d{4,6}[）)]", duty_raw):
        # Extract source name: text between code and duty keyword
        src_match = re.search(r"[（(]\d{4,6}[）)]([\u4e00-\u9fff]+)", duty_raw)
        if src_match:
            domestic_source = src_match.group(1).strip()
        duty_match = re.search(r"(照章\w*)", duty_raw)
        if duty_match:
            duty_exemption = duty_match.group(1)
    else:
        domestic_source = source_raw
        duty_exemption = duty_raw

    # Also check source column for any remaining data
    if not domestic_source and source_raw:
        domestic_source = source_raw
        domestic_source = re.sub(r"[（(]\d{4,6}[）)]", "", domestic_source).strip()

    # Clean up duty: combine with code "(1)" if needed
    duty_code = ""
    for ds in col_data.get("duty", []):
        if re.match(r"^\(\d+\)$", ds["text"].strip()):
            duty_code = ds["text"].strip()
            break

    if duty_exemption and duty_code:
        if not duty_exemption.endswith(duty_code):
            duty_exemption = duty_exemption + duty_code
    elif duty_code and not duty_exemption:
        duty_exemption = "照章征税" + duty_code

    # Clean domestic_source: remove any trailing duty text
    domestic_source = re.sub(r"照章.*$", "", domestic_source).strip()

    # Build item(s)
    for item_info in item_nos:
        item = {
            "item_no": item_info["no"],
            "product_code": product_code,
            "product_name": product_name,
            "spec_model": spec_model,
            "quantity_unit": quantity_unit,
            "unit_price": unit_price,
            "total_price": total_price,
            "currency": currency,
            "origin_country": origin_country,
            "final_dest_country": dest_country,
            "domestic_source": domestic_source,
            "duty_exemption": duty_exemption,
        }
        items.append(item)

    items = [it for it in items if not _is_empty_item(it)]
    return items


def _assign_price_fields(item: dict, price_data: list) -> None:
    """
    从"单价/总价/币制"合并列的数据中正确分配单价、总价和币制。
    预录单中该列数据实际顺序为：总价→币制→单价（与列标题顺序不同）。
    使用数值大小区分：总价=单价×数量≥单价，因此较小值=单价，较大值=总价。
    """
    _price_vals = []
    for p in price_data:
        p_stripped = p.strip().replace(",", "")
        if re.match(r"^[\d,.]+$", p_stripped):
            _price_vals.append(p_stripped)
        else:
            if not item.get("currency"):
                item["currency"] = p.strip()

    if len(_price_vals) >= 2:
        # 按数值排序：较小=单价，较大=总价
        _sorted = sorted(_price_vals, key=lambda x: float(x.replace(",", "")))
        item["unit_price"] = _sorted[0]
        item["total_price"] = _sorted[-1]
    elif len(_price_vals) == 1:
        item["unit_price"] = _price_vals[0]

    if not item.get("currency") and len(price_data) >= 3:
        item["currency"] = price_data[2].strip()


def _split_name_and_spec(name_entries):
    """
    从"商品名称及规格型号"列的多个 span 中分离品名与规格型号。

    name_entries: [(y, text), ...] —— 同一商品在该列的所有文本 span。

    预录单里品名通常在单元格上方（y 较小）、是简短中文；规格型号在下方、
    多为以 "|" 分隔的申报要素串（如 "1|2|家用|PET|DELAMU牌|无型号"）。
    不能依赖 span 的原始收集顺序——PDF 绘制顺序不可靠，规格 span 可能排在
    品名 span 之前，导致品名/规格整体颠倒（见 docs/memory.md #21）。
    改用 y 升序 + 内容启发式：
      - 按 y 升序遍历
      - 含 "|" 的归规格；不含 "|" 的，第一个作品名，其余（如尺寸描述）归规格
      - 若全是含 "|" 的规格，取 y 最小的兜底作品名
    返回 (product_name, spec_model)
    """
    if not name_entries:
        return "", ""
    ordered = sorted(name_entries, key=lambda t: t[0])
    product_name = ""
    spec_parts = []
    for _y, text in ordered:
        if not text:
            continue
        if "|" in text:
            spec_parts.append(text)
        elif not product_name:
            product_name = text
        else:
            spec_parts.append(text)
    if not product_name:
        product_name = ordered[0][1]
        spec_parts = [t for _y, t in ordered[1:] if t]
    return product_name, " ".join(p for p in spec_parts if p)


def extract_pre_recording_items_by_grid(page_info: PageInfo) -> list:
    """
    核对单「字段分层 + x 列分布」商品明细提取（如 0060228GDM 这类无"仅供核对"
    标记的核对单变体）。项号(1,2,3...)横向排列，每个商品占一个 x 列；编号/名称/
    规格/数量/价格各自在不同 y 层，按项号的 x 区间归位。详见 docs/memory.md #16。
    """
    spans = extract_spans_with_positions(page_info)
    if not spans:
        return []

    # 按 y 分行（容差 3）
    rows = {}
    for s in spans:
        rows.setdefault(round(s["y"] / 3) * 3, []).append(s)

    # 项号层：含从 1 起连续小整数的行
    anchor_items = []
    for yk in sorted(rows.keys()):
        nums = [(s["x"], int(s["text"])) for s in rows[yk]
                if re.match(r"^\d{1,3}$", s["text"].strip())]
        nums.sort()
        vals = [n for _, n in nums]
        if len(vals) >= 2 and vals == list(range(1, len(vals) + 1)):
            anchor_items = [{"item_no": str(n), "x": x, "y": yk} for x, n in nums]
            break
    if len(anchor_items) < 2:
        return []

    # 编号层：8-10 位数字密集的行（用于名称 y 锚定）
    code_y = anchor_items[0]["y"]
    for yk in sorted(rows.keys()):
        if len([s for s in rows[yk] if re.match(r"^\d{8,10}$", s["text"].strip())]) >= 2:
            code_y = yk
            break

    # 算每个 item 的 x 区间（相邻锚点中点）
    xs = [it["x"] for it in anchor_items]
    for i, it in enumerate(anchor_items):
        left = (xs[i - 1] + xs[i]) / 2 if i > 0 else xs[0] - (xs[1] - xs[0]) / 2
        right = (xs[i] + xs[i + 1]) / 2 if i < len(xs) - 1 else xs[-1] + (xs[-1] - xs[-2]) / 2
        it["xrange"] = (left, right)
        it["code"] = it["spec"] = ""
        it["quantities"] = []
        it["prices"] = []
        it["name_cands"] = []

    _labels = {
        "境内发货人", "境外收货人", "生产销售单位", "合同协议号", "出境关别", "运输方式",
        "监管方式", "贸易国（地区）", "贸易国(地区)", "运抵国（地区）", "运抵国(地区)", "指运港",
        "离境口岸", "包装种类", "件数", "毛重(千克)", "毛重（千克）", "净重(千克)", "净重（千克）",
        "成交方式", "征免性质", "随附单证及编号", "标记唛码及备注", "征免", "境内货源地",
        "最终目的国(地区)", "最终目的国（地区）", "原产国(地区)", "原产国（地区）", "数量及单位",
        "单价/总价/币制", "商品名称及规格型号", "商品编号", "项号", "备案号", "申报日期",
        "出口日期", "提运单号", "运输工具名称及航次号", "许可证号", "杂费", "保费", "运费",
        "申报单位", "报关人员", "海关编号", "预录入编号", "照章征税", "一般征税", "一般贸易",
        "中国", "中国香港", "加拿大", "人民币", "CNY", "盐田", "先出后结", "无品牌", "N/M", "备注",
    }
    UNIT_RE = re.compile(r"(个|件|千克|公斤|吨|克|套|台|张|米|盒|包)$")

    def _find(x):
        for it in anchor_items:
            if it["xrange"][0] <= x < it["xrange"][1]:
                return it
        return None

    anchor_y = anchor_items[0]["y"]
    for s in spans:
        t = s["text"].strip()
        if not t or abs(s["y"] - anchor_y) < 5:
            continue
        clean = re.sub(r"\([A-Za-z0-9]+\)", "", t).strip()
        if t in _labels or clean in _labels:
            continue
        it = _find(s["x"])
        if not it:
            continue
        if re.match(r"^\d{8,10}$", t):
            if not it["code"]:
                it["code"] = t
        elif PRICE_RE.match(t) and "." in t:
            try:
                it["prices"].append((float(t.replace(",", "")), t))
            except ValueError:
                pass
        elif UNIT_RE.search(t):
            it["quantities"].append(t)
        elif "|" in t:
            it["spec"] = (it["spec"] + " " + t).strip() if it["spec"] else t
        elif re.search(r"[一-鿿]", t) and len(t) <= 12:
            it["name_cands"].append((abs(s["y"] - code_y), t))

    result = []
    for it in anchor_items:
        ps = sorted(it["prices"])
        unit_price = ps[0][1] if ps else ""
        total_price = ps[-1][1] if ps else ""
        name = min(it["name_cands"])[1] if it["name_cands"] else ""
        spec = (name + "|" + it["spec"]).strip("|") if name else it["spec"]
        result.append({
            "item_no": it["item_no"],
            "product_code": it["code"],
            "product_name": name,
            "product_name_spec": spec,
            "quantity_unit": " / ".join(it["quantities"]),
            "unit_price": unit_price,
            "total_price": total_price,
        })
    return [r for r in result if not _is_empty_item(r)]


def extract_pre_recording_items_horizontal(page_info: PageInfo) -> list:
    """
    横向倒排"仅供核对用"格式商品提取（如 0060228GDM 录入单）。

    与标准预录单相反，该格式：项号(1,2,3..)印在页面底部，各字段数据印在项号
    **上方**；每个商品横向占一**列**(x)，多列并排；列头标签纵向分散在不同 y。
    老的 extract_pre_recording_items_by_position 假设"表头同行+数据在下"对此失效。
    本函数：用项号数据行的 x 定列 → 上方数据按列聚合 → 列内按文本模式+y 识别字段。
    详见 docs/memory.md #15。
    """
    spans = extract_spans_with_positions(page_info)
    if not spans:
        return []

    # 1. 找项号锚点：纯小整数 spans 按 y 聚类，选"≥2 个连续整数且 y 最大(倒排底部)"的组
    int_spans = [s for s in spans if re.match(r"^\d{1,3}$", s["text"].strip())]
    y_clusters = []
    for s in int_spans:
        for c in y_clusters:
            if abs(c["y"] - s["y"]) < 5:
                c["spans"].append(s)
                break
        else:
            y_clusters.append({"y": s["y"], "spans": [s]})

    anchor = None  # [(no, x), ...] 已按 x 排序
    for c in sorted(y_clusters, key=lambda k: -k["y"]):
        vals = sorted((int(s["text"]), s["x"]) for s in c["spans"])
        nums = [v[0] for v in vals]
        if len(nums) >= 2 and all(nums[i + 1] - nums[i] in (1, 2) for i in range(len(nums) - 1)):
            anchor = vals
            break
    if not anchor:
        return []  # 不是横向倒排格式（标准预录单项号分散在不同 y，不会聚到同一簇）

    centers = [a[1] for a in anchor]
    item_y = next(s["y"] for s in int_spans
                  if int(s["text"]) == anchor[0][0] and abs(s["x"] - anchor[0][1]) < 5)

    # 2. 列边界 = 相邻列中心的中点
    bounds = []
    for i in range(len(centers)):
        lo = (centers[i - 1] + centers[i]) / 2 if i > 0 else centers[i] - 25
        hi = (centers[i] + centers[i + 1]) / 2 if i + 1 < len(centers) else centers[i] + 25
        bounds.append((lo, hi))

    # 3. 按列分组（排除项号数据行本身、列头标签 span）
    _LABEL_SKIP = ("及规格型号", "及单位", "/总价/币制", "目的国(地区)", "目的国（地区）",
                   "原产国(地区)", "原产国（地区）", "境内货源地", "中华人民共和国海关")
    columns = [[] for _ in range(len(centers))]
    for s in spans:
        t = s["text"].strip()
        if not t or t in ("项号", "商品编号", "征免", "页码/页数", "预录入编号：", "海关编号："):
            continue
        if any(k in t for k in _LABEL_SKIP):
            continue
        if abs(s["y"] - item_y) < 5 and re.match(r"^\d{1,3}$", t):
            continue  # 排除项号本身
        for i, (lo, hi) in enumerate(bounds):
            if lo <= s["x"] < hi:
                columns[i].append(s)
                break

    # 4. 逐列解析字段
    _COUNTRY = {"中国", "加拿大", "美国", "日本", "韩国", "德国", "法国", "英国",
                "澳大利亚", "越南", "印度", "意大利", "西班牙", "巴西", "墨西哥",
                "俄罗斯", "泰国", "马来西亚", "印度尼西亚", "新加坡", "中国香港",
                "中国台湾", "荷兰", "阿联酋", "土耳其", "波兰"}
    _CURRENCY = {"人民币": "CNY", "CNY": "CNY", "美元": "USD", "USD": "USD",
                 "欧元": "EUR", "EUR": "EUR", "港币": "HKD", "HKD": "HKD", "日元": "JPY"}
    _QTY = re.compile(r"^\d+(?:\.\d+)?(个|件|套|台|张|盒|只|支|千克|公斤|吨|米)")
    _DUTY = ("照章征税", "全免", "特案减免", "保函", "自贸协定")

    items = []
    for i, col in enumerate(columns):
        item = {"item_no": str(anchor[i][0]), "product_code": "", "product_name": "",
                "spec_model": "", "quantity_unit": "", "unit_price": "", "total_price": "",
                "currency": "", "origin_country": "", "dest_country": "", "final_dest_country": "",
                "domestic_source": "", "duty_exemption": ""}
        col_sorted = sorted(col, key=lambda s: -s["y"])  # 从下往上
        prices, countries, names, specs = [], [], [], []
        code_y = None
        for s in col_sorted:
            t = s["text"].strip()
            if re.match(r"^\d{8,10}$", t) and not item["product_code"]:
                item["product_code"] = t
                code_y = s["y"]
                continue
            if "|" in t:
                specs.append(t)
                continue
            if _QTY.match(t):
                continue  # 数量改由函数末尾的行级提取（横向格式 x 偏移大，列内会串列）
            if PRICE_RE.match(t) and ("." in t or len(t) >= 4) and not re.match(r"^\d{1,3}$", t):
                prices.append(t)
                continue
            if t in _CURRENCY:
                item["currency"] = _CURRENCY[t]
                continue
            m = re.match(r"^\((\d+)\)(.+)$", t)  # 货源地，可能粘征免："(33029)宁波其他照章征税"
            if m:
                code, rest = m.group(1), m.group(2)
                for w in _DUTY:
                    if w in rest:
                        if not item["duty_exemption"]:
                            item["duty_exemption"] = w
                        rest = rest.replace(w, "").strip()
                        break
                item["domestic_source"] = "({}){}".format(code, rest)
                continue
            if any(t.startswith(w) for w in _DUTY):
                if not item["duty_exemption"]:
                    item["duty_exemption"] = t
                continue
            if t in _COUNTRY:
                countries.append((s["y"], t))
                continue
            if re.match(r"^\([A-Z]{2,4}\)$", t):  # 国家代码 (CHN)(CAN) 跳过
                continue
            if re.match(r"^[一-鿿]{2,8}$", t):  # 潜在商品名称
                names.append((s["y"], t))
                continue
        if specs:
            item["spec_model"] = " ".join(specs)
        if names:
            if code_y is not None:
                above = [(y, n) for y, n in names if y < code_y]
                item["product_name"] = (max(above, key=lambda x: x[0])[1] if above
                                        else max(names, key=lambda x: x[0])[1])
            else:
                item["product_name"] = max(names, key=lambda x: x[0])[1]
        if prices:  # 单价<总价（总价=单价×数量≥单价）
            pn = []
            for p in prices:
                try:
                    pn.append((float(p.replace(",", "")), p))
                except ValueError:
                    pass
            if pn:
                pn.sort(key=lambda x: x[0])
                item["unit_price"] = pn[0][1]
                if len(pn) >= 2:
                    item["total_price"] = pn[-1][1]
        if countries:  # y 大的=原产国(下方)，y 小的=目的国(上方)
            countries.sort(key=lambda x: -x[0])
            item["origin_country"] = countries[0][1]
            if len(countries) >= 2:
                item["dest_country"] = countries[1][1]
                item["final_dest_country"] = countries[1][1]
        items.append(item)

    # 数量字段归位：用项号 anchor 的 x 做「左闭右开」区间 [项号x, 下一项号x) 把每个
    # 数量 span 归到对应 item。三种方案的取舍：
    #   (a) 按列边界中点归位——不行：数量 x 系统性偏右（=项号x+20，越过中点 项号x+16），
    #       会落到下一 item（#19 踩过）。
    #   (b) 按数值去重 + idx 顺序分配——不行：不同 item 可能同数量（item2 和 item6 都是
    #       "36套"），去重会误删合法值；且同 item 多个数量 span（如 item10 的"30个"+"30件"
    #       重复渲染）会让序列长度 ≠ item 数，从丢失点整体错位（#23）。
    #   (c) 本方案：项号 x ≤ 数量 x < 下一项号 x —— 区间宽度=列宽(~32) > 数量偏移(20)，
    #       稳定覆盖；同 item 的多个数量 span 都归同一 item，再 in-place 去重。
    _anchor_by_x = sorted(anchor, key=lambda a: a[1])
    _axs = [a[1] for a in _anchor_by_x]

    def _qty_owner(x):
        # 项号 span 的 x 比同列数据 span 略大 ~0.2px（字符渲染起始位置差异），
        # 直接用 cx<=x 会让重量(x≈项号x-0.2)不满足条件、被推给上一个 item。
        # 左右各减 2px 容差：分界点落在「项号x-2」，介于本 item 主数量(x≈项号x+20)
        # 与下一 item 重量(x≈下一项号x-0.2)之间，安全容差窗 [0.2, 12]。
        for i, cx in enumerate(_axs):
            nxt = _axs[i + 1] if i + 1 < len(_axs) else cx + 32
            if cx - 2 <= x < nxt - 2:
                return str(_anchor_by_x[i][0])
        return None

    _item_by_no = {it["item_no"]: it for it in items}
    for it in items:
        it["_qty_main"] = []
        it["_qty_wt"] = []
    for s in spans:
        t = s["text"].strip()
        m = _QTY.match(t)
        if not m:
            continue
        owner = _qty_owner(s["x"])
        if owner is None:
            continue
        it = _item_by_no.get(owner)
        if not it:
            continue
        if m.group(1) in ("千克", "公斤", "吨"):
            it["_qty_wt"].append(t)
        else:
            it["_qty_main"].append(t)
    for it in items:
        main = list(dict.fromkeys(it.pop("_qty_main", [])))  # 保序去重：滤掉同 item 重复渲染
        wt = list(dict.fromkeys(it.pop("_qty_wt", [])))
        it["quantity_unit"] = " / ".join(main + wt)

    return [it for it in items if not _is_empty_item(it)]


def extract_pre_recording_items_by_position(page_info: PageInfo) -> list:
    """
    用位置感知方式从预录单中提取商品明细
    动态检测列位置：从表头行读取各列的 x 坐标，不依赖固定值
    """
    spans = extract_spans_with_positions(page_info)
    # 横向倒排"仅供核对用"格式（项号在底部、数据在上方、每商品占一列）：
    # 老逻辑假设"表头同行+数据在下"会完全失效。先尝试横向提取，命中则直接返回。
    _h = extract_pre_recording_items_horizontal(page_info)
    if _h:
        return _h


    # ---- 第一步：找到表头行并提取列位置 ----
    # 表头关键词 → 列 ID 映射
    header_keywords = {
        "项号": "item_no",
        "商品编号": "product_code",
        "商品名称": "product_name",
        "规格型号": "product_name",
        "数量": "quantity",
        "单价": "unit_price_col",
        "总价": "total_price_col",
        "币制": "currency_col",
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
                if col_id not in col_positions:
                    col_positions[col_id] = s["x"]
                # 对非价格列保持 break（防止 "项号商品编号" 匹配多个不同列）
                # 对价格列不 break（允许一个 span 同时设置 单价/总价/币制）
                if col_id not in ("unit_price_col", "total_price_col", "currency_col"):
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

    # 检测价格列是分开的还是合并的（同一 x 坐标）
    _price_x_vals = set()
    for _k in ("unit_price_col", "total_price_col", "currency_col"):
        if _k in col_positions:
            _price_x_vals.add(round(col_positions[_k]))

    _separate_price_cols = len(_price_x_vals) >= 2  # 至少2个不同的 x → 分开
    if not _separate_price_cols:
        # 合并价格列为单个 "price" 列
        _any_price_x = col_positions.get("unit_price_col") or col_positions.get("total_price_col") or col_positions.get("currency_col")
        if _any_price_x is not None:
            col_positions["price"] = _any_price_x
        for _k in ("unit_price_col", "total_price_col", "currency_col"):
            col_positions.pop(_k, None)

    # 如果"数量"和"单价/总价/币制"合在一个 span 里（续页常见），
    # 需要从数据中推断 price 列的位置
    _price_col_name = "unit_price_col" if _separate_price_cols else "price"
    if _price_col_name not in col_positions and "quantity" in col_positions:
        # 找数据中看起来像价格的 span（纯数字带小数点，如 60.2900）
        data_spans_after_header = [sp for sp in spans if sp["y"] > header_y + 5]
        qty_x = col_positions["quantity"]
        # 价格通常在数量列的右侧，找比 quantity x 更大且看起来像价格的 span
        price_candidates = []
        for ds in data_spans_after_header:
            if PRICE_RE.match(ds["text"].strip()):
                # 价格数字的 x 应该大于 quantity x，且在 origin_country 之前
                if ds["x"] > qty_x + 30:
                    price_candidates.append(ds["x"])
        if price_candidates:
            # 取中位数作为 price 列位置
            price_candidates.sort()
            col_positions[_price_col_name] = price_candidates[len(price_candidates) // 2]
        elif "origin_country" in col_positions:
            # 退而求其次：在 quantity 和 origin_country 之间取中点
            col_positions[_price_col_name] = (qty_x + col_positions["origin_country"]) / 2

    # 修正：当 origin_country / dest_country 与 price 列共享同一 x 位置时，
    # 用数据 span 的实际位置推断它们真正的列边界
    _data_refined_cols = set()  # 记录由数据驱动修正的列
    _merged_price_x = col_positions.get("price") or col_positions.get("unit_price_col")
    if _merged_price_x is not None:
        data_spans_after_header = [sp for sp in spans if sp["y"] > header_y + 5]
        _source_x = col_positions.get("source", 690)
        # 找数据中价格和国家名的 x 分布
        _price_data_xs = []
        _country_data_xs = []
        for ds in data_spans_after_header:
            txt = ds["text"].strip()
            if PRICE_RE.match(txt):
                if ds["x"] > _merged_price_x - 5:
                    _price_data_xs.append(ds["x"])
            elif re.match(r"^[\u4e00-\u9fff]{2,3}$", txt):
                # 只收集在 price 和 source 列之间的中文词（排除货源地）
                if ds["x"] > _merged_price_x and ds["x"] < _source_x - 20:
                    _country_data_xs.append(ds["x"])

        # 用数据 x 位置修正列位置
        if _price_data_xs:
            _price_data_xs.sort()
            col_positions[_price_col_name] = _price_data_xs[len(_price_data_xs) // 2]
            _data_refined_cols.add(_price_col_name)

        # 用价格数据的最大 x 作为国家列的分界下限
        _price_max_x = max(_price_data_xs) + 30 if _price_data_xs else _merged_price_x + 60

        if _country_data_xs:
            _country_data_xs = [x for x in _country_data_xs if x >= _price_max_x]
            _country_data_xs.sort()
            _unique_country_xs = sorted(set(round(x) for x in _country_data_xs))
            if "origin_country" in col_positions and col_positions["origin_country"] == _merged_price_x:
                if len(_unique_country_xs) >= 2:
                    col_positions["origin_country"] = _unique_country_xs[0]
                    col_positions["dest_country"] = _unique_country_xs[1]
                    _data_refined_cols.update(["origin_country", "dest_country"])
                elif len(_unique_country_xs) == 1:
                    col_positions["origin_country"] = _unique_country_xs[0]
                    _data_refined_cols.add("origin_country")
                    col_positions.pop("dest_country", None)
        elif "origin_country" in col_positions and col_positions["origin_country"] == _merged_price_x:
            # 没有找到国家级数据，移除这些无效列
            col_positions.pop("origin_country", None)
            col_positions.pop("dest_country", None)

    # 纵向布局检测：如果列头太少（<3），说明列头可能是垂直排列的
    if len(col_positions) < 3:
        vertical_items = _extract_items_vertical_layout(spans, page_info)
        if vertical_items:
            return vertical_items

    if "item_no" not in col_positions:
        return []

    # ---- 第二步：动态计算列边界 ----
    # 列边界 = [(col_id, x_start, x_end), ...]
    # 使用相邻列中点作为分界，避免列间重叠导致数据错列
    sorted_cols = sorted(col_positions.items(), key=lambda c: c[1])

    col_boundaries = []
    for i, (col_id, x_center) in enumerate(sorted_cols):
        x_start = x_center - 15
        if i + 1 < len(sorted_cols):
            next_x = sorted_cols[i + 1][1]
            if abs(next_x - x_center) < 10:
                # 同一位置的合并列头（如"原产国/目的国"合在一个 span）→ 零宽，
                # 后续由数据驱动修正或文本回退填充
                x_end = x_center
            elif col_id in _data_refined_cols or sorted_cols[i + 1][0] in _data_refined_cols:
                # 数据驱动定位的列：位置已基于实际数据，用中点即可
                x_end = (x_center + next_x) / 2
            else:
                # 表头定位的列：延伸到下一列起始，消除间隙
                x_end = next_x - 15
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
        if not (item_x_start <= s["x"] < item_x_end):
            continue
        # 项号: 纯数字, 1-3位 (01, 02, 1, 2, 10, etc.)
        if re.match(r"^\d{1,3}$", text):
            item_start_ys.append(s["y"])
        # 项号+商品编码合并的 span（如 "1       8304000000"）
        elif re.match(r"^\d{1,3}\s+\d{8,10}$", text):
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

    # 当 row_slots 数量不足以覆盖所有 item 时，回退到 y 坐标分组
    # 否则落在 slot 外的 item 会被丢弃
    if row_slots and len(row_slots) >= len(item_start_ys):
        # 使用水平线精确分组
        # 找到每个项号所在的 slot
        item_slot_map = []  # [(item_no_text, slot_idx), ...]
        for s in data_spans:
            text = s["text"].strip()
            if not (item_x_start <= s["x"] < item_x_end):
                continue
            # 纯数字项号
            if re.match(r"^\d{1,3}$", text):
                for si, (y_top, y_bottom) in enumerate(row_slots):
                    if y_top < s["y"] < y_bottom:
                        item_slot_map.append((text, si))
                        break
            # 项号+商品编码合并的 span
            elif re.match(r"^(\d{1,3})\s+(\d{8,10})$", text):
                m = re.match(r"^(\d{1,3})\s+(\d{8,10})$", text)
                for si, (y_top, y_bottom) in enumerate(row_slots):
                    if y_top < s["y"] < y_bottom:
                        item_slot_map.append((m.group(1), si))
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
            name_entries = []  # product_name 列的 (y, text)，按 y 排序分离品名/规格
            for s in item_spans:
                col = get_col_id(s["x"])
                if col:
                    stext = s["text"].strip()
                    # 处理项号+商品编码合并的 span（如 "1       8304000000"）
                    merged_m = re.match(r"^(\d{1,3})\s+(\d{8,10})$", stext)
                    if merged_m:
                        if "item_no" not in cols:
                            cols["item_no"] = []
                        cols["item_no"].append(merged_m.group(1))
                        if "product_code" not in cols:
                            cols["product_code"] = []
                        cols["product_code"].append(merged_m.group(2))
                        continue
                    # 处理商品编码+商品名称合并的 span（如 "4202920000 收纳箱"）
                    code_name_m = re.match(r"^(\d{8,10})\s+(.+)$", stext)
                    if code_name_m:
                        if "product_code" not in cols:
                            cols["product_code"] = []
                        cols["product_code"].append(code_name_m.group(1))
                        name_entries.append((s["y"], code_name_m.group(2).strip()))
                        continue
                    # 修正列分配
                    if col == "item_no" and re.match(r"^\d{6,}$", stext):
                        col = "product_code"
                    elif col == "product_code" and not re.match(r"^\d{6,}$", stext):
                        col = "product_name"
                    # 货源地 "(数字)中文" 长地名 x 偏左（短地名 x≈676.8、长地名 x≈667.8），
                    # 可能落到目的国列边界外，按内容特征强制归 source
                    if re.match(r"^[（(]\d{4,6}[）)]", stext) and col != "source":
                        col = "source"
                    if col == "product_name":
                        name_entries.append((s["y"], stext))
                    else:
                        if col not in cols:
                            cols[col] = []
                        cols[col].append(s["text"])

            product_name, spec_model = _split_name_and_spec(name_entries)
            item = {
                "item_no": str(int(cols.get("item_no", ["0"])[0])) if cols.get("item_no") and cols["item_no"][0].isdigit() else cols.get("item_no", [""])[0],
                "product_code": (cols.get("product_code") or [""])[0],
                "product_name": product_name,
                "spec_model": spec_model,
                "quantity_unit": " / ".join(cols.get("quantity", [])),
                "origin_country": " ".join(cols.get("origin_country", [])),
                "final_dest_country": " ".join(cols.get("dest_country", [])),
                "domestic_source": " ".join(cols.get("source", [])),
                "duty_exemption": " ".join(cols.get("duty", [])),
            }

            # 价格字段：分开列 vs 合并列
            if _separate_price_cols:
                item["unit_price"] = (cols.get("unit_price_col") or [""])[0]
                item["total_price"] = (cols.get("total_price_col") or [""])[0]
                item["currency"] = (cols.get("currency_col") or [""])[0]
            else:
                _pd = cols.get("price", [])
                _assign_price_fields(item, _pd)

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
            name_entries = []  # product_name 列的 (y, text)，按 y 排序分离品名/规格
            for s in item_spans:
                col = get_col_id(s["x"])
                if col:
                    stext = s["text"].strip()
                    # 处理项号+商品编码合并的 span
                    merged_m = re.match(r"^(\d{1,3})\s+(\d{8,10})$", stext)
                    if merged_m:
                        if "item_no" not in cols:
                            cols["item_no"] = []
                        cols["item_no"].append(merged_m.group(1))
                        if "product_code" not in cols:
                            cols["product_code"] = []
                        cols["product_code"].append(merged_m.group(2))
                        continue
                    # 处理商品编码+商品名称合并的 span（如 "4202920000 收纳箱"）
                    code_name_m = re.match(r"^(\d{8,10})\s+(.+)$", stext)
                    if code_name_m:
                        if "product_code" not in cols:
                            cols["product_code"] = []
                        cols["product_code"].append(code_name_m.group(1))
                        name_entries.append((s["y"], code_name_m.group(2).strip()))
                        continue
                    if col == "item_no" and re.match(r"^\d{6,}$", stext):
                        col = "product_code"
                    elif col == "product_code" and not re.match(r"^\d{6,}$", stext):
                        col = "product_name"
                    # 货源地 "(数字)中文" 长地名 x 偏左（短地名 x≈676.8、长地名 x≈667.8），
                    # 可能落到目的国列边界外，按内容特征强制归 source
                    if re.match(r"^[（(]\d{4,6}[）)]", stext) and col != "source":
                        col = "source"
                    if col == "product_name":
                        name_entries.append((s["y"], stext))
                    else:
                        if col not in cols:
                            cols[col] = []
                        cols[col].append(s["text"])

            product_name, spec_model = _split_name_and_spec(name_entries)
            item = {
                "item_no": str(int(cols.get("item_no", ["0"])[0])) if cols.get("item_no") and cols["item_no"][0].isdigit() else cols.get("item_no", [""])[0],
                "product_code": (cols.get("product_code") or [""])[0],
                "product_name": product_name,
                "spec_model": spec_model,
                "quantity_unit": " / ".join(cols.get("quantity", [])),
                "origin_country": " ".join(cols.get("origin_country", [])),
                "final_dest_country": " ".join(cols.get("dest_country", [])),
                "domestic_source": " ".join(cols.get("source", [])),
                "duty_exemption": " ".join(cols.get("duty", [])),
            }

            # 价格字段：分开列 vs 合并列
            if _separate_price_cols:
                item["unit_price"] = (cols.get("unit_price_col") or [""])[0]
                item["total_price"] = (cols.get("total_price_col") or [""])[0]
                item["currency"] = (cols.get("currency_col") or [""])[0]
            else:
                _pd = cols.get("price", [])
                _assign_price_fields(item, _pd)

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
            cleaned = re.sub(r"[（(]\d{4,6}[）)]", "", src)
            cleaned = re.sub(r"照章.*$", "", cleaned)
            cleaned = cleaned.strip()
            if cleaned:
                item["domestic_source"] = cleaned
            # 只有征免列为空或只有纯代码 "(1)" 时，才从货源地补入
            if "照章" in src:
                if not duty or duty in ("(1)", "（1）"):
                    item["duty_exemption"] = "照章征税(1)"

        # 不做盲目规范化 — 保留原始提取值，让比对引擎判断对错
        # 例如 "(1)-照章" ≠ "照章征税(1)"，应如实展示为不通过

        # 修货源地：清除区域代码如 (33079)、（44199） 和征免代码 (1)、（1）
        src = item.get("domestic_source", "")
        if src:
            src = re.sub(r"[（(]\d{4,6}[）)]", "", src)
            src = re.sub(r"[（(]1[）)]\s*$", "", src)
            src = src.strip()
            item["domestic_source"] = src

        # 修征免：补全只有代码的征免字段
        duty = item.get("duty_exemption", "")
        if duty in ("(1)", "（1）"):
            item["duty_exemption"] = "照章征税(1)"

    items = [it for it in items if not _is_empty_item(it)]
    return items
