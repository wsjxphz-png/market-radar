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
                       repurchase: dict = None, refs: dict = None,
                       flow_df=None) -> str:
    """今日资金区块（ltc 卡与合并卡共用口径 — Task 6 统一双轨漂移）：
    板块归因（术语表展开 + 承接 reasons）+ 南向（术语表展开 + 参照标签）+ 回购（可选）+ 桥接行。
    回购由调用方决定：ltc 卡回购在长期数据区块 → 传 None；合并卡资金区块内 → 传实际数据。

    2026-08-11 审计（用户要求口径一致，非声明）：板块名统一东财口径 + 净额显示
    东财聚合值（与板块判断资金行同源同算法，flow_df 完整时全量聚合，否则 focus 内
    近似聚合）；括号内只列 focus 前 6 的焦点子行业明细，是构成示例而非全量，
    求和不必等于聚合总量。"""
    from math import isfinite
    from val_config import em_name_for
    lines = []
    # ── 东财聚合净额（与 _flow_sl_map 同算法：逐行 THS→东财映射后求和）──
    agg = {}
    if flow_df is not None and hasattr(flow_df, "iterrows"):
        for _, row in flow_df.iterrows():
            em = em_name_for(str(row.get("industry", "")))
            if em:
                v = float(row.get("super_large_net_yi", 0) or 0)
                if isfinite(v):
                    agg[em] = agg.get(em, 0.0) + v
    else:
        for f in focus:
            em = em_name_for(f.get("industry", ""))
            if em:
                v = float(f.get("sl_net", 0) or 0)
                if isfinite(v):
                    agg[em] = agg.get(em, 0.0) + v
    # ── 按东财板块分组 focus（同板块子行业合并显示；行内带各自 em 供分支判断）──
    groups = {}
    for f in focus[:6]:
        industry = f.get("industry", "")
        groups.setdefault(em_name_for(industry) or industry, []).append(
            (em_name_for(industry), f))
    for key, rows in groups.items():
        em, first = rows[0]
        acc = first.get("accum", {})
        if em:
            # 东财板块行：聚合净额（与板块判断同值）+ focus 内细分构成
            total = agg.get(key)
            detail = " / ".join(f"{f['industry']} {f['sl_net']:+.1f}" for _, f in rows)
            # 聚合行 tag 按聚合净额符号定（子行业 tag 可能与聚合方向相反，如
            # 半导体 -18.2 + 电子化学品 +23.53 = +5.3 转正仍标"资金流出"会自相矛盾）
            agg_tag = ("资金流入" if (total or 0) > 0
                       else "资金流出" if (total or 0) < 0 else "资金动作")
            lines.append(f"• {key}：{agg_tag}")
            if total is not None:
                suffix = f"（东财聚合；核心子行业：{detail}）" if len(rows) > 1 else "（东财聚合）"
                lines.append(f"  {_term('净额')} {total:+.1f}亿{suffix} ｜ 股价 {first['chg_pct']:+.1f}% ｜ 分位 {first['sl_percentile']:.0f}%")
            else:
                lines.append(f"  {_term('净额')}（细分：{detail}）")
        else:
            # 无东财映射（不在 12 板块内）：原样直显
            lines.append(f"• {key}：{first['tag'] or '资金动作'}")
            lines.append(f"  {_term('净额')} {first['sl_net']:+.1f}亿｜ 股价 {first['chg_pct']:+.1f}% ｜ 分位 {first['sl_percentile']:.0f}%")
        # 2026-08-11 审计：聚合行（多子行业合并）不显示承接——first 的承接文本
        # 是子行业级推断，挂在聚合板块上牛头不对马嘴
        if acc.get("reasons") and (not em or len(rows) == 1):
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
    lines.append("【估值温度】（基于价格位置，非 PE；2026-08-11 审计标注：东财板块K线被封时"
                 "为同花顺子行业K线口径——如医药生物=化学制药/中药）")
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
