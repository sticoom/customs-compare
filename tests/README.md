# 回归测试基线

防止"修一个格式、坏另一个格式"。每次改 `src/` 下的提取代码后跑一次，自动报告哪些样本的提取结果变了。

## 为什么需要

PDF 提取依赖"坐标/内容启发式"，每条修复都内嵌假设，新格式踩中就炸。历史教训：`#19` 修横向格式的"重复渲染"埋了 `#23` 跨 item 同值的雷；`#21` 修品名暴露了 `#22` 货源地。没有全格式回归基线时，每改一处只能凭手头 1-2 份样本验证，漏掉的线上炸。

本基线把历史样本的提取结果固化为 golden，**改代码后跑一次，立刻看到哪些样本变了**——修复目标的变更是预期的，其他变更是回归警报。

## 用法

```bash
# 对比模式：跑全部样本，报告 diff（有意外变化退出码 1，可接 CI / pre-commit）
python tests/regress.py

# 重建 golden：首次建立基线，或主动接受某次变更后更新
python tests/regress.py --update-golden
```

## 两类样本

| 类型 | 跑什么 | golden 存什么 |
|---|---|---|
| **pair**（配对）| `diagnose(报关单, 预录单)` | 比对报告快照（双方提取值 + 比对状态） |
| **single**（单文件）| `parse_pdf` + 对应提取器 | `doc_types` + `items` |

## 当前样本（5 份，覆盖三种格式）

| id | 类型 | 格式 | 验证点 |
|---|---|---|---|
| `pair_20260717011` | 配对 | 横向倒排核对单 + 综合包 | `#23` 数量项号x区间归位主样本 |
| `pair_20260710002` | 配对 | 横向倒排核对单 + 综合包 | 横向格式回归 |
| `pair_20260612002` | 配对 | 综合包 + 标准预录单 | 4列网格/综合包格式 |
| `single_pre6` | 单文件 | 标准纵向预录单 | `horizontal` dispatch 不误触发 |
| `single_20260529004` | 单文件 | 报关资料综合包 | 速玛-AU 批次，格式多样性 |

## 加新样本

1. 把 PDF 放到 `tests/fixtures/`，按 `pair_<合同号>_<customs|pre>.pdf` 或 `single_<id>.pdf` 命名
2. 在 `tests/regress.py` 的 `PAIRS` 或 `SINGLES` 列表加一条
3. `python tests/regress.py --update-golden` 生成基线
4. 抽检生成的 golden，确认关键字段（如 `quantity_unit`）正确
5. 提交

## golden 是"当前行为基线"，不是"正确性基线"

golden 固化的是**当前版本的提取结果**，用于检测**变化**，不保证本身全对。例如 `pair_20260612002` 的 golden 里 fail=50，说明该批次还有未修的提取问题——这没关系，未来修复时 fail 数下降、golden 变化，跑 regress 会报警，开发者确认"这是预期的修复变化"后 `--update-golden` 接受新基线。

## 敏感性

样本含真实公司名、合同号、商品数据。仓库因此设为 **private**。`fixtures/*.pdf` 和 `golden/*.json` 都进 git（团队 clone 即用）。

## 与 diagnose.py 的关系

`regress.py` 复用 `scripts/diagnose.py` 的 `diagnose()` 函数（pair 模式）和 `src/` 的提取函数（single 模式）。单次问题排查仍用 `diagnose.py`，批量回归用 `regress.py`。
