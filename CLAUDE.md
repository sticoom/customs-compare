---
version: "2.0"
last_verified: "2026-06-16"
tags: [pdf, customs, declaration, reconciliation, streamlit]
dependencies:
  - python >= 3.10
  - streamlit >= 1.30
  - pymupdf (fitz) >= 1.23
  - openpyxl >= 3.1
github: https://github.com/sticoom/customs-compare
---

# 报关单 vs 预录单 智能比对工具

> **TL;DR** — 用户上传报关单 + 预录单 PDF，系统按合同协议号自动配对，逐字段校验并生成比对报告。核心难点：预录单「仅供核对用」格式文本乱序，必须用 span 坐标提取。

## 文档导航

本 CLAUDE.md 放**业务逻辑 + 强制规则**。历史踩坑单独维护：

| 想知道什么 | 读哪个文件 |
|----------|-----------|
| 以前出过什么事、根因、修复方法、速查表 | [docs/memory.md](docs/memory.md) |
| 独立诊断脚本（出问题时第一选择） | [scripts/diagnose.py](scripts/diagnose.py) |

排障流程：**先查 `docs/memory.md` 速查表 → 命中后看详细记录 → 仍未解决时跑 `scripts/diagnose.py`**

## 强制规则

### MUST DO

- ✅ 改完字段提取/比对逻辑后，**必须**跑 `python scripts/diagnose.py <报关单PDF> <预录单PDF>` 验证至少 3 份样本
- ✅ 改完必须用文本/ASCII 展示 before/after 给用户看（不能只说"改好了"）
- ✅ 发现新坑必须追加到 `docs/memory.md` 末尾，**不删除已有记录**——编号连续递增，同时在速查表加一行

### MUST NOT

- ❌ 禁止依赖 AI 视觉模型兜底（DeepSeek VL2 API 不兼容，详见 memory.md 架构性项 C）
- ❌ 禁止用纯文本正则提取「仅供核对用」预录单表头，必须用 span x/y 坐标（详见 memory.md 架构性项 A）
- ❌ 禁止合同协议号正则匹配 FBA 提运单号格式（详见 memory.md 架构性项 B）

## 项目概述

自动对比报关单和预录单 PDF，按合同协议号自动配对，逐字段校验并生成比对报告。

## 技术栈

- **Streamlit** — Web 界面（app.py）
- **PyMuPDF (fitz)** — PDF 文本和 span 坐标提取
- **openpyxl** — Excel 报告生成
- **纯 Python** — 所有提取和比对逻辑，无 AI 依赖

## 核心数据流

```
用户上传 PDF（报关单 + 预录单）
  ↓
pdf_parser.py → parse_pdf() 解析每页文本，identify_doc_type() 识别文档类型
  ↓ post-processing：primary_type 传播，续页归类
app.py → 按 get_contract_no_from_customs/pre() 提取合同协议号分组
  ↓ collect_pages_from_pdfs() 按类型收集页面
field_extractor.py → extract_all_fields() 提取表头 + 商品明细
  ↓ 位置感知提取 → 文本正则兜底 → 缺失字段补充
comparator.py → run_comparison() 逐字段比对
  ↓
app.py → 展示结果 + excel_exporter.py 导出报告
```

## 关键模块

### pdf_parser.py — PDF 解析与页面分类

- `parse_pdf(data, filename)` → 解析 PDF，返回 ParsedPDF（含 PageInfo 列表）
- `identify_doc_type(text)` → 根据关键词识别页面类型
- `extract_spans_with_positions(page)` → 提取带 x/y 坐标的文本 span
- `extract_pre_recording_fields_by_position(page)` → 用 span 坐标提取预录单表头
- `extract_pre_recording_items_by_position(page)` → 用 span 坐标提取预录单商品明细
- `extract_horizontal_lines(page)` → 提取 PDF 水平线（用于行槽位划分）

### field_extractor.py — 字段提取

- `extract_customs_header(text)` → 报关单表头（正则提取，排版简单）
- `extract_customs_items(text)` → 报关单商品明细（正则提取）
- `extract_pre_recording_header(text)` → 预录单表头（文本正则，作为位置提取的补充）
- `_hedui_text_fallback(fields, text)` → "仅供核对用"格式的文本兜底
- `_extract_items_from_hedui(text)` → 核对单格式商品提取（含"仅供核对"标记的页面）
- `_extract_items_from_continuation(text)` → 续页商品提取（通用文本解析）
- `extract_all_fields(customs_pages, pre_pages, contract_pages)` → 主提取入口

### comparator.py — 比对引擎

- `compare_exact(val1, val2)` → 精确匹配（规范化后比较）
- `compare_fixed(val, fixed_value)` → 固定值校验（关键词匹配，代码部分可选）
- `compare_fuzzy_spec(spec1, spec2)` → 规格型号模糊匹配（按 | 分段，映射转换后集合比较）
- `compare_headers(customs_header, pre_header)` → 表头比对
- `compare_items(customs_items, pre_items)` → 商品明细比对
- `run_comparison(extracted)` → 完整比对入口

### config.py — 配置中心

- `AI_CONFIG` — AI 模型配置（当前用 DeepSeek，但实际已不使用 AI）
- `DOC_TYPE_KEYWORDS` — 文档类型识别关键词
- `CUSTOMS_HEADER_FIELDS` — 表头字段规则（18个字段）
- `CUSTOMS_ITEM_FIELDS` — 商品明细字段规则（10个字段）
- `DOMESTIC_SOURCE_MAPPING` — 境内货源地映射表
- `SPEC_MODEL_MAPPING` — 规格型号映射（境内自主品牌→1，不确定是否享惠→2）

## 两种 PDF 格式及其差异

### 报关单

**老格式（排版规整）**：标签和值分行紧邻，文本提取顺序正确，正则直接匹配（`r"发货单位\s*\n?\s*(.+?)(?:\n|$)"`）。

**新模板（4列网格，20260625 起）**：
- 标签名不同（境内发货人/生产销售单位/境外收货人，非老的发货单位/经营单位）
- 表头是 4 列网格：标签行 + 值行 x 对齐，但标签和值隔几行（get_text 按块输出），老正则完全失效
- 用 `extract_customs_header_by_grid`（span 坐标，标签正下方 dy<14 取值）；doc_type 靠"海关编号有值"判定（规则0）
- 商品行字段顺序：名称→规格→数量→重量→单价→总价→CNY→原产国→目的国→货源地→照章征税（原产/目的/货源地在 CNY 行**之后**）
- 详见 memory.md #15 #20

### 预录单（三种格式）

**标准格式（少见）**：排版类似报关单，正则可提取。

**"仅供核对用"纵向倒排（常见）**：
- 页面含"仅供核对"/"核对单"标记
- 标签在页面底部（y≈750-785），值散布在上方（y≈40-750），文本顺序打乱
- 用 `extract_pre_recording_fields_by_position` + `_hedui_text_fallback`（span 坐标）
- 详见架构性项 A

**"仅供核对用"横向倒排（0060228GDM 类）**：
- 无"仅供核对"文字标记，但核心标签在页底（y>500），靠几何判据 is_hedui 识别（#16）
- **商品横向铺成多列**：项号(1-N)在页面最底部一行，每个商品占一列(x)；各字段在项号**上方**、纵向分散在不同 y
- 用 `extract_pre_recording_items_horizontal`（项号定列 + 列内文本模式识别 + 数量行级去重），在 `extract_pre_recording_items_by_position` 开头 dispatch
- 详见 memory.md #19

## 配对逻辑

1. 用户在报关单/预录单两个上传框分别上传文件
2. 每个文件调用 `parse_pdf` 解析，得到页面列表（含 doc_type）
3. 报关单文件用 `get_contract_no_from_customs` 提取合同协议号
4. 预录单文件用 `get_contract_no_from_pre` 提取合同协议号
5. 按合同协议号分组配对
6. `collect_pages_from_pdfs` 按类型收集页面：
   - customs=True：收集 customs_declaration + contract 页面（并附加 pre_recording 页面用于核对单商品补充）
   - pre=True：收集 pre_recording + unknown（含商品数据）页面
7. 每对调用 `extract_all_fields` + `run_comparison`

## 比对规则

| check_type | 含义 | 通过条件 |
|---|---|---|
| `match` | 精确匹配 | 规范化后值相同（数值字段用 float 比较） |
| `fixed` | 固定值 | 预录单值包含固定值的关键词部分 |
| `fuzzy` | 模糊匹配 | 按关键字/映射表匹配（规格型号、境内货源地、目的国） |
| `manual` | 人工确认 | 报关单无对应字段，展示预录单值供人工判断 |

## 运行

```bash
pip install -r requirements.txt
streamlit run app.py

# 独立诊断（不依赖 Streamlit）
python scripts/diagnose.py <报关单PDF> <预录单PDF>            # 可读文本
python scripts/diagnose.py <报关单PDF> <预录单PDF> --json     # JSON
```
