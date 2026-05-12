"""
报关单 vs 预录单 智能比对工具
Streamlit 主入口
"""
import streamlit as st
import tempfile
import os
from src.pdf_parser import parse_multiple_pdfs, get_page_text_by_type, extract_pre_recording_fields_by_position
from src.field_extractor import extract_all_fields, extract_customs_header, extract_pre_recording_header
from src.comparator import run_comparison, STATUS_PASS, STATUS_FAIL, STATUS_FUZZY, STATUS_MANUAL
from src.excel_exporter import export_to_excel, export_multiple_to_excel


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="报关单 vs 预录单 智能比对",
    page_icon="📋",
    layout="wide",
)

st.title("📋 报关单 vs 预录单 智能比对")

# ============================================================
# 自定义 CSS
# ============================================================
st.markdown("""
<style>
/* 上传区容器 */
.upload-zone {
    border: 2px dashed #cbd5e1;
    border-radius: 12px;
    padding: 8px 16px 16px 16px;
    background: #f8fafc;
    min-height: 120px;
}
.upload-zone-customs {
    border-color: #fca5a5;
    background: #fef2f2;
}
.upload-zone-pre {
    border-color: #93c5fd;
    background: #eff6ff;
}
/* 文件标签 */
.file-tag {
    display: inline-block;
    background: #e2e8f0;
    color: #334155;
    border-radius: 6px;
    padding: 2px 10px;
    margin: 2px 4px 2px 0;
    font-size: 13px;
}
.file-tag-customs {
    background: #fecaca;
    color: #991b1b;
}
.file-tag-pre {
    background: #bfdbfe;
    color: #1e40af;
}
/* 问题项卡片 */
.issue-card {
    border-left: 4px solid;
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 0 8px 8px 0;
    background: #ffffff;
}
.issue-fail { border-color: #ef4444; background: #fef2f2; }
.issue-fuzzy { border-color: #f59e0b; background: #fffbeb; }
.issue-manual { border-color: #3b82f6; background: #eff6ff; }
/* 人工确认行 */
.manual-row {
    display: flex;
    align-items: center;
    padding: 6px 12px;
    border-bottom: 1px solid #e2e8f0;
}
.manual-row:last-child { border-bottom: none; }
/* 概览卡片 */
.summary-card {
    border-radius: 10px;
    padding: 12px 16px;
    text-align: center;
}
.summary-pass { background: #dcfce7; color: #166534; }
.summary-fail { background: #fee2e2; color: #991b1b; }
.summary-fuzzy { background: #fef3c7; color: #92400e; }
.summary-manual { background: #dbeafe; color: #1e40af; }
/* 配对结果表 */
.pairing-table {
    width: 100%;
    border-collapse: collapse;
}
.pairing-table th {
    background: #f1f5f9;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 2px solid #e2e8f0;
}
.pairing-table td {
    padding: 6px 12px;
    border-bottom: 1px solid #f1f5f9;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# 辅助函数
# ============================================================
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
    return ""


def get_contract_no_from_pre(parsed_pdf) -> str:
    """从预录单 PDF 中提取合同协议号"""
    for page in parsed_pdf.pages:
        if page.doc_type == "pre_recording":
            header = extract_pre_recording_fields_by_position(page)
            if header.get("contract_no"):
                return header["contract_no"]
            header = extract_pre_recording_header(page.text)
            if header.get("contract_no"):
                return header["contract_no"]
    for page in parsed_pdf.pages:
        header = extract_pre_recording_fields_by_position(page)
        if header.get("contract_no"):
            return header["contract_no"]
        header = extract_pre_recording_header(page.text)
        if header.get("contract_no"):
            return header["contract_no"]
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
    if customs and not customs_pages:
        for pdf in pdfs:
            for page in pdf.pages:
                if page.doc_type == "unknown":
                    customs_pages.append(page)
    if pre and not pre_pages:
        for pdf in pdfs:
            for page in pdf.pages:
                if page.doc_type == "unknown":
                    pre_pages.append(page)
    return customs_pages, contract_pages, pre_pages, pre_continuation_pages


def render_status_icon(status: str) -> str:
    return {STATUS_PASS: "✅", STATUS_FAIL: "❌", STATUS_FUZZY: "⚠️", STATUS_MANUAL: "🔍"}.get(status, "➖")


def render_issue_detail(customs_val, pre_val, notes):
    """渲染单个问题项的详情对比"""
    col_c, col_arrow, col_p = st.columns([5, 1, 5])
    with col_c:
        st.markdown(f'<div style="background:#fef2f2;border-radius:8px;padding:8px 12px;font-size:13px;">'
                    f'<div style="color:#991b1b;font-weight:600;margin-bottom:2px;">报关单</div>'
                    f'{customs_val or "(空)"}</div>', unsafe_allow_html=True)
    with col_arrow:
        st.markdown("<div style='text-align:center;padding-top:24px;font-size:18px;color:#94a3b8;'>≠</div>",
                    unsafe_allow_html=True)
    with col_p:
        st.markdown(f'<div style="background:#eff6ff;border-radius:8px;padding:8px 12px;font-size:13px;">'
                    f'<div style="color:#1e40af;font-weight:600;margin-bottom:2px;">预录单</div>'
                    f'{pre_val or "(空)"}</div>', unsafe_allow_html=True)
    if notes:
        st.markdown(f'<div style="color:#64748b;font-size:12px;padding:4px 12px;">💡 {notes}</div>',
                    unsafe_allow_html=True)


def render_comparison_result(result, idx):
    """渲染单组比对结果"""
    summary = result["summary"]
    contract_no = result.get("contract_no", "")

    # ---- 概览卡片 ----
    cols = st.columns(4)
    metrics = [
        ("✅ 通过", summary["pass_count"], "summary-pass"),
        ("❌ 不通过", summary["fail_count"], "summary-fail"),
        ("⚠️ 模糊", summary["fuzzy_count"], "summary-fuzzy"),
        ("🔍 待确认", summary["manual_count"], "summary-manual"),
    ]
    for col, (label, count, css_class) in zip(cols, metrics):
        with col:
            st.markdown(f'<div class="summary-card {css_class}">'
                        f'<div style="font-size:24px;font-weight:700;">{count}</div>'
                        f'<div style="font-size:12px;">{label}</div></div>', unsafe_allow_html=True)

    # 文件来源 + 导出
    c_files = result.get("customs_filenames", [])
    p_files = result.get("pre_filenames", [])
    file_col, export_col = st.columns([4, 1])
    with file_col:
        parts = []
        if c_files:
            parts.append("报关单: " + ", ".join(c_files))
        if p_files:
            parts.append("预录单: " + ", ".join(p_files))
        st.caption("  |  ".join(parts))
    with export_col:
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp_name = tmp.name
        tmp.close()
        try:
            export_to_excel(result, tmp_name)
            with open(tmp_name, "rb") as f:
                excel_data = f.read()
        finally:
            os.unlink(tmp_name)
        st.download_button(
            "📥 导出Excel",
            data=excel_data,
            file_name=f"比对报告_{contract_no}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download_{idx}",
            use_container_width=True,
        )

    # ---- 收集问题项 ----
    fail_items = []  # (location, item_no, label, customs_val, pre_val, notes)
    fuzzy_items = []
    manual_items = []

    for r in result["header_results"]:
        if r["status"] == STATUS_FAIL:
            fail_items.append(("表头", "-", r["label"], r["customs_value"], r["pre_value"], r.get("notes", "")))
        elif r["status"] == STATUS_FUZZY:
            fuzzy_items.append(("表头", "-", r["label"], r["customs_value"], r["pre_value"], r.get("notes", "")))
        elif r["status"] == STATUS_MANUAL:
            manual_items.append(("表头", "-", r["label"], r["customs_value"], r["pre_value"]))

    for item_result in result["item_results"]:
        for f in item_result["fields"]:
            loc = f"项号 {item_result['item_no']}"
            if f["status"] == STATUS_FAIL:
                fail_items.append((loc, item_result["item_no"], f["label"], f["customs_value"], f["pre_value"], f.get("notes", "")))
            elif f["status"] == STATUS_FUZZY:
                fuzzy_items.append((loc, item_result["item_no"], f["label"], f["customs_value"], f["pre_value"], f.get("notes", "")))

    # ---- 不通过项 ----
    if fail_items:
        st.markdown(f'<div style="font-weight:600;font-size:15px;color:#dc2626;margin-top:12px;">'
                    f'❌ 不通过项 ({len(fail_items)})</div>', unsafe_allow_html=True)
        for loc, item_no, label, c_val, p_val, notes in fail_items:
            header = f"**{loc}** / {label}" if loc != "-" else f"**{label}**"
            with st.expander(header):
                render_issue_detail(c_val, p_val, notes)

    # ---- 模糊匹配项 ----
    if fuzzy_items:
        st.markdown(f'<div style="font-weight:600;font-size:15px;color:#d97706;margin-top:12px;">'
                    f'⚠️ 模糊匹配项 ({len(fuzzy_items)})</div>', unsafe_allow_html=True)
        for loc, item_no, label, c_val, p_val, notes in fuzzy_items:
            header = f"**{loc}** / {label}" if loc != "-" else f"**{label}**"
            with st.expander(header):
                render_issue_detail(c_val, p_val, notes)

    # ---- 人工确认项 ----
    if manual_items:
        st.markdown(f'<div style="font-weight:600;font-size:15px;color:#2563eb;margin-top:12px;">'
                    f'🔍 人工确认项 ({len(manual_items)})</div>', unsafe_allow_html=True)
        st.markdown('<div style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">', unsafe_allow_html=True)
        for loc, item_no, label, c_val, p_val in manual_items:
            st.markdown(
                f'<div style="display:flex;align-items:center;padding:8px 12px;border-bottom:1px solid #f1f5f9;">'
                f'<div style="flex:0 0 140px;font-weight:600;font-size:13px;">{label}</div>'
                f'<div style="flex:1;font-size:13px;color:#991b1b;">{c_val or "(空)"}</div>'
                f'<div style="flex:0 0 24px;text-align:center;color:#94a3b8;">vs</div>'
                f'<div style="flex:1;font-size:13px;color:#1e40af;">{p_val or "(空)"}</div>'
                f'</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # 全部通过的情况
    if not fail_items and not fuzzy_items and not manual_items:
        st.markdown(
            '<div style="background:#dcfce7;border-radius:10px;padding:20px;text-align:center;color:#166534;font-size:16px;">'
            '🎉 全部比对通过，无不一致项！</div>', unsafe_allow_html=True)

    # ---- 通过项明细 ----
    pass_header = [r for r in result["header_results"] if r["status"] == STATUS_PASS]
    pass_items = []
    for item_result in result["item_results"]:
        for f in item_result["fields"]:
            if f["status"] == STATUS_PASS:
                pass_items.append((item_result["item_no"], f))

    if pass_header or pass_items:
        st.markdown(f'<div style="font-weight:600;font-size:15px;color:#16a34a;margin-top:16px;">'
                    f'✅ 通过项明细 ({len(pass_header) + len(pass_items)})</div>', unsafe_allow_html=True)

        if pass_header:
            header_data = []
            for r in pass_header:
                note = r.get("notes", "")
                if r.get("check_type") == "fixed" and not note:
                    note = f"预录单须为固定值: {r.get('fixed_value', '')}"
                header_data.append({
                    "位置": "表头",
                    "字段名称": r["label"],
                    "报关单值": r["customs_value"] or "(空)",
                    "预录单值": r["pre_value"] or "(空)",
                    "备注": note,
                })
            st.dataframe(
                header_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "报关单值": st.column_config.TextColumn(width="medium"),
                    "预录单值": st.column_config.TextColumn(width="medium"),
                    "备注": st.column_config.TextColumn(width="large"),
                },
            )

        if pass_items:
            item_data = []
            for item_no, f in pass_items:
                note = f.get("notes", "")
                if f.get("check_type") == "fixed" and not note:
                    note = f"预录单须为固定值: {f.get('fixed_value', '')}"
                item_data.append({
                    "位置": f"项号 {item_no}",
                    "字段名称": f["label"],
                    "报关单值": f["customs_value"] or "(空)",
                    "预录单值": f["pre_value"] or "(空)",
                    "备注": note,
                })
            st.dataframe(
                item_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "报关单值": st.column_config.TextColumn(width="medium"),
                    "预录单值": st.column_config.TextColumn(width="medium"),
                    "备注": st.column_config.TextColumn(width="large"),
                },
            )


# ============================================================
# 上传区
# ============================================================
tab_compare, tab_help = st.tabs(["📤 上传比对", "📖 使用说明"])

with tab_compare:
    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="upload-zone upload-zone-customs">', unsafe_allow_html=True)
        st.markdown("**🔴 报关单**  有红色盖章 · 可含合同/装箱单等")
        customs_files = st.file_uploader(
            "选择报关单 PDF",
            type=["pdf"],
            accept_multiple_files=True,
            key="customs_upload",
            label_visibility="collapsed",
        )
        if customs_files:
            st.markdown(f'<div style="font-size:12px;color:#64748b;margin-bottom:4px;">已选择 {len(customs_files)} 个文件</div>', unsafe_allow_html=True)
            for f in customs_files:
                st.markdown(f'<span class="file-tag file-tag-customs">📄 {f.name}</span>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="upload-zone upload-zone-pre">', unsafe_allow_html=True)
        st.markdown("**🔵 预录单**  无红色盖章 · 支持多文件上传")
        pre_files = st.file_uploader(
            "选择预录单 PDF",
            type=["pdf"],
            accept_multiple_files=True,
            key="pre_upload",
            label_visibility="collapsed",
        )
        if pre_files:
            st.markdown(f'<div style="font-size:12px;color:#64748b;margin-bottom:4px;">已选择 {len(pre_files)} 个文件</div>', unsafe_allow_html=True)
            for f in pre_files:
                st.markdown(f'<span class="file-tag file-tag-pre">📄 {f.name}</span>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # 比对按钮
    can_compare = bool(customs_files) and bool(pre_files)
    btn_label = "🚀 开始比对"
    if customs_files and pre_files:
        btn_label = f"🚀 开始比对 — {len(customs_files)} 份报关单 + {len(pre_files)} 份预录单"

    if st.button(btn_label, disabled=not can_compare, use_container_width=True, type="primary"):
        with st.spinner("正在解析和比对..."):
            progress = st.progress(0, text="正在解析 PDF...")

            customs_data = [(f.name, f.read()) for f in customs_files]
            pre_data = [(f.name, f.read()) for f in pre_files]

            customs_parsed = parse_multiple_pdfs(customs_data)
            pre_parsed = parse_multiple_pdfs(pre_data)

            progress.progress(30, text="正在识别文档并提取合同协议号...")

            # 按 合同协议号 分组
            customs_by_contract = {}
            customs_no_contract = []
            for parsed in customs_parsed:
                contract_no = get_contract_no_from_customs(parsed)
                if contract_no:
                    customs_by_contract.setdefault(contract_no, []).append(parsed)
                else:
                    customs_no_contract.append(parsed)

            pre_by_contract = {}
            pre_no_contract = []
            for parsed in pre_parsed:
                contract_no = get_contract_no_from_pre(parsed)
                if contract_no:
                    pre_by_contract.setdefault(contract_no, []).append(parsed)
                else:
                    pre_no_contract.append(parsed)

            progress.progress(50, text="正在配对和提取字段...")

            # 配对
            all_contract_nos = sorted(
                set(list(customs_by_contract.keys()) + list(pre_by_contract.keys()))
            )
            pairs = []
            for contract_no in all_contract_nos:
                c_pdfs = customs_by_contract.get(contract_no, [])
                p_pdfs = pre_by_contract.get(contract_no, [])
                c_pages, contract_pages, _, _ = collect_pages_from_pdfs(c_pdfs, customs=True)
                _, _, p_pages, p_cont = collect_pages_from_pdfs(p_pdfs, pre=True)
                pairs.append({
                    "contract_no": contract_no,
                    "customs_pages": c_pages,
                    "contract_pages": contract_pages,
                    "pre_pages": p_pages,
                    "pre_continuation_pages": p_cont,
                    "customs_filenames": [pdf.filename for pdf in c_pdfs],
                    "pre_filenames": [pdf.filename for pdf in p_pdfs],
                })

            # 逐组比对
            results = []
            for i, pair in enumerate(pairs):
                extracted = extract_all_fields(
                    pair["customs_pages"], pair["pre_pages"],
                    pair["contract_pages"], pair["pre_continuation_pages"],
                )
                result = run_comparison(extracted)
                result["contract_no"] = pair["contract_no"]
                result["customs_filenames"] = pair["customs_filenames"]
                result["pre_filenames"] = pair["pre_filenames"]
                results.append(result)
                progress.progress(
                    50 + int(45 * (i + 1) / len(pairs)),
                    text=f"正在比对 {i + 1}/{len(pairs)} — 合同协议号: {pair['contract_no']}...",
                )

            progress.progress(100, text="比对完成！")

            st.session_state["comparison_results"] = results
            st.session_state["pairing_info"] = {
                "customs_by_contract": {k: [p.filename for p in v] for k, v in customs_by_contract.items()},
                "pre_by_contract": {k: [p.filename for p in v] for k, v in pre_by_contract.items()},
                "unmatched_customs": [p.filename for p in customs_no_contract],
                "unmatched_pre": [p.filename for p in pre_no_contract],
            }

    # ============================================================
    # 结果展示
    # ============================================================
    if "comparison_results" in st.session_state:
        results = st.session_state["comparison_results"]
        pairing_info = st.session_state.get("pairing_info", {})

        st.markdown("---")

        # ---- 配对结果 + 全部导出 ----
        header_col, export_all_col = st.columns([5, 1])
        with header_col:
            st.subheader("📋 配对结果")
        with export_all_col:
            if len(results) > 1:
                tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
                tmp_name = tmp.name
                tmp.close()
                try:
                    export_multiple_to_excel(results, tmp_name)
                    with open(tmp_name, "rb") as f:
                        all_excel_data = f.read()
                finally:
                    os.unlink(tmp_name)
                st.download_button(
                    "📥 全部导出",
                    data=all_excel_data,
                    file_name="比对报告_全部.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_all",
                    use_container_width=True,
                )

        # 配对表格
        c_by_c = pairing_info.get("customs_by_contract", {})
        p_by_c = pairing_info.get("pre_by_contract", {})
        all_nos = sorted(set(list(c_by_c.keys()) + list(p_by_c.keys())))

        pairing_rows = []
        for contract_no in all_nos:
            c_names = ", ".join(c_by_c.get(contract_no, [])) or "—"
            p_names = ", ".join(p_by_c.get(contract_no, [])) or "—"
            # 从 results 中获取该组的状态摘要
            r = next((r for r in results if r.get("contract_no") == contract_no), None)
            fail = r["summary"]["fail_count"] if r else 0
            fuzzy = r["summary"]["fuzzy_count"] if r else 0
            manual = r["summary"]["manual_count"] if r else 0
            status_parts = []
            if fail:
                status_parts.append(f"❌ {fail}")
            if fuzzy:
                status_parts.append(f"⚠️ {fuzzy}")
            if manual:
                status_parts.append(f"🔍 {manual}")
            status = "  ".join(status_parts) if status_parts else "✅"
            pairing_rows.append({
                "合同协议号": contract_no,
                "报关单": c_names,
                "预录单": p_names,
                "状态": status,
            })
        st.dataframe(pairing_rows, use_container_width=True, hide_index=True)

        # 未匹配警告
        unmatched_c = pairing_info.get("unmatched_customs", [])
        unmatched_p = pairing_info.get("unmatched_pre", [])
        if unmatched_c:
            st.warning(f"⚠️ 以下报关单未识别到合同协议号: {', '.join(unmatched_c)}")
        if unmatched_p:
            st.warning(f"⚠️ 以下预录单未识别到合同协议号: {', '.join(unmatched_p)}")

        st.markdown("---")

        # ---- 逐组比对详情 ----
        for idx, result in enumerate(results):
            summary = result["summary"]
            contract_no = result.get("contract_no", "")

            # expander 标题：合同协议号 + 状态摘要
            status_parts = []
            if summary["fail_count"] > 0:
                status_parts.append(f"❌ {summary['fail_count']}")
            if summary["fuzzy_count"] > 0:
                status_parts.append(f"⚠️ {summary['fuzzy_count']}")
            if summary["manual_count"] > 0:
                status_parts.append(f"🔍 {summary['manual_count']}")
            status_text = "  ".join(status_parts) if status_parts else "✅ 全部通过"
            is_clean = summary["fail_count"] == 0 and summary["fuzzy_count"] == 0

            with st.expander(f"**{contract_no}** — {status_text}", expanded=not is_clean or idx == 0):
                render_comparison_result(result, idx)

# ============================================================
# 使用说明
# ============================================================
with tab_help:
    st.markdown("""
    ## 使用说明

    ### 1. 上传文件
    - **🔴 报关单区域**：上传含红色盖章的报关单 PDF（可包含报关单+合同+装箱单+发票等多页），支持同时上传多份
    - **🔵 预录单区域**：上传无红色盖章的预录单 PDF，支持同时上传多份
    - 系统会自动从每个文件中提取**合同协议号**，按合同协议号自动配对报关单和预录单

    ### 2. 自动配对规则
    1. 解析每个 PDF，识别页面类型（报关单/预录单/合同等）
    2. 从每个文件的主页面提取合同协议号
    3. 按合同协议号将报关单和预录单配对
    4. 逐组执行比对，每组独立展示结果

    ### 3. 文档识别规则
    | 特征 | 报关单 | 预录单 |
    |------|-------|-------|
    | 红色盖章 | 有 | 无 |
    | 出口口岸字段 | 有，值为 `-` | 无此字段 |
    | 出境关别字段 | 无此字段 | 有，值如"大鹏海关" |

    ### 4. 比对规则
    - **✅ 匹配**：报关单和预录单的值完全一致
    - **✅ 固定值**：预录单的值符合固定要求
    - **⚠️ 模糊匹配**：规格型号、境内货源地等按关键字匹配
    - **🔍 人工确认**：运输方式、出境关别等需人工判断的字段

    ### 5. 数量及单位行交换
    - 报关单第1行(件数) ↔ 预录单第3行(件数)
    - 报关单第2行(千克) ↔ 预录单第1行(千克)

    ### 6. 规格型号特殊映射
    - 报关单 `境内自主品牌` → 预录单 `1`
    - 报关单 `不确定是否享惠` → 预录单 `2`
    """)
