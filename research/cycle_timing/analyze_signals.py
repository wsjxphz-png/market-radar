#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检验 top_score / bottom_score 的前瞻区分度，不涉及交易策略。"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
SIG = BASE / "signals"
OUT = BASE / "results"
OUT.mkdir(exist_ok=True)

PEAKS = ["2007-10-16", "2009-08-04", "2015-06-08", "2018-01-24", "2021-02-18", "2024-10-08"]
TROUGHS = ["2008-11-04", "2012-12-04", "2016-01-28", "2018-12-27", "2020-03-23", "2022-10-31", "2024-09-13"]


def add_forward(df):
    close = df["close"]
    df["fwd_60"] = close.shift(-60) / close - 1
    df["fwd_120"] = close.shift(-120) / close - 1
    df["fwd_250"] = close.shift(-250) / close - 1
    for n in (60, 120, 250):
        future_min = close[::-1].rolling(n, min_periods=1).min()[::-1].shift(-1)
        df[f"mdd_{n}"] = future_min / close - 1
    return df


def bucket_table(df, score_col, sample_step=20):
    d = df.dropna(subset=[score_col, "fwd_120"]).copy()
    d = d.iloc[::sample_step].copy()
    bins = [0, 0.2, 0.4, 0.6, 0.8, 1.01]
    labels = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
    d["bucket"] = pd.cut(d[score_col], bins=bins, labels=labels, right=False)
    rows = []
    for label, g in d.groupby("bucket", observed=True):
        rows.append({
            "bucket": label,
            "n": len(g),
            "fwd60_mean": g["fwd_60"].mean(),
            "fwd120_mean": g["fwd_120"].mean(),
            "fwd120_median": g["fwd_120"].median(),
            "fwd250_mean": g["fwd_250"].mean(),
            "mdd120_mean": g["mdd_120"].mean(),
            "pct_fwd120_lt_-10": (g["fwd_120"] < -0.10).mean(),
            "pct_mdd120_lt_-15": (g["mdd_120"] < -0.15).mean(),
        })
    return pd.DataFrame(rows)


def episodes(df, score_col, threshold=0.6, min_len=5, gap_days=20):
    d = df.dropna(subset=[score_col]).reset_index(drop=True)
    mask = d[score_col] >= threshold
    groups = (mask != mask.shift()).cumsum()
    cand = []
    for _, g in d[mask].groupby(groups[mask]):
        if len(g) >= min_len:
            cand.append((g.index[0], g.index[-1], g))
    picked = []
    last_start = None
    for start_i, end_i, g in cand:
        start_date = d.loc[start_i, "date"]
        if last_start is not None and (start_date - last_start).days < gap_days:
            continue
        picked.append((start_i, end_i, g))
        last_start = start_date
    rows = []
    for start_i, end_i, g in picked:
        start = d.loc[start_i]
        rows.append({
            "start": start["date"],
            "end": d.loc[end_i, "date"],
            "days": len(g),
            "max_score": g[score_col].max(),
            "close": start["close"],
            "fwd_60": start["fwd_60"],
            "fwd_120": start["fwd_120"],
            "fwd_250": start["fwd_250"],
            "mdd_120": start["mdd_120"],
            "mdd_250": start["mdd_250"],
        })
    return pd.DataFrame(rows)


def event_table(df, events, score_col):
    d = df.reset_index(drop=True)
    rows = []
    for e in events:
        target = pd.Timestamp(e)
        pos = (d["date"] - target).abs().idxmin()
        lo = max(0, pos - 120)
        hi = min(len(d) - 1, pos + 20)
        w = d.iloc[lo:hi + 1]
        hit = w[w[score_col] >= 0.6]
        first = hit["date"].iloc[0] if len(hit) else pd.NaT
        lead = (target - first).days if len(hit) else None
        rows.append({
            "event": target.date(),
            "near_close": d.loc[pos, "close"],
            "max_score": w[score_col].max(),
            "first_hit": first.date() if len(hit) else None,
            "lead_days": lead,
            "score_at_event": d.loc[pos, score_col],
            "dims_at_event": d.loc[pos, "top_dims" if score_col == "top_score" else "bottom_dims"],
        })
    return pd.DataFrame(rows)


def fmt_pct(x):
    return "nan" if pd.isna(x) else f"{x:.1%}"


def main():
    all_lines = []
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        df = pd.read_csv(SIG / f"signals_{name}.csv", parse_dates=["date"])
        df = add_forward(df)
        lines = [f"\n{'='*78}", f"{name.upper()}   {df.date.min().date()} -> {df.date.max().date()}"]
        for score_col, fwd_label in [("top_score", "顶部风险"), ("bottom_score", "底部机会")]:
            bt = bucket_table(df, score_col)
            lines.append(f"\n[{fwd_label}] 分数分桶（每20个交易日抽样，降低重叠）")
            lines.append(bt.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        # 事件研究
        top_ev = event_table(df, PEAKS, "top_score")
        bot_ev = event_table(df, TROUGHS, "bottom_score")
        lines.append("\n[历史大顶] top_score>=0.6 是否提前触发")
        lines.append(top_ev.to_string(index=False))
        lines.append("\n[历史大底] bottom_score>=0.6 是否提前/同步触发")
        lines.append(bot_ev.to_string(index=False))
        # 连续触发段
        for score_col, label in [("top_score", "顶部"), ("bottom_score", "底部")]:
            ep = episodes(df, score_col)
            if len(ep):
                ep["hit"] = ep["mdd_120"] < -0.10 if score_col == "top_score" else ep["fwd_120"] > 0.05
                lines.append(f"\n[{label}] 连续触发段（>=5日，间隔>=20日）")
                lines.append(ep.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
                lines.append(f"命中率: {ep['hit'].mean():.0%}  ({ep['hit'].sum()}/{len(ep)})")
            else:
                lines.append(f"\n[{label}] 无连续触发段")
        text = "\n".join(lines)
        print(text)
        all_lines.append(text)
        df.to_csv(SIG / f"signals_{name}_fwd.csv", index=False, encoding="utf-8-sig")

    (OUT / "signal_quality.txt").write_text("\n".join(all_lines), encoding="utf-8")
    print(f"\nwritten {OUT / 'signal_quality.txt'}")


if __name__ == "__main__":
    main()
