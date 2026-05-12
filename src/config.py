"""
报关单 vs 预录单 智能比对工具 - 配置文件
包含：AI 模型配置、比对规则、映射表
"""

# ============================================================
# AI 模型配置（可切换 provider）
# ============================================================
AI_CONFIG = {
    # 当前使用智谱免费模型，后续可改为 "deepseek", "openai" 等
    "provider": "zhipu",
    # 智谱 AI
    "zhipu": {
        "text_model": "glm-4-flash",      # 免费文本模型
        "vision_model": "glm-4v-flash",    # 免费视觉模型
        "api_key": "",                      # 填入你的 API Key
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    # DeepSeek（备用）
    "deepseek": {
        "text_model": "deepseek-chat",
        "vision_model": "deepseek-vl2",
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
    },
}


def get_ai_config():
    """获取当前 provider 的 AI 配置"""
    provider = AI_CONFIG["provider"]
    cfg = AI_CONFIG[provider].copy()
    cfg["provider"] = provider
    return cfg


# ============================================================
# 文档类型识别关键词
# ============================================================
DOC_TYPE_KEYWORDS = {
    "customs_declaration": {
        # 报关单：含此标题 且 出口口岸为空(-)
        "title_keywords": ["中华人民共和国海关出口货物报关单"],
        "negative_keywords": ["仅供核对"],
        "empty_field": "出口口岸",
    },
    "pre_recording": {
        # 预录单：含出境关别 且 值不为空
        "has_field": "出境关别",
        "title_keywords": ["中华人民共和国海关出口货物报关单"],
    },
    "contract": {
        # 合同页：提取买方
        "title_keywords": ["合同", "CONTRACT"],
    },
    "packing_list": {
        "title_keywords": ["装箱单", "PACKING LIST"],
    },
    "invoice": {
        "title_keywords": ["发票", "INVOICE"],
    },
}


# ============================================================
# 报关单字段提取规则（项号前）
# ============================================================
CUSTOMS_HEADER_FIELDS = [
    {
        "id": "sender_unit",
        "label": "发货单位",
        "customs_field": "发货单位",
        "pre_field": "境内发货人",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "buyer",
        "label": "买方",
        "customs_field": "买方",         # 来自合同页
        "pre_field": "境外收货人",
        "check_type": "match",
        "notes": "来自报关资料PDF合同页的买方/Buyers字段",
        "source_page": "contract",
    },
    {
        "id": "business_unit",
        "label": "经营单位",
        "customs_field": "经营单位",
        "pre_field": "生产销售单位",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "contract_no",
        "label": "合同协议号",
        "customs_field": "合同协议号",
        "pre_field": "合同协议号",
        "check_type": "match",
        "notes": "配对依据",
    },
    {
        "id": "package_type",
        "label": "包装种类",
        "customs_field": "包装种类",
        "pre_field": "包装种类",
        "check_type": "fixed",
        "fixed_value": "(22)纸制或纤维板制盒/箱",
        "notes": "预录单必须为固定值",
    },
    {
        "id": "transport_mode",
        "label": "运输方式",
        "customs_field": "运输方式",
        "pre_field": "运输方式",
        "check_type": "manual",
        "notes": "待人工确认",
    },
    {
        "id": "trade_mode",
        "label": "贸易方式",
        "customs_field": "贸易方式",
        "pre_field": "监管方式",
        "check_type": "fixed",
        "fixed_value": "(0110)一般贸易",
        "notes": "预录单必须为固定值",
    },
    {
        "id": "trade_country",
        "label": "贸易国",
        "customs_field": "贸易国",
        "pre_field": "贸易国（地区）",
        "check_type": "fixed",
        "fixed_value": "(HKG)中国香港",
        "notes": "预录单必须为固定值",
    },
    {
        "id": "quantity",
        "label": "件数",
        "customs_field": "件数",
        "pre_field": "件数",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "gross_weight",
        "label": "毛重（千克）",
        "customs_field": "毛重（千克）",
        "pre_field": "毛重(千克)",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "net_weight",
        "label": "净重（千克）",
        "customs_field": "净重（千克）",
        "pre_field": "净重(千克)",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "deal_mode",
        "label": "成交方式",
        "customs_field": "成交方式",
        "pre_field": "成交方式",
        "check_type": "fixed",
        "fixed_value": "(3)FOB",
        "notes": "预录单必须为固定值",
    },
    {
        "id": "duty_nature",
        "label": "征免性质",
        "customs_field": "征免性质",
        "pre_field": "征免性质",
        "check_type": "fixed",
        "fixed_value": "(101)一般征税",
        "notes": "预录单必须为固定值",
    },
    {
        "id": "dest_country",
        "label": "运抵国（地区）",
        "customs_field": "运抵国（地区）",
        "pre_field": "运抵国（地区）",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "dest_port",
        "label": "指运港",
        "customs_field": "指运港",
        "pre_field": "指运港",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "exit_customs",
        "label": "出境关别",
        "customs_field": None,  # 报关单无此字段
        "pre_field": "出境关别",
        "check_type": "manual",
        "notes": "待人工确认，报关单无此字段",
    },
    {
        "id": "exit_port",
        "label": "离境口岸",
        "customs_field": None,
        "pre_field": "离境口岸",
        "check_type": "manual",
        "notes": "待人工确认，报关单无此字段",
    },
    {
        "id": "attached_docs",
        "label": "随附单证及编号",
        "customs_field": None,
        "pre_field": "随附单证及编号",
        "check_type": "manual",
        "notes": "待人工确认，报关单无此字段",
    },
    {
        "id": "marks_remarks",
        "label": "标记唛码及备注",
        "customs_field": None,
        "pre_field": "标记唛码及备注",
        "check_type": "manual",
        "notes": "待人工确认，报关单无此字段",
    },
]


# ============================================================
# 商品明细字段提取规则（项号后）
# ============================================================
CUSTOMS_ITEM_FIELDS = [
    {
        "id": "product_code",
        "label": "商品编码",
        "customs_field": "商品编号",
        "pre_field": "商品编号",
        "check_type": "match",
        "notes": "",
    },
    {
        "id": "product_name_spec",
        "label": "商品名称及规格型号",
        "customs_field": "商品名称及规格型号",
        "pre_field": "商品名称、规格型号",
        "check_type": "fuzzy",
        "notes": "名称精确匹配；规格按|分隔逐项比对；最后一项可模糊",
    },
    {
        "id": "quantity_unit",
        "label": "数量及单位",
        "customs_field": "数量及单位",
        "pre_field": "数量及单位",
        "check_type": "match",
        "swap_rows": True,
        "notes": "行交换：报关单第1行=预录单第3行，报关单第2行=预录单第1行",
    },
    {
        "id": "unit_price",
        "label": "单价",
        "customs_field": "单价",
        "pre_field": "单价",
        "check_type": "match",
        "notes": "预录单中单价在'单价/总价/币制'列第1行",
    },
    {
        "id": "total_price",
        "label": "总价",
        "customs_field": "总价",
        "pre_field": "总价",
        "check_type": "match",
        "notes": "预录单中总价在'单价/总价/币制'列第2行",
    },
    {
        "id": "currency",
        "label": "币制",
        "customs_field": "币制",
        "pre_field": "币制",
        "check_type": "fixed",
        "fixed_value": "人民币",
        "notes": "预录单必须为：人民币",
    },
    {
        "id": "origin_country",
        "label": "原产国（地区）",
        "customs_field": None,
        "pre_field": "原产国(地区)",
        "check_type": "fixed",
        "fixed_value": "中国(CHN)",
        "notes": "预录单必须为：中国（CHN），报关单无此字段",
    },
    {
        "id": "final_dest_country",
        "label": "最终目的国（地区）",
        "customs_field": "最终目的国（地区）",
        "pre_field": "最终目的国(地区)",
        "check_type": "fuzzy",
        "notes": "按国家名称/代码匹配",
    },
    {
        "id": "domestic_source",
        "label": "境内货源地",
        "customs_field": "境内货源地",
        "pre_field": "境内货源地",
        "check_type": "fuzzy",
        "notes": "按城市关键字匹配（映射表）",
    },
    {
        "id": "duty_exemption",
        "label": "征免",
        "customs_field": "征免",
        "pre_field": "征免",
        "check_type": "fixed",
        "fixed_value": "照章征税(1)",
        "notes": "预录单必须为：照章征税（1）",
    },
]


# ============================================================
# 规格型号特殊映射
# ============================================================
SPEC_MODEL_MAPPING = {
    "境内自主品牌": "1",
    "不确定是否享惠": "2",
}


# ============================================================
# 境内货源地映射表（预录单 → 关键字匹配）
# ============================================================
DOMESTIC_SOURCE_MAPPING = {
    "东莞市": "东莞",
    "金华市": "金华",
    "江门市": "江门",
    "宁波市": "宁波",
    "台州市": "台州",
    "滨州市": "滨州",
    "上海市": "上海",
    "菏泽市": "菏泽",
    "南平市": "南平",
    "佛山市": "佛山",
    "中山市": "中山",
    "温州市": "温州",
    "临沂市": "临沂",
    "深圳特区": "深圳特区",
    "惠州市": "惠州",
    "广州市": "广州",
    "衢州市": "衢州",
    "玉林市": "玉林",
    "泉州市": "泉州",
    "丽水市": "丽水",
    "河源市": "河源",
    "上饶市": "上饶",
}
