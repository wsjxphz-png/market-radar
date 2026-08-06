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


def render_board_aggregate(facts_list: list, sectors: list) -> tuple:
    """板块聚合卡(2026-08-07 用户审计重构)：12 核心板块 × (事实+短线/定投判断)，
    返回 (glossary, card)：名词释义独立返回，由调用方渲染到交易手册之后
    （2026-08-07 用户风险点1：一行式板块信息密度高，释义必须紧跟手册，不能后置）；
    未映射的 THS 板块一行列出；三维方向矛盾时输出标准化「维度分歧」模板
    （用户风险点2：区分「视角不同」与「数据冲突」）。
    替代 估值判断/板块总览/板块操作信号/板块全貌 四个重复区块。

    facts_list: build_board_facts 输出(12 板块，含 trend_state/valuation/fund_state/
      sl_net/main_pct/metric/years/terms)；sectors: THS 板块列表(29，含 name/rating)。"""
    from val_explain import (synthesize, glossary_for, dimension_signals,
                             format_dimension_conflict, explain_fund_state)
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

    lines = [f"━━━ 🔷 板块判断（{len(per_board)} 板块 · 事实+短线/定投） ━━━"]
    em_names = {f.get("board") for f in facts_list if isinstance(f, dict)}
    for i, (facts, syn) in enumerate(per_board, 1):
        board = syn.get("board") or "未知板块"
        facts_block = syn.get("facts") or "（数据缺失）"
        short = syn.get("short_term_judge") or "无法判断（推断）"
        dca = syn.get("dca_judge") or "无法判断（推断）"
        fund_state = facts.get("fund_state") if isinstance(facts, dict) else None
        lines.append("")
        lines.append(f"▌{i}. {board}")
        lines.append(f"◆ {facts_block}")
        # 维度分歧标准化：三维(趋势/估值/资金)方向相反 → 模板话术，非自由发挥
        sig = dimension_signals(facts.get("trend_state", "") if isinstance(facts, dict) else "",
                                facts.get("valuation", "") if isinstance(facts, dict) else "",
                                fund_state or "")
        fund_text = explain_fund_state(fund_state, facts.get("sl_net") if isinstance(facts, dict) else None)
        conflict = format_dimension_conflict(board, short, dca, fund_text, sig)
        if conflict:
            lines.append(conflict)
        else:
            lines.append(f"◆ 短线：{short}｜定投：{dca}")

    # 其他板块（THS 未映射进核心 12 板块的，一行列出不展开）
    others = []
    for s in sectors or []:
        em = em_name_for(s.get("name", ""))
        if em in em_names:
            continue  # 已入聚合卡
        others.append(f"{s['name']}({s.get('rating', '?')})")
    if others:
        lines.append("")
        lines.append(f"📋 其他板块（{len(others)}）：{'、'.join(others)}")
    return glossary, "\n".join(lines)
