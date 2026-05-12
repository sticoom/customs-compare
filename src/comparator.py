"""
比对引擎：逐字段执行校验规则
"""
import re
from src.config import (
    CUSTOMS_HEADER_FIELDS,
    CUSTOMS_ITEM_FIELDS,
    SPEC_MODEL_MAPPING,
    DOMESTIC_SOURCE_MAPPING,
)


# 比对结果状态
STATUS_PASS = "pass"            # ✅ 通过
STATUS_FAIL = "fail"            # ❌ 不通过
STATUS_FUZZY = "fuzzy"          # ⚠️ 模糊匹配
STATUS_MANUAL = "manual"        # 🔍 待人工确认
STATUS_EMPTY = "empty"          # 值为空，跳过


def normalize_value(val: str) -> str:
    """规范化值：去空格、统一标点、去规格前缀"""
    if not val:
        return ""
    val = val.strip()
    val = re.sub(r"\s+", "", val)  # 去所有空格
    val = val.replace("（", "(").replace("）", ")")
    # 去掉"规格："前缀，报关单和预录单格式不同但内容一致
    val = re.sub(r"^规格[：:]", "", val)
    return val


def compare_exact(val1: str, val2: str) -> bool:
    """精确匹配"""
    return normalize_value(val1) == normalize_value(val2)


def compare_fixed(val: str, fixed_value: str) -> bool:
    """固定值校验：检查值中是否包含固定值的核心内容（关键字 + 代码缺一不可）"""
    nv = normalize_value(val)
    fv = normalize_value(fixed_value)
    if not nv:
        return False
    # 完全匹配
    if nv == fv:
        return True
    # 提取括号内的代码和关键字
    code_match = re.search(r"\((\w+)\)", fv)
    keyword = re.sub(r"\([^)]*\)", "", fv).strip()
    if code_match:
        code = code_match.group(1)
        # 关键字和代码必须同时存在
        has_keyword = keyword in nv if keyword else True
        has_code = code in nv
        return has_keyword and has_code
    # 无代码时仅检查关键字
    if keyword and keyword in nv:
        return True
    return False


def _flatten_spec_parts(parts_list):
    """将规格型号的管道段拆分为最小粒度子项集合（去规格前缀、按逗号拆分）"""
    result = set()
    for p in parts_list:
        cleaned = re.sub(r"^规格[：:]\s*", "", p)
        sub_parts = re.split(r"[，,、]\s*", cleaned)
        for sp in sub_parts:
            nv = normalize_value(sp)
            if nv:
                result.add(nv)
    return result


def compare_fuzzy_spec(spec1: str, spec2: str) -> dict:
    """
    规格型号比对：只看内容是否一致，不管位置。
    - 按 | 分隔后做映射转换，忽略空段
    - 将所有非空段拆分为最小粒度后作为集合比较
    - 报关单内容必须全部出现在预录单中才算通过
    """
    # 按 | 分隔，去掉空段
    parts1 = [p.strip() for p in spec1.split("|") if p.strip()]
    parts2 = [p.strip() for p in spec2.split("|") if p.strip()]

    # 映射转换：报关单中的文字 → 预录单中的数字
    mapped_parts1 = []
    for p in parts1:
        mapped = p
        for cn_key, num_val in SPEC_MODEL_MAPPING.items():
            if cn_key in p:
                mapped = p.replace(cn_key, num_val)
        mapped_parts1.append(mapped)

    # 拆分为最小粒度集合
    set1 = _flatten_spec_parts(mapped_parts1)
    set2 = _flatten_spec_parts(parts2)

    only_in_1 = set1 - set2
    only_in_2 = set2 - set1

    # 构建展示用详情
    details = []
    max_len = max(len(mapped_parts1), len(parts2))
    for i in range(max_len):
        p1 = mapped_parts1[i] if i < len(mapped_parts1) else ""
        p2 = parts2[i] if i < len(parts2) else ""
        if not p1 and not p2:
            continue
        match = compare_exact(p1, p2) if p1 and p2 else False
        details.append({
            "item1": p1 or "(空)",
            "item2": p2 or "(空)",
            "match": match,
            "note": "" if match else "位置不同",
        })

    if not only_in_1 and not only_in_2:
        return {"match": True, "details": details}
    elif not only_in_1:
        # 报关单内容全部在预录单中，预录单多了一些额外项
        return {"match": True, "details": details}
    else:
        note = "报关单有但预录单缺失: " + ", ".join(only_in_1)
        return {"match": False, "details": details, "note": note}


def compare_domestic_source(val1: str, val2: str) -> dict:
    """
    境内货源地比对：预录单的城市名必须与报关单一致
    返回: {"match": bool, "detail": str}
    """
    nv1 = normalize_value(val1)
    nv2 = normalize_value(val2)

    # 直接匹配
    if nv1 == nv2:
        return {"match": True, "detail": "完全一致"}

    # 提取城市名（从报关单和预录单中分别提取）
    c1_city = ""
    c2_city = ""
    for full_name, keyword in DOMESTIC_SOURCE_MAPPING.items():
        if keyword in nv1 or full_name in nv1:
            c1_city = keyword
        if keyword in nv2 or full_name in nv2:
            c2_city = keyword

    if c1_city and c2_city:
        if c1_city == c2_city:
            return {"match": True, "detail": f"城市一致: {c1_city}"}
        else:
            return {"match": False, "detail": f"城市不一致: 报关单={c1_city}, 预录单={c2_city}"}

    # 尝试直接比较（包含关系）
    if nv1 and nv2:
        for full_name, keyword in DOMESTIC_SOURCE_MAPPING.items():
            if (keyword in nv1 or full_name in nv1) and (keyword in nv2 or full_name in nv2):
                return {"match": True, "detail": f"城市一致: {keyword}"}

    return {"match": False, "detail": f"不一致: 报关单={val1}, 预录单={val2}"}


def compare_country(val1: str, val2: str) -> bool:
    """国家名称/代码模糊匹配"""
    nv1 = normalize_value(val1)
    nv2 = normalize_value(val2)
    if nv1 == nv2:
        return True

    # 国家代码映射
    country_map = {
        "德国": ["DEU", "DE", "GERMANY", "德国"],
        "美国": ["USA", "US", "UNITEDSTATES", "美国"],
        "英国": ["GBR", "GB", "UK", "UNITEDKINGDOM", "英国"],
        "法国": ["FRA", "FR", "FRANCE", "法国"],
        "日本": ["JPN", "JP", "JAPAN", "日本"],
        "韩国": ["KOR", "KR", "KOREA", "韩国"],
        "中国": ["CHN", "CN", "CHINA", "中国"],
        "澳大利亚": ["AUS", "AU", "AUSTRALIA", "澳大利亚"],
        "中国香港": ["HKG", "HK", "HONGKONG", "中国香港", "香港"],
        "加拿大": ["CAN", "CA", "CANADA", "加拿大"],
        "新加坡": ["SGP", "SG", "SINGAPORE", "新加坡"],
        "荷兰": ["NLD", "NL", "NETHERLANDS", "荷兰"],
        "意大利": ["ITA", "IT", "ITALY", "意大利"],
        "西班牙": ["ESP", "ES", "SPAIN", "西班牙"],
        "印度": ["IND", "IN", "INDIA", "印度"],
        "泰国": ["THA", "TH", "THAILAND", "泰国"],
        "马来西亚": ["MYS", "MY", "MALAYSIA", "马来西亚"],
        "越南": ["VNM", "VN", "VIETNAM", "越南"],
        "印度尼西亚": ["IDN", "ID", "INDONESIA", "印度尼西亚"],
        "菲律宾": ["PHL", "PH", "PHILIPPINES", "菲律宾"],
        "墨西哥": ["MEX", "MX", "MEXICO", "墨西哥"],
        "巴西": ["BRA", "BR", "BRAZIL", "巴西"],
        "俄罗斯": ["RUS", "RU", "RUSSIA", "俄罗斯"],
        "新西兰": ["NZL", "NZ", "NEWZEALAND", "新西兰"],
        "阿联酋": ["ARE", "AE", "UNITEDARABEMIRATES", "阿联酋"],
        "沙特阿拉伯": ["SAU", "SA", "SAUDIARABIA", "沙特阿拉伯"],
    }

    def normalize_country(v):
        v = v.upper()
        for cn_name, codes in country_map.items():
            if cn_name in v or any(c in v for c in codes):
                return cn_name
        return v

    return normalize_country(nv1) == normalize_country(nv2)


def compare_quantity_with_swap(customs_qty: str, pre_qty: str) -> dict:
    """
    数量及单位比对（带行交换规则）
    报关单第1行(件) = 预录单第3行(件)
    报关单第2行(千克) = 预录单第1行(千克)
    """
    customs_lines = [l.strip() for l in customs_qty.split("/") if l.strip()]
    pre_lines = [l.strip() for l in pre_qty.split("/") if l.strip()]

    details = []

    # 按单位分类
    def extract_units(lines):
        kg_val = ""
        piece_val = ""
        ge_val = ""
        for line in lines:
            if "千克" in line:
                kg_val = line
            elif "件" in line:
                piece_val = line
            elif "个" in line:
                ge_val = line
        return kg_val, piece_val, ge_val

    c_kg, c_piece, _ = extract_units(customs_lines)
    p_kg, p_piece, p_ge = extract_units(pre_lines)

    # 比对：报关单的千克 vs 预录单的千克
    kg_match = compare_exact(c_kg, p_kg) if c_kg and p_kg else True
    # 比对：报关单的件 vs 预录单的件
    piece_match = compare_exact(c_piece, p_piece) if c_piece and p_piece else True

    all_match = kg_match and piece_match

    return {
        "match": all_match,
        "details": {
            "千克": {"customs": c_kg, "pre": p_kg, "match": kg_match},
            "件": {"customs": c_piece, "pre": p_piece, "match": piece_match},
        },
    }


# ============================================================
# 主比对入口
# ============================================================

def compare_headers(customs_header: dict, pre_header: dict) -> list:
    """
    比对表头字段（项号前）
    返回: [{field_id, label, customs_value, pre_value, status, notes}, ...]
    """
    results = []

    for field_def in CUSTOMS_HEADER_FIELDS:
        fid = field_def["id"]
        customs_val = customs_header.get(fid, "")
        pre_val = pre_header.get(fid, "")
        check_type = field_def["check_type"]
        notes = field_def.get("notes", "")

        if check_type == "manual":
            status = STATUS_MANUAL
        elif check_type == "match":
            if not customs_val and not pre_val:
                status = STATUS_EMPTY
            elif compare_exact(customs_val, pre_val):
                status = STATUS_PASS
            else:
                status = STATUS_FAIL
        elif check_type == "fixed":
            if not pre_val:
                status = STATUS_EMPTY
            elif compare_fixed(pre_val, field_def.get("fixed_value", "")):
                status = STATUS_PASS
            else:
                status = STATUS_FAIL
        else:
            status = STATUS_MANUAL

        results.append({
            "field_id": fid,
            "label": field_def["label"],
            "customs_value": customs_val,
            "pre_value": pre_val,
            "status": status,
            "notes": notes,
            "check_type": check_type,
            "fixed_value": field_def.get("fixed_value", ""),
        })

    return results


def compare_items(customs_items: list, pre_items: list) -> list:
    """
    比对商品明细（项号后），按项号一一配对
    返回: [{item_no, fields: [{field_id, label, ...}], item_status}, ...]
    """
    results = []

    # 按项号建立索引
    customs_map = {item["item_no"]: item for item in customs_items}
    pre_map = {item["item_no"]: item for item in pre_items}

    all_item_nos = sorted(set(list(customs_map.keys()) + list(pre_map.keys())), key=lambda x: int(x) if x.isdigit() else 0)

    for item_no in all_item_nos:
        c_item = customs_map.get(item_no, {})
        p_item = pre_map.get(item_no, {})

        item_result = {
            "item_no": item_no,
            "fields": [],
        }

        for field_def in CUSTOMS_ITEM_FIELDS:
            fid = field_def["id"]
            c_val = c_item.get(fid, "")
            p_val = p_item.get(fid, "")
            check_type = field_def["check_type"]
            notes = field_def.get("notes", "")

            if check_type == "match":
                if fid == "quantity_unit":
                    # 数量及单位有行交换规则
                    cmp = compare_quantity_with_swap(c_val, p_val)
                    status = STATUS_PASS if cmp["match"] else STATUS_FAIL
                    notes = "行交换比对"
                elif fid in ("unit_price", "total_price"):
                    # 价格数值比对
                    try:
                        c_num = float(c_val.replace(",", ""))
                        p_num = float(p_val.replace(",", ""))
                        status = STATUS_PASS if c_num == p_num else STATUS_FAIL
                    except (ValueError, AttributeError):
                        status = STATUS_FAIL if c_val and p_val else STATUS_EMPTY
                else:
                    if not c_val and not p_val:
                        status = STATUS_EMPTY
                    elif compare_exact(c_val, p_val):
                        status = STATUS_PASS
                    else:
                        status = STATUS_FAIL

            elif check_type == "fixed":
                fixed_val = field_def.get("fixed_value", "")
                if fid == "origin_country":
                    # 原产国：只校验预录单
                    if not p_val:
                        status = STATUS_EMPTY
                    elif compare_fixed(p_val, fixed_val):
                        status = STATUS_PASS
                    else:
                        status = STATUS_FAIL
                elif not p_val and not c_val:
                    status = STATUS_EMPTY
                elif compare_fixed(p_val, fixed_val) or compare_fixed(c_val, fixed_val):
                    status = STATUS_PASS
                else:
                    status = STATUS_FAIL

            elif check_type == "fuzzy":
                if fid == "product_name_spec":
                    # 商品名称+规格型号
                    c_name = c_item.get("product_name", "")
                    p_name = p_item.get("product_name", "")
                    c_spec = c_item.get("spec_model", "")
                    p_spec = p_item.get("spec_model", "")

                    name_match = compare_exact(c_name, p_name)
                    spec_result = compare_fuzzy_spec(c_spec, p_spec)

                    # 组装完整的规格值用于显示
                    c_full = f"{c_name}|{c_spec}" if c_spec else c_name
                    p_full = f"{p_name}|{p_spec}" if p_spec else p_name

                    # 在 notes 中展示详细的逐项比对结果
                    detail_parts = []
                    for d in spec_result.get("details", []):
                        if d.get("match"):
                            detail_parts.append(f"[OK] {d.get('item1','')}")
                        else:
                            note_txt = d.get("note", "")
                            detail_parts.append(f"[!!] 报关单:{d.get('item1','')} vs 预录单:{d.get('item2','')}" +
                                               (f" ({note_txt})" if note_txt else ""))
                    spec_notes = "; ".join(detail_parts) if detail_parts else ""

                    if name_match and spec_result["match"]:
                        status = STATUS_PASS
                        notes = "名称和规格均一致"
                    elif name_match:
                        status = STATUS_FUZZY
                        notes = f"商品名称一致，规格型号部分差异 | {spec_notes}"
                    else:
                        status = STATUS_FAIL
                        notes = f"商品名称不一致 | {spec_notes}"

                    # 将完整值覆盖到 customs_value 和 pre_value，方便用户对比
                    c_val = c_full
                    p_val = p_full

                elif fid == "domestic_source":
                    if not c_val and not p_val:
                        status = STATUS_EMPTY
                    else:
                        ds_result = compare_domestic_source(c_val, p_val)
                        if ds_result["match"]:
                            status = STATUS_PASS
                            notes = ds_result["detail"]
                        else:
                            status = STATUS_FAIL
                            notes = ds_result["detail"]

                elif fid == "final_dest_country":
                    if not c_val and not p_val:
                        status = STATUS_EMPTY
                    elif compare_country(c_val, p_val):
                        status = STATUS_PASS
                    else:
                        status = STATUS_FUZZY
                        notes = "目的国需人工确认"
                else:
                    status = STATUS_MANUAL

            else:
                status = STATUS_MANUAL

            item_result["fields"].append({
                "field_id": fid,
                "label": field_def["label"],
                "customs_value": c_val,
                "pre_value": p_val,
                "status": status,
                "notes": notes,
                "check_type": check_type,
                "fixed_value": field_def.get("fixed_value", ""),
            })

        results.append(item_result)

    return results


def run_comparison(extracted: dict) -> dict:
    """
    执行完整比对
    extracted: extract_all_fields 的返回值
    返回: {
        "header_results": [...],
        "item_results": [...],
        "summary": {pass_count, fail_count, fuzzy_count, manual_count}
    }
    """
    header_results = compare_headers(extracted["customs_header"], extracted["pre_header"])
    item_results = compare_items(extracted["customs_items"], extracted["pre_items"])

    # 统计
    all_results = header_results.copy()
    for item in item_results:
        all_results.extend(item["fields"])

    summary = {
        "pass_count": sum(1 for r in all_results if r["status"] == STATUS_PASS),
        "fail_count": sum(1 for r in all_results if r["status"] == STATUS_FAIL),
        "fuzzy_count": sum(1 for r in all_results if r["status"] == STATUS_FUZZY),
        "manual_count": sum(1 for r in all_results if r["status"] == STATUS_MANUAL),
    }

    return {
        "header_results": header_results,
        "item_results": item_results,
        "summary": summary,
        "contract_no": extracted["customs_header"].get("contract_no", ""),
    }
