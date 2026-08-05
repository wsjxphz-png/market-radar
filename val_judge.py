"""估值判定框架（第三版）：主指标 + 修正1(仅成长) + PB否决权 + 二维置信度 + 执行建议"""
from typing import Optional
from val_config import is_cyclical, PCT_LOW, PCT_HIGH, PB_VETO_GAP


def judge_valuation(board: str, main_pct: Optional[float], trend: str,
                    pb_pct: Optional[float], fund_state: str,
                    pb_ref: Optional[float]) -> dict:
    cyclical = is_cyclical(board)
    note_parts = []

    # ── 主指标估值判定 ──
    if main_pct is None:
        return {"board": board, "verdict": "观察", "dominant": "数据不足",
                "confidence": "低", "action": "skip", "note": "估值数据不可用（推断）"}
    if main_pct < PCT_LOW:
        verdict = "便宜"
    elif main_pct > PCT_HIGH:
        verdict = "贵"
    else:
        verdict = "合理"

    # ── 修正1：仅主指标=PE（非周期）时启用；趋势降→倾向错杀 / 升→倾向陷阱 ──
    if not cyclical and verdict == "便宜":
        if trend == "down":
            note_parts.append("主指标趋势下行，倾向错杀（盈利回升中，推断）")
        elif trend == "up":
            note_parts.append("主指标趋势上行，倾向陷阱（盈利仍在下滑，推断）")

    # ── PB 交叉：只降级不升级 ──
    if verdict == "便宜" and pb_pct is not None and pb_pct > PB_VETO_GAP:
        note_parts.append(f"PB 分位 {pb_pct:.0f}% > {PB_VETO_GAP}%（否决：PB 说真话）")
        verdict = "观察"

    # ── 资金维（估算，低可信度；方向性粗筛）──
    fund_dim = None
    if fund_state == "inflow_confirm":
        fund_dim = "up"
    elif fund_state == "outflow_confirm":
        fund_dim = "down"
    elif fund_state == "single_day":
        fund_dim = "single"
    # cold_start/unknown → fund_dim 保持 None

    # ── 置信度：二维独立（主指标估值 + 资金）──
    est_dim = 1 if (verdict in ("便宜", "贵")) else 0.5
    if fund_dim == "up" or fund_dim == "down":
        fund_dim_ok = 1
    elif fund_dim == "single":
        fund_dim_ok = 0.5
    else:
        fund_dim_ok = 0
    dims = est_dim + fund_dim_ok
    if fund_dim == "down" and verdict == "便宜":
        confidence = "低"  # 矛盾：资金流出确认
    elif dims >= 2:
        confidence = "高"
    elif dims >= 1:
        confidence = "中"
    else:
        confidence = "低"
    if fund_dim is None:
        note_parts.append("资金维数据积累中（冷启动）" if fund_state == "cold_start" else "资金维数据不可用")

    # ── 执行建议（无量纲）──
    if verdict == "贵":
        action, dominant = "none", "估值"
    elif verdict == "观察":
        action, dominant = "skip", "估值+PB否决" if "否决" in "".join(note_parts) else "数据不足"
    elif verdict == "便宜":
        if confidence == "高":
            action, dominant = "full", "估值+资金"
        elif confidence == "中":
            action, dominant = "half", "估值" if fund_dim is None else "估值+资金"
        else:
            action, dominant = "skip", "估值（矛盾）"
    else:  # 合理
        action, dominant = "half" if confidence == "高" else "skip", "估值"

    note_parts.append("推断")
    return {"board": board, "verdict": verdict, "dominant": dominant,
            "confidence": confidence, "action": action,
            "note": "；".join(note_parts)}
