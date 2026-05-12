# 报关单 vs 预录单 智能比对工具

自动对比报关单和预录单 PDF，逐字段校验并生成比对报告。

## 功能

- 上传报关单和预录单 PDF，按**合同协议号**自动配对
- 逐字段比对表头信息和商品明细
- 支持多种比对规则：精确匹配、固定值校验、模糊匹配、人工确认
- 自动识别文档类型（报关单/预录单/合同/装箱单/发票）
- 比对结果导出为 Excel 报告

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 AI（可选）

编辑 `src/config.py`，填入智谱 API Key（用于文本提取失败时的兜底辅助）：

```python
"zhipu": {
    "api_key": "你的API Key",
}
```

> 不配置 AI 也能正常使用，AI 仅作为辅助手段。

### 启动

```bash
streamlit run app.py
```

浏览器打开后即可使用。

## 项目结构

```
customs-compare/
├── app.py                 # 主程序入口 (Streamlit Web 界面)
├── requirements.txt       # Python 依赖
├── src/
│   ├── config.py          # 配置中心：AI 模型、字段规则、映射表
│   ├── pdf_parser.py      # PDF 解析：文本提取 + 文档类型识别
│   ├── field_extractor.py # 字段提取：从文本中提取各字段值
│   ├── comparator.py      # 比对引擎：逐字段执行校验规则
│   ├── excel_exporter.py  # Excel 导出：生成比对报告
│   └── ai_assistant.py    # AI 辅助：文本提取失败时的兜底（可选）
├── docs/
│   └── plans/             # 产品需求文档
└── 预录单校验字段关系.xlsx   # 字段校验规则参考表
```

## 模块说明

| 模块 | 说明 |
|---|---|
| **app.py** | Streamlit 主界面，处理文件上传、调用各模块完成解析-提取-比对-展示全流程 |
| **src/pdf_parser.py** | 用 PyMuPDF 提取 PDF 文本，根据关键词识别文档类型（报关单/预录单/合同等） |
| **src/field_extractor.py** | 从解析后的文本中，按 config 定义的规则提取表头字段和商品明细字段 |
| **src/comparator.py** | 比对引擎，按字段规则执行精确匹配、固定值校验、模糊匹配，返回通过/不通过/模糊/人工确认四种状态 |
| **src/excel_exporter.py** | 将比对结果导出为带格式的 Excel 报告（通过绿/不通过红/模糊黄/人工蓝） |
| **src/ai_assistant.py** | 调用智谱 AI 模型辅助提取，当规则提取失败时作为兜底方案 |
| **src/config.py** | 所有配置的集中管理：AI 模型选择、文档识别关键词、字段提取规则、比对规则、映射表 |

## 比对规则

| 状态 | 说明 |
|---|---|
| ✅ 通过 | 报关单和预录单的值完全一致，或预录单为固定值 |
| ❌ 不通过 | 两者的值不一致 |
| ⚠️ 模糊匹配 | 按关键字匹配（如规格型号、境内货源地） |
| 🔍 人工确认 | 报关单无对应字段，需人工判断预录单值是否正确 |

## 数据流

```
用户上传 PDF
    ↓
pdf_parser.py → 提取文本 + 识别文档类型
    ↓
field_extractor.py → 提取表头字段 + 商品明细
    ↓
comparator.py → 逐字段比对
    ↓
app.py → 展示结果 + excel_exporter.py 导出报告
```

## 技术栈

- **Streamlit** — Web 界面
- **PyMuPDF (fitz)** — PDF 文本提取
- **openpyxl** — Excel 报告生成
- **智谱 AI (zhipuai)** — 可选的 AI 辅助提取
