"""
AI 辅助模块：可配置 provider（智谱/DeepSeek/OpenAI 等）
用于文本提取失败时的兜底辅助
"""
import json
import base64
from src.config import get_ai_config


def _call_zhipu_text(prompt: str, text_snippet: str, config: dict) -> str:
    """调用智谱文本模型"""
    try:
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=config["api_key"])
        response = client.chat.completions.create(
            model=config["text_model"],
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text_snippet},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI调用失败: {str(e)}"


def _call_zhipu_vision(prompt: str, image_base64: str, config: dict) -> str:
    """调用智谱视觉模型"""
    try:
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=config["api_key"])
        response = client.chat.completions.create(
            model=config["vision_model"],
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ]},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI视觉调用失败: {str(e)}"


def _call_deepseek_text(prompt: str, text_snippet: str, config: dict) -> str:
    """调用 DeepSeek 文本模型"""
    import httpx
    try:
        response = httpx.post(
            f"{config['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json={
                "model": config["text_model"],
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text_snippet},
                ],
            },
            timeout=30,
        )
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"DeepSeek调用失败: {str(e)}"


def ai_extract_fields(text_snippet: str, field_names: list) -> dict:
    """
    用 AI 文本模型辅助提取失败的字段
    text_snippet: 相关文本片段
    field_names: 需要提取的字段名列表
    返回: {字段名: 提取值}
    """
    config = get_ai_config()
    provider = config["provider"]

    prompt = f"""从以下报关单/预录单文本中提取指定字段的值。
需要提取的字段: {', '.join(field_names)}

请以 JSON 格式返回结果，例如:
{json.dumps({f: "提取的值" for f in field_names[:2]}, ensure_ascii=False)}

如果某个字段在文本中找不到，对应值设为空字符串。
只返回 JSON，不要其他内容。"""

    if provider == "zhipu":
        result_text = _call_zhipu_text(prompt, text_snippet, config)
    elif provider == "deepseek":
        result_text = _call_deepseek_text(prompt, text_snippet, config)
    else:
        return {f: "" for f in field_names}

    # 解析 JSON 结果
    try:
        # 尝试提取 JSON 部分
        json_match = __import__("re").search(r"\{[^}]+\}", result_text, __import__("re").DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(result_text)
    except json.JSONDecodeError:
        return {f: "" for f in field_names}


def ai_vision_extract(image_base64: str, doc_type: str) -> dict:
    """
    用 AI 视觉模型从 PDF 页面图像中提取字段（兜底）
    image_base64: 页面图像的 base64 编码
    doc_type: 文档类型 (customs_declaration / pre_recording / contract)
    返回: {字段名: 值}
    """
    config = get_ai_config()
    provider = config["provider"]

    prompts = {
        "customs_declaration": """请识别这张报关单图像，提取以下字段并以 JSON 格式返回：
发货单位, 买方, 经营单位, 合同协议号, 包装种类, 运输方式, 贸易方式, 贸易国,
件数, 毛重(千克), 净重(千克), 成交方式, 征免性质, 运抵国(地区), 指运港,
以及每个商品项目的：项号, 商品编码, 商品名称, 规格型号, 数量及单位, 最终目的国, 单价, 总价, 币制, 境内货源地, 征免""",
        "pre_recording": """请识别这张预录单图像，提取以下字段并以 JSON 格式返回：
境内发货人, 境外收货人, 生产销售单位, 合同协议号, 出境关别, 包装种类, 运输方式,
监管方式, 贸易国(地区), 件数, 毛重(千克), 净重(千克), 成交方式, 征免性质,
运抵国(地区), 指运港, 离境口岸, 随附单证及编号, 标记唛码及备注,
以及每个商品项目的：项号, 商品编码, 商品名称, 规格型号, 数量及单位, 单价, 总价, 币制, 原产国(地区), 最终目的国(地区), 境内货源地, 征免""",
        "contract": """请识别这张合同图像，提取买方(Buyers)的完整名称，以 JSON 格式返回：{"买方": "xxx"}""",
    }

    prompt = prompts.get(doc_type, "请识别这张图像中的关键信息，以 JSON 格式返回。")

    if provider == "zhipu":
        result_text = _call_zhipu_vision(prompt, image_base64, config)
    else:
        return {}

    try:
        json_match = __import__("re").search(r"\{[\s\S]+\}", result_text)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(result_text)
    except json.JSONDecodeError:
        return {}
