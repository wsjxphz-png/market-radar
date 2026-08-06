"""估值判断区块渲染（卡片顶部）+ 板块总览区块渲染（三层结构：事实→说明→判断）"""
import logging
from typing import List

logger = logging.getLogger(__name__)

ACTION_TEXT = {"full": "按计划定投", "half": "半额定投/等资金确认", "skip": "只观察，不买", "none": "不追加"}


def _fmt_years(years: float) -> str:
    """年数显示：整数不带小数（2.0→"2"），否则保留一位（7.1→"7.1"）"""
    return f"{years:.0f}" if float(years) == int(years) else f"{years:.1f}"


def format_valuation_block(judgements: List[dict], snapshots: List[dict]) -> str:
    if not judgements:
        return ""
    lines = ["━━━ 💰 估值判断（推断） ━━━"]
    for j in judgements:
        snap = next((s for s in snapshots if s["board"] == j["board"]), {})
        src = snap.get("source", "")
        if src == "pe":
            pos = f"PE分位 {snap.get('pe_pct', 0):.0f}%"
            years = snap.get("years")
            if years is not None:
                # 数据窗口诚实性：标注指数历史实际年数（931865 等新指数窗口远短于 10 年）
                pos += f"（指数历史 {_fmt_years(years)} 年）"
            if snap.get("pb_pct") is not None:
                pos += f" | PB {snap['pb_pct']:.0f}%"  # pb_pct 是分位（0-100），加 % 防误读为 PB 比值
        elif src == "pb":
            pos = snap.get("note", "PB 数据")
        else:
            main_pct = snap.get("main_pct")
            if main_pct is not None:
                pos = f"价格位置 {main_pct:.0f}%"
            else:
                # F2: 估值源全败时降级不崩溃 — main_pct=None 渲染 note，不触发格式错误
                pos = snap.get("note") or "估值数据不可用"
        lines.append(f"- {j['board']}：{j['verdict']}（{pos}）")
        conf = {"高": "高置信度", "中": "中置信度", "低": "低置信度"}.get(j["confidence"], "观察")
        lines.append(f"  {conf}｜{ACTION_TEXT.get(j['action'], '观察')}｜{j['note']}")
    return "\n".join(lines)


def build_board_overview(board_facts: list) -> str:
    """板块总览区块（三层结构：事实→说明→判断），每板块调用 val_explain.synthesize。

    synthesize 输出契约（Task 4 实测，逐 key 核对防静默降级）：
      board / facts（趋势·估值·资金三行）→ 事实层
      explanation（翻译+阈值口径+名词解释）→ 说明层
      short_term_judge / dca_judge / conflict_note（非空时）→ 判断层
    判断"无法判断"照常渲染（判断可缺席是设计）；board_facts 空 → 返回 ""（F2 不输出空标题）。
    board_facts 契约（merge_main build_board_facts 组装）：
      board/trend_state(TREND_STATE 词表)/valuation(verdict)/fund_state/
      sl_net/main_pct/metric/years/terms（首次出现名词，渲染层判定）。"""
    if not board_facts:
        return ""
    from val_explain import synthesize
    lines = [f"━━━ 🧭 板块总览（{len(board_facts)} 板块 · 事实→说明→判断） ━━━"]
    for i, facts in enumerate(board_facts, 1):
        if not isinstance(facts, dict):
            continue
        conflict = None
        try:
            syn = synthesize(facts)
            header = syn.get("board", "未知板块") or "未知板块"
            facts_block = syn.get("facts") or "（数据异常，事实层缺失）"
            explanation = syn.get("explanation") or "（数据不足，暂无说明）"
            short_judge = syn.get("short_term_judge") or "无法判断：数据异常（推断）"
            dca_judge = syn.get("dca_judge") or "无法判断：数据异常（推断）"
            conflict = syn.get("conflict_note")
        except Exception as exc:
            # 单板块异常兜底（Task 6 审查 Important）：synthesize 抛错或输出缺 key
            # → 该板块降级"无法判断：数据异常"，其余板块照常渲染，不击穿整卡
            logger.warning("板块总览渲染降级 board=%s: %s", facts.get("board", "?"), exc)
            header = str(facts.get("board", "未知板块"))
            facts_block = "（数据异常，事实层缺失）"
            explanation = "（数据异常，说明层缺失）"
            short_judge = "无法判断：数据异常（推断）"
            dca_judge = "无法判断：数据异常（推断）"
        lines.append("")
        lines.append(f"▌{i}. {header}")
        lines.append("◆ 事实")
        lines.append(facts_block)
        lines.append("◆ 说明")
        lines.append(explanation)
        lines.append("◆ 判断")
        lines.append(f"・ {short_judge}")
        lines.append(f"・ {dca_judge}")
        if conflict:
            lines.append(f"・ {conflict}")
    return "\n".join(lines)


def render_board_aggregate(facts_list: list, sectors: list,
                           judgements: list = None,
                           board_ok: bool = True,
                           fund_flow_hist_ok: bool = True) -> tuple:
    """板块聚合卡(2026-08-07 用户审计重构)：12 核心板块 × (事实+短线/定投判断)，
    返回 (glossary, card)：名词释义独立返回，由调用方渲染到交易手册之后
    （2026-08-07 用户风险点1：一行式板块信息密度高，释义必须紧跟手册，不能后置）；
    未映射的 THS 板块一行列出；三维方向矛盾时输出标准化「维度分歧」模板
    （用户风险点2：区分「视角不同」与「数据冲突」）。
    替代 估值判断/板块总览/板块操作信号/板块全貌/资金流向 五个重复区块。

    facts_list: build_board_facts 输出(12 板块，含 trend_state/valuation/fund_state/
      sl_net/main_pct/metric/years/terms)；sectors: THS 板块列表(29，含 name/rating)；
    judgements: 估值判定(confidence/action/note，聚合卡判断行加置信度)；
    board_ok/fund_flow_hist_ok: 数据状态标注(M2：降级必须可见，禁止静默缺块)。"""
    from val_explain import (synthesize, glossary_for, dimension_signals,
                             format_dimension_conflict, fund_state_short)
    from val_config import em_name_for

    if not facts_list:
        return "", ""

    per_board = []
    all_terms = set()
    for facts in facts_list:
        if not isinstance(facts, dict):
            continue
        try:
            syn = synthesize(facts)
        except Exception:
            syn = {"board": str(facts.get("board", "?")), "facts": "（数据异常）",
                   "short_term_judge": "无法判断：数据异常（推断）",
                   "dca_judge": "无法判断：数据异常（推断）"}
        board = syn.get("board") or "未知板块"
        all_terms.update(facts.get("terms") or [])
        per_board.append((facts, syn))

    # 名词释义（一次，给手册后渲染用）
    glossary = glossary_for(sorted(all_terms))

    lines = [f"━━━ 🔷 板块判断（{len(per_board)} 板块 · 便宜度+趋势+定投决策） ━━━"]
    # 趋势共振统计：多少板块站上 20/60 日线（2026-08-07 用户需求：中长期持有看中期趋势）
    n_up = sum(1 for f, _ in per_board if isinstance(f, dict)
               and f.get("trend_state") == "above20_rising")
    n_mid = sum(1 for f, _ in per_board if isinstance(f, dict)
                and f.get("trend_mid") == "above60")
    n_down = sum(1 for f, _ in per_board if isinstance(f, dict)
                 and f.get("trend_state") == "below20_falling")
    n_flat = len(per_board) - n_up - n_down
    reso = ("趋势共振向上" if n_up >= len(per_board) * 0.7 else
            "趋势共振向下" if n_down >= len(per_board) * 0.7 else
            "板块分化，趋势未确认")
    lines.append(f"📊 趋势共振：站上20日线 {n_up}/{len(per_board)} · 站上60日线 "
                 f"{n_mid}/{len(per_board)} · 20日线下 {n_down} · 方向不明 {n_flat} → **{reso}**")
    # M2: 数据降级必须显式标注（板块当日概览/资金流历史缺失时，读者须知道来源缺口）
    if board_ok is not None and not board_ok:
        lines.append("> ⚠️ 板块当日概览（涨跌/领涨股）数据缺失，以下评级基于技术面K线+估值。")
    if fund_flow_hist_ok is not None and not fund_flow_hist_ok:
        lines.append("> ⚠️ 资金流历史数据缺失（5日/10日），当日资金流仍显示。")
    jmap = {j.get("board"): j for j in (judgements or []) if isinstance(j, dict)}

    em_names = {f.get("board") for f in facts_list if isinstance(f, dict)}
    for i, (facts, syn) in enumerate(per_board, 1):
        board = syn.get("board") or "未知板块"
        dca = syn.get("dca_judge") or "无法判断（推断）"
        # 2026-08-07 用户需求重构：板块行=便宜度/趋势/资金/定投决策（删除个股短线语言）
        val_short = {"便宜": "便宜", "合理": "合理", "贵": "贵"}.get(
            facts.get("valuation") if isinstance(facts, dict) else "", "—")
        pct = facts.get("main_pct") if isinstance(facts, dict) else None
        metric = facts.get("metric", "PE") if isinstance(facts, dict) else "PE"
        years = facts.get("years") if isinstance(facts, dict) else None
        val_line = f"{val_short}" + (f"（{metric} 分位 {pct:.0f}%" if pct is not None else f"（{metric} 分位不可用")
        val_line += f"，窗口 {_fmt_years(years)} 年）" if years is not None else "）"
        trend_state = facts.get("trend_state") if isinstance(facts, dict) else "unknown"
        trend_mid = facts.get("trend_mid") if isinstance(facts, dict) else None
        if trend_state == "above20_rising" and trend_mid == "above60":
            trend_line = "上升期（站上20/60日线）——趋势已启动"
        elif trend_state == "above20_rising":
            trend_line = "上升期（站上20日线，60日线下）——趋势初现"
        elif trend_state == "around20_oscillation":
            trend_line = "震荡（20日线附近）——方向不明"
        elif trend_state == "below20_falling":
            trend_line = "下降期（20日线下方）——趋势未启动"
        else:
            trend_line = "趋势数据不足"
        fund_state = facts.get("fund_state") if isinstance(facts, dict) else None
        fund_line = fund_state_short(fund_state,
                                     facts.get("sl_net") if isinstance(facts, dict) else None)
        lines.append("")
        lines.append(f"▌{i}. {board}")
        lines.append(f"◆ 便宜度：{val_line}")
        lines.append(f"◆ 趋势：{trend_line}")
        lines.append(f"◆ 资金：{fund_line}")
        # M3: 估值判定附加置信度（原估值判断区块信息保留，防信息维度丢失）
        j = jmap.get(board)
        if j and j.get("confidence"):
            conf = {"高": "高置信", "中": "中置信", "低": "低置信"}.get(j["confidence"], j["confidence"])
            dca = f"{dca}（{conf}）"
        lines.append(f"◆ 定投：{dca}")
        # 定投矛盾解释（2026-08-07）：便宜+趋势未启动 = 微笑曲线与"等待趋势"的关系
        if (facts.get("valuation") == "便宜" if isinstance(facts, dict) else False) \
                and trend_state in ("around20_oscillation", "below20_falling", "unknown"):
            lines.append("  → 便宜≠马上买：微笑曲线的「跌时多买」是定投纪律（有估值支撑），"
                         "但须等趋势启动（站上20/60日线）再加大买入，避免越买越跌")

    # 其他板块（THS 未映射进核心 12 板块的，一行列出不展开）
    others = []
    for s in sectors or []:
        em = em_name_for(s.get("name", ""))
        if em in em_names:
            continue  # 已入聚合卡
        others.append(f"{s.get('name', '?')}({s.get('rating', '?')})")
    if others:
        lines.append("")
        lines.append(f"📋 其他板块（{len(others)}）：{'、'.join(others)}")
    return glossary, "\n".join(lines)
