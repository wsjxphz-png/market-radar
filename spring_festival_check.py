#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
import akshare as ak
import pandas as pd
import numpy as np

df = ak.stock_zh_index_daily(symbol="sh000001")
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date")

years = range(2005, 2026)
results = []
for y in years:
    mask = (df["date"] >= f"{y}-01-15") & (df["date"] <= f"{y}-03-15")
    period = df[mask].copy()
    if len(period) < 15:
        continue
    period["gap"] = period["date"].diff().dt.days
    # 找最大休市间隔（春节）
    max_idx = period["gap"].idxmax()
    if pd.isna(max_idx) or period.loc[max_idx, "gap"] < 3:
        continue
    gap_date = period.loc[max_idx, "date"]
    pre = period[period["date"] < gap_date]
    post = period[period["date"] >= gap_date]
    if len(pre) < 5 or len(post) < 5:
        continue
    pre5 = (pre["close"].iloc[-1] / pre["close"].iloc[-6] - 1) if len(pre) >= 6 else 0
    post5 = (post["close"].iloc[min(4, len(post)-1)] / post["close"].iloc[0] - 1) if len(post) >= 1 else 0
    post20 = (post["close"].iloc[min(19, len(post)-1)] / post["close"].iloc[0] - 1) if len(post) >= 1 else 0
    results.append({"year": y, "pre5": pre5, "post5": post5, "post20": post20})

rd = pd.DataFrame(results)
print("A-share Spring Festival effect (SSE Composite):")
print(f"  {'Year':<6} {'Pre-5d':<10} {'Post-5d':<10} {'Post-20d':<10}")
for _, r in rd.iterrows():
    print(f"  {r['year']:<6} {r['pre5']:+.2%}     {r['post5']:+.2%}      {r['post20']:+.2%}")

print(f"\n  Avg Pre-5d:  {rd['pre5'].mean():+.2%}")
print(f"  Avg Post-5d: {rd['post5'].mean():+.2%}")
print(f"  Avg Post-20d:{rd['post20'].mean():+.2%}")
print(f"  Pre-5d up:   {(rd['pre5']>0).mean():.0%}")
print(f"  Post-5d up:  {(rd['post5']>0).mean():.0%}")
print(f"  Post-20d up: {(rd['post20']>0).mean():.0%}")

# 2021 specifically
r2021 = rd[rd["year"]==2021]
if len(r2021) > 0:
    r = r2021.iloc[0]
    print(f"\n  2021 Spring Festival:")
    print(f"    Pre-5d:  {r['pre5']:+.2%} (strong rally into holiday)")
    print(f"    Post-5d: {r['post5']:+.2%} (sold off immediately after)")
    print(f"    Post-20d:{r['post20']:+.2%}")
