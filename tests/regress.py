#!/usr/bin/env python3
"""
回归测试基线 —— 跑全部 fixture，对比 golden，报告提取结果的变化。

用法:
  python tests/regress.py                  # 对比模式，报告 diff（有意外变化退出码 1）
  python tests/regress.py --update-golden  # 重建 golden（首次 / 主动接受新基线）

为什么需要它：
  PDF 提取靠"坐标/内容启发式"，每修一个格式可能破坏另一个（memory.md #19→#23 就是
  "修 A 坏 B"）。本脚本把历史样本的提取结果固化为 golden，改代码后跑一次，立刻看到
  哪些样本结果变了 —— 修复目标的变更是预期的，其他变更是回归警报。

两类样本:
  - pair:  报关单 + 预录单配对，跑 diagnose()，golden 存比对报告快照（含双方提取值）
  - single: 单 PDF，跑 parse + 提取，golden 存 doc_types + items

加新样本: 把 PDF 放到 tests/fixtures/，在下方 PAIRS / SINGLES 加一条，跑 --update-golden。
"""
import sys
import os
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from diagnose import diagnose  # noqa: E402
from src.pdf_parser import parse_pdf, extract_pre_recording_items_by_position  # noqa: E402
from src.field_extractor import extract_customs_items  # noqa: E402

GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden"

# ------------------------------------------------------------------
# 样本登记表 —— 加新样本在这里加一条
# ------------------------------------------------------------------
PAIRS = [
    {
        "id": "pair_20260717011",
        "customs_pdf": "tests/fixtures/pair_20260717011_customs.pdf",
        "pre_pdf": "tests/fixtures/pair_20260717011_pre.pdf",
        "formats": ["横向倒排核对单", "合并报关资料综合包"],
        "notes": "#23 数量字段项号x区间归位的主验证样本（J18632B-125箱）",
    },
    {
        "id": "pair_20260710002",
        "customs_pdf": "tests/fixtures/pair_20260710002_customs.pdf",
        "pre_pdf": "tests/fixtures/pair_20260710002_pre.pdf",
        "formats": ["横向倒排核对单", "合并报关资料综合包"],
        "notes": "横向格式回归样本（J18632B-241箱）",
    },
    {
        "id": "pair_20260612002",
        "customs_pdf": "tests/fixtures/pair_20260612002_customs.pdf",
        "pre_pdf": "tests/fixtures/pair_20260612002_pre.pdf",
        "formats": ["报关资料综合包", "标准预录单"],
        "notes": "20260612002 批次，4列网格/综合包格式",
    },
]

SINGLES = [
    {
        "id": "single_pre6",
        "pdf": "tests/fixtures/single_pre6.pdf",
        "formats": ["标准纵向预录单"],
        "notes": "标准格式回归，确认 horizontal dispatch 不误触发",
    },
    {
        "id": "single_20260529004",
        "pdf": "tests/fixtures/single_20260529004.pdf",
        "formats": ["报关资料综合包"],
        "notes": "速玛-AU 批次，格式多样性样本",
    },
]


def _abs(rel):
    return PROJECT_ROOT / rel


# ------------------------------------------------------------------
# 快照生成
# ------------------------------------------------------------------
def snapshot_pair(reg):
    report = diagnose(str(_abs(reg["customs_pdf"])), str(_abs(reg["pre_pdf"])))
    return {
        "contract_no": report["contract_no"],
        "summary": report["summary"],
        "header_fields": [
            {
                "field_id": h["field_id"],
                "customs_value": h["customs_value"],
                "pre_value": h["pre_value"],
                "status": h["status"],
            }
            for h in report["header_results"]
        ],
        "items": [
            {
                "item_no": it["item_no"],
                "fields": [
                    {
                        "field_id": f["field_id"],
                        "customs_value": f["customs_value"],
                        "pre_value": f["pre_value"],
                        "status": f["status"],
                    }
                    for f in it["fields"]
                ],
            }
            for it in report["item_results"]
        ],
    }


def snapshot_single(reg):
    with open(_abs(reg["pdf"]), "rb") as f:
        data = f.read()
    parsed = parse_pdf(data, os.path.basename(reg["pdf"]))
    doc_types = [p.doc_type for p in parsed.pages]
    items = []
    for p in parsed.pages:
        if p.doc_type == "pre_recording":
            items.extend(extract_pre_recording_items_by_position(p))
        elif p.doc_type == "customs_declaration":
            items.extend(extract_customs_items(p.text))
    return {"doc_types": doc_types, "items": items}


# ------------------------------------------------------------------
# diff
# ------------------------------------------------------------------
def _truncate(v, n=80):
    s = str(v)
    return s if len(s) <= n else s[:n] + "..."


def diff_value(path, old, new, out):
    """递归对比两个值，差异行写入 out 列表"""
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            diff_value(f"{path}.{k}", old.get(k, "<无>"), new.get(k, "<无>"), out)
    elif isinstance(old, list) and isinstance(new, list):
        m = max(len(old), len(new))
        for i in range(m):
            if i >= len(new):
                out.append(f"  {path}[{i}] 删除: {_truncate(old[i])}")
            elif i >= len(old):
                out.append(f"  {path}[{i}] 新增: {_truncate(new[i])}")
            else:
                diff_value(f"{path}[{i}]", old[i], new[i], out)
    else:
        out.append(f"  {path}: {_truncate(old)} → {_truncate(new)}")


def run():
    ap = argparse.ArgumentParser(description="报关单/预录单提取回归测试")
    ap.add_argument("--update-golden", action="store_true", help="重建 golden")
    args = ap.parse_args()

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    total = len(PAIRS) + len(SINGLES)
    print(f"回归测试: {len(PAIRS)} 配对 + {len(SINGLES)} 单文件 = {total} 样本")
    print("=" * 64)

    passed, failed, written = 0, 0, 0

    for reg in PAIRS:
        rid = reg["id"]
        snap = snapshot_pair(reg)
        gpath = GOLDEN_DIR / f"{rid}.json"
        s = snap["summary"]
        if args.update_golden or not gpath.exists():
            gpath.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            written += 1
            tag = "重建" if args.update_golden else "首次生成"
            print(f"  ✓ {rid}: golden {tag} (pass={s.get('pass_count')}, fail={s.get('fail_count')})")
            continue
        golden = json.loads(gpath.read_text(encoding="utf-8"))
        diffs = []
        diff_value(rid, golden, snap, diffs)
        if diffs:
            failed += 1
            print(f"  ✗ {rid}: {len(diffs)} 处变化")
            for d in diffs[:30]:
                print(d)
            if len(diffs) > 30:
                print(f"  ... 还有 {len(diffs) - 30} 处")
        else:
            passed += 1
            print(f"  ✓ {rid}: 无变化 (pass={s.get('pass_count')}, fail={s.get('fail_count')})")

    for reg in SINGLES:
        rid = reg["id"]
        snap = snapshot_single(reg)
        gpath = GOLDEN_DIR / f"{rid}.json"
        if args.update_golden or not gpath.exists():
            gpath.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            written += 1
            tag = "重建" if args.update_golden else "首次生成"
            print(f"  ✓ {rid}: golden {tag} (doc_types={snap['doc_types']}, items={len(snap['items'])})")
            continue
        golden = json.loads(gpath.read_text(encoding="utf-8"))
        diffs = []
        diff_value(rid, golden, snap, diffs)
        if diffs:
            failed += 1
            print(f"  ✗ {rid}: {len(diffs)} 处变化")
            for d in diffs[:30]:
                print(d)
            if len(diffs) > 30:
                print(f"  ... 还有 {len(diffs) - 30} 处")
        else:
            passed += 1
            print(f"  ✓ {rid}: 无变化 (items={len(snap['items'])})")

    print("=" * 64)
    if args.update_golden:
        print(f"完成: {written} 个 golden 已写入 tests/golden/")
    else:
        print(f"结果: {passed} 无变化 / {failed} 有变化 / {written} 新建")
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
