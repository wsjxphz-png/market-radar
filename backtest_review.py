#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
月度回测复盘 — 回顾交易日志，评估信号正确率，输出改进建议。

用法:
  python backtest_review.py              # 回看最近30天
  python backtest_review.py --days 60    # 回看60天
  python backtest_review.py --month 2026-06  # 指定月份

输出:
  📊 账户表现 vs 基准
  📈 信号正确率统计
  ⚠️ 发现的系统弱点
  🔧 规则改进建议
"""

import os, sys, json, argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import warnings
warnings.filterwarnings("ignore")

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.jsonl")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")


# ═══════════════════════════════════════════════════════════
# 数据工具
# ═══════════════════════════════════════════════════════════

def fetch_index_history(code: str, name: str, days: int = 60) -> Optional[pd.DataFrame]:
    """获取指数历史K线"""
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days+10)).strftime("%Y%m%d")
        df = ak.stock_zh_index_daily(symbol=code)
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={"date": "date", "close": "close", "open": "open"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df[["date", "close"]].dropna()
    except Exception as e:
        print(f"  ⚠️ 获取{name}失败: {e}")
        return None


def get_forward_return(df: pd.DataFrame, date_str: str, days: int) -> Optional[float]:
    """计算某个日期之后N天的收益率"""
    d = pd.Timestamp(date_str)
    row = df[df["date"] == d]
    if len(row) == 0:
        # 找最近的下一个交易日
        future = df[df["date"] >= d]
        if len(future) < days + 1:
            return None
        start_price = float(future["close"].iloc[0])
        end_idx = min(days, len(future) - 1)
        end_price = float(future["close"].iloc[end_idx])
    else:
        idx = df[df["date"] == d].index[0]
        future = df.iloc[idx:]
        if len(future) < days + 1:
            return None
        start_price = float(future["close"].iloc[0])
        end_price = float(future["close"].iloc[min(days, len(future)-1)])
    return round((end_price / start_price - 1) * 100, 1)


# ═══════════════════════════════════════════════════════════
# 信号评估
# ═══════════════════════════════════════════════════════════

def evaluate_index_signal(signal: str, forward_5d: float, forward_10d: float) -> Tuple[bool, str]:
    """
    评估大盘仓位建议是否正确。
    - 建议空仓/轻仓 + 后续下跌 = 正确
    - 建议重仓 + 后续上涨 = 正确
    """
    is_bearish = any(x in signal for x in ("空仓", "轻仓", "0-20%", "下跌趋势"))
    is_bullish = any(x in signal for x in ("重仓", "70-80%", "可以持仓"))

    if is_bearish:
        correct = forward_10d < 0
        return correct, f"看空→后续10天{forward_10d:+.1f}% {'✅' if correct else '❌'}"
    elif is_bullish:
        correct = forward_10d > 0
        return correct, f"看多→后续10天{forward_10d:+.1f}% {'✅' if correct else '❌'}"
    else:
        # 中性建议，只要不大跌就算对
        correct = forward_10d > -3
        return correct, f"中性→后续10天{forward_10d:+.1f}% {'✅' if correct else '❌（跌幅过大）'}"


def evaluate_sector_signal(action: str, forward_5d: float) -> Tuple[bool, str]:
    """评估板块操作建议是否正确"""
    if any(x in action for x in ("减仓", "清仓", "回避")):
        correct = forward_5d < 0
        return correct, f"看空→后续5天{forward_5d:+.1f}% {'✅' if correct else '❌ 错失涨幅'}"
    elif any(x in action for x in ("入场", "买入", "考虑入场")):
        correct = forward_5d > 0
        return correct, f"看多→后续5天{forward_5d:+.1f}% {'✅' if correct else '❌ 入场即跌'}"
    else:
        return True, f"等待→后续5天{forward_5d:+.1f}% (不评判)"


# ═══════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════

def run_backtest(days: int = 30) -> str:
    if not os.path.exists(TRADE_LOG):
        return "⚠️ 无交易日志，回测无法进行。请先运行 market_dashboard.py。"

    # 加载日志
    entries = []
    with open(TRADE_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue

    if not entries:
        return "⚠️ 交易日志为空。"

    # 按日期范围筛选
    today = datetime.now()
    cutoff = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    entries = [e for e in entries if e.get("date", "") >= cutoff]
    if not entries:
        return f"⚠️ 最近{days}天无日志记录。"

    print(f"📋 加载 {len(entries)} 条日志 ({cutoff} ~ {today.strftime('%Y-%m-%d')})")

    # 获取上证指数历史数据用于评估
    print("📊 获取基准数据...")
    sh_idx = fetch_index_history("sh000001", "上证指数", days=days+20)
    if sh_idx is None:
        return "⚠️ 无法获取指数数据，回测中止。"

    # 评估每条信号
    index_results = []
    sector_results = []
    dates_covered = set()

    for entry in entries:
        etype = entry.get("type", "")
        date_str = entry.get("date", "")
        if not date_str:
            continue
        dates_covered.add(date_str)

        if etype == "index":
            signal = entry.get("position", entry.get("recommendation", ""))
            f5 = get_forward_return(sh_idx, date_str, 5)
            f10 = get_forward_return(sh_idx, date_str, 10)
            if f5 is not None and f10 is not None:
                correct, note = evaluate_index_signal(signal, f5, f10)
                index_results.append({
                    "date": date_str, "correct": correct, "note": note,
                    "signal": signal,
                })
            else:
                index_results.append({
                    "date": date_str, "correct": None, "note": "数据不足",
                    "signal": signal,
                })

        elif etype == "sector":
            action = entry.get("recommendation", "")
            target = entry.get("target", "")
            # 板块回测需要板块价格，这里用上证指数近似
            f5 = get_forward_return(sh_idx, date_str, 5)
            if f5 is not None:
                correct, note = evaluate_sector_signal(action, f5)
                sector_results.append({
                    "date": date_str, "target": target, "correct": correct,
                    "note": note, "action": action,
                })

    # 统计
    valid_index = [r for r in index_results if r["correct"] is not None]
    valid_sector = [r for r in sector_results if r["correct"] is not None]

    idx_correct = sum(1 for r in valid_index if r["correct"])
    idx_total = len(valid_index)
    idx_rate = idx_correct / idx_total * 100 if idx_total > 0 else 0

    sec_correct = sum(1 for r in valid_sector if r["correct"])
    sec_total = len(valid_sector)
    sec_rate = sec_correct / sec_total * 100 if sec_total > 0 else 0

    # 账户表现
    portfolio_return = 0.0
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, encoding='utf-8') as f:
                pf = json.load(f)
            portfolio_return = pf.get("cumulative_return_pct", 0.0)
        except Exception:
            pass

    # 基准表现（同期上证）
    first_date = min(dates_covered) if dates_covered else cutoff
    last_date = max(dates_covered) if dates_covered else today.strftime("%Y-%m-%d")
    benchmark_return = get_forward_return(sh_idx, first_date, days) or 0.0

    # 生成报告
    lines = [
        f"# 📊 回测复盘报告",
        f"",
        f"**回看周期**: {first_date} → {last_date}（{len(dates_covered)}个交易日）",
        f"**报告生成**: {today.strftime('%Y-%m-%d')}",
        f"",
        f"---",
        f"",
        f"## 💰 账户表现",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 模拟账户累计收益率 | **{portfolio_return:+.1f}%** |",
        f"| 同期上证指数 | **{benchmark_return:+.1f}%** |",
        f"| 相对超额收益 | **{portfolio_return - benchmark_return:+.1f}%** |",
        f"",
    ]

    if portfolio_return > benchmark_return:
        lines.append(f"✅ 模拟账户跑赢大盘 {portfolio_return - benchmark_return:.1f}个百分点。")
    else:
        lines.append(f"⚠️ 模拟账户跑输大盘 {benchmark_return - portfolio_return:.1f}个百分点，需审视信号质量。")

    lines.extend([
        f"",
        f"---",
        f"",
        f"## 📈 信号正确率",
        f"",
    ])

    if idx_total > 0:
        lines.append(f"**大盘仓位建议**: {idx_correct}/{idx_total} = **{idx_rate:.0f}%**")
        lines.append(f"")
        for r in valid_index:
            lines.append(f"- {r['date']}: {r['note']}")
        lines.append(f"")

    if sec_total > 0:
        lines.append(f"**板块操作建议**: {sec_correct}/{sec_total} = **{sec_rate:.0f}%**")
        lines.append(f"")
        for r in valid_sector:
            lines.append(f"- {r['date']} {r['target']}: {r['action']} → {r['note']}")
        lines.append(f"")

    # 发现的问题
    lines.extend([
        f"---",
        f"",
        f"## ⚠️ 系统弱点分析",
        f"",
    ])

    issues = []

    if idx_rate < 60:
        issues.append(f"- 大盘仓位建议正确率仅{idx_rate:.0f}%，低于60%阈值。**建议**：检查三周期趋势判断是否过于滞后——周线/月线信号在震荡市中容易反复。")
    if sec_rate < 50:
        issues.append(f"- 板块操作建议正确率仅{sec_rate:.0f}%。**建议**：板块判断叠加更多确认条件（如站上20日线+量能放大），减少假信号。")
    if idx_rate >= 60 and sec_rate >= 50:
        issues.append(f"- 信号质量总体可接受。继续积累数据以识别周期性模式（如月末效应、财报季等）。")

    # 假信号分析
    false_buys = [r for r in valid_sector if not r["correct"] and any(x in r.get("action","") for x in ("入场","买入","考虑入场"))]
    false_sells = [r for r in valid_sector if not r["correct"] and any(x in r.get("action","") for x in ("减仓","清仓"))]

    if false_buys:
        issues.append(f"- 买入假信号 {len(false_buys)} 次（入场即跌）：{', '.join(r['date'] for r in false_buys)}。" +
                       "**建议**：入场条件加严——底分型+站上5日线+放量，三条件缺一不可。")
    if false_sells:
        issues.append(f"- 卖出假信号 {len(false_sells)} 次（卖出即涨）：{', '.join(r['date'] for r in false_sells)}。" +
                       "**建议**：卖出前确认是否仅为正常回踩（缩量+不破关键均线），避免恐慌性清仓。")

    if not issues:
        issues.append("- 暂无足够数据识别系统弱点。继续积累。")

    lines.extend(issues)

    # 改进建议
    lines.extend([
        f"",
        f"---",
        f"",
        f"## 🔧 规则改进建议",
        f"",
    ])

    suggestions = []

    # 基于信号统计给出建议
    signal_statuses = defaultdict(list)
    for entry in entries:
        if entry.get("type") == "index" and "signals" in entry:
            for name, status in entry["signals"].items():
                signal_statuses[name].append(status)

    for name, statuses in signal_statuses.items():
        danger_pct = sum(1 for s in statuses if s == "danger") / len(statuses) * 100
        caution_pct = sum(1 for s in statuses if s == "caution") / len(statuses) * 100
        if danger_pct > 30:
            suggestions.append(f"- **{name}** 信号偏空比例 {danger_pct:.0f}%，如果市场实际未大跌，说明该指标阈值可能需要放宽。")
        if caution_pct > 50:
            suggestions.append(f"- **{name}** 模糊信号占比 {caution_pct:.0f}%——caution 太多等于没给信号。考虑收紧阈值，减少中间地带。")

    if not suggestions:
        suggestions.append("- 信号分布正常，暂无调整建议。继续积累数据。")

    lines.extend(suggestions)
    lines.extend([
        f"",
        f"---",
        f"",
        f"*回测基于《趋势交易论》框架 | 数据来源: AKShare | 仅供参考，不构成投资建议*",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 飞书推送
# ═══════════════════════════════════════════════════════════

def send_feishu(content: str) -> bool:
    """推送复盘报告到飞书。复用 dashboard 相同的环境变量。"""
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    chat_id = os.environ.get("FEISHU_CHAT_ID") or "oc_94e85ee81df40d0ac71c358861427b06"

    if not app_id or not app_secret:
        return False

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
        if resp.status_code != 200:
            return False
        token = resp.json().get("tenant_access_token")
    except Exception:
        return False

    # 超长内容分段发送（飞书卡片 max 约30KB）
    max_chars = 28000
    if len(content) <= max_chars:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📊 月度回测复盘"}},
            "elements": [{"tag": "markdown", "content": content}],
        }
        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "interactive",
                      "content": json.dumps(card, ensure_ascii=False)},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
            return resp.status_code == 200 and resp.json().get("code") == 0
        except Exception:
            return False
    else:
        # 分段
        parts = []
        remaining = content
        while len(remaining) > max_chars:
            split_at = remaining.rfind("---", 0, max_chars)
            if split_at < 1000:
                split_at = remaining.rfind("\n\n", 0, max_chars)
            if split_at < 1000:
                split_at = max_chars
            parts.append(remaining[:split_at])
            remaining = remaining[split_at:]
        parts.append(remaining)

        ok_all = True
        for i, part in enumerate(parts):
            card = {
                "config": {"wide_screen_mode": True},
                "header": {"template": "blue", "title": {"tag": "plain_text", "content": f"📊 月度回测复盘 ({i+1}/{len(parts)})"}},
                "elements": [{"tag": "markdown", "content": part}],
            }
            try:
                resp = requests.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json={"receive_id": chat_id, "msg_type": "interactive",
                          "content": json.dumps(card, ensure_ascii=False)},
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
                ok = resp.status_code == 200 and resp.json().get("code") == 0
                if not ok:
                    ok_all = False
            except Exception:
                ok_all = False
        return ok_all


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="月度回测复盘")
    parser.add_argument("--days", type=int, default=30, help="回看天数")
    parser.add_argument("--month", type=str, help="指定月份 (YYYY-MM)")
    parser.add_argument("--dry-run", action="store_true", help="仅打印，不推送飞书")
    args = parser.parse_args()

    if args.month:
        y, m = args.month.split("-")
        import calendar
        days_in_month = calendar.monthrange(int(y), int(m))[1]
        args.days = days_in_month + 20

    print("=" * 50)
    print(f"  回测复盘 - 最近{args.days}天")
    print("=" * 50)

    report = run_backtest(days=args.days)
    print("\n" + report)

    if not args.dry_run:
        ok = send_feishu(report)
        print(f"\n[>] 飞书: {'OK' if ok else 'FAIL (检查 FEISHU_APP_ID/FEISHU_APP_SECRET)'}")
    else:
        print("\n[i] Dry run — 未推送飞书")
