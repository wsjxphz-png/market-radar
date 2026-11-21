# -*- coding: utf-8 -*-
"""把状态机结果写成飞书卡片 markdown。

排版原则：
- 先给结论，再给依据；每块之间用分隔线，手机上也能一眼扫完。
- 关键标签加粗；动作前用数字编号；不堆长段落。
- 主文不出现 top_adj、分位、MA200 这类黑话；必须出现时立刻解释。
"""
from __future__ import annotations

import pandas as pd

from config import DICTIONARY
from state import Analysis, Mag7State, MarketState

SEP = "━━━━━━━━━━━━━━━"


def _num(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.2f}"


def _icon(stage: int) -> str:
    return {6: "🟢", 5: "🟢", 4: "🟡", 3: "🟠", 2: "🔴", 1: "🔴"}.get(stage, "⚪")


def _bold_action(text: str) -> str:
    """把“防守：不新买”变成“**防守：** 不新买”，只处理第一个冒号。"""
    if "：" in text:
        head, tail = text.split("：", 1)
        return f"**{head}：** {tail}"
    return text


def _market_block(s: MarketState) -> str:
    label = s.label
    if s.stage == 4 and s.branch == "pullback":
        label = "回调中（长期仍强）"
    gap = s.gap200
    if pd.isna(gap):
        where = "数据不足，先看价格本身。"
    elif gap >= 0:
        where = f"比一年平均成本（200 日线）高 {abs(gap):.1%}"
    else:
        where = f"比一年平均成本（200 日线）低 {abs(gap):.1%}"

    dd = "" if pd.isna(s.drawdown) else f"，比最近高点低 {abs(s.drawdown):.1%}"
    return "\n".join([
        f"{_icon(s.stage)} **{s.market.emoji} {s.market.name}** · {label}",
        f"**现在**：价格 {_num(s.close)}，{where}{dd}。",
        f"**这意味着**：{s.meaning}",
        f"**现在怎么做**：{s.action}",
    ])


def _mag7_line(x: Mag7State) -> str:
    gap = x.gap200
    if pd.isna(gap):
        side = "数据不足"
    elif gap >= 0:
        side = f"在 200 日线上方 {abs(gap):.1%}"
    else:
        side = f"在 200 日线下方 {abs(gap):.1%}"
    label = x.label
    if x.stage == 4 and pd.notna(x.ma200) and x.close > x.ma200:
        label = "回调中（长期仍强）"
    return f"**{x.symbol} {x.name}**：{label}（{side}）→ {x.action}"


def _mag7_block(a: Analysis) -> str:
    lines = [f"**🏆 七姐妹：{a.mag7_breadth}/7 在 200 日线上方**（只看趋势，不预测）"]
    lines.extend(_mag7_line(x) for x in a.mag7)
    breadth = a.mag7_breadth
    if breadth < 4:
        read = "只有少数还在一年平均成本上方，说明上涨不健康，别把指数当成七只都强。"
    elif breadth <= 5:
        read = "强弱参半，只能做还在 200 日线上方的，弱的等它自己修好。"
    else:
        read = "多数在一年平均成本上方，内部比较健康；但个股仍要各自看止损。"
    lines.append(f"**广度**：{breadth}/7 在 200 日线上方。{read}")
    return "\n".join(lines)


def _risk_block(a: Analysis) -> str:
    if not a.risk_flags:
        return "**⚠️ 风险灯**：目前没有明显亮灯。\n趋势、信用、波动、广度暂时没有同时恶化；不代表不会跌，只是没有明显预警。"
    lines = [f"**⚠️ 风险灯：{len(a.risk_flags)} 项亮着**"]
    lines.extend(f"- **{cat}**：{text}" for cat, text in a.risk_flags)
    lines.append("这些是“风险在积累”的迹象，不是“明天就跌”的预测。")
    lines.append("**4 盏或以上**：只留最强，不新开；**2 盏或以下**：可以按动作正常执行。")
    return "\n".join(lines)


def _actions(a: Analysis) -> list[str]:
    by = a.by_key
    qqq = by.get("qqq")
    spy = by.get("spy")
    semis = by.get("soxq")
    china = [by[k] for k in ("kweb", "fxi") if k in by]
    out: list[str] = []

    if qqq is not None:
        if qqq.stage == 0:
            out.append("QQQ 数据不足，今天不做判断。")
        elif qqq.stage <= 2:
            out.append(
                f"不新买、不加杠杆；等 QQQ 站上 20 日线（约 {qqq.ma20:.0f}）并连续 5 天不破低点，再考虑试探。"
            )
        elif qqq.stage == 3:
            out.append("QQQ 刚止跌：最多试 1/3 仓位，止损放前低；不要一次买满。")
        elif qqq.stage == 4:
            out.append("QQQ 在修复：按规则从 1/3 加到 2/3，止损放 200 日线。")
        else:
            out.append("QQQ 趋势确认/健康：可以按规则持有或接近满仓，跌破 200 日线再减。")

    if spy is not None and 0 < spy.stage <= 2:
        out.append("大盘也在 200 日线下方：个股反弹容易被大盘拖回去，别用个股的强势掩盖整体。")
    elif spy is not None and spy.stage == 0:
        out.append("SPY 数据不足，今天不做判断。")

    if semis is not None:
        if semis.stage == 0:
            out.append("半导体数据不足，今天不做判断。")
        elif semis.stage <= 2:
            out.append("半导体在 200 日线下方：这条线不碰，等 SOXQ 先站上 20 日线。")
        elif semis.stage == 3:
            out.append("半导体刚止跌：只能小仓试，半导体波动大，仓位减半。")

    strong = [x.symbol for x in a.mag7 if x.stage >= 5]
    pullback = [x.symbol for x in a.mag7 if x.stage == 4]
    waiting = [x.symbol for x in a.mag7 if x.stage == 3]
    weak = [x.symbol for x in a.mag7 if x.stage in (1, 2)]
    unknown = [x.symbol for x in a.mag7 if x.stage == 0]
    if strong or pullback or waiting or weak or unknown:
        parts = []
        if strong:
            parts.append(f"只留强的（{'、'.join(strong)}）")
        if pullback:
            label = "观察，不新买" if qqq is not None and qqq.stage <= 2 else "观察/小仓"
            parts.append(f"{label}（{'、'.join(pullback)}）")
        if waiting:
            parts.append(f"等确认（{'、'.join(waiting)}）")
        if weak:
            parts.append(f"弱的（{'、'.join(weak)}）减仓，不摊平")
        if unknown:
            parts.append(f"数据不足（{'、'.join(unknown)}）")
        out.append("七姐妹：" + "；".join(parts) + "。")

    if china and all(0 < s.stage <= 3 for s in china):
        out.append("中概：KWEB/FXI 还在 200 日线下方，总仓位不超过 10–15%，单票 5–8%；"
                   "政策大新闻先减半；等 20 日线收复 + 政策信号再加。")

    out.append("以上都是规则输出，不是每天猜涨跌；下周只有状态变化时才需要改动作。")
    return out


def _changes(a: Analysis) -> list[str]:
    by = a.by_key
    out: list[str] = []
    qqq = by.get("qqq")
    out.append(f"**QQQ**：{qqq.change}" if qqq else "**QQQ**：数据不足，等数据。")
    semis = by.get("soxq")
    out.append(f"**半导体**：{semis.change}" if semis else "**半导体**：数据不足，等数据。")
    china = by.get("kweb") or by.get("fxi")
    out.append(
        f"**中概**：{china.change} 另外，中国出台刺激/监管政策时，以政策为准，不等均线。"
        if china else "**中概**：数据不足，等数据。"
    )
    out.append(f"**风险灯**：从现在的 {len(a.risk_flags)} 项降到 3 项或以下 = 风险在释放；升到 5 项或以上 = 风险在加速积累。")
    return out


def _dictionary() -> list[str]:
    lines = ["**📖 小白词典**（看不懂的名词看这里）"]
    for term, plain in DICTIONARY:
        lines.append(f"- **{term}**：{plain}")
    lines.append("- **阶段 1-6**：1=下跌；2=深度下跌；3=止跌观察；4=回调/修复；5=趋势确认；6=趋势健康。")
    return lines


def _stance_line(a: Analysis) -> str | None:
    """把“防守/试仓/加仓/持有”展开成一句具体动作。"""
    us = a.us_block
    if not us:
        return None
    if all(s.stage == 0 for s in us):
        return "👉 **数据不足，先不判断**；等数据恢复。"
    if all(s.stage >= 5 for s in us):
        return "👉 **“持有”具体是：** 仓位 80-100%，不追高，单票 ≤15%，跌破 200 日线减半。"
    if any(s.stage >= 4 for s in us):
        return "👉 **“加仓”具体是：** 只加 50 日线在 200 日线上方的，单票 ≤15%，止损 200 日线。"
    if any(s.stage == 3 for s in us):
        return ("👉 **“试仓”具体是：** 用计划仓位的 1/3（约 20-30%），只买最强 1-3 只，"
                "单票 ≤10%，止损放 60 日低点下方 2-3%。")
    return ("👉 **“防守”具体是：** 不新买；跌破 200 日线的持仓减半/清掉；"
            "现金提高到 30-50%；等连续 5 天站稳 20 日线再试。")


def _weekly_block(a: Analysis) -> list[str]:
    """周报专用：本周（近 5 个交易日）涨跌、周内位置、10 周线。"""
    lines = ["**📈 本周变化（近 5 个交易日）**"]
    for w in a.weekly:
        if w.close_pos <= 0.1:
            pos = "收在周内最低"
        elif w.close_pos >= 0.9:
            pos = "收在周内最高"
        else:
            pos = f"收在周内区间 {w.close_pos:.0%}"
        ma = ""
        if pd.notna(w.ma10w):
            side = "上方" if w.close > w.ma10w else "下方"
            ma = f"，在 10 周线{side} {abs(w.close / w.ma10w - 1):.1%}"
        lines.append(f"- **{w.name}**：本周 {w.week_return:+.1%}，{pos}{ma}")
    extras = []
    if pd.notna(a.vix_week_chg):
        extras.append(f"VIX 本周 {a.vix_week_chg:+.0f}")
    if pd.notna(a.credit_week_chg):
        extras.append(f"信用利差本周 {a.credit_week_chg:+.0f}bp")
    if extras:
        lines.append("- " + "；".join(extras))
    below = sum(1 for w in a.weekly if pd.notna(w.ma10w) and w.close < w.ma10w)
    if a.weekly and below >= len(a.weekly) * 0.6:
        read = "周线也在走弱，不是只有日线的噪音；防守要继续。"
    elif a.weekly and below <= len(a.weekly) * 0.3:
        read = "周线在修复，日线的反弹有周线支持；可以按阶段慢慢加。"
    else:
        read = "周线分化，按每个市场的动作分别处理。"
    lines.append(f"**这意味着**：{read}")
    return lines


def _risk_trend_block(a: Analysis, detailed: bool = False) -> list[str]:
    """把广度、信用、波动、趋势的 20 天变化摊开，让用户看到判断依据。"""
    if not a.risk_trend:
        return ["**📊 广度/信用/波动**：数据不足，暂时无法判断。"]
    lines = ["**📊 广度、信用、波动（过去 20 天）**"]
    for x in a.risk_trend:
        mark = "⚠️" if x.worsening else "✅"
        suffix = f" — {x.note}" if x.note else ""
        lines.append(f"- {mark} **{x.label}**：{x.detail}{suffix}")
    total = len(a.risk_trend)
    w = a.risk_worsening
    if w >= total * 0.6:
        read = "多数在恶化 → 风险在积累，不是刚发生；现在要防守。"
    elif w <= total * 0.3:
        read = "多数在改善 → 风险在释放；可以开始找机会，但等趋势确认。"
    else:
        read = "好坏参半 → 风险没有单边恶化，按每个市场的动作分别处理。"
    lines.append(f"**结论**：{total} 项里 {w} 项在恶化，{read}")
    if detailed:
        lines.append("说明：这不是预测哪一天跌；它告诉你风险在变好还是变坏，用来提前收紧或放松仓位。")
    return lines


def render_brief(a: Analysis) -> str:
    """每天收盘后的短卡：状态、依据、动作、关键价位，手机一屏左右。"""
    date_str = a.data_date.strftime("%Y.%m.%d")
    qqq = a.by_key.get("qqq")
    spy = a.by_key.get("spy")
    lines = [
        f"**一句话：** {a.headline}",
    ]
    stance = _stance_line(a)
    if stance:
        lines.append(stance)
    if a.quality_warnings:
        lines.append("⚠️ **数据质量**：" + "；".join(a.quality_warnings))
    lines.extend(["", "**📊 五个市场**"])

    for s in a.states:
        gap = s.gap200
        if pd.isna(gap):
            where = "数据不足"
        else:
            side = "下方" if gap < 0 else "上方"
            where = f"200日线{side} {abs(gap):.1%}"
        dd = "" if pd.isna(s.drawdown) else f"，距高点 -{abs(s.drawdown):.1%}"
        lines.append(f"{_icon(s.stage)} **{s.market.name} {s.market.symbol}** · {s.label}（{where}{dd}）")

    below = [s for s in a.states if pd.notna(s.ma200) and s.close < s.ma200]
    if a.states and len(below) == len(a.states):
        meaning = "所有市场都在 200 日线下方，趋势还没修好；反弹先当反弹，不当反转。"
    elif a.states and len(below) >= len(a.states) / 2:
        meaning = "多数市场在 200 日线下方，整体偏防守；只有少数市场还在趋势里。"
    else:
        meaning = "市场之间在分化，按每个市场的动作分别处理，不要用一个市场的强弱代表全部。"
    lines.append(f"👉 **这意味着：** {meaning}")
    lines.append(SEP)
    lines.extend(_risk_trend_block(a, detailed=False))

    strong = [x.symbol for x in a.mag7 if x.stage >= 5]
    pullback = [x.symbol for x in a.mag7 if x.stage == 4]
    waiting = [x.symbol for x in a.mag7 if x.stage == 3]
    weak = [x.symbol for x in a.mag7 if x.stage in (1, 2)]
    unknown = [x.symbol for x in a.mag7 if x.stage == 0]
    lines.append(SEP)
    lines.append(f"**🏆 七姐妹：{a.mag7_breadth}/7 在 200 日线上方**")
    groups = [
        ("强", strong), ("回调", pullback), ("等确认", waiting),
        ("弱", weak), ("数据不足", unknown),
    ]
    lines.append(" ｜ ".join(f"**{name}：** {'、'.join(items)}" for name, items in groups if items) or "数据不足")
    if qqq is not None and 0 < qqq.stage <= 2:
        lines.append("大盘在防守阶段：**强的不加，回调的观察，弱的减**；不摊平。")
    else:
        lines.append("**强的可持有，回调的观察/小仓，弱的减**；不摊平。")

    lines.append(SEP)
    lines.append("**🧭 今天怎么做**")
    acts = [x for x in _actions(a) if not x.startswith("以上都是")]
    for i, x in enumerate(acts[:5], 1):
        lines.append(f"{i}. {_bold_action(x)}")

    if qqq is not None and all(pd.notna(x) for x in (qqq.ma20, qqq.ma50, qqq.ma200, qqq.low60)):
        lines.append(SEP)
        levels = f"QQQ 20日线 {qqq.ma20:.0f} / 50日线 {qqq.ma50:.0f} / 200日线 {qqq.ma200:.0f} / 60日低点 {qqq.low60:.0f}"
        if spy is not None and pd.notna(spy.ma200):
            levels += f"；SPY 200日线 {spy.ma200:.0f}"
        lines.append(f"**📍 关键价位**：{levels}")

    lines.append(SEP)
    lines.append("**⚠️ 什么会改变**")
    lines.append(f"- **QQQ**：{qqq.change if qqq is not None else '等数据。'}")
    lines.append(f"- **风险灯**：从 {len(a.risk_flags)} 项降到 3 项或以下 = 风险在释放；升到 5 项或以上 = 风险在加速。")

    lines.append(SEP)
    lines.append("**📖 200日线**=过去一年平均成本；**20日线**=过去一个月平均成本；站上 20 日线是止跌第一步。")
    lines.append("规则输出，不是投资建议；按自己的风险承受度调整仓位。")
    return "\n".join(lines)


def render_text(a: Analysis) -> str:
    date_str = a.data_date.strftime("%Y.%m.%d")
    lines = [f"**数据日期**：{date_str}（美股最近一个交易日）"]
    if a.quality_warnings:
        lines.append("⚠️ **数据质量**：" + "；".join(a.quality_warnings))
    lines.extend(["", f"**一句话：** {a.headline}"])
    stance = _stance_line(a)
    if stance:
        lines.append(stance)
    lines.append(SEP)
    lines.append("**📊 市场状态**")
    lines.extend(_market_block(s) for s in a.states)
    lines.append(SEP)
    lines.extend(_risk_trend_block(a, detailed=True))
    if a.weekly:
        lines.append(SEP)
        lines.extend(_weekly_block(a))
    lines.append(SEP)
    lines.append(_mag7_block(a))
    lines.append(SEP)
    lines.append(_risk_block(a))
    lines.append(SEP)
    lines.append("**🧭 本周动作（按顺序做）**")
    lines.extend(f"{i}. {_bold_action(x)}" for i, x in enumerate(_actions(a), 1))
    if any(s.stage <= 2 for s in a.us_block):
        qqq = a.by_key.get("qqq")
        trigger = f"（约 {qqq.ma20:.0f}）" if qqq is not None and pd.notna(qqq.ma20) else ""
        lines.append("")
        lines.append("**🚪 如果转好，怎么进（现在还没到）**")
        lines.append(f"1. 等 QQQ 连续 5 天站稳 20 日线{trigger}。")
        lines.append("2. 先用计划仓位的 1/3（约 20-30%）。")
        lines.append("3. 只买 200 日线上方、相对最强的 1-3 只；单票 ≤10%。")
        lines.append("4. 止损放 60 日低点下方 2-3%；跌破就走，不补仓。")
    lines.append(SEP)
    lines.append("**⚠️ 什么会改变这个判断**")
    lines.extend(f"- {x}" for x in _changes(a))
    lines.append(SEP)
    lines.extend(_dictionary())
    lines.append("")
    lines.append("规则输出，不是投资建议；市场没有百分百，按自己的风险承受度调整仓位。")
    return "\n".join(lines)


def card(a: Analysis, weekly: bool = False) -> dict:
    """飞书 interactive 卡片：标题 + 颜色 + markdown 正文。"""
    kind = "周报" if weekly else "日报"
    date_str = a.data_date.strftime("%m.%d")
    us = a.us_block
    if not us or all(s.stage == 0 for s in us):
        template = "grey"
    elif any(s.stage >= 4 for s in us):
        template = "green"
    elif any(s.stage == 3 for s in us):
        template = "orange"
    else:
        template = "red"
    return {
        "title": f"🇺🇸 美股状态{kind} · {date_str}",
        "content": render_text(a) if weekly else render_brief(a),
        "template": template,
    }
