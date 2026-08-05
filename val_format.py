"""估值判断区块渲染（卡片顶部）"""
from typing import List

ACTION_TEXT = {"full": "按计划定投", "half": "半额定投/等资金确认", "skip": "只观察，不买", "none": "不追加"}


def format_valuation_block(judgements: List[dict], snapshots: List[dict]) -> str:
    if not judgements:
        return ""
    lines = ["━━━ 💰 估值判断（推断） ━━━"]
    for j in judgements:
        snap = next((s for s in snapshots if s["board"] == j["board"]), {})
        src = snap.get("source", "")
        if src == "pe":
            pos = f"PE分位 {snap.get('pe_pct', 0):.0f}%"
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
