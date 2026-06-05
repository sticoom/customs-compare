"""
字段提取器：从报关单/预录单/合同页文本中提取各字段值
"""
import re
from src.config import (
    CUSTOMS_HEADER_FIELDS,
    CUSTOMS_ITEM_FIELDS,
    SPEC_MODEL_MAPPING,
    DOMESTIC_SOURCE_MAPPING,
)

# 数量及单位行的通用匹配：数字+中文字符（如 "16套"、"37千克"、"100件"）
QTY_UNIT_RE = re.compile(r"^\d+(\.\d+)?[\u4e00-\u9fff]+$")


# ============================================================
# 报关单字段提取
# ============================================================

def extract_customs_header(text: str) -> dict:
    """
    从报关单文本中提取项号前的表头字段
    返回: {field_id: extracted_value}
    """
    fields = {}

    # 发货单位
    m = re.search(r"发货单位\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["sender_unit"] = m.group(1).strip() if m else ""

    # 经营单位
    m = re.search(r"经营单位\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["business_unit"] = m.group(1).strip() if m else ""

    # 合同协议号
    m = re.search(r"合同协议号\s*(\d+)", text)
    fields["contract_no"] = m.group(1).strip() if m else ""

    # 包装种类
    m = re.search(r"包装种类\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["package_type"] = m.group(1).strip() if m else ""

    # 运输方式
    m = re.search(r"运输方式\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["transport_mode"] = m.group(1).strip() if m else ""

    # 贸易方式
    m = re.search(r"贸易方式\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["trade_mode"] = m.group(1).strip() if m else ""

    # 贸易国
    m = re.search(r"贸易国\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["trade_country"] = m.group(1).strip() if m else ""

    # 件数
    m = re.search(r"件数\s*\n?\s*(\d+)", text)
    fields["quantity"] = m.group(1).strip() if m else ""

    # 毛重
    m = re.search(r"毛重（?千克）?\s*\n?\s*(\d+)", text)
    fields["gross_weight"] = m.group(1).strip() if m else ""

    # 净重
    m = re.search(r"净重（?千克）?\s*\n?\s*(\d+)", text)
    fields["net_weight"] = m.group(1).strip() if m else ""

    # 成交方式
    m = re.search(r"成交方式\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["deal_mode"] = m.group(1).strip() if m else ""

    # 征免性质
    m = re.search(r"征免性质\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["duty_nature"] = m.group(1).strip() if m else ""

    # 运抵国
    m = re.search(r"运抵国（?地区）?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["dest_country"] = m.group(1).strip() if m else ""

    # 指运港
    m = re.search(r"指运港\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["dest_port"] = m.group(1).strip() if m else ""

    # 人工确认字段（报关单可能没有）
    fields["exit_customs"] = ""
    fields["exit_port"] = ""
    fields["attached_docs"] = ""
    fields["marks_remarks"] = ""

    # 出口口岸（报关单特有，用于判断文档类型）
    m = re.search(r"出口口岸\s*\n?\s*(.+?)(?:\n|$)", text)
    if m:
        val = m.group(1).strip()
        if val == "-":
            fields["exit_customs"] = ""

    return fields


def extract_customs_items(text: str) -> list:
    """
    从报关单文本中提取商品明细（项号后）
    返回: [{field_id: value, ...}, ...]
    """
    items = []

    # 找到项号区域的开始位置
    item_section_match = re.search(r"项号\s*\n", text)
    if not item_section_match:
        return items

    # 找到税费征收情况之前的部分
    tax_match = re.search(r"税费征收情况", text)
    item_text = text[item_section_match.end():]
    if tax_match:
        item_text = text[item_section_match.end():tax_match.start()]

    # 按项号分割（项号从1开始）
    # 匹配模式：行首的数字（项号）+ 10位商品编码 + 可选商品名称 + 后续内容直到下一个项号或结尾
    # 支持两种格式：编码独占一行 或 编码+名称同行（核对单格式）
    item_pattern = re.compile(
        r"(?:^|\n)\s*(\d+)\s*\n\s*(\d{10})\s*(.*?)\s*\n(.+?)(?=\n\s*\d+\s*\n\s*\d{10}|\Z)",
        re.DOTALL,
    )

    matches = list(item_pattern.finditer(item_text))

    if not matches:
        # 尝试更宽松的匹配
        return _extract_items_loose(item_text)

    for match in matches:
        item_no = match.group(1)
        product_code = match.group(2)
        product_name_inline = match.group(3).strip()
        content = match.group(4).strip()
        # 如果编码行有商品名称，将其拼接到内容前面
        if product_name_inline:
            content = product_name_inline + "\n" + content

        item = _parse_customs_item_content(item_no, product_code, content)
        items.append(item)

    return items


def _parse_customs_item_content(item_no: str, product_code: str, content: str) -> dict:
    """解析单个报关单商品条目的内容"""
    lines = [l.strip() for l in content.split("\n") if l.strip()]

    item = {
        "item_no": item_no,
        "product_code": product_code,
        "product_name": "",
        "spec_model": "",
        "quantity_unit": "",
        "dest_country": "",
        "unit_price": "",
        "total_price": "",
        "currency": "",
        "domestic_source": "",
        "duty_exemption": "",
        "final_dest_country": "",
    }

    # 商品名称（第一行非数字非属性的内容）
    name_lines = []
    spec_lines = []
    found_name = False

    # 先确定行的类型，用于精确分隔字段
    # 报关单列顺序：名称 → 规格型号 → 数量 → 目的国 → 单价 → 总价+CNY → 人民币 → 货源地 → 照章
    # 规则：spec_model 在商品名称之后、数量行之前
    #       货源地 在"人民币"之后、"照章"之前
    is_qty_line = [bool(QTY_UNIT_RE.match(l)) for l in lines]

    # 找第一个数量行的索引（spec_model 的边界）
    first_qty_idx = None
    for i, is_qty in enumerate(is_qty_line):
        if is_qty:
            first_qty_idx = i
            break

    for i, line in enumerate(lines):
        # 在数量行之后的内容都不是 spec_model
        if first_qty_idx is not None and i >= first_qty_idx:
            break

        # 跳过纯数字行或已知的结构性行
        if QTY_UNIT_RE.match(line):
            continue
        if re.match(r"^[\d,.]+$", line):  # 纯数字（价格）
            continue
        if line in ["照章", "人民币", "CNY"]:
            continue
        # 跳过数量+单位组合（如 "12套", "42套"）
        if QTY_UNIT_RE.match(line):
            continue
        # 跳过国家名称
        if line in ["德国", "美国", "英国", "法国", "日本", "韩国", "澳大利亚", "中国"]:
            continue
        # 跳过价格+CNY（如 "259.2 CNY"）
        if re.match(r"^[\d,.]+\s*CNY$", line):
            continue

        if not found_name:
            # 第一行是商品名称
            name_lines.append(line)
            found_name = True
        else:
            # 后续行是规格型号
            spec_lines.append(line)

    if name_lines:
        item["product_name"] = name_lines[0]
    item["spec_model"] = " ".join(spec_lines)

    # 提取数量/单位（使用通用模式匹配所有中文计量单位）
    qty_lines = []
    for line in lines:
        if QTY_UNIT_RE.match(line):
            qty_lines.append(line)
    item["quantity_unit"] = " / ".join(qty_lines)

    # 提取目的地国家：在最后一个数量行之后、第一个价格行之前的非空内容
    last_qty_idx = -1
    first_price_idx = len(lines)
    for i, line in enumerate(lines):
        if QTY_UNIT_RE.match(line):
            last_qty_idx = i
        if re.match(r"^\d+\.\d+$", line) and first_price_idx == len(lines):
            first_price_idx = i
    # 也找 "数字 CNY" 格式的价格行
    for i, line in enumerate(lines):
        if re.match(r"[\d,.]+\s*CNY", line) and first_price_idx == len(lines):
            first_price_idx = i

    if last_qty_idx >= 0:
        for i in range(last_qty_idx + 1, first_price_idx):
            line = lines[i].strip()
            if line and not re.match(r"^[\d.]+$", line):
                item["dest_country"] = line
                item["final_dest_country"] = line
                break

    # 提取价格
    for i, line in enumerate(lines):
        if re.match(r"^\d+\.\d+$", line):
            if not item["unit_price"]:
                item["unit_price"] = line
            else:
                item["total_price"] = line

    # 提取含价格+CNY的行（如 "9121.2 CNY"）
    for line in lines:
        m = re.match(r"([\d,.]+)\s*CNY", line)
        if m:
            item["total_price"] = m.group(1)
            item["currency"] = "人民币"

    # 提取币制
    if "人民币" in content or "CNY" in content:
        item["currency"] = "人民币"

    # 提取境内货源地：在"人民币"行之后、"照章"行之前的内容
    rmb_idx = None
    duty_idx = None
    for i, line in enumerate(lines):
        if line == "人民币" and rmb_idx is None:
            rmb_idx = i
        if line == "照章" and duty_idx is None:
            duty_idx = i

    if rmb_idx is not None and duty_idx is not None:
        # 人民币和照章之间的非空行就是货源地
        for i in range(rmb_idx + 1, duty_idx):
            if lines[i].strip():
                item["domestic_source"] = lines[i].strip()
                break
    elif rmb_idx is not None:
        # 没有照章行，取人民币后面第一个非空行
        for i in range(rmb_idx + 1, len(lines)):
            if lines[i].strip() and lines[i].strip() != "照章":
                item["domestic_source"] = lines[i].strip()
                break

    # 提取征免
    if "照章" in content:
        item["duty_exemption"] = "照章"

    return item


def _extract_items_from_continuation(text: str) -> list:
    """
    从没有表头的续页中提取商品条目（文本回退模式）
    适用于两种格式：
    1. 标准格式：项号\n商品编码\n名称\n规格型号\n...
    2. 核对单格式：项号\n商品编码 商品名称\n规格型号\n...
    """
    items = []
    # 匹配: 项号(1-3位) + 8-10位商品编码 + 可选商品名称 + 内容直到下一个项号
    # 核对单格式中编码和名称在同一行，如 "3924900000 置物架"
    item_pattern = re.compile(
        r"(?:^|\n)\s*(\d{1,3})\s*\n\s*(\d{8,10})\s*(.*?)\s*\n(.+?)(?=\n\s*\d{1,3}\s*\n\s*\d{8,10}|\Z)",
        re.DOTALL,
    )
    for match in item_pattern.finditer(text):
        item_no = match.group(1)
        product_code = match.group(2)
        product_name_inline = match.group(3).strip()  # 编码同行可能有的商品名称
        content = match.group(4).strip()
        # 如果编码行有商品名称，将其拼接到内容前面
        if product_name_inline:
            content = product_name_inline + "\n" + content
        # 截断到税费征收情况或页脚
        footer_match = re.search(r"税费征收情况|兹申明|申报单位|报关人员|自报自缴", content)
        if footer_match:
            content = content[:footer_match.start()].strip()
        if content:
            item = _parse_customs_item_content(item_no, product_code, content)
            items.append(item)
    return items


def _extract_items_from_hedui(text: str) -> list:
    """
    从核对单格式（"仅供核对用"标记）中提取商品条目
    核对单的列头是垂直排列（同一x不同y），数据区域在文本末尾以特定顺序出现
    数据顺序：项号, 品名, 数量(多个), 总价, 币制, 单价, 原产国代码, 原产国,
             目的国, 征免, 货源地, 目的国代码, 征免代码, 商品编码, 规格型号
    """
    if "仅供核对" not in text:
        return []

    lines = text.split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    # 找到数据区域起点：小数字(1-3位)后跟中文产品名，后续有数量行
    data_start = None
    for i in range(len(lines) - 2):
        if re.match(r'^\d{1,3}$', lines[i]):
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if (re.match(r'^[\u4e00-\u9fff]', next_line)
                    and '|' not in next_line
                    and next_line not in ('人民币', '照章', '照章征税', '照章征税(1)')):
                has_qty = any(QTY_UNIT_RE.match(lines[j])
                             for j in range(i + 2, min(i + 8, len(lines))))
                if has_qty:
                    data_start = i
                    break

    if data_start is None:
        return []

    # 收集数据行直到页脚
    footer_pats = ('税费征收', '合计总价', '录入员', '兹声明', '兹申明', '录入单位')
    data_lines = []
    for i in range(data_start, len(lines)):
        if any(lines[i].startswith(fp) for fp in footer_pats):
            break
        data_lines.append(lines[i])

    if not data_lines:
        return []

    # 按项号分组
    item_groups = []
    current_lines = []
    for i, line in enumerate(data_lines):
        is_item_start = (
            re.match(r'^\d{1,3}$', line)
            and i + 1 < len(data_lines)
            and re.match(r'^[\u4e00-\u9fff]', data_lines[i + 1])
            and '|' not in data_lines[i + 1]
            and data_lines[i + 1] not in ('人民币', '照章', '照章征税')
        )
        if is_item_start:
            if current_lines:
                item_groups.append(current_lines)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        item_groups.append(current_lines)

    items = []
    for group in item_groups:
        item = _parse_hedui_item(group)
        if item.get("product_code") or item.get("product_name"):
            items.append(item)

    return items


def _parse_hedui_item(lines: list) -> dict:
    """解析核对单中单个商品的数据行"""
    item = {
        "item_no": "",
        "product_code": "",
        "product_name": "",
        "spec_model": "",
        "quantity_unit": "",
        "unit_price": "",
        "total_price": "",
        "currency": "",
        "origin_country": "",
        "final_dest_country": "",
        "domestic_source": "",
        "duty_exemption": "",
    }

    if not lines:
        return item

    remaining = list(lines)

    # item_no: 第一个1-3位数字
    if re.match(r'^\d{1,3}$', remaining[0]):
        item["item_no"] = remaining[0]
        remaining = remaining[1:]

    spec_parts = []
    qty_lines = []
    price_lines = []
    countries = {'中国', '美国', '德国', '英国', '法国', '日本', '韩国', '澳大利亚',
                 '加拿大', '意大利', '西班牙', '荷兰', '比利时', '瑞士', '新加坡'}

    for line in remaining:
        # 商品编码 (8-10位数字)
        if re.match(r'^\d{8,10}$', line) and not item["product_code"]:
            item["product_code"] = line
            continue

        # 数量行
        if QTY_UNIT_RE.match(line):
            qty_lines.append(line)
            continue

        # 价格行 (小数)
        if re.match(r'^\d+\.\d+$', line):
            price_lines.append(line)
            continue

        # 币制（CJK radical ⼈ U+2F46 可能替代 人 U+4EBA）
        normalized = line.replace('\u2f08', '人').replace('\u2f46', '人')
        if normalized in ('人民币', '美元', '欧元', '港币', '日元', '英镑'):
            item["currency"] = normalized
            continue

        # 规格型号 (含 | 的行)
        if '|' in line:
            spec_parts.append(line)
            continue

        # 征免
        if '照章' in line or '全免' in line:
            item["duty_exemption"] = line  # 保留完整值（含代码）
            continue

        # 境内货源地 (含括号数字+地名)
        if re.search(r'\(\d{4,6}\)', line):
            cleaned = re.sub(r'\(\d{4,6}\)', '', line).strip()
            if cleaned:
                item["domestic_source"] = cleaned
            continue

        # 国家名：第一次→原产国，第二次→目的国
        if line in countries:
            if not item["origin_country"]:
                item["origin_country"] = line
            elif not item["final_dest_country"]:
                item["final_dest_country"] = line
            continue

        # 产品名 (第一个中文开头的非特殊行)
        if re.match(r'^[\u4e00-\u9fff]', line) and not item["product_name"]:
            item["product_name"] = line
            continue

        # 纯代码如 (CHN), (USA), (1) - 征免代码合并到 duty_exemption
        if re.match(r'^\([A-Z0-9]+\)$', line):
            code = line[1:-1]  # 去掉括号
            if re.match(r'^\d{1,2}$', code) and item["duty_exemption"]:
                # 小数字代码(1-2位) → 征免代码，合并到 duty_exemption
                if f"({code})" not in item["duty_exemption"]:
                    item["duty_exemption"] = item["duty_exemption"] + f"({code})"
            continue

    # 组装
    item["quantity_unit"] = " / ".join(qty_lines)
    item["spec_model"] = " ".join(spec_parts)

    # 区分单价/总价：小数位>=3的是单价，<3的是总价
    if len(price_lines) >= 2:
        for p in price_lines:
            dec_len = len(p.split('.')[1]) if '.' in p else 0
            if dec_len >= 3:
                item["unit_price"] = p
            else:
                item["total_price"] = p
    elif len(price_lines) == 1:
        p = price_lines[0]
        dec_len = len(p.split('.')[1]) if '.' in p else 0
        if dec_len >= 3:
            item["unit_price"] = p
        else:
            item["total_price"] = p

    return item


def _extract_items_loose(item_text: str) -> list:
    """宽松模式提取商品条目"""
    items = []

    # 按数字+10位商品编码的模式分割
    parts = re.split(r"\n\s*(?=\d+\s*\n\s*\d{10})", item_text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        m = re.match(r"(\d+)\s*\n\s*(\d{10})(.*)", part, re.DOTALL)
        if m:
            item_no = m.group(1)
            product_code = m.group(2)
            content = m.group(3)
            item = _parse_customs_item_content(item_no, product_code, content)
            items.append(item)

    return items


# ============================================================
# 预录单字段提取
# ============================================================

def extract_pre_recording_header(text: str) -> dict:
    """
    从预录单文本中提取项号前的表头字段
    """
    fields = {}

    # 境内发货人
    m = re.search(r"境内发货人\s*\(.*?\)\s*\n?\s*(.+?)(?:\n|$)", text)
    if not m:
        m = re.search(r"境内发货人\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["sender_unit"] = m.group(1).strip() if m else ""

    # 境外收货人
    m = re.search(r"境外收货人\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["buyer"] = m.group(1).strip() if m else ""

    # 生产销售单位
    m = re.search(r"生产销售单位\s*\(.*?\)\s*\n?\s*(.+?)(?:\n|$)", text)
    if not m:
        m = re.search(r"生产销售单位\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["business_unit"] = m.group(1).strip() if m else ""

    # 合同协议号
    m = re.search(r"合同协议号\s*\n?\s*(\d+)", text)
    fields["contract_no"] = m.group(1).strip() if m else ""

    # 包装种类（预录单格式：(22)纸制或纤维板制盒/箱）
    m = re.search(r"包装种类\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["package_type"] = m.group(1).strip() if m else ""

    # 运输方式
    m = re.search(r"运输方式\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["transport_mode"] = m.group(1).strip() if m else ""

    # 监管方式
    m = re.search(r"监管方式\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["trade_mode"] = m.group(1).strip() if m else ""

    # 贸易国（地区）
    m = re.search(r"贸易国（?地区）?\s*\(?\w*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["trade_country"] = m.group(1).strip() if m else ""

    # 件数
    m = re.search(r"件数\s*\n?\s*(\d+)", text)
    fields["quantity"] = m.group(1).strip() if m else ""

    # 毛重
    m = re.search(r"毛重（?千克）?\s*\n?\s*(\d+)", text)
    fields["gross_weight"] = m.group(1).strip() if m else ""

    # 净重
    m = re.search(r"净重（?千克）?\s*\n?\s*(\d+)", text)
    fields["net_weight"] = m.group(1).strip() if m else ""

    # 成交方式
    m = re.search(r"成交方式\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["deal_mode"] = m.group(1).strip() if m else ""

    # 征免性质
    m = re.search(r"征免性质\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["duty_nature"] = m.group(1).strip() if m else ""

    # 运抵国（地区）
    m = re.search(r"运抵国（?地区）?\s*\(?\w*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["dest_country"] = m.group(1).strip() if m else ""

    # 指运港
    m = re.search(r"指运港\s*\(?\w*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["dest_port"] = m.group(1).strip() if m else ""

    # 出境关别
    m = re.search(r"出境关别\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["exit_customs"] = m.group(1).strip() if m else ""

    # 离境口岸
    m = re.search(r"离境口岸\s*\(?\d*\)?\s*\n?\s*(.+?)(?:\n|$)", text)
    fields["exit_port"] = m.group(1).strip() if m else ""

    # 随附单证及编号
    m = re.search(r"随附单证及编号\s*\n?\s*(.+?)(?:\n|$)", text)
    if m:
        fields["attached_docs"] = m.group(1).strip()
    else:
        # 尝试匹配 "随附单证1:xxx" 格式
        m = re.search(r"随附单证\d*:*(.+?)(?:\n|$)", text)
        fields["attached_docs"] = m.group(1).strip() if m else ""

    # 标记唛码及备注
    m = re.search(r"标记唛码及备注\s*\n?\s*(.+?)(?:\n|$)", text)
    if m:
        fields["marks_remarks"] = m.group(1).strip()
    else:
        m = re.search(r"备注:\s*(.+?)(?:\n|$)", text)
        fields["marks_remarks"] = m.group(1).strip() if m else ""

    return fields


def extract_pre_recording_items(text: str) -> list:
    """
    从预录单文本中提取商品明细（项号后）
    """
    items = []

    # 找到项号区域
    item_section_match = re.search(r"项号\s*\n", text)
    if not item_section_match:
        return items

    item_text = text[item_section_match.end():]

    # 预录单每个项号的格式：
    # 1 \n 商品编号 商品名称 \n 规格型号 \n 数量行1 \n 数量行2 \n 数量行3 \n 单价 \n 原产国 \n 目的国 \n 货源地 \n 征免
    # 按项号分割
    parts = re.split(r"\n\s*(?=\d+\s*\n)", item_text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        m = re.match(r"(\d+)\s*\n(.*)", part, re.DOTALL)
        if not m:
            continue

        item_no = m.group(1)
        content = m.group(2)
        item = _parse_pre_recording_item(item_no, content)
        if item.get("product_code"):
            items.append(item)

    return items


def _parse_pre_recording_item(item_no: str, content: str) -> dict:
    """解析单个预录单商品条目"""
    lines = [l.strip() for l in content.split("\n") if l.strip()]

    item = {
        "item_no": item_no,
        "product_code": "",
        "product_name": "",
        "spec_model": "",
        "quantity_unit": "",
        "unit_price": "",
        "total_price": "",
        "currency": "",
        "origin_country": "",
        "final_dest_country": "",
        "domestic_source": "",
        "duty_exemption": "",
    }

    if not lines:
        return item

    # 第一行通常是 "商品编号 商品名称"
    first_line = lines[0]
    code_match = re.match(r"(\d{10})\s*(.*)", first_line)
    if code_match:
        item["product_code"] = code_match.group(1)
        item["product_name"] = code_match.group(2).strip()

    # 后续行解析
    qty_lines = []
    price_lines = []

    for line in lines[1:]:
        # 跳过纯点线
        if set(line) <= {".",}:
            continue

        # 规格型号（含 | 分隔的行）
        if "|" in line and not line.startswith("("):
            if not item["spec_model"]:
                item["spec_model"] = line
            else:
                item["spec_model"] += " " + line
            continue

        # 数量行（xx千克 / xx个 / xx件 / xx套 等）
        if QTY_UNIT_RE.match(line):
            qty_lines.append(line)
            continue

        # 价格
        if re.match(r"^[\d,.]+$", line):
            price_lines.append(line)
            continue

        # 原产国
        if line == "中国" or re.match(r"中国\s*\(CHN\)", line) or "(CHN)" in line:
            item["origin_country"] = line
            continue

        # 目的国：含3字母国家代码括号的行，如 "加拿大 (CAN)", "(DEU)", "德国"
        if re.match(r"^\(?\w{3}\)?$", line):  # 如 (DEU), (USA)
            continue
        country_m = re.match(r"(.+?)\s*\(?([A-Z]{3})\)?\s*$", line)
        if country_m:
            item["final_dest_country"] = country_m.group(1).strip()
            continue
        # 纯中文国家名
        if re.match(r"^[\u4e00-\u9fff]{2,5}$", line):
            # 常见国家名检测（排除已知非国家的词）
            non_countries = {"人民币", "照章", "照章征税", "家用", "收纳", "无型号", "无款号"}
            if line not in non_countries and not re.match(r"^\d+", line):
                item["final_dest_country"] = line
                continue

        # 境内货源地：如 "福州其他 (35019) 照章征税" 或 "(44199)东莞"
        if re.search(r"\(\d{4,6}\)", line) or "照章" in line:
            # 提取货源地名称：去掉括号数字代码和"照章征税"后缀
            cleaned = re.sub(r"\(\d{4,6}\)", "", line)
            cleaned = re.sub(r"照章.*$", "", cleaned)
            cleaned = re.sub(r"^\(?\d{4,6}\)?\s*", "", cleaned)  # 开头的数字代码
            cleaned = cleaned.strip()
            if cleaned:
                item["domestic_source"] = cleaned
            if "照章" in line:
                item["duty_exemption"] = "照章征税(1)"
            continue

        # 币制
        if line == "人民币":
            item["currency"] = line
            continue

        # 征免
        if "照章" in line:
            item["duty_exemption"] = "照章征税(1)"
            continue

    # 组装数量行
    item["quantity_unit"] = " / ".join(qty_lines)

    # 组装价格行
    if len(price_lines) >= 1:
        item["unit_price"] = price_lines[0]
    if len(price_lines) >= 2:
        item["total_price"] = price_lines[1]
    if len(price_lines) >= 3:
        item["currency"] = "人民币"

    return item


# ============================================================
# 合同页字段提取
# ============================================================

def extract_contract_buyer(text: str) -> str:
    """
    从合同页提取买方字段
    """
    # 尝试多种格式匹配买方
    patterns = [
        r"买\s*方\s*\n?\s*(.+?)(?:\n|$)",
        r"Buyers?:\s*\n?\s*(.+?)(?:\n|$)",
        r"Sold\s+to\s+(.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and not val.startswith("日") and not val.startswith("Date"):
                return val
    return ""


# ============================================================
# 主入口：从解析后的 PDF 中提取所有字段
# ============================================================

def extract_all_fields(customs_pages: list, pre_pages: list, contract_pages: list,
                       pre_continuation_pages: list = None) -> dict:
    """
    主提取入口
    customs_pages: [PageInfo, ...] - 报关单页（可能包含 customs_declaration 和 pre_recording 类型的页面）
    pre_pages: [PageInfo, ...] - 预录单页
    contract_pages: [PageInfo, ...] - 合同页
    pre_continuation_pages: [PageInfo, ...] - 预录单续页（unknown类型但含商品数据）
    返回: {
        "customs_header": {...},
        "pre_header": {...},
        "customs_items": [...],
        "pre_items": [...],
        "buyer": "...",  # 来自合同页
    }
    """
    # 将报关单页面按类型分组处理
    customs_decl_pages = [p for p in customs_pages if p.doc_type == "customs_declaration"]
    pre_rec_in_customs = [p for p in customs_pages if p.doc_type == "pre_recording"]
    other_customs_pages = [p for p in customs_pages if p.doc_type not in ("customs_declaration", "pre_recording")]

    contract_text = "\n\n".join([p.text for p in contract_pages])

    # 报关单页面用文本正则提取（排版简单，效果好）
    customs_text = "\n\n".join([p.text for p in customs_decl_pages])
    customs_header = extract_customs_header(customs_text)
    customs_items = extract_customs_items(customs_text)

    # Fallback: 标准提取失败时，尝试续页/核对单格式提取
    if not customs_items:
        customs_items = _extract_items_from_continuation(customs_text)
    if not customs_items:
        customs_items = _extract_items_from_hedui(customs_text)

    # 逐页补充：核对单格式的续页中，"项号"在页面底部，
    # extract_customs_items 只提取"项号"之后的内容，会漏掉上方的数据。
    # 因此逐页用 _extract_items_from_continuation 补充缺失的项号。
    existing_nos = {it["item_no"] for it in customs_items}
    for page in customs_decl_pages:
        page_items = _extract_items_from_continuation(page.text)
        for item in page_items:
            if item["item_no"] not in existing_nos:
                customs_items.append(item)
                existing_nos.add(item["item_no"])
    if len(customs_items) > len(existing_nos) - 1:
        customs_items.sort(key=lambda x: int(x["item_no"]) if x.get("item_no", "").isdigit() else 999)

    # 如果报关单 PDF 包含核对单页面（pre_recording），也提取其商品数据
    # 核对单通常包含完整的商品项号列表，可补充报关单缺失的项号
    if pre_rec_in_customs:
        from src.pdf_parser import (
            extract_pre_recording_fields_by_position,
            extract_pre_recording_items_by_position,
        )
        # 如果报关单页面没有表头信息，用核对单页面的表头
        if not customs_header or not customs_header.get("sender_unit"):
            hedui_header = extract_pre_recording_header(pre_rec_in_customs[0].text)
            if hedui_header:
                for k, v in hedui_header.items():
                    if v and (k not in customs_header or not customs_header[k]):
                        customs_header[k] = v
        # 从核对单页面提取商品
        existing_nos = {it["item_no"] for it in customs_items}
        for page in pre_rec_in_customs:
            items = extract_pre_recording_items_by_position(page)
            if not items:
                items = _extract_items_from_hedui(page.text)
            if not items:
                items = _extract_items_from_continuation(page.text)
            for item in items:
                if item["item_no"] not in existing_nos:
                    customs_items.append(item)
                    existing_nos.add(item["item_no"])
        # 按项号排序
        customs_items.sort(key=lambda x: int(x["item_no"]) if x.get("item_no", "").isdigit() else 999)

    # 处理 other_customs_pages（unknown 类型等）
    if other_customs_pages:
        other_text = "\n\n".join([p.text for p in other_customs_pages])
        other_items = extract_customs_items(other_text)
        if not other_items:
            other_items = _extract_items_from_continuation(other_text)
        existing_nos = {it["item_no"] for it in customs_items}
        for item in other_items:
            if item["item_no"] not in existing_nos:
                customs_items.append(item)
                existing_nos.add(item["item_no"])
        customs_items.sort(key=lambda x: int(x["item_no"]) if x.get("item_no", "").isdigit() else 999)

    # 预录单用位置感知提取（双列排版，纯文本会错乱）
    pre_header = {}
    pre_items = []

    if pre_pages:
        from src.pdf_parser import (
            extract_pre_recording_fields_by_position,
            extract_pre_recording_items_by_position,
        )
        # 用第一个预录单页面提取表头
        pre_header = extract_pre_recording_fields_by_position(pre_pages[0])

        # "仅供核对用"格式的文本兜底：位置提取不稳定的字段用正则补全
        if "仅供核对" in pre_pages[0].text:
            pre_header = _hedui_text_fallback(pre_header, pre_pages[0].text)

        # 所有预录单页面提取商品明细
        for page in pre_pages:
            items = extract_pre_recording_items_by_position(page)
            if not items:
                # 续页可能没有表头行，位置感知提取失败，回退到文本提取
                items = _extract_items_from_continuation(page.text)
            if not items:
                # 核对单格式（列头垂直排列），用专门的文本解析器
                items = _extract_items_from_hedui(page.text)

            # 位置感知提取可能因列边界问题丢失字段（如总价），
            # 用文本提取结果补充缺失字段
            if items:
                text_items = _extract_items_from_continuation(page.text)
                if not text_items:
                    text_items = _extract_items_from_hedui(page.text)
                if text_items:
                    text_map = {it["item_no"]: it for it in text_items}
                    for item in items:
                        fallback = text_map.get(item["item_no"])
                        if fallback:
                            for key in list(item.keys()):
                                if not item[key] and fallback.get(key):
                                    item[key] = fallback[key]
                    # 将文本提取中发现但位置提取遗漏的 item 补充进来
                    existing_nos = {it["item_no"] for it in items}
                    for ti in text_items:
                        if ti["item_no"] not in existing_nos:
                            items.append(ti)
                            existing_nos.add(ti["item_no"])

            pre_items.extend(items)

    # 预录单续页（多页预录单的后续页，不含标题但含商品数据）
    if pre_continuation_pages:
        from src.pdf_parser import extract_pre_recording_items_by_position
        for page in pre_continuation_pages:
            items = extract_pre_recording_items_by_position(page)
            if not items:
                items = _extract_items_from_continuation(page.text)
            if not items:
                items = _extract_items_from_hedui(page.text)

            # 文本提取补充缺失字段
            if items:
                text_items = _extract_items_from_continuation(page.text)
                if not text_items:
                    text_items = _extract_items_from_hedui(page.text)
                if text_items:
                    text_map = {it["item_no"]: it for it in text_items}
                    for item in items:
                        fallback = text_map.get(item["item_no"])
                        if fallback:
                            for key in list(item.keys()):
                                if not item[key] and fallback.get(key):
                                    item[key] = fallback[key]

            pre_items.extend(items)

    result = {
        "customs_header": customs_header,
        "pre_header": pre_header,
        "customs_items": customs_items,
        "pre_items": pre_items,
        "buyer": extract_contract_buyer(contract_text),
    }

    # 把买方写入报关单 header
    if result["buyer"]:
        result["customs_header"]["buyer"] = result["buyer"]

    return result


def _hedui_text_fallback(fields: dict, text: str) -> dict:
    """
    "仅供核对用"格式专用文本正则兜底。
    该格式的值在文本中以固定模式出现，用特定正则逐字段提取。
    仅填充 fields 中为空或值明显不合理的字段。
    """
    lines = text.split("\n")
    # 过滤空行
    lines = [l.strip() for l in lines if l.strip()]

    # 收集所有非标签、非噪声的值行
    _noise = {
        "征免", "境内货源地", "最终目的国(地区)", "原产国(地区)", "单价/总价/币制",
        "数量及单位", "商品名称及规格型号", "商品编号", "项号",
        "中华人民共和国海关出口货物报关单", "页码/页数", "仅供核对用", "仅供核对使用",
        "核对单，仅供核对使用", "海关编号", "预录入编号", "备案号", "申报日期",
        "出口日期", "出境关别", "境内发货人", "提运单号", "运输工具名称及航次号",
        "运输方式", "境外收货人", "许可证号", "征免性质", "监管方式", "生产销售单位",
        "离境口岸", "指运港", "运抵国（地区）", "运抵国(地区)", "贸易国（地区）",
        "贸易国(地区)", "合同协议号", "杂费", "保费", "运费", "成交方式",
        "净重(千克)", "毛重(千克)", "件数", "包装种类", "随附单证及编号",
        "标记唛码及备注", "特殊关系确认", "价格影响确认", "支付特许权使用费确认",
        "公式定价确认", "暂定价格确认", "自报自缴", "水运中转", "申报单位",
        "电话", "报关人员证号", "报关人员", "兹申明", "申报单位（签章）",
        "海关批注及签章",
    }

    def _is_line_noise(line):
        clean = re.sub(r"\([A-Za-z0-9]+\)", "", line).strip()
        if not clean:
            return True
        if clean in _noise:
            return True
        for n in _noise:
            if n in clean:
                return True
        if re.match(r"^\d+/\d+$", clean):
            return True
        if clean.startswith("*"):
            return True
        if re.match(r"^打印时间", clean):
            return True
        return False

    value_lines = [l for l in lines if not _is_line_noise(l)]

    # ---- 标签定位提取（核对单格式：值在标签前面） ----
    # 核对单文本中，值出现在对应标签的上方/前面
    # 例如："中国香港\n贸易国（地区）(HKG)" → trade_country = "中国香港"
    _label_field_map = {
        "贸易国": "trade_country",
        "运抵国": "dest_country",
        "指运港": "dest_port",
        "离境口岸": "exit_port",
        "出境关别": "exit_customs",
        "监管方式": "trade_mode",
        "征免性质": "duty_nature",
        "运输方式": "transport_mode",
        "成交方式": "deal_mode",
        "包装种类": "package_type",
    }
    _label_value_clean = {
        "exit_customs": lambda v: re.sub(r"^\(?\d+\)?", "", v).strip() if re.match(r"^\(\d+\)", v) else v,
        "trade_mode": lambda v: re.sub(r"^\(\d+\)", "", v).strip(),
        "duty_nature": lambda v: re.sub(r"^\(\d+\)", "", v).strip(),
        "transport_mode": lambda v: re.sub(r"^\(\d+\)", "", v).strip(),
        "deal_mode": lambda v: re.sub(r"^\(\d+\)", "", v).strip(),
        "package_type": lambda v: re.sub(r"^\(\d+\)", "", v).strip(),
    }
    # 记录已被标签定位使用的行索引，避免重复匹配
    _label_used = set()
    for i, line in enumerate(lines):
        for label, field_id in _label_field_map.items():
            if label not in line:
                continue
            if _is_valid_field_value(field_id, fields.get(field_id, "")):
                continue
            # 向前找值：值在标签之前
            for j in range(i - 1, max(i - 6, -1), -1):
                if j in _label_used:
                    continue
                prev = lines[j].strip() if j >= 0 else ""
                if not prev or _is_line_noise(prev):
                    continue
                # 清理括号中的代码
                val = re.sub(r"\([A-Za-z0-9]+\)$", "", prev).strip()
                cleaner = _label_value_clean.get(field_id)
                if cleaner:
                    val = cleaner(val)
                if val and _is_valid_field_value(field_id, val):
                    fields[field_id] = val
                    _label_used.add(j)
                break

    # 合并相邻的括号代码行和值行
    # 例如 "(3104)" + "北仑海关" → "(3104)北仑海关"
    merged = []
    i = 0
    while i < len(value_lines):
        line = value_lines[i]
        # 如果当前行是纯括号代码（如 "(3104)"），和下一行合并
        if re.match(r"^\([A-Za-z0-9]+\)$", line) and i + 1 < len(value_lines):
            next_line = value_lines[i + 1]
            # 不合并两个代码行
            if not re.match(r"^\([A-Za-z0-9]+\)$", next_line):
                merged.append(f"{line}{next_line}")
                i += 2
                continue
        merged.append(line)
        i += 1

    # 对每个字段，用特定模式在合并后的值行中查找
    field_patterns = {
        "sender_unit": [
            # 发货人：含"公司"的中文名（通常是第一个出现的）
            r"^([\u4e00-\u9fff][\u4e00-\u9fff\w]+公司)$",
        ],
        "buyer": [
            # 境外收货人：英文名（含大写字母和括号）
            r"^([A-Z][A-Z\s\(\)]+LIMITED\s*[A-Z]*)$",
            r"^([A-Z][A-Z\s\(\)]+CO\.\s*[Ll]td\.?)$",
            r"^([A-Z][A-Z\s\(\)]+INC\.?)$",
        ],
        "business_unit": [
            # 生产销售单位：含"公司"的中文名
            r"^([\u4e00-\u9fff][\u4e00-\u9fff\w]+公司)$",
        ],
        "contract_no": [
            # 合同协议号：纯数字日期格式（如 20260521006, 20260514003）优先
            r"^(\d{10,12})$",
            # FBA/FCL等大写字母开头的编号（如 FBA603N147833NB0）
            r"^([A-Z]{2,}[A-Z0-9]+)$",
        ],
        "exit_customs": [
            r"^(\([\d]+\)([\u4e00-\u9fff]+海关))$",
            r"^\(([\u4e00-\u9fff]+海关)\)$",
            r"^([\u4e00-\u9fff]+海关)$",
        ],
        "transport_mode": [
            r"^([\u4e00-\u9fff]+运输)$",
        ],
        "trade_mode": [
            r"^(\(\d+\))?([\u4e00-\u9fff]+贸易)$",
            r"^([\u4e00-\u9fff]+贸易)$",
        ],
        "duty_nature": [
            r"^(\(\d+\))?([\u4e00-\u9fff]+征税)$",
            r"^([\u4e00-\u9fff]+征税)$",
        ],
        "trade_country": [
            r"^(\(\w+\))?(中国[\u4e00-\u9fff]+)$",
            r"^([\u4e00-\u9fff]+)$",  # 通用国家名
        ],
        "dest_country": [
            r"^([\u4e00-\u9fff]{2,4})$",  # 国家名
        ],
        "dest_port": [
            r"^([\u4e00-\u9fff]{2,4})$",  # 港口名
        ],
        "deal_mode": [
            r"^(FOB|CIF|CFR|FCA|CPT|CIP|DAP|DDP|EXW|FAS)$",
        ],
        "package_type": [
            r"^(纸制或纤维板制盒/箱|木箱|纸箱|托盘|裸装|散装)",
        ],
        "attached_docs": [
            r"^(随附单证\d*:.*+)$",
        ],
        "marks_remarks": [
            r"^(备注[：:].+)$",
        ],
    }

    # 按字段逐个匹配
    used_indices = set()

    # 特殊处理：合同协议号优先选纯数字日期格式（如20260514003），
    # 再选FBA格式（如FBA603N147833NB0），排除提运单号（如18632-DLM250748）
    if not _is_valid_field_value("contract_no", fields.get("contract_no", "")):
        numeric_match = ("", -1)
        alpha_match = ("", -1)
        for idx, line in enumerate(merged):
            if idx in used_indices:
                continue
            if re.match(r"^\d{10,12}$", line) and _is_valid_field_value("contract_no", line):
                if numeric_match[1] < 0:
                    numeric_match = (line, idx)
            if re.match(r"^[A-Z]{2,}[A-Z0-9]+$", line) and _is_valid_field_value("contract_no", line):
                if alpha_match[1] < 0:
                    alpha_match = (line, idx)
        if numeric_match[0]:
            fields["contract_no"] = numeric_match[0]
            used_indices.add(numeric_match[1])
        elif alpha_match[0]:
            fields["contract_no"] = alpha_match[0]
            used_indices.add(alpha_match[1])

    # 特殊处理的字段：需要按出现顺序区分重复值
    company_count = 0  # 用于区分发货人和生产销售单位
    country_count = 0   # 用于区分运抵国和指运港

    for field_id, patterns in field_patterns.items():
        current_val = fields.get(field_id, "")

        # 跳过已经正确提取的字段（验证当前值是否合理）
        if _is_valid_field_value(field_id, current_val):
            continue

        # 在 merged 值行中搜索
        for idx, line in enumerate(merged):
            if idx in used_indices:
                continue
            for pattern in patterns:
                m = re.match(pattern, line)
                if m:
                    val = m.group(m.lastindex or 0) if m.lastindex else m.group(0)

                    # 验证替换值也必须合理
                    if not _is_valid_field_value(field_id, val):
                        continue

                    # 特殊处理：发货人和生产销售单位都是公司名
                    if field_id == "sender_unit":
                        if company_count == 0:
                            fields[field_id] = val
                            used_indices.add(idx)
                            company_count += 1
                        break
                    elif field_id == "business_unit":
                        company_count += 1
                        if company_count >= 2:
                            fields[field_id] = val
                            used_indices.add(idx)
                        break
                    else:
                        fields[field_id] = val
                        used_indices.add(idx)
                        break
            else:
                continue
            break

    # 件数/毛重/净重：查找纯数字值
    _fill_numeric_fields(fields, merged, used_indices)

    return fields


def _is_valid_field_value(field_id: str, value: str) -> bool:
    """检查字段值是否合理（用于判断位置提取的结果是否需要被覆盖）"""
    if not value or value == "-":
        return False

    # 字段 → 合理值的特征
    validators = {
        "sender_unit": lambda v: bool(re.search(r"[\u4e00-\u9fff]", v) and "公司" in v or "企业" in v or "厂" in v or "部" in v),
        "buyer": lambda v: bool(re.search(r"[A-Za-z]", v)),
        "business_unit": lambda v: bool(re.search(r"[\u4e00-\u9fff]", v) and "公司" in v or "企业" in v or "厂" in v),
        "contract_no": lambda v: bool(re.search(r"[A-Z0-9-]", v) and len(v) <= 30 and "箱" not in v and "袋" not in v and "公司" not in v),
        "exit_customs": lambda v: "海关" in v,
        "transport_mode": lambda v: bool(re.search(r"[\u4e00-\u9fff]+运输|[\u4e00-\u9fff]+航运", v)),
        "trade_mode": lambda v: bool(re.search(r"贸易|加工|保税", v)) and "公司" not in v and "海关" not in v and "香港" not in v and len(v) <= 10,
        "duty_nature": lambda v: bool(re.search(r"征税|免税|退税", v)) and len(v) <= 6,
        "trade_country": lambda v: len(v) <= 8 and bool(re.search(r"^[\u4e00-\u9fff（）()A-Z]+$", v)) and "公司" not in v and "海关" not in v and "贸易" not in v and "征税" not in v and "运输" not in v,
        "dest_country": lambda v: len(v) <= 6 and bool(re.search(r"^[\u4e00-\u9fff]+$", v)) and "公司" not in v and "海关" not in v and "贸易" not in v and "征税" not in v and "运输" not in v,
        "dest_port": lambda v: len(v) <= 8 and bool(re.search(r"^[\u4e00-\u9fff]+$", v)) and "贸易" not in v and "征税" not in v and "海关" not in v and "公司" not in v,
        "deal_mode": lambda v: v in ("FOB", "CIF", "CFR", "FCA", "CPT", "CIP", "DAP", "DDP", "EXW", "FAS"),
        "package_type": lambda v: bool(re.search(r"纸制|木箱|纸箱|托盘|裸装|散装", v)),
        "quantity": lambda v: bool(re.match(r"^\d+$", v)),
        "gross_weight": lambda v: bool(re.match(r"^[\d.]+$", v)),
        "net_weight": lambda v: bool(re.match(r"^[\d.]+$", v)),
        "attached_docs": lambda v: "随附单证" in v or v.strip() != "",
        "marks_remarks": lambda v: "备注" in v or "N/M" in v or v.strip() != "",
    }

    validator = validators.get(field_id)
    if validator:
        return validator(value)
    return True  # 未知字段，保留当前值


def _fill_numeric_fields(fields: dict, merged_lines: list, used_indices: set):
    """从文本行中提取件数、毛重、净重等数值字段"""
    # 收集所有纯数字行（排除已使用的）
    numbers = []
    for idx, line in enumerate(merged_lines):
        if idx in used_indices:
            continue
        # 纯整数（可能是件数、毛重、净重）
        if re.match(r"^\d+$", line):
            numbers.append((idx, line, int(line)))
        # 小数（可能是重量）
        elif re.match(r"^\d+\.\d+$", line):
            numbers.append((idx, line, float(line)))

    # 按数值大小和出现位置判断字段
    # 件数通常是中等大小的整数（10-10000）
    # 毛重 > 净重 > 0
    # 假设件数、毛重、净重在文本中相邻出现

    int_nums = [(idx, line, val) for idx, line, val in numbers
                if isinstance(val, int) and val > 1 and val < 100000]

    # 如果件数未提取，找最可能的值
    if not fields.get("quantity") or not re.match(r"^\d+$", fields.get("quantity", "")):
        # 件数通常是三个数值（件数、毛重、净重）中的第一个，且通常最小
        if len(int_nums) >= 3:
            # 取第一组三个连续整数
            for i in range(len(int_nums) - 2):
                if int_nums[i+1][0] - int_nums[i][0] <= 2 and int_nums[i+2][0] - int_nums[i+1][0] <= 2:
                    fields["quantity"] = str(int_nums[i][2])
                    used_indices.add(int_nums[i][0])
                    # 毛重和净重
                    if not fields.get("gross_weight") or not re.match(r"^[\d.]+$", fields.get("gross_weight", "")):
                        fields["gross_weight"] = str(int_nums[i+1][2])
                        used_indices.add(int_nums[i+1][0])
                    if not fields.get("net_weight") or not re.match(r"^[\d.]+$", fields.get("net_weight", "")):
                        fields["net_weight"] = str(int_nums[i+2][2])
                        used_indices.add(int_nums[i+2][0])
                    break
        elif len(int_nums) >= 1:
            # 只有一个整数，可能是件数
            fields["quantity"] = str(int_nums[0][2])
            used_indices.add(int_nums[0][0])

    # 如果毛重/净重未提取，从小数中查找
    float_nums = [(idx, line, val) for idx, line, val in numbers if isinstance(val, float)]
    if not fields.get("gross_weight") and float_nums:
        # 第一个较大的小数可能是毛重
        for idx, line, val in float_nums:
            if idx not in used_indices and val > 10:
                fields["gross_weight"] = line
                used_indices.add(idx)
                break
    if not fields.get("net_weight") and float_nums:
        for idx, line, val in float_nums:
            if idx not in used_indices and val > 5:
                fields["net_weight"] = line
                used_indices.add(idx)
                break
