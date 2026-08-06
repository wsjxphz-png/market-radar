"""板块总览说明层：事实翻译 / 名词解释 / 场景裁决器（全部规则化模板，无 AI）

三层结构：事实（机器可核验数据）→ 说明（数据含义翻译+名词解释，学习载体）
→ 判断（基于事实的推论，标"推断"，可缺席）。

场景分流（判断不冲突的核心）：
- 趋势定短线：趋势交易论（趋势第一性 / 永不下跌补仓 / 520战法=5日线+20日线金叉买死叉卖 /
  不破20日线不动 / 高位放量滞涨=出局）——参考 .claude/skills/趋势交易论/
- 估值定定投：微笑曲线（跌时多买份额→涨时份额升值）——参考 ch20-ETF定投与指数投资.md
- 资金做确认：连续 3 日净流入/流出为方向确认，单日不作数
冲突时按用途分流，两场景各管各的，不互相覆盖；无法裁决输出"无法判断：…，等待证据"。

趋势状态词表（TREND_STATE，Task 5 渲染层负责从 sector_monitor trend_phase 映射）：
- above20_rising          20 日线上方上升期（rally/bullish 对齐）
- below20_falling         20 日线下方下降期（downtrend 对齐）
- around20_oscillation    20 日线附近震荡期（oscillation/topping/bottoming/mixed 对齐）
- unknown                 趋势状态数据不足
"""
import logging
from typing import Dict, List, Optional
from val_config import PCT_LOW, PCT_HIGH

logger = logging.getLogger(__name__)

# synthesize 事实契约 key：缺失时降级并告警（Task 4 Issue 3 加固——渲染层组装错位
# 会在卡片上静默降级而非报错，日志必须显式暴露缺 key；sl_net 缺席属"数据不可用"合法，不告警）
_CONTRACT_KEYS = ("board", "trend_state", "valuation", "fund_state",
                  "main_pct", "metric", "years", "terms")


def _facts_get(facts: dict, key: str, default):
    """事实取数：key 缺失（契约违反）→ logger.warning + 降级；值为 None 属数据不可用，不告警"""
    if key not in facts:
        logger.warning("synthesize 契约缺 key: %s（降级为 %r；board=%s）",
                       key, default, facts.get("board", "?"))
    return facts.get(key, default)

# ============================================================
# GLOSSARY：名词大白话解释（首次出现才渲染，判定归渲染层）
# 板块总览词汇自持；LTC 卡片术语（超大单/主力/净额等）仍在 ltc_config.GLOSSARY
# ============================================================
GLOSSARY = {
    "PE": "市盈率：市值 ÷ 年利润，可理解为'多少年回本'，数值越低越便宜",
    "PB": "市净率：市值 ÷ 净资产，衡量花多少钱买 1 元净资产，数值越低越便宜",
    "分位": "百分位：当前数值在历史区间里的排名位置（0-100），越低代表越便宜",
    "净流入": "买入金额 - 卖出金额为正，代表资金整体在买（净流入）；为负即净流出",
    "20日线": "最近 20 个交易日收盘价的平均线，趋势票的生命线——不破说明趋势还在",
    "5日线": "最近 5 个交易日收盘价的平均线，短线情绪的分界线",
    "微笑曲线": "定投微笑曲线：跌的时候坚持买，成本越摊越低，涨回来时赚得更多——跌时多买，涨时少买",
    "520战法": "5 日线与 20 日线金叉（5 日线上穿 20 日线）买入、死叉（下穿）卖出——简单有效",
    "趋势第一性": "方向大于技巧：顺势而为，不与趋势为敌——趋势不对，努力白费",
    "永不下跌补仓": "下跌时不要加钱摊低成本（散户最致命的操作）——越补越套",
    "高位放量滞涨": "涨到高位后放大量但价格不涨，是最明确的派发（出货）信号",
}

# 趋势状态 → 大白话翻译（对 TREND_STATE 词表）
TREND_STATE_EXPLAIN = {
    "above20_rising": "20 日线上方上升期 = 短线处于顺势",
    "below20_falling": "20 日线下方下降期 = 短线处于逆势",
    "around20_oscillation": "20 日线附近震荡 = 短线方向不明",
    "unknown": "趋势状态数据不足",
}

# 资金状态 → 大白话翻译（对 compute_fund_state 词表）
FUND_STATE_EXPLAIN = {
    "inflow_confirm": "连续 3 日主力净流入 = 有持续性的买入，不是一天的热闹",
    "outflow_confirm": "连续 3 日主力净流出 = 有持续性的卖出，不是一天的撤退",
    "single_day": "仅单日主力净流入/流出 = 一天的热闹，方向未确认",
    "cold_start": "资金留痕不足 3 日 = 状态积累中，方向未确认",
    "unknown": "资金数据不可用",
}


# ============================================================
# 事实翻译（说明层）：把机器数据翻译成人类语言，不掺判断
# ============================================================

def explain_percentile(pct: Optional[float], metric: str,
                       years: Optional[float] = None) -> str:
    """分位翻译："PE 分位 15% = 过去 10 年只有 15% 的时间比现在便宜"

    years 为分位数据实际窗口年数（数据窗口诚实性：指数历史可能不足十年，
    不得虚构窗口；未提供时只说"历史区间"）。pct 为 None → 数据不可用。"""
    if pct is None:
        return "估值分位数据不可用"
    pct_s = f"{pct:.0f}%"
    if years is not None:
        window = f"过去 {_fmt_years(years)} 年"
    else:
        window = "历史区间内"
    return f"{metric} 分位 {pct_s} = {window}只有 {pct_s} 的时间比现在便宜"


def _fmt_years(years: float) -> str:
    """年数显示：整数不带小数（10→"10"），否则保留一位（0.5→"0.5"）"""
    return f"{years:.0f}" if float(years) == int(years) else f"{years:.1f}"


def explain_fund_state(fund_state: str, sl_net: Optional[float]) -> str:
    """资金状态翻译："连续 3 日净流入 = 有持续性的买入，不是一天的热闹"

    sl_net 为当日主力净额（亿元），有值则附上具体数字供核验；None 不虚构。"""
    base = FUND_STATE_EXPLAIN.get(fund_state)
    if base is None:
        return "资金状态未知"
    if sl_net is not None and fund_state in ("inflow_confirm", "outflow_confirm",
                                             "single_day"):
        sign = "+" if float(sl_net) >= 0 else ""
        return f"{base}（今日净额 {sign}{float(sl_net):.1f} 亿）"
    return base


def explain_trend(trend_state: str) -> str:
    """趋势翻译："20 日线上方上升期 = 短线处于顺势"（对 TREND_STATE 词表）"""
    return TREND_STATE_EXPLAIN.get(trend_state, "趋势状态未知")


def glossary_for(terms: List[str]) -> str:
    """首次出现的名词大白话解释；按给定顺序去重输出，未知词跳过不虚构"""
    seen, lines = set(), []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        desc = GLOSSARY.get(t)
        if desc:
            lines.append(f"{t}：{desc}")
    return "\n".join(lines)


# ============================================================
# 场景裁决器（判断层）：全部标"推断"，可缺席
# ============================================================

def judge_short_term(trend_state: str, valuation: str) -> str:
    """短线判断（趋势交易论）：趋势第一性——上升顺势持有、下降不抄底、震荡等待

    valuation 仅用于排除"估值便宜所以抄底"的诱惑：下降趋势中估值便宜
    也不抄底（永不下跌补仓），绝不给定投口径的结论。"""
    if trend_state == "above20_rising":
        line = "短线（推断）：顺势持有，不破 20 日线不离场（趋势第一性）"
        if valuation == "贵":
            line += "；估值贵不改趋势，破位才走"
        return line
    if trend_state == "below20_falling":
        line = "短线（推断）：不抄底——下降趋势中永不下跌补仓，等反转信号（站上 20 日线/520 金叉）再进"
        if valuation == "便宜":
            line += "；估值便宜也不抄底，跌市无底"
        return line
    if trend_state == "around20_oscillation":
        return "短线（推断）：等待——震荡中方向不明，等 20 日线方向明确（520 金叉/死叉）再行动"
    return "无法判断：趋势状态不明，等待证据（推断）"


def judge_dca(valuation: str) -> str:
    """定投判断（微笑曲线）：便宜多买、合理按计划、贵则减量；观察=无法判断"""
    if valuation == "便宜":
        return "定投（推断）：可加码——估值便宜时多买份额（微笑曲线：跌时多买）"
    if valuation == "合理":
        return "定投（推断）：按计划——估值合理，维持正常定投"
    if valuation == "贵":
        return "定投（推断）：减量——估值贵时少买或暂停（微笑曲线：高位不追买）"
    return "无法判断：估值证据不足（观察），等待证据（推断）"


def dimension_signals(trend_state: str, valuation: str, fund_state: str) -> dict:
    """三维方向判定（2026-08-07 用户需求：维度分歧标准化标注的前提）。
    短线=趋势标尺、定投=估值标尺、资金=流向标尺；返回 多/空/中性。"""
    short = {"above20_rising": "多", "below20_falling": "空",
             "around20_oscillation": "中性"}.get(trend_state, "中性")
    dca = {"便宜": "多", "合理": "中性", "贵": "空"}.get(valuation, "中性")
    fund = {"inflow_confirm": "多", "outflow_confirm": "空"}.get(fund_state, "中性")
    return {"short": short, "dca": dca, "fund": fund}


def format_dimension_conflict(board: str, short_judge: str, dca_judge: str,
                              fund_text: str, signals: dict) -> str:
    """维度分歧标准化话术（2026-08-07 用户需求：固定模板，区分「视角不同」与「数据冲突」）。
    三维中出现多/空相反 → 输出模板明确告知这是评价标尺差异，非系统出错。"""
    vals = [signals.get("short"), signals.get("dca"), signals.get("fund")]
    has_bull = "多" in vals
    has_bear = "空" in vals
    if not (has_bull and has_bear):
        return ""
    lines = [f"维度分歧（{board}）：", "　【短线维度】" + short_judge,
             "　【定投维度】" + dca_judge]
    if fund_text:
        lines.append("　【资金维度】" + fund_text)
    lines.append("——短线看趋势标尺、定投看估值标尺、资金看流向标尺，三个维度独立成立，"
                 "结论相反是「视角不同」，不是系统冲突；具体操作以各自维度为准。")
    return "\n".join(lines)


# ============================================================
# synthesize：三层结构组装
# ============================================================

def synthesize(facts: dict) -> dict:
    """板块事实 → 三层结构（facts / explanation / short_term_judge / dca_judge / conflict_note）

    facts 契约（key 可缺省，缺省按"数据不足"降级）：
      board       板块名
      trend_state TREND_STATE 词表
      valuation   val_judge 的 verdict：便宜/合理/贵/观察
      fund_state  compute_fund_state 词表：inflow_confirm/outflow_confirm/single_day/cold_start/unknown
      sl_net      当日主力净额（亿元，可 None）
      main_pct    主指标分位（0-100，可 None）
      metric      主指标名：PE/PB/价格位置
      years       分位数据窗口年数（可 None）
      terms       首次出现的名词（渲染层判定，本函数直接解释）
    """
    board = _facts_get(facts, "board", "")
    trend_state = _facts_get(facts, "trend_state", "unknown") or "unknown"
    valuation = _facts_get(facts, "valuation", "观察") or "观察"
    fund_state = _facts_get(facts, "fund_state", "unknown") or "unknown"
    sl_net = facts.get("sl_net")            # None = 数据不可用（合法），非契约违反
    main_pct = _facts_get(facts, "main_pct", None)
    metric = _facts_get(facts, "metric", "PE") or "PE"
    years = _facts_get(facts, "years", None)
    terms = _facts_get(facts, "terms", []) or []

    # ── 事实层：机器可核验数据，原样呈现 ──
    fact_parts = [f"板块：{board}"]
    fact_parts.append(f"趋势：{TREND_STATE_EXPLAIN.get(trend_state, '未知')}")
    val_fact = _fact_valuation(main_pct, metric, years)
    fact_parts.append(f"估值：{val_fact}")
    fund_fact = _fact_fund(fund_state, sl_net)
    fact_parts.append(f"资金：{fund_fact}")
    facts_block = "\n".join(fact_parts)

    # ── 说明层：翻译 + 阈值口径 + 名词解释 ──
    expl_parts = []
    if main_pct is not None:
        if metric == "价格位置":
            # 窗口诚实性：价格位置取自 K 线全程 min/max（fetch_board_kline days=1300，
            # 约 5 年），无"近一年"截断——不得虚构窗口，只说"历史区间"
            expl_parts.append(f"价格位置 {main_pct:.0f}% = 当前价格在历史区间高低点之间的位置，越低越便宜")
        else:
            expl_parts.append(explain_percentile(main_pct, metric, years))
        expl_parts.append(f"阈值口径（val_config）：分位 <{PCT_LOW}% 视为便宜，>{PCT_HIGH}% 视为贵，中间为合理")
    if trend_state in TREND_STATE_EXPLAIN:
        expl_parts.append(explain_trend(trend_state))
    fund_expl = explain_fund_state(fund_state, sl_net)
    if fund_expl and fund_expl != "资金状态未知":
        expl_parts.append(fund_expl)
    glossary = glossary_for(terms)
    if glossary:
        expl_parts.append("名词解释：\n" + glossary)
    explanation = "\n".join(expl_parts)

    # ── 判断层：双场景独立裁决 + 冲突分流 ──
    short_term_judge = judge_short_term(trend_state, valuation)
    dca_judge = judge_dca(valuation)
    conflict_note = _conflict_note(trend_state, valuation, short_term_judge, dca_judge)

    return {"board": board, "facts": facts_block, "explanation": explanation,
            "short_term_judge": short_term_judge, "dca_judge": dca_judge,
            "conflict_note": conflict_note}


def _fact_valuation(main_pct, metric, years) -> str:
    if main_pct is None:
        # 带上指标名（Task 6 审查 Minor）：metric 缺省降级为 PE 时，事实层必须确定性地
        # 出现 "PE"——"分位数据不可用" 不带指标名，断言只能写弱析取
        return f"{metric} 分位数据不可用"
    if metric == "价格位置":
        return f"价格位置 {main_pct:.0f}%"
    s = f"{metric} 分位 {main_pct:.0f}%"
    if years is not None:
        s += f"（窗口 {_fmt_years(years)} 年）"
    return s


def _fact_fund(fund_state, sl_net) -> str:
    base = FUND_STATE_EXPLAIN.get(fund_state)
    if base is None:
        return "数据不可用"
    if sl_net is not None and fund_state in ("inflow_confirm", "outflow_confirm", "single_day"):
        sign = "+" if float(sl_net) >= 0 else ""
        return f"{base}（今日净额 {sign}{float(sl_net):.1f} 亿）"
    return base


def _conflict_note(trend_state: str, valuation: str,
                   short_term_judge: str, dca_judge: str) -> str:
    """冲突分流说明：两场景方向相反时各自保留、不互相覆盖，并声明无法统一裁决。

    方向相反的两对组合：短线顺势(升)↔定投减量(贵)；短线不抄底(降)↔定投加码(便宜)。
    趋势/估值证据均不足（两判断都缺席）→ 整体"无法判断"。
    其余（方向一致或一方向未明）→ 无冲突，不输出。"""
    short_up = trend_state == "above20_rising" and "无法判断" not in short_term_judge
    short_down = trend_state == "below20_falling" and "无法判断" not in short_term_judge
    dca_buy = valuation == "便宜"
    dca_reduce = valuation == "贵"
    if short_down and dca_buy:
        return ("场景分流（推断）：短线看趋势（下降不抄底）与定投看估值（便宜可加码）"
                "方向相反——短钱等反转信号，长钱攒低位筹码，各管各的场景，不互相覆盖；"
                "无法统一裁决，等待趋势反转证据（站上 20 日线/520 金叉）")
    if short_up and dca_reduce:
        return ("场景分流（推断）：短线看趋势（顺势持有）与定投看估值（贵则减量）"
                "方向相反——短线吃趋势尾段，定投控制成本，各管各的场景，不互相覆盖")
    if "无法判断" in short_term_judge and "无法判断" in dca_judge:
        return "无法判断：趋势与估值证据均不足，等待证据（推断）"
    return ""
