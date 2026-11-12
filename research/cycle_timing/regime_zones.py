#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回答两个问题：
1) 最近两年是否和之前不一样，信号是否在衰减；
2) 顶部/底部能否只判断“区间/时间段”，以及这个区间有多宽。
"""
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_strategy import load, metrics
from robustness import prep, topnormal

BASE = Path(__file__).parent
SIG = BASE / "signals"
OUT = BASE / "results"


def episode_starts(s, col, threshold, min_len=5, gap=20):
    mask = s[col] >= threshold
    groups = (mask != mask.shift()).cumsum()
    cand = []
    for _, g in s[mask].groupby(groups[mask]):
        if len(g) >= min_len:
            cand.append((g.index[0], g.index[-1]))
    picked = []
    last = None
    for a, b in cand:
        if last is not None and (s.date.iloc[a] - last).days < gap:
            continue
        picked.append((a, b))
        last = s.date.iloc[a]
    return picked


def zone_stats(s, col, threshold, label):
    rows = []
    for a, b in episode_starts(s, col, threshold):
        close = s.close.values
        n = len(close)
        end = min(b + 120, n - 1)
        fut = close[a + 1:end + 1]
        if len(fut) == 0:
            continue
        lo = int(np.argmin(fut)) + 1
        hi = int(np.argmax(fut)) + 1
        post = close[b + 1:min(b + 121, n)]
        rows.append({
            "start": s.date.iloc[a].date(),
            "zone_days": b - a + 1,
            "zone_ret": close[b] / close[a] - 1,
            "fwd20": close[a + 20] / close[a] - 1 if a + 20 < n else np.nan,
            "fwd60": close[a + 60] / close[a] - 1 if a + 60 < n else np.nan,
            "fwd120": close[a + 120] / close[a] - 1 if a + 120 < n else np.nan,
            "max_up_120": close[a + hi] / close[a] - 1,
            "max_dd_120": close[a + lo] / close[a] - 1,
            "days_to_low": lo,
            "days_to_high": hi,
            "post_zone_dd_120": (min(post) / close[b] - 1) if len(post) > 5 else np.nan,
        })
    return pd.DataFrame(rows)


def zone_summary():
    lines = ["\n[区间特征] 顶部>=0.6 / 底部>=0.8 的连续触发段（>=5日，间隔>=20日）"]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = pd.read_csv(SIG / f"signals_{name}.csv", parse_dates=["date"])
        s = s.sort_values("date").reset_index(drop=True)
        for col, th, label in [("top_score", 0.6, "顶部"), ("bottom_score", 0.8, "底部")]:
            z = zone_stats(s, col, th, label)
            if z.empty:
                continue
            lines.append(f"\n{name.upper()} {label} n={len(z)}")
            lines.append(
                f"  区间时长 中位 {z.zone_days.median():.0f} 日 "
                f"(P25-P75 {z.zone_days.quantile(.25):.0f}-{z.zone_days.quantile(.75):.0f})  "
                f"区间内涨跌 中位 {z.zone_ret.median():+.1%}")
            lines.append(
                f"  触发后 120 日: 最大下跌中位 {z.max_dd_120.median():+.1%}  "
                f"最大上涨中位 {z.max_up_120.median():+.1%}  "
                f"跌超10%占比 {(z.max_dd_120 < -0.10).mean():.0%}")
            lines.append(
                f"  最坏点出现在触发后 中位 {z.days_to_low.median():.0f} 个交易日 "
                f"(P25-P75 {z.days_to_low.quantile(.25):.0f}-{z.days_to_low.quantile(.75):.0f})  "
                f"最好点中位 {z.days_to_high.median():.0f} 日")
            lines.append(
                f"  区间结束后 120 日再跌 中位 {z.post_zone_dd_120.median():+.1%}  "
                f"再跌超10%占比 {(z.post_zone_dd_120 < -0.10).mean():.0%}")
    return "\n".join(lines)


def stability():
    """按 5 年一段看信号的前瞻区分度，以及策略/持有的分段收益。"""
    lines = ["\n[信号是否衰减] 每段内 top>=0.6 与 top<0.6 的 120 日前瞻收益差"]
    spans = [("2008-2012", "2008-01-01", "2012-12-31"),
             ("2013-2017", "2013-01-01", "2017-12-31"),
             ("2018-2022", "2018-01-01", "2022-12-31"),
             ("2023-2026", "2023-01-01", "2026-12-31")]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = load(name)
        s["fwd120"] = s.close.shift(-120) / s.close - 1
        lines.append(f"\n{name.upper()}")
        for label, a, b in spans:
            d = s[(s.date >= a) & (s.date <= b)]
            hi = d.loc[d.top_score >= 0.6, "fwd120"].dropna()
            lo = d.loc[d.top_score < 0.6, "fwd120"].dropna()
            if len(hi) < 5:
                lines.append(f"  {label}  top>=0.6 n={len(hi)} 样本不足")
                continue
            lines.append(f"  {label}  top>=0.6 n={len(hi):>4} mean={hi.mean():>7.1%}  "
                         f"top<0.6 mean={lo.mean():>7.1%}  差={hi.mean()-lo.mean():>7.1%}")
    return "\n".join(lines)


def recent():
    """最近两年 vs 之前：年化波动、站上200日线天数、年度收益、策略收益。"""
    lines = ["\n[最近两年 vs 之前]"]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = load(name)
        s = s.dropna(subset=["ma200"]).copy()
        s["ret"] = s.close.pct_change()
        s["above"] = (s.close > s.ma200).astype(float)
        eras = [("2013-2019", "2013-01-01", "2019-12-31"),
                ("2020-2023", "2020-01-01", "2023-12-31"),
                ("2024-2026", "2024-01-01", "2026-12-31")]
        lines.append(f"\n{name.upper()}")
        for label, a, b in eras:
            d = s[(s.date >= a) & (s.date <= b)]
            if len(d) < 100:
                continue
            vol = d.ret.std() * np.sqrt(252)
            lines.append(f"  {label}  年化波动 {vol:>6.1%}  站上200日线 {d.above.mean():>5.0%}  "
                         f"区间收益 {d.close.iloc[-1]/d.close.iloc[0]-1:>7.1%}")

    # 策略在最近两年的表现
    lines.append("\n  策略 topnormal(0.6/0.4) 分年收益 vs 持有")
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        w = topnormal(s, 0.6, 0.4)
        m, ret, _ = metrics(s, w)
        hold = pd.Series(s.open.shift(-1).values / s.open.values - 1,
                         index=pd.to_datetime(s.date)).dropna()
        row = []
        for y in range(2023, 2027):
            a = ret[ret.index.year == y]
            b = hold[hold.index.year == y]
            if len(a) == 0:
                continue
            row.append(f"{y}: 策略{(1+a).prod()-1:+.1%}/持有{(1+b).prod()-1:+.1%}")
        lines.append(f"  {name.upper():<6} " + "  ".join(row))
    return "\n".join(lines)


def main():
    text = "\n".join([stability(), recent(), zone_summary()])
    (OUT / "regime_zones.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
