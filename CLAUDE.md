# 报关单 vs 预录单 智能比对工具

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

### 报关单（排版规整）
- 标签和值分行排列，文本提取顺序正确
- 正则直接匹配即可，如 `r"发货单位\s*\n?\s*(.+?)(?:\n|$)"`
- 可能含多页（合同页、装箱单、发票等附加页面）

### 预录单（两种格式）

**标准格式**（少見）：排版类似报关单，正则可提取

**"仅供核对用"格式**（常见，核心难点）：
- 页面含"仅供核对"/"核对单"标记
- PDF 排版是旋转/倒排的：标签在页面底部（y≈750-785），值散布在上方（y≈40-750）
- 文本提取后顺序完全打乱，标签和值混在一起
- 同一逻辑行的内容可能在不同 y 位置（如"照章征税"和"(1)"在相同 y 但不同 x）
- **必须用 span 坐标匹配，不能仅靠文本正则**

## 易错点与修复记录

### 1. 预录单表头提取失败（仅供核对用格式）

**现象**：发货单位、买方、经营单位等关键字段全部为空

**根因**：预录单文本是乱序的，正则无法匹配。初始版本只用正则，完全无法提取。

**修复**：实现两层提取策略：
1. `extract_pre_recording_fields_by_position` — 用 span 的 x/y 坐标定位标签，在标签的 x 列范围内找值
2. `_hedui_text_fallback` — 文本正则兜底，处理坐标提取遗漏的字段

**关键细节**：
- x 列宽度用相邻标签的 x 中点动态计算，不是固定值
- 噪声标签（如"预录入编号："、"20260514003"）需要排除，否则会误匹配为值
- 值验证函数 `_is_valid_field_value` 防止公司名匹配为国家等问题

### 2. 合同协议号与提运单号混淆

**现象**：合同协议号提取为 "18632-DLM250748" 或 "FBA603N147833NB0"，实际应为 "20260514003" 或 "20260521006"

**根因**：正则模式 `r"^(\d{5,}-[A-Z]{2,}\d+)$"` 同时匹配了提运单号格式

**修复**：
- 移除会匹配提运单号的正则模式
- 特殊处理：优先匹配 10-12 位纯数字日期格式（如 20260514003），其次才匹配 FBA 格式
- 扫描全部文本行而非只看相邻行

### 3. 多页预录单续页分类错误

**现象**：FBA603旧.pdf 有 4 页（1/4 到 4/4），page 1-3 被误判为 customs_declaration，导致 36 条商品只提取到 6 条

**根因**：
- Page 0 有"仅供核对"标记 → 正确识别为 pre_recording
- Page 1-3 没有"仅供核对"，但有"出口货物报关单"标题 → 被判为 customs_declaration
- post-processing 找 primary_type 时要求匹配"出境关别"或"出口口岸"正则，但核对单格式的文本中这些字段值在乱序位置，正则匹配不上

**修复**：在 primary_type 判定中增加"仅供核对"/"核对单"标记检测：
```python
has_hedui = "仅供核对" in p.text or "核对单" in p.text
if has_exit_customs or has_export_port or has_hedui:
    primary_type = p.doc_type
```

### 4. 商品明细总价为空

**现象**：36 条预录单商品中，部分条目的总价为空（如 item 16, 17, 20），但单价正常

**根因**：位置感知提取的列边界过窄。价格列 header 在 x=429.4，下一列（原产国）在 x=509.9，中点为 469.65。但实际总价值在 x=471.0，超出边界 1.35px。

**修复**：位置感知提取后，用文本提取（`_extract_items_from_continuation`）结果补充缺失字段：
```python
for key in list(item.keys()):
    if not item[key] and fallback.get(key):
        item[key] = fallback[key]
```

### 5. 固定值比对过于严格

**现象**：包装种类"纸制或纤维板制盒/箱" vs 固定值"(22)纸制或纤维板制盒/箱" → 失败。成交方式"FOB" vs "(3)FOB" → 失败。

**根因**：`compare_fixed` 要求关键字 AND 代码同时存在，但预录单提取的值通常不含代码（代码和值在不同 span，提取时未合并）

**修复**：改为关键词匹配即可通过，代码部分可选：
```python
if keyword and keyword in nv:
    return True
```
效果："照章征税" 匹配 "照章征税(1)" ✅，"照章" 不匹配 "照章征税(1)" ❌（因为"照章征税"不在"照章"中）

### 6. 数值字段字符串比较失败

**现象**：毛重 "194" vs "194.0" → 不匹配

**修复**：在 `compare_headers` 中对件数/毛重/净重用数值比较：
```python
elif fid in ("quantity", "gross_weight", "net_weight"):
    c_num = float(str(customs_val).replace(",", ""))
    p_num = float(str(pre_val).replace(",", ""))
    status = STATUS_PASS if c_num == p_num else STATUS_FAIL
```

### 7. 征免字段值不完整

**现象**：预录单商品明细的征免只显示"照章"或"照章征税"，缺少"(1)"代码

**根因**：
- `_parse_hedui_item` 中主动用 `re.sub(r'\(\d+\)', '', line)` 剥离了代码
- "(1)" 作为独立文本行被 `^\([A-Z0-9]+\)$` 匹配后直接跳过

**修复**：
- 保留完整值（不再剥离代码）
- 将纯数字代码行 "(1)" 合并到已提取的 duty_exemption 字段

### 8. DeepSeek VL2 API 不兼容

**现象**：调用 DeepSeek VL2 视觉模型时报错 `unknown variant image_url, expected text`

**根因**：DeepSeek 标准聊天 API 不支持图片输入，VL2 需要单独的 API 端点

**修复**：移除 AI 视觉兜底，完全依赖纯 Python 提取（位置感知 + 文本正则），效果反而更稳定

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
```
