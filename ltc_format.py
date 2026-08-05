# ltc_format.py
"""飞书卡片：日更/长更分离 + 术语解释 + 搭桥 + 诚实声明"""
from typing import List
from ltc_config import GLOSSARY, CARD_EMOJIS, load_quarterly_context

E = CARD_EMOJIS

def _term(term: str) -> str:
    return f"{term}（{GLOSSARY[term]}）" if term in GLOSSARY else term

def build_quarterly_block() -> tuple:
    """季度背景块 + 过期标记（非今日数据，仅作背景；内容来自季度自动刷新 JSON，缺时回退内置默认）"""
    ctx = load_quarterly_context()
    lines = [f"{E['long']} 季度背景（更新于 {ctx['updated']}，非今日数据，下次更新 {ctx['next_update']}）"]
    for v in ctx["key_facts"].values():
        lines.append(f"- {v}")
    expired = False
    from ltc_config import is_expired
    if is_expired(ctx.get("updated", "")):
        expired = True
        lines.append(f"{E['warn']} 季度背景已过期，请尽快更新")
    return "\n".join(lines), expired

def build_fund_section(focus: List[dict], southbound: dict = None,
                       repurchase: dict = None, refs: dict = None) -> str:
    """今日资金区块（ltc 卡与合并卡共用口径 — Task 6 统一双轨漂移）：
    板块归因（术语表展开 + 承接 reasons）+ 南向（术语表展开 + 参照标签）+ 回购（可选）+ 桥接行。
    回购由调用方决定：ltc 卡回购在长期数据区块 → 传 None；合并卡资金区块内 → 传实际数据。"""
    lines = []
    for f in focus[:6]:
        acc = f.get("accum", {})
        lines.append(f"• {f['industry']}：{f['tag'] or '资金动作'}")
        lines.append(f"  {_term('净额')} {f['sl_net']:+.1f}亿 ｜ 股价 {f['chg_pct']:+.1f}% ｜ 分位 {f['sl_percentile']:.0f}%")
        if acc.get("reasons"):
            lines.append(f"  承接：{acc.get('period', '')}（推断）｜ " + "；".join(acc["reasons"][:2]))
    sb = (southbound or {}).get("southbound_net_yi")
    if sb is not None:
        label = (refs or {}).get("southbound_label", "参照积累中")
        lines.append(f"• {_term('南向')}：{sb:+.1f}亿（{label}）")
    items = ((repurchase or {}).get("items") or [])[:3]
    if items:
        lines.append("• 近4周回购：" + "；".join(f"{i['name']} {i['amount_yi']:.1f}亿" for i in items))
    lines.append(f"{E['bridge']} 对你意味着什么：单日资金信号不等于趋势，观察是否连续多日出现。")
    return "\n".join(lines)

def format_card(data_date: str, interpretation_text: str, focus: List[dict],
                southbound: dict, valuation: List[dict], repurchase: dict,
                references: dict) -> str:
    lines = []
    lines.append(f"{E['header']} 每日资金观察 · {data_date}")
    lines.append("")
    lines.append(interpretation_text.strip())
    lines.append("")
    # ── 今日数据（与合并卡共用 build_fund_section 口径） ──
    lines.append(f"━━━ {E['daily']} 今日数据（当日更新） ━━━")
    lines.append(build_fund_section(focus, southbound, None, references))
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
    lines.append("资金净额按板块流入-流出推断资金方向（推断，非实名）；唯一实名披露是季报，每季度更新。承接周期判断为推断。本内容不构成任何买卖建议。")
    return "\n".join(lines)
