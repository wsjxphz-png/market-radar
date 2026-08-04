# ltc_narrative.py
"""规则保真 + AI 润色：事实清单 → DeepSeek 解读 → 校验 → 模板回退"""
import json, logging
from typing import Optional
import requests
from ltc_config import BANNED_PHRASES

logger = logging.getLogger(__name__)

def build_facts(data: dict, focus: list, refs: dict) -> dict:
    """已核验事实清单：只含实测数字与标签，AI 只能引用这里的内容"""
    facts = {
        "data_date": data.get("data_date", ""),
        "focus": [{
            "industry": f.get("industry"), "tag": f.get("tag"),
            "sl_net": f.get("sl_net"), "chg_pct": f.get("chg_pct"),
            "sl_percentile": f.get("sl_percentile"),
            "accum_period": f.get("accum", {}).get("period"),
            "accum_reasons": f.get("accum", {}).get("reasons", []),
        } for f in focus],
        "southbound": {
            "value": (data.get("southbound") or {}).get("southbound_net_yi"),
            "ref_label": refs.get("southbound_label", "参照积累中"),
        },
        "repurchase_top": [{"name": i["name"], "amount_yi": i["amount_yi"], "phase": i["phase"]}
                           for i in (data.get("repurchase") or {}).get("items", [])[:3]],
        "valuation_note": data.get("valuation_note", ""),
    }
    return facts

def template_interpretation(facts: dict) -> str:
    """模板回退：只填核验过的数字"""
    parts = []
    for f in facts.get("focus", [])[:3]:
        action = {"逆势吸筹嫌疑": "价格在跌但大资金在买",
                  "派发嫌疑": "价格在涨但大资金在卖",
                  "资金关注": "大资金集中流入",
                  "资金撤离": "大资金在撤离"}.get(f["tag"], "资金动作明显")
        parts.append(f"{f['industry']}（{action}，超大单{f['sl_net']:+.1f}亿，{f['accum_period']}）")
    sb = facts.get("southbound", {})
    sb_txt = f"南向资金{sb['value']:+.1f}亿（{sb.get('ref_label','参照积累中')}）" if sb.get("value") is not None else "南向数据暂不可用"
    if not parts:
        return f"今日各板块资金动作分散，无明显集中方向。{sb_txt}。单日资金行为不代表趋势，建议结合多日观察。（数据日期 {facts['data_date']}）"
    return f"今日资金焦点：{'；'.join(parts)}。{sb_txt}。以上均为推断，资金流不代表实名持仓。（数据日期 {facts['data_date']}）"

def validate_output(text: str) -> bool:
    for w in BANNED_PHRASES:
        if w in text:
            return False
    return True

def call_deepseek(facts: dict, api_key: str, model: str = "deepseek-chat") -> Optional[str]:
    if not api_key:
        return None
    system = (
        "你是《每日资金观察》的解读员。读者是零基础股票小白，不提供任何买卖建议。"
        "只允许引用给定事实中的数字和标签，禁止编造任何数字、新闻、传闻或人物观点。"
        "禁止出现买入/卖出/加仓/减仓等操作指令。视角放在中长期：单日信号要说明持续性未知。"
        "用大白话，100-150 字，分两段：今天发生了什么；对你意味着什么。"
    )
    user = json.dumps(facts, ensure_ascii=False)
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}],
                "temperature": 0.4, "max_tokens": 300},
            timeout=60)
        if resp.status_code != 200:
            logger.warning("DeepSeek HTTP %s: %s", resp.status_code, resp.text[:120])
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if not validate_output(content):
            logger.warning("DeepSeek output rejected by validation")
            return None
        return content[:300]
    except Exception as e:
        logger.warning("DeepSeek call failed: %s", str(e)[:120])
        return None

def interpretation(facts: dict, api_key: str = "", model: str = "deepseek-chat") -> str:
    """AI 解读 → 校验失败/调用失败 → 模板回退"""
    ai = call_deepseek(facts, api_key, model)
    if ai:
        return ai
    return template_interpretation(facts)
