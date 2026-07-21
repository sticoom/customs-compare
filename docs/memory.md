# 修复记录 (Memory)

> 本文件按时间顺序记录报关单/预录单比对工具的历史修复案例。**属于"以前出过什么事"的历史性内容**。
>
> 排障时先查「修复索引」速查表，命中后看「详细记录」拿修复模式。新坑追加末尾，**不删除已有记录**。

---

## 修复索引（速查表）

| # | 症状关键词 | 修复位置 | 日期 |
|---|----------|---------|------|
| 1 | 价格字段错行（单价/总价/币制合并列） | `pdf_parser.py::_assign_price_fields()` | 2026-05-28 |
| 2 | 单价为空（2 位小数单价） | 同上，数值大小比较 | 2026-05-29 |
| 3 | 贸易国提取为离境口岸 | `field_extractor.py::_hedui_text_fallback()` 标签定位 | 2026-06-03 |
| 4 | 征免性质拼接多余值 | `field_extractor.py::_is_valid_field_value()` 收紧 `len≤6` | 2026-06-03 |
| 5 | 续页分类错误（核对单被判报关单） | `pdf_parser.py` primary_type 增加「核对单」标记 | 2026-05-30 |
| 6 | 总价为空（位置列边界过窄） | `field_extractor.py::extract_all_fields()` 文本兜底补充 | 2026-05-31 |
| 7 | 固定值比对过严 | `comparator.py::compare_fixed()` 关键词匹配 | 2026-06-01 |
| 8 | 数值字段字符串比较失败 | `comparator.py::compare_headers()` float 比较 | 2026-06-01 |
| 9 | 征免字段缺少代码 | `field_extractor.py::_parse_hedui_item()` 保留完整值 | 2026-06-02 |
| 10 | row_slots 不足丢 item | `pdf_parser.py` 回退 y 坐标分组 + 文本补充 | 2026-06-05 |
| 11 | 商品编码+名称合并 span 未拆分 | `pdf_parser.py` 正则拆分 `^(\d{8,10})\s+(.+)$` | 2026-06-05 |
| 12 | item_no 前导零导致跨页重复 | `field_extractor.py` 统一 `str(int(item_no))` | 2026-06-11 |
| 13 | 整数单价/总价漏抓（正则写死小数点） | `field_extractor.py` + `pdf_parser.py` 统一 `PRICE_RE` | 2026-06-16 |
| 14 | 空项/空行泄漏到对比结果 | 两文件 `_is_empty_item()` 在每个 return 前过滤 | 2026-06-16 |
| 15 | 报关单新模板(海关编号已签发)被误判预录单 + 4列网格表头 | `pdf_parser.py::identify_doc_type()` 规则0 + `extract_customs_header_by_grid()` | 2026-06-26 |
| 16 | 录入单无"仅供核对"标记的核对单变体(标签在页底) | `pdf_parser.py::extract_pre_recording_fields_by_position()` is_hedui 检测 y>500 | 2026-06-26 |
| 17 | 录入单续页(只有商品)被兜底判成报关单 | `pdf_parser.py::parse_pdf()` primary_type fallback | 2026-06-26 |
| 18 | 文件传反导致整份比对错位 | `scripts/diagnose.py` 合并两 PDF 按 doc_type 自动判角色 | 2026-06-26 |
| 19 | 录入单横向倒排(项号在底/数据在上/每商品一列)商品全错 | `pdf_parser.py::extract_pre_recording_items_horizontal()` | 2026-06-26 |
| 20 | 报关单商品原产国/目的国/货源地漏提(CNY+照章征税全称) | `field_extractor.py::_parse_customs_item_content()` 币制行后顺序提取 | 2026-06-26 |
| 21 | 预录单品名被规格串占据/品名规格整体颠倒 | `pdf_parser.py::_split_name_and_spec()` + `extract_pre_recording_items_by_position()` | 2026-07-16 |
| 22 | 预录单货源地长地名误归目的国列(domestic_source空) | `pdf_parser.py::extract_pre_recording_items_by_position()` 内容特征override归source | 2026-07-16 |
| 23 | 横向核对单数量跨item错位(重量对/件套错位) | `pdf_parser.py::extract_pre_recording_items_horizontal()` 项号x区间归位 | 2026-07-20 |
| 24 | 预录单提取重构(分类器+独立提取器两层架构) | `pdf_parser.py::classify_pre_recording_layout` + `extract_pre_recording_standard_vertical` | 2026-07-21 |

**架构性历史项（不在 fix-log 序列）**：
- **A. 预录单「仅供核对用」格式必须用 span 坐标提取**——两层策略：位置感知 + 文本兜底
- **B. 合同协议号正则不要匹配 FBA 格式**——会被提运单号污染
- **C. DeepSeek VL2 API 不兼容**——移除 AI 视觉兜底，纯 Python 更稳定

---

## 详细修复记录

### #1. 价格字段错行（单价/总价/币制合并列）
- **日期**：2026-05-28
- **现象**：预录单"单价/总价/币制"合并列数据顺序为 总价→币制→单价，但代码按 单价→总价→币制 映射，导致三字段全部错位
- **根因**：`extract_pre_recording_items_by_position()` 中 price_data 的映射顺序固定为 `[0]=unit_price, [1]=total_price, [2]=currency`，但实际数据顺序不同
- **修复**：pdf_parser.py 新增 `_assign_price_fields()` 函数，用数值比较法：较小值=单价，较大值=总价（因为 总价=单价×数量≥单价），非数字=币制
- **影响**：pdf_parser.py 两处调用点（标准格式和核对单格式）

### #2. 单价为空（2位小数单价）
- **日期**：2026-05-29
- **现象**：20260602001 批次预录单单价全部为空，但总价和币制正常
- **根因**：初版用小数位数判断（≥3位=单价），但该批次单价只有2位小数（如59.32），判断失败
- **修复**：改用数值大小比较替代小数位数判断，`_assign_price_fields()` 按 float 排序
- **影响**：pdf_parser.py `_assign_price_fields()`

### #3. 贸易国提取为离境口岸（蛇口 vs 中国香港）
- **日期**：2026-06-03
- **现象**：预录单 trade_country 提取为"蛇口"（实际是 exit_port），应为"中国香港"；duty_nature 为"一般征税 澳大利亚"（拼接了多余值）
- **根因**：`_hedui_text_fallback()` 中通用正则 `^([\u4e00-\u9fff]+)` 顺序扫描，"蛇口"在文本中先于"中国香港"被匹配；duty_nature 验证器允许长度≤10
- **修复**：
  - field_extractor.py 新增标签定位提取块：扫描标签关键词，取其前面的非噪声行作为值（核对单格式值在标签前）
  - duty_nature 验证器收紧为 `len(v) ≤ 6`
- **影响**：field_extractor.py `_hedui_text_fallback()`

### #4. 征免性质拼接多余值
- **日期**：2026-06-03
- **现象**：同 #3，duty_nature 为"一般征税 澳大利亚"
- **根因**：位置感知提取时相邻列值被拼接；验证器 `len≤10` 放过了过长值
- **修复**：`len(v) ≤ 6`，合法值如"一般征税"为4字符，拼接后"一般征税 澳大利亚"为8字符被拦截
- **影响**：field_extractor.py `_is_valid_field_value()`

### #5. 续页分类错误（核对单续页被判为报关单）
- **日期**：2026-05-30
- **现象**：FBA603旧.pdf 4页中 page 1-3 被误判为 customs_declaration，36条商品只提取到6条
- **根因**：续页无"仅供核对"标记但有"出口货物报关单"标题→被判为报关单；primary_type 判定要求匹配"出境关别"正则，但核对单格式文本中这些字段在乱序位置
- **修复**：primary_type 判定增加 `has_hedui = "仅供核对" in p.text or "核对单" in p.text` 检测
- **影响**：pdf_parser.py post-processing 逻辑

### #6. 总价为空（位置提取列边界过窄）
- **日期**：2026-05-31
- **现象**：36条预录单商品中部分条目总价为空（如 item 16, 17, 20），单价正常
- **根因**：价格列 header 在 x=429.4，下一列在 x=509.9，中点 469.65。但实际总价值在 x=471.0，超出边界 1.35px
- **修复**：位置感知提取后，用 `_extract_items_from_continuation()` 文本提取结果补充缺失字段
- **影响**：field_extractor.py `extract_all_fields()`

### #7. 固定值比对过严
- **日期**：2026-06-01
- **现象**：包装种类"纸制或纤维板制盒/箱" vs "(22)纸制或纤维板制盒/箱"→失败；成交方式"FOB" vs "(3)FOB"→失败
- **根因**：`compare_fixed` 要求关键字 AND 代码同时存在，但预录单提取值通常不含代码
- **修复**：改为关键词匹配即可，代码部分可选：`if keyword and keyword in nv`
- **影响**：comparator.py `compare_fixed()`

### #8. 数值字段字符串比较失败
- **日期**：2026-06-01
- **现象**：毛重 "194" vs "194.0"→不匹配
- **根因**：字符串直接比较 "194" != "194.0"
- **修复**：`compare_headers()` 中对件数/毛重/净重用 float 比较
- **影响**：comparator.py `compare_headers()`

### #9. 征免字段缺少代码
- **日期**：2026-06-02
- **现象**：预录单商品明细征免只显示"照章"或"照章征税"，缺少"(1)"代码
- **根因**：`_parse_hedui_item()` 中 `re.sub(r'\(\d+\)', '', line)` 主动剥离了代码；纯数字代码行"(1)"被跳过
- **修复**：保留完整值不再剥离代码，将纯数字代码行"(1)"合并到 duty_exemption 字段
- **影响**：field_extractor.py `_parse_hedui_item()`

### #10. row_slots 不足导致部分 item 被丢弃
- **日期**：2026-06-05
- **现象**：预录单-33箱.pdf 3个商品只提取到1个（item 1），items 2和3全部字段为空
- **根因**：PDF 中只有2条水平线（表头底线 + item 1底线），产生1个 row_slot `(253.0, 284.0)`。Items 2 (y=285.3) 和 3 (y=316.2) 落在 slot 范围外被丢弃
- **修复**：
  - `extract_pre_recording_items_by_position()` 中 `if row_slots:` 改为 `if row_slots and len(row_slots) >= len(item_start_ys):`，当 slot 数量不足时回退到 y 坐标分组
  - `extract_all_fields()` 中增加安全网：位置提取后将文本提取发现的额外 item 补充进来
- **影响**：pdf_parser.py `extract_pre_recording_items_by_position()`、field_extractor.py `extract_all_fields()`

### #11. 商品编码+商品名称合并 span 未拆分
- **日期**：2026-06-05
- **现象**：724箱预录单 product_name 包含编码前缀 `4202920000 收纳箱`，导致商品名称比对全部失败
- **根因**：标准格式预录单中，商品编码和商品名称在同一个 span（如 `'4202920000 收纳箱'` x=61.0），位置提取将整个 span 归入 product_name 列
- **修复**：在 row_slots 路径和 y 坐标路径中增加 `re.match(r"^(\d{8,10})\s+(.+)$", stext)` 匹配，拆分为 product_code 和 product_name
- **影响**：pdf_parser.py `extract_pre_recording_items_by_position()` 两处 span 处理块

### #12. item_no 前导零导致跨页重复 item
- **日期**：2026-06-11
- **现象**：154箱核对单2页预录单，实际15条商品被提取为24条，多出9条重复项（01-06+07-09），导致比对大量错行和字段缺失
- **根因**：文本提取 `_extract_items_from_continuation` 的 item_no 保留前导零（"01"），位置提取通过 `str(int(...))` 已去除前导零（"1"）。安全网比较时 "01" != "1"，将已存在的 item 当作新 item 补充进来
- **修复**：在 `_extract_items_from_continuation` 和 `_parse_hedui_item` 中统一 `str(int(item_no))` 去除前导零
- **影响**：field_extractor.py `_extract_items_from_continuation()`、`_parse_hedui_item()`

### #13. 整数单价/总价漏抓（正则写死小数点）
- **日期**：2026-06-16
- **现象**：报关单单价/总价为整数时（如 `5`、`2123`）完全抓不到，单价为空；小数（如 `25.28`）正常。预录单路径已部分修复（`^[\d,.]+$`），但报关单文本路径和 pdf_parser 多处仍是旧的 `^\d+\.\d+$`
- **根因**：价格正则散落在至少 6 处，各自写死了"必须有小数点"：`^\d+\.\d+$`、`^\d+\.\d{2,4}$`、`^\d+\.\d{1,2}$`。这些模式只匹配小数，整数单价/总价在真实业务里是存在的（尤其总价经常是整数），于是整条记录的价格字段全空，进而可能被 #14 当成空项过滤掉、或显示为比对失败
- **修复**：两文件顶部各定义统一常量 `PRICE_RE = re.compile(r"^\d[\d,]*\.?\d*$")`，支持整数(`5`)、小数(`25.28`)、千分位(`1,234.56`)、多位小数(`25.2800`)。把以下所有写死小数点的位置统一替换为 `PRICE_RE.match(...)`：
  - `field_extractor.py::_parse_customs_item_content()` 2 处（定位价格区起点 + 提取单价/总价）
  - `pdf_parser.py::_extract_items_vertical_layout()` 1 处（价格 span 分类）
  - `pdf_parser.py::extract_pre_recording_items_by_position()` 2 处（价格候选检测、列边界修正）
- **关键教训**：价格正则必须**单点定义、处处复用**，不能再让 `^\d+\.\d+$` 这类写死小数点的模式散落各处——每次新增 PDF 格式都可能在某条路径重新冒出来。`PRICE_RE` 不要求小数位数，因为整数价格合法
- **影响**：field_extractor.py、pdf_parser.py 共 5 处价格匹配点
- **关联**：与 #2（小数位数判断）不同——#2 是"用小数位数区分单/总价"，#13 是"正则根本不匹配整数"。#13 修复后 `_assign_price_fields()` 的输入也会受益

### #14. 空项/空行泄漏到对比结果
- **日期**：2026-06-16
- **现象**：对比结果里出现完全没有有效数据的商品行（无商品编号、无名称、无价格、无数量），干扰阅读、拉低通过率
- **根因**：提取链路有 5+ 条路径（`extract_customs_items`、`_extract_items_from_continuation`、`_extract_items_from_hedui`、`_extract_items_loose`、`extract_pre_recording_items`、`extract_pre_recording_items_by_position`、`_extract_items_vertical_layout`），任何一条在正则切分/位置回退时都可能产生"空壳 item"（只有 item_no 或全是空字段），却没有统一的出口过滤
- **修复**：两文件各定义 `_is_empty_item(item)`，检查 `product_code`/`product_name`/`quantity_unit`/`unit_price`/`total_price` 是否**全部**为空。在每个提取函数的 `return items` 前加 `items = [it for it in items if not _is_empty_item(it)]`
  - field_extractor.py：5 个 return 点（`extract_customs_items`、`_extract_items_from_continuation`、`_extract_items_from_hedui`、`_extract_items_loose`、`extract_pre_recording_items`）
  - pdf_parser.py：2 个 return 点（`_extract_items_vertical_layout`、`extract_pre_recording_items_by_position`）
- **关键教训**：提取层有"多路径兜底"架构（位置感知→文本→核对单→续页），每条路径都是空项的可能来源。**过滤必须放在每个出口**，不能只在一处统一过滤——因为 `extract_all_fields` 里各路径结果会 merge，中途的空项会污染 merge 逻辑（如把空项的 item_no 也算进 `existing_nos`）
- **影响**：field_extractor.py 5 处、pdf_parser.py 2 处 return 点；comparator.py 价格比较顺带增强空字符串防御（`float(c_val) if c_val else None`）

### #15. 报关单新模板识别 + 4列网格表头提取
- **日期**：2026-06-26
- **现象**：20260625001 报关单（海关编号 4403961BEF）用"境内发货人/监管方式/境外收货人"标签排版，被 identify_doc_type 规则3 误判为预录单；表头是 4 列网格（标签行+值行 x 对齐，标签和值隔几行），老 `extract_customs_header` 正则（找"发货单位"且假设标签紧邻值）完全失效，sender_unit 抓成相邻标签"提运单号"
- **根因**：老格式标签名"发货单位/经营单位"且标签紧邻值；新模板标签名不同 + 网格排版标签值分离
- **修复**：(1) identify_doc_type 加规则0：`re.search(r"海关编号[:：\s]*[0-9][0-9A-Z]{5,}", text)` 有值→报关单（最可靠判据，优先级最高，预录单海关编号为空不会误命中）；(2) 新增 `extract_customs_header_by_grid(page_info)`：用 span 坐标，找标签后在正下方(0<dy<14)x 最近处取值；extract_all_fields 优先用 grid，提取不到 sender_unit/contract_no 才回退文本正则
- **影响**：pdf_parser.py::identify_doc_type、extract_customs_header_by_grid；field_extractor.py::extract_all_fields

### #16. 录入单无"仅供核对"标记的核对单变体
- **日期**：2026-06-26
- **现象**：0060228GDM 录入单不含"仅供核对"/"整合申报"字样，但实为乱序核对单格式（核心表头标签印在页面底部 y>500，值在上方），extract_pre_recording_fields_by_position 按标准格式（标签在顶部 y<200）处理会错位
- **根因**：is_hedui 检测只认"仅供核对"文字标记，漏掉这种无标记变体
- **修复**：is_hedui 增加几何判据——境内发货人/境外收货人/合同协议号 等核心标签若出现在 y>500，判为核对单格式（用 x±25 列范围找值）
- **影响**：pdf_parser.py::extract_pre_recording_fields_by_position

### #17. 录入单续页被兜底判成报关单
- **日期**：2026-06-26
- **现象**：0060228GDM 录入单 Page1（续页，只有商品无表头）被判 customs_declaration，混进报关单侧，导致整份比对错位
- **根因**：续页含"出口货物报关单"标题进 identify_doc_type 首分支，但无出境关别值/仅供核对/境内发货人等表头标签，规则 0-6 全不命中，落到兜底 `return "customs_declaration"`；parse_pdf 的 primary_type 纠正逻辑要求"强证据"(出境关别值/出口口岸/仅供核对)，该录入单 Page0 一个都不满足→primary_type=None→纠正没触发
- **修复**：parse_pdf 的 primary_type 查找加 fallback——强证据全 miss 时，信任 identify_doc_type 已给出的首个 pre/customs 判定作为 primary_type，让续页归到正确类型
- **影响**：pdf_parser.py::parse_pdf
- **关联**：是 #5（续页分类）的变体——#5 靠"核对单"标记，本坑是无标记的纯商品续页

### #18. 文件传反导致整份比对错位
- **日期**：2026-06-26
- **现象**：用户把录入单当报关单参数传（或上传框放反），diagnose 按 argv 顺序把录入单页面归报关单侧、报关单页面归录入单侧，collect 的"补救"逻辑反而加剧混乱，比对全部错位
- **根因**：diagnose 依赖 argv 位置参数区分报关单/录入单，不可信
- **修复**：diagnose 改为合并两份 PDF 所有页面，按 doc_type 自动归类（customs_declaration→报关单侧，pre_recording→录入单侧），不依赖 argv 顺序；合同协议号相同即视为一对
- **影响**：scripts/diagnose.py::diagnose（注：app.py 的 collect_pages_from_pdfs 仍有同样问题，待统一）
- **关联**：与架构性项 B（合同协议号配对）一致——以合同协议号为配对键，不靠文件名/顺序

### #19. 录入单横向倒排格式商品提取
- **日期**：2026-06-26
- **现象**：0060228GDM 录入单是横向倒排——项号(1-8)印在页面底部，各字段印在项号**上方**，每个商品横向占一**列**(x)；extract_pre_recording_items_by_position 假设"表头同行+数据在下"完全失效，商品编码读到 9 位（残缺）、数量多列拼接、项号全空
- **根因**：老函数第一步找表头行 y（项号/商品编号 span 同一 y），但该格式各列列头纵向分散在不同 y（项号 y≈799、商品编号 y≈737、数量 y≈461…）；且数据在表头(项号)**上方**而非下方，`data_spans = [s for s in spans if s["y"] > header_y+5]` 返回空
- **修复**：新增 `extract_pre_recording_items_horizontal(page_info)`：(1) 纯小整数 span 按 y 聚类找项号锚点行（≥2 连续整数、y 最大），其 x 即列中心；(2) 相邻列中心中点定列边界，上方数据按列聚合；(3) 列内按文本模式识别字段（编码 `^\d{8,10}$`/名称/规格含`|`/单价总价 PRICE_RE/币制/国名集合/货源地 `^\(\d+\)`/征免）；(4) 数量字段因 x 偏移大且有重复渲染 span，改为**行级提取**——分主数量(个件)与重量(千克)，按数值去重(保留 x 最大)后按 x 升序分配列。在 extract_pre_recording_items_by_position 开头 dispatch：命中横向格式直接返回，老格式零影响
- **关键教训**：横向倒排判据="项号数据行在页面底部(y大)且≥2个连续整数"；标准预录单项号分散在不同 y 不会聚到同一簇，故 dispatch 不误触发。数量字段的 x 在该格式下不可靠（系统性偏移+重复 span），必须行级按序分配，不能按列边界
- **影响**：pdf_parser.py 新增 extract_pre_recording_items_horizontal + extract_pre_recording_items_by_position 开头 dispatch
- **样本局限**：仅 0060228GDM 1 份录入单验证（用户确认模板固定），换批次需回归

### #20. 报关单商品原产国/目的国/货源地漏提
- **日期**：2026-06-26
- **现象**：20260625001 报关单商品的货源地(宁波其他)、目的国(加拿大)、原产国(中国)都没提到，比对报"报关单空 vs 录入单有"
- **根因**：`_parse_customs_item_content` 货源地锚点写死 `line=="人民币"` 和 `line=="照章"`，但该报关单用全称"CNY"和"照章征税"，锚点 miss；且目的国提取假设在"数量行后、价格行前"，但该格式目的国在"币制行后"
- **修复**：改为在币制行(CNY/人民币)之后、征免行(照章*)之前，按顺序提取 tail[0]=原产国 / tail[1]=目的国 / tail[2]=货源地；锚点放宽为 `line in ("人民币","CNY")` 和 `line.startswith("照章")`；用 `not item.get()` 保护已填字段
- **影响**：field_extractor.py::_parse_customs_item_content

### #21. 预录单品名被规格串占据（品名/规格整体颠倒）
- **日期**：2026-07-16
- **现象**：J18632B 预录单全部 33 条商品的 `product_name` 被填成规格串（如 `1|2|家用|PET|DELAMU牌|无型号`），真品名（收纳盒/塑料线槽…）被塞进 `spec_model`；比对时表现为"预录单品名缺失/全错"，33 条商品明细全部不通过
- **根因**：`extract_pre_recording_items_by_position` 把"商品名称及规格型号"列（同 x：品名在上 y 小、规格在下 y 大且带 `|`）的所有 span 按**列表追加顺序**分配——`[0]→product_name`、`[1:]→spec_model`。但 span 追加顺序来自 PDF 原始绘制顺序（`extract_spans_with_positions`），不可靠：该格式里规格 span 排在品名 span **前面**，导致品名/规格整体颠倒。`extract_all_fields` 第 988 行对预录单续页（unknown）也调用同一函数，所以 page0 + 续页全部商品一致出错
- **修复**：新增 `_split_name_and_spec(name_entries)`——按 y 升序遍历，含 `|` 的归规格、不含 `|` 的第一个作品名、其余（如尺寸描述 `24MM*…`）归规格；全含 `|` 时取 y 最小兜底。两处 item 构造（row_slots 路径 + y 坐标回退路径）改为把 product_name 列 span 收集成 `(y, text)` 并调用该函数，不再依赖列表顺序
- **关键教训**：同一单元格内跨 span 的字段（品名 vs 规格）不能用收集顺序区分——PDF span 绘制顺序不稳定，必须用 y 坐标排序 + 内容启发式（预录单申报要素规格以 `|` 分隔）定位
- **影响**：pdf_parser.py::_split_name_and_spec（新增）、extract_pre_recording_items_by_position 两处 item 构造
- **验证**：J18632B（33 条全修复，比对 41→8 不通过）+ 回归 20260612002.pdf、预录单-6.pdf 老格式品名仍正确
- **关联**：#11 处理同一 span 内"编码+名称"合并；本坑处理同列内"品名+规格"跨 span 顺序。剩余 8 条不通过是货源地（如 `(33029)宁波其他`）误归目的国列的另一独立 bug，未在本坑处理（见 #22）

### #22. 预录单货源地长地名误归目的国列（domestic_source 为空）
- **日期**：2026-07-16
- **现象**：#21 修复后剩余 8 条商品 `domestic_source` 为空，货源地（如 `(33029)宁波其他`、`(44209)中山其他`）被误拼进 `final_dest_country`（变成 `加拿大 (33029)宁波其他 (CAN)`）；短地名（台州/东莞）正常
- **根因**：货源地列表头 x=683，列左边界按表头法 = 683−15 = **668**。但货源地 span 左对齐、城市名长度不同导致起始 x 不同：短地名（台州/东莞，2 字）x≈676.8 > 668 归列正确；长地名（宁波其他/中山其他，4 字）x≈**667.8** < 668 落入左侧的目的国列。表头 x 不代表数据起始 x，固定偏移边界对"文本宽度可变"的列不可靠
- **修复**：两处 item 构造循环（row_slots + y 回退）的列修正段加内容特征 override——`re.match(r"^[（(]\d{4,6}[）)]", stext)` 命中即强制归 `source` 列。征免代码 `(1)` 只有 1 位数字不匹配，不会误伤
- **关键教训**：货源地有强特征 `(4-6位数字)中文`（区域代码+城市名），比 x 坐标更可靠。列边界用表头 x ± 固定偏移对文本宽度可变的列（城市名长短不一）会漏，应用内容特征兜底
- **影响**：pdf_parser.py::extract_pre_recording_items_by_position 两处列修正段
- **验证**：J18632B 8 条货源地异常全消（比对 344 通过 / 0 不通过）+ 回归 20260612002.pdf、预录单-6.pdf 货源地仍正确
- **关联**：与 #21 同次发现（#21 修复品名后暴露的剩余 8 条），独立根因（列边界 vs span 顺序）

### #23. 横向倒排核对单数量字段跨 item 错位（重量对、件/套错位）
- **日期**：2026-07-20
- **现象**：J18632B-DLM250833 预录单（横向倒排核对单）全部商品的 `quantity_unit` 跨 item 错位——**重量(千克)对齐正确，但主数量(件/套/个)整体向下错位一个 item**：item N 拿到真实 item N+1 的数量。比对表现为"明明对得上的数量全 fail"，如 item2 锅架 `报=25千克/36套` vs `预=204套/25千克`（204套 本属 item3）。Page0 共 6 条 item 全错位，Page1 14 条从 item10 起继续错位
- **根因**：`extract_pre_recording_items_horizontal` 末尾的数量归位用「按数值全局去重 + idx 顺序分配」（#19 引入），两个缺陷叠加：
  - (1) `_dedup_by_num` 按**数值**全局去重，把不同 item 的同值数量（item2 和 item6 都是"36套"，x 相距 130px）当成同一 item 的重复渲染删掉 → 主数量序列长度 < item 数，从丢失点起整体下移一位
  - (2) idx 顺序分配强假设 `span 数 == item 数`，但同 item 可能有多 个数量 span（Page1 item10 有"30个"+"30件"两个重复渲染 span）→ 序列长度 > item 数，从多余点起整体下移
  - 重量字段因为各 item 重量值都不同（13/25/287/50/35/75...），去重不影响，所以重量一直对——这也解释了"千克对、件套错"的不对称现象
- **修复**：改用项号 anchor 的 x 坐标做「左闭右开」区间 `[项号x-2, 下一项号x-2)` 归位每个数量 span（文件 `pdf_parser.py::extract_pre_recording_items_horizontal`，删 `_dedup_by_num`，新增 `_qty_owner`）。同 item 的多个 span 自然归同一区间，再 in-place 保序去重滤掉重复渲染。区间宽度 = 列宽(~32) > 主数量偏移(20)，左右各减 2px 容差是因为**项号 span 的 x 比同列数据 span 的 x 略大 ~0.2px**（字符渲染起始位置差异），不减容差会让重量(x≈项号x-0.2)不满足 `cx<=x` 被推给上一个 item
- **关键教训**：横向格式数量归位有三种方案，只有「项号 x 区间」可靠——
  - (a) 按列边界**中点**归位 ❌：数量 x 系统性偏右(=项号x+20)，越过中点(项号x+16)落到下一 item（#19 已踩）
  - (b) 按数值去重 + **idx 顺序**分配 ❌：跨 item 同值被误删、同 item 多 span 让序列长度对不上（本坑）
  - (c) **项号 x 区间** `[项号x, 下一项号x)` ✓：每个 item 的数据 x 落在 [项号x, 项号x+列宽) 内（重量靠左 ±0.2、主数量靠右 +20），区间稳定覆盖。这是唯一不依赖"span 数 == item 数"假设的方案
- **影响**：`pdf_parser.py::extract_pre_recording_items_horizontal`（数量归位段重写）；标准纵向预录单不受影响（horizontal 函数找不到项号 anchor 时返回 []，dispatch 回退到 `extract_pre_recording_items_by_position` 标准路径）
- **验证**：J18632B-125箱 `quantity_unit` 10 fail→0 fail（汇总 202→209 pass / 10→3 fail，剩 3 fail 是 product_code 截断 + 规格串顺序的其他已知问题）；J18632B-241箱 344 pass / 0 fail；预录单-6（标准纵向）horizontal 不触发，quantity_unit 正常
- **关联**：#19 引入横向提取时用 idx+去重 处理"重复渲染"，当时仅 0060228GDM 1 份样本验证（恰好无跨 item 同值），未覆盖"跨 item 同值"和"同 item 多 span"两种情况。本坑是 #19 的后继完善

### #24. 预录单商品提取重构：格式分类器 + 独立提取器（两层架构，非 bug 修复）
- **日期**：2026-07-21
- **现象**：（架构改进，非 bug）`extract_pre_recording_items_by_position` 525 行单函数把"格式判别 + 三种格式（横向倒排/纵向布局/标准纵向）的提取逻辑"全混在一起，开头还"尝试性"先调一次 horizontal 探路。改一种格式要通读全文确定影响面——`#19→#23`、`#21→#22` 都是改一种格式时碰了共享代码
- **根因**：缺"格式分类"抽象层，判别与提取耦合在一个函数
- **修复**：拆为两层架构——
  - `classify_pre_recording_layout(page_info, spans) → "horizontal" / "standard_vertical"`（判据复用 `_find_horizontal_item_anchor`，与 horizontal 提取器共用，保证等价）
  - `extract_pre_recording_standard_vertical(page_info, spans)`（原主逻辑整体搬入，含 vertical_layout 二级 dispatch）
  - `extract_pre_recording_items_by_position` 瘦身为 classify + dispatch + 安全网（~12 行）
  - `_find_horizontal_item_anchor(spans)` 从 horizontal 抽出，分类器与 horizontal 共用，消除重构前 horizontal 被白跑一次的开销
  - 删除死代码 `extract_pre_recording_items_by_grid`（horizontal 前身，全仓库无调用）
- **关键决策**：
  - **保守方案**：分类器只判 horizontal（判据明确可复用），vertical_layout 保留在 standard_vertical 内部二级 dispatch——其轻量判据（同 x 找≥3 列头关键词）与现状 `col_positions<3` 的等价性在 5 样本上无法验证（样本无 vertical_layout 页面），不引入未验证判据
  - **安全网**：分类器判 horizontal 但提取空时回退 standard_vertical，完整保留"尝试性 dispatch"语义，分类器失误时不比现状差
  - **渐进 5 步**（每步 regress.py 全 PASS 才进下一步）：A 抽 anchor → B 抽 standard_vertical → C spans 透传 → D 接入分类器 → E 删死代码
- **影响**：`pdf_parser.py`（新增 3 函数、改 2 函数、删 1 函数）；`field_extractor.py` 不动（4 处调用签名/行为不变）
- **验证**：5 步全程 `tests/regress.py` 全 5 样本"无变化"，提取结果零变化——这正是"重构无回归"的证据
- **关联**：回归基线（`tests/regress.py`，#23 同批建立）让重构可验证；回答用户"为什么总出错"的架构对策——降低单次修复爆炸半径，改一种格式只动对应提取器

---

## 架构性历史项（基础类，独立于迭代修复序列）

### A. 预录单「仅供核对用」格式必须用 span 坐标提取

**现象**：发货单位、买方、经营单位等关键字段全部为空

**根因**：预录单文本是乱序的，正则无法匹配。初始版本只用正则，完全无法提取。

**修复**：实现两层提取策略：
1. `extract_pre_recording_fields_by_position` — 用 span 的 x/y 坐标定位标签，在标签的 x 列范围内找值
2. `_hedui_text_fallback` — 文本正则兜底，处理坐标提取遗漏的字段

**关键细节**：
- x 列宽度用相邻标签的 x 中点动态计算，不是固定值
- 噪声标签（如"预录入编号："、"20260514003"）需要排除，否则会误匹配为值
- 值验证函数 `_is_valid_field_value` 防止公司名匹配为国家等问题

### B. 合同协议号与提运单号混淆

**现象**：合同协议号提取为 "18632-DLM250748" 或 "FBA603N147833NB0"，实际应为 "20260514003" 或 "20260521006"

**根因**：正则模式 `r"^(\d{5,}-[A-Z]{2,}\d+)$"` 同时匹配了提运单号格式

**修复**：
- 移除会匹配提运单号的正则模式
- 特殊处理：优先匹配 10-12 位纯数字日期格式（如 20260514003），其次才匹配 FBA 格式
- 扫描全部文本行而非只看相邻行

### C. DeepSeek VL2 API 不兼容

**现象**：调用 DeepSeek VL2 视觉模型时报错 `unknown variant image_url, expected text`

**根因**：DeepSeek 标准聊天 API 不支持图片输入，VL2 需要单独的 API 端点

**修复**：移除 AI 视觉兜底，完全依赖纯 Python 提取（位置感知 + 文本正则），效果反而更稳定

---

## 如何追加新坑

发现新问题时，按以下格式追加到本文件末尾：

```markdown
### N+1. 简短标题
- 日期：YYYY-MM-DD
- 现象：用户看到的现象
- 根因：根本原因（不是表象）
- 修复：具体修复方法（文件::函数）
- 影响：受影响的文件和函数
```

同时把速查表里加一行：`| N+1 | 症状关键词 | 文件::函数 | 日期 |`

**追加原则**：
- 不删除已有记录
- 编号连续递增
- 如果新发现修正了旧 pit，在旧 pit 末尾加「⚠️ 已被 #N 修正」标注，但保留原文
