"""建议→结果反馈环（2026-08-07 用户裁决问题2，v3 基础版）：板块决策留痕回看统计。

读 data/fund_decisions.jsonl（板块定投决策：便宜/趋势/动作），对每条记录取 N 个
交易日后的板块指数实际表现，按定投动作分组统计平均涨跌幅与胜率——
系统开始"复盘自己的板块建议"（月度运行；无新数据源，K线 fetch_board_kline 覆盖留痕窗口）。

用法: python fund_review.py [--days 5 20]
"""
import json
import os
from datetime import datetime
from typing import Callable, List, Optional

REVIEW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "fund_decisions.jsonl")
DEFAULT_LOOKBACKS = (5, 20)


def load_history(path: str = REVIEW_FILE) -> List[dict]:
    """读留痕（JSONL）；非 dict 行过滤。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def future_returns(history: List[dict], fetcher: Callable[[str], Optional[object]],
                   lookbacks: tuple = DEFAULT_LOOKBACKS) -> List[dict]:
    """对每条板块决策留痕，算 N 交易日后涨跌幅。
    fetcher(board) → 板块K线 DataFrame（date/close 升序）——注入以便测试。
    基准 = 记录日的板块指数收盘（K线定位）；数据不足（记录日距今不足 N 交易日）→ None。"""
    out = []
    for r in history:
        if not r.get("board") or r.get("action") is None:
            continue
        kline = fetcher(r["board"])
        if kline is None or len(kline) < 1:
            continue
        dates = kline["date"].astype(str)
        try:
            i = int(dates[dates == str(r["date"])].index[0])
        except (IndexError, KeyError):
            continue  # 留痕日期不在 K线窗口 → 跳过
        closes = kline["close"].tolist()
        base = closes[i]
        if not base or base != base:
            continue
        row = {"date": r["date"], "board": r["board"], "action": r.get("action"),
               "cheap": r.get("cheap"), "trend": r.get("trend")}
        for n in lookbacks:
            j = i + n
            row[f"ret_{n}"] = round((closes[j] - base) / base * 100, 2) \
                if j < len(closes) and closes[j] == closes[j] else None
        out.append(row)
    return out


def summarize(returns: List[dict], lookbacks: tuple = DEFAULT_LOOKBACKS) -> List[dict]:
    """按定投动作分组：样本数 / 平均 N 日涨跌幅 / 正收益占比。"""
    groups = {}
    for r in returns:
        groups.setdefault(r["action"], []).append(r)
    summary = []
    order = {"多买": 0, "观察": 1, "减量": 2, "暂停/减量": 3, "按计划": 4, "正常定投": 5}
    for action, rows in groups.items():
        g = {"action": action, "n": len(rows)}
        for n in lookbacks:
            vals = [x[f"ret_{n}"] for x in rows if x.get(f"ret_{n}") is not None]
            if vals:
                g[f"ret_{n}_avg"] = round(sum(vals) / len(vals), 2)
                g[f"ret_{n}_win"] = round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
            else:
                g[f"ret_{n}_avg"] = None
                g[f"ret_{n}_win"] = None
        summary.append(g)
    return sorted(summary, key=lambda g: order.get(g["action"], 99))


def _fetch_kline(board: str):
    """生产 fetcher：板块K线（1300 天历史覆盖留痕窗口）"""
    from ltc_data import fetch_board_kline
    return fetch_board_kline(board, days=1300)


def render_report(summary: List[dict], lookbacks: tuple = DEFAULT_LOOKBACKS) -> str:
    lines = [f"## 📋 板块建议→结果反馈（{datetime.now():%Y-%m-%d}）", "",
             "动作 | 样本 | " + " | ".join(f"{n}日均涨幅" for n in lookbacks)
             + " | " + " | ".join(f"{n}日胜率" for n in lookbacks)]
    for g in summary:
        avg = " | ".join(f"{g.get(f'ret_{n}_avg', '—')}%" for n in lookbacks)
        win = " | ".join(f"{g.get(f'ret_{n}_win', '—')}%" for n in lookbacks)
        lines.append(f"{g['action']} | {g['n']} | {avg} | {win}")
    lines.append("")
    lines.append("> 「多买」胜率>55% 说明【便宜+趋势】买入条件有预测力；≤50% 说明无信息——"
                 "校准月据此调决策表。样本不足 10 个时结论不可靠。")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import sys as _sys
    try:  # Windows GBK 控制台打印 emoji 会崩
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="板块决策留痕回看统计（建议→结果反馈环）")
    parser.add_argument("--days", nargs="+", type=int, default=list(DEFAULT_LOOKBACKS))
    parser.add_argument("--file", default=REVIEW_FILE)
    args = parser.parse_args()
    history = load_history(args.file)
    if not history:
        print("无板块决策留痕（fund_decisions.jsonl 为空或不存在）——先等推送积累。")
        return 1
    lookbacks = tuple(args.days)
    returns = future_returns(history, _fetch_kline, lookbacks)
    print(render_report(summarize(returns, lookbacks), lookbacks))
    print(f"\n（共 {len(history)} 条留痕，{len(returns)} 条可回看）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
