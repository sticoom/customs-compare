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
    # 匹配模式：行首的数字（项号）+ 后续内容直到下一个项号或结尾
    item_pattern = re.compile(
        r"(?:^|\n)\s*(\d+)\s*\n\s*(\d{10})\s*\n(.+?)(?=\n\s*\d+\s*\n\s*\d{10}|\Z)",
        re.DOTALL,
    )

    matches = list(item_pattern.finditer(item_text))

    if not matches:
        # 尝试更宽松的匹配
        return _extract_items_loose(item_text)

    for match in matches:
        item_no = match.group(1)
        product_code = match.group(2)
        content = match.group(3).strip()

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
    适用于预录单续页：项号\n商品编码\n名称\n规格型号\n... 的垂直排列格式
    """
    items = []
    # 匹配: 项号(1-3位) + 10位商品编码 + 内容直到下一个项号
    item_pattern = re.compile(
        r"(?:^|\n)\s*(\d{1,3})\s*\n\s*(\d{8,10})\s*\n(.+?)(?=\n\s*\d{1,3}\s*\n\s*\d{8,10}|\Z)",
        re.DOTALL,
    )
    for match in item_pattern.finditer(text):
        item_no = match.group(1)
        product_code = match.group(2)
        content = match.group(3).strip()
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
            duty = re.sub(r'\(\d+\)', '', line).strip()
            item["duty_exemption"] = duty if duty else line
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

        # 跳过纯代码如 (CHN), (USA), (1)
        if re.match(r'^\([A-Z0-9]+\)$', line):
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
    customs_pages: [PageInfo, ...] - 报关单页
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
    customs_text = "\n\n".join([p.text for p in customs_pages])
    contract_text = "\n\n".join([p.text for p in contract_pages])

    # 报关单用文本正则提取（排版简单，效果好）
    customs_header = extract_customs_header(customs_text)
    customs_items = extract_customs_items(customs_text)

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
        # 所有预录单页面提取商品明细
        for page in pre_pages:
            items = extract_pre_recording_items_by_position(page)
            if not items:
                # 续页可能没有表头行，位置感知提取失败，回退到文本提取
                items = _extract_items_from_continuation(page.text)
            if not items:
                # 核对单格式（列头垂直排列），用专门的文本解析器
                items = _extract_items_from_hedui(page.text)
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
