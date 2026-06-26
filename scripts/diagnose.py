#!/usr/bin/env python3
"""
报关单修复 Agent — 诊断脚本
从 app.py 核心逻辑提取，独立运行，输出结构化 JSON 诊断报告。
用法: python diagnose.py <报关单PDF> <预录单PDF>
"""
import sys
import os
import json
import re

# 确保项目根目录在 path 中
# diagnose.py 位于 scripts/，需向上 1 层到项目根
_here = os.path.abspath(__file__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, PROJECT_ROOT)

from src.pdf_parser import parse_multiple_pdfs, get_page_text_by_type, extract_pre_recording_fields_by_position
from src.field_extractor import extract_all_fields, extract_customs_header, extract_pre_recording_header
from src.comparator import run_comparison, STATUS_PASS, STATUS_FAIL, STATUS_FUZZY, STATUS_MANUAL


def get_contract_no_from_customs(parsed_pdf) -> str:
    """从报关单 PDF 中提取合同协议号"""
    for page in parsed_pdf.pages:
        if page.doc_type in ("customs_declaration",):
            header = extract_customs_header(page.text)
            if header.get("contract_no"):
                return header["contract_no"]
    for page in parsed_pdf.pages:
        header = extract_customs_header(page.text)
        if header.get("contract_no"):
            return header["contract_no"]
        header = extract_pre_recording_header(page.text)
        if header.get("contract_no"):
            return header["contract_no"]
    for page in parsed_pdf.pages:
        m = re.search(r'(?:^|\n|\s)(20\d{9,11})(?:\s|\n|$)', page.text)
        if m and _is_valid_contract_no(m.group(1)):
            return m.group(1)
    return ""


def _is_valid_contract_no(val: str) -> bool:
    if not val or not val.strip():
        return False
    return bool(re.match(r"^[A-Za-z0-9]+$", val.strip()))


def get_contract_no_from_pre(parsed_pdf) -> str:
    """从预录单 PDF 中提取合同协议号"""
    for page in parsed_pdf.pages:
        if page.doc_type == "pre_recording":
            header = extract_pre_recording_header(page.text)
            if header.get("contract_no") and _is_valid_contract_no(header["contract_no"]):
                return header["contract_no"]
    for page in parsed_pdf.pages:
        if page.doc_type == "pre_recording":
            header = extract_pre_recording_fields_by_position(page)
            if header.get("contract_no") and _is_valid_contract_no(header["contract_no"]):
                return header["contract_no"]
    for page in parsed_pdf.pages:
        header = extract_pre_recording_header(page.text)
        if header.get("contract_no") and _is_valid_contract_no(header["contract_no"]):
            return header["contract_no"]
    for page in parsed_pdf.pages:
        m = re.search(r'(?:^|\n|\s)(20\d{9,11})(?:\s|\n|$)', page.text)
        if m and _is_valid_contract_no(m.group(1)):
            return m.group(1)
    return ""


def collect_pages_from_pdfs(pdfs, customs=False, pre=False):
    """从 ParsedPDF 列表中收集并分类页面"""
    customs_pages, contract_pages, pre_pages, pre_continuation_pages = [], [], [], []
    for pdf in pdfs:
        for page in pdf.pages:
            if page.doc_type == "customs_declaration":
                customs_pages.append(page)
            elif page.doc_type == "contract":
                contract_pages.append(page)
            elif page.doc_type == "pre_recording":
                pre_pages.append(page)
            elif page.doc_type == "unknown":
                if "项号" in page.text and "商品编号" in page.text:
                    pre_continuation_pages.append(page)
    if customs:
        for pdf in pdfs:
            for page in pdf.pages:
                if page.doc_type == "pre_recording" and page not in customs_pages:
                    customs_pages.append(page)
        if not customs_pages:
            for pdf in pdfs:
                for page in pdf.pages:
                    if page.doc_type == "unknown":
                        customs_pages.append(page)
    if pre and not pre_pages:
        for pdf in pdfs:
            for page in pdf.pages:
                if page.doc_type == "customs_declaration":
                    pre_pages.append(page)
        if not pre_pages:
            for pdf in pdfs:
                for page in pdf.pages:
                    if page.doc_type == "unknown":
                        pre_pages.append(page)
    if pre and pre_pages:
        pre_page_ids = {id(p) for p in pre_pages}
        for pdf in pdfs:
            for page in pdf.pages:
                if page.doc_type == "customs_declaration" and id(page) not in pre_page_ids:
                    if re.search(r"\n\d{1,3}\s*\n\s*\d{8,10}", page.text):
                        pre_continuation_pages.append(page)
    return customs_pages, contract_pages, pre_pages, pre_continuation_pages


def diagnose(customs_path: str, pre_path: str) -> dict:
    """运行完整诊断，返回结构化报告"""
    # 读取文件
    with open(customs_path, "rb") as f:
        customs_data = [(os.path.basename(customs_path), f.read())]
    with open(pre_path, "rb") as f:
        pre_data = [(os.path.basename(pre_path), f.read())]

    # 解析 PDF
    customs_parsed = parse_multiple_pdfs(customs_data)
    pre_parsed = parse_multiple_pdfs(pre_data)

    # 文档信息
    doc_info = {
        "customs_file": os.path.basename(customs_path),
        "pre_file": os.path.basename(pre_path),
        "customs_pages": [],
        "pre_pages": [],
    }
    for parsed in customs_parsed:
        for page in parsed.pages:
            doc_info["customs_pages"].append({
                "page_index": page.page_index,
                "doc_type": page.doc_type,
                "text_preview": page.text[:200] if page.text else "",
            })
    for parsed in pre_parsed:
        for page in parsed.pages:
            doc_info["pre_pages"].append({
                "page_index": page.page_index,
                "doc_type": page.doc_type,
                "text_preview": page.text[:200] if page.text else "",
            })

    # 提取合同号
    customs_contract = get_contract_no_from_customs(customs_parsed[0])
    pre_contract = get_contract_no_from_pre(pre_parsed[0])

    # 收集页面：按内容自动判角色，不依赖 argv 顺序。
    # 用户可能把报关单/录入单传反（如 20260625 这批），位置参数不可信。
    # 合并两份 PDF 所有页面，customs_declaration→报关单侧，pre_recording→录入单侧，
    # 合同协议号相同即视为一对。详见 docs/memory.md。
    _all_pages = [p for _parsed in (customs_parsed + pre_parsed) for p in _parsed.pages]
    c_pages = [p for p in _all_pages if p.doc_type == "customs_declaration"]
    contract_pages = [p for p in _all_pages if p.doc_type == "contract"]
    p_pages = [p for p in _all_pages if p.doc_type == "pre_recording"]
    p_cont = [p for p in _all_pages if p.doc_type == "unknown"
              and "项号" in p.text and "商品编号" in p.text]

    # 提取字段 + 比对
    extracted = extract_all_fields(c_pages, p_pages, contract_pages, p_cont)
    result = run_comparison(extracted)

    # 构建报告
    report = {
        "doc_info": doc_info,
        "contract_no": {
            "customs": customs_contract,
            "pre": pre_contract,
            "matched": customs_contract == pre_contract and bool(customs_contract),
        },
        "summary": result.get("summary", {}),
        "header_results": [],
        "item_results": [],
    }

    # 表头比对结果
    for item in result.get("header_results", []):
        entry = {
            "field_id": item.get("field_id", ""),
            "field_name": item.get("field_name", ""),
            "customs_value": item.get("customs_value", ""),
            "pre_value": item.get("pre_value", ""),
            "status": item.get("status", ""),
        }
        report["header_results"].append(entry)

    # 商品明细比对结果
    for item in result.get("item_results", []):
        entry = {
            "item_no": item.get("customs_item_no", item.get("pre_item_no", "")),
            "fields": [],
        }
        for field in item.get("fields", []):
            entry["fields"].append({
                "field_id": field.get("field_id", ""),
                "field_name": field.get("field_name", ""),
                "customs_value": field.get("customs_value", ""),
                "pre_value": field.get("pre_value", ""),
                "status": field.get("status", ""),
            })
        report["item_results"].append(entry)

    return report


def format_report(report: dict) -> str:
    """格式化为可读文本"""
    lines = []
    di = report["doc_info"]

    lines.append("=" * 60)
    lines.append("报关单 vs 预录单 诊断报告")
    lines.append("=" * 60)

    # 文档信息
    lines.append(f"\n📄 报关单: {di['customs_file']}")
    for p in di["customs_pages"]:
        lines.append(f"   Page {p['page_index']}: {p['doc_type']}")
    lines.append(f"📄 预录单: {di['pre_file']}")
    for p in di["pre_pages"]:
        lines.append(f"   Page {p['page_index']}: {p['doc_type']}")

    # 合同号
    cn = report["contract_no"]
    lines.append(f"\n🔖 合同协议号: 报关单={cn['customs']} 预录单={cn['pre']} 匹配={'✅' if cn['matched'] else '❌'}")

    # 汇总
    s = report["summary"]
    lines.append(f"\n{'=' * 60}")
    lines.append(f"比对汇总: ✅ {s.get('pass_count',0)} 通过 | ❌ {s.get('fail_count',0)} 不通过 | ⚠️ {s.get('fuzzy_count',0)} 模糊 | 🔍 {s.get('manual_count',0)} 人工")
    lines.append(f"{'=' * 60}")

    # 表头异常
    header_issues = [h for h in report["header_results"] if h["status"] != STATUS_PASS]
    if header_issues:
        lines.append("\n📋 表头异常字段:")
        for h in header_issues:
            icon = {"fail": "❌", "fuzzy": "⚠️", "manual": "🔍"}.get(h["status"], "➖")
            lines.append(f"  {icon} {h['field_name']} ({h['field_id']})")
            lines.append(f"     报关单: {h['customs_value']}")
            lines.append(f"     预录单: {h['pre_value']}")

    # 商品明细异常
    item_issues = []
    for item in report["item_results"]:
        bad_fields = [f for f in item["fields"] if f["status"] != STATUS_PASS]
        if bad_fields:
            item_issues.append((item["item_no"], bad_fields))
    if item_issues:
        lines.append(f"\n📦 商品明细异常 ({len(item_issues)} 条):")
        for item_no, fields in item_issues:
            lines.append(f"  项号 {item_no}:")
            for f in fields:
                icon = {"fail": "❌", "fuzzy": "⚠️", "manual": "🔍"}.get(f["status"], "➖")
                lines.append(f"    {icon} {f['field_name']}: 报关单={f['customs_value']} 预录单={f['pre_value']}")

    if not header_issues and not item_issues:
        lines.append("\n✅ 所有字段比对通过，未发现异常！")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python diagnose.py <报关单PDF> <预录单PDF> [--json]")
        print("  --json  输出 JSON 格式（默认输出可读文本）")
        sys.exit(0)

    customs_path = args[0]
    pre_path = args[1]
    output_json = "--json" in args

    if not os.path.isfile(customs_path):
        print(f"错误: 文件不存在: {customs_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(pre_path):
        print(f"错误: 文件不存在: {pre_path}", file=sys.stderr)
        sys.exit(1)

    print(f"正在诊断...", file=sys.stderr)
    try:
        report = diagnose(customs_path, pre_path)
        if output_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_report(report))
    except Exception as e:
        print(f"诊断失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
