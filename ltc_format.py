# ltc_format.py
"""飞书卡片：日更/长更分离 + 术语解释 + 搭桥 + 诚实声明"""
from typing import List
from ltc_config import GLOSSARY, CARD_EMOJIS, QUARTERLY_CONTEXT

E = CARD_EMOJIS

def _term(term: str) -> str:
    return f"{term}（{GLOSSARY[term]}）" if term in GLOSSARY else term

def build_quarterly_block() -> tuple:
    """季度背景块 + 过期标记（非今日数据，仅作背景）"""
    ctx = QUARTERLY_CONTEXT
    lines = [f"{E['long']} 季度背景（更新于 {ctx['updated']}，非今日数据，下次更新 {ctx['next_update']}）"]
    for v in ctx["key_facts"].values():
        lines.append(f"- {v}")
    expired = False
    from ltc_config import is_expired
    if is_expired(ctx.get("updated", "")):
        expired = True
        lines.append(f"{E['warn']} 季度背景已过期，请尽快更新")
    return "\n".join(lines), expired

def format_card(data_date: str, interpretation_text: str, focus: List[dict],
                southbound: dict, valuation: List[dict], repurchase: dict,
                references: dict) -> str:
    lines = []
    lines.append(f"{E['header']} 每日资金观察 · {data_date}")
    lines.append("")
    lines.append(interpretation_text.strip())
    lines.append("")
    # ── 今日数据 ──
    lines.append(f"━━━ {E['daily']} 今日数据（当日更新） ━━━")
    for f in focus[:6]:
        acc = f.get("accum", {})
        lines.append(f"• {f['industry']}：{f['tag'] or '资金动作'}")
        lines.append(f"  {_term('超大单')} {f['sl_net']:+.1f}亿 ｜ 股价 {f['chg_pct']:+.1f}% ｜ 分位 {f['sl_percentile']:.0f}%")
        if acc.get("reasons"):
            lines.append(f"  承接：{acc['period']}（推断）｜ " + "；".join(acc["reasons"][:2]))
    if southbound:
        sb = southbound.get("southbound_net_yi")
        if sb is not None:
            label = references.get("southbound_label", "参照积累中")
            lines.append(f"• {_term('南向')}：{sb:+.1f}亿（{label}）")
    lines.append("")
    lines.append(f"{E['bridge']} 对你意味着什么：单日资金信号不等于趋势，观察是否连续多日出现。")
    lines.append("")
    # ── 长期数据 ──
    lines.append(f"━━━ {E['long']} 长期数据 ━━━")
    ok_vals = [v for v in valuation if v.get("ok")]
    lines.append("【估值温度】（基于价格位置，非 PE）")
    if ok_vals:
        for v in ok_vals[:6]:
            lines.append(f"- {v['board']}：价格处 5 年区间 {v['position_pct']:.0f}% 分位（vs 60日均线 {v['price_vs_ma60_pct']:+.1f}%）")
        failed = len(valuation) - len(ok_vals)
        if failed > 0:
            # FR-1.4：部分板块源失败不得静默消失，明确标注（复盘发现：EM 被限流时仅银行/半导体有同花顺同名板块）
            lines.append(f"- 另有 {failed} 个板块当日估值源不可用，未显示（不编造数据）")
    else:
        lines.append("- 暂无数据")
    lines.append("")
    lines.append(f"【近4周回购】（{repurchase.get('period', '')}）")
    for i in repurchase.get("items", [])[:3]:
        lines.append(f"- {i['name']}：{i['amount_yi']:.1f}亿（{i['phase']}）")
    lines.append("")
    qb, _ = build_quarterly_block()  # 过期警告由本函数内部单通道输出
    lines.append(qb)
    lines.append("")
    lines.append(f"━━━ {E['honest']} 诚实声明 ━━━")
    lines.append("资金流按交易金额大小推断资金类型（超大单≈机构），不是实名数据；唯一实名披露是季报，每季度更新。承接周期判断为推断。本内容不构成任何买卖建议。")
    return "\n".join(lines)
