#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建日频周期信号。所有信号只使用当日收盘及以前可用的数据。"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
CACHE = BASE / "cache"
OUT = BASE / "signals"
OUT.mkdir(exist_ok=True)


def read(name, **kwargs):
    df = pd.read_csv(CACHE / name, **kwargs)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def pct_rank(s, window, min_periods):
    return s.rolling(window, min_periods=min_periods).apply(
        lambda x: float((x < x[-1]).mean()), raw=True)


def load_index(name):
    df = read(f"index_{name}.csv")
    return df.sort_values("date").reset_index(drop=True)


def load_pe(name):
    df = read(f"pe_{name}.csv").sort_values("date").reset_index(drop=True)
    df["pe_pct"] = pct_rank(df["pe_ttm"], 120, 36)
    return df


def load_bond():
    df = read("bond_10y.csv").sort_values("date").reset_index(drop=True)
    df["cn_10y"] = df["cn_10y"].ffill()
    return df


def load_money():
    df = read("money_supply.csv")
    df["month"] = pd.to_datetime(df["month"])
    df = df.sort_values("month").reset_index(drop=True)
    df["gap"] = df["m1_yoy"] - df["m2_yoy"]
    df["gap_chg3"] = df["gap"] - df["gap"].shift(3)
    df["avail"] = df["month"] + pd.Timedelta(days=15)
    return df


def load_social():
    df = read("social_financing.csv")
    df["month"] = pd.to_datetime(df["month"])
    df = df.sort_values("month").reset_index(drop=True)
    df["sz_3m"] = df["sz_inc"].rolling(3).sum()
    df["sz_yoy"] = df["sz_3m"] / df["sz_3m"].shift(12) - 1
    df["avail"] = df["month"] + pd.Timedelta(days=15)
    return df


def load_margin():
    df = read("margin.csv").sort_values("date").reset_index(drop=True)
    df["margin_20d_chg"] = df["balance"] / df["balance"].shift(20) - 1
    df["margin_pct"] = pct_rank(df["balance"], 750, 250)
    # 两融数据收盘后披露，信号日只能用前一交易日的数据
    df["avail"] = df["date"] + pd.Timedelta(days=1)
    return df


def load_qvix():
    df = read("qvix.csv").sort_values("date").reset_index(drop=True)
    df["qvix_pct"] = pct_rank(df["qvix"], 750, 250)
    df["qvix_20d_chg"] = df["qvix"] - df["qvix"].shift(20)
    return df


def build_breadth():
    """90 个同花顺行业指数：站上 200 日线比例、20 日新高新低比例。"""
    frames = []
    for f in sorted((CACHE / "sectors").glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["date"]).sort_values("date")
        if len(df) < 220:
            continue
        close = df["close"]
        ma200 = close.rolling(200).mean()
        hi20 = close.rolling(20).max()
        lo20 = close.rolling(20).min()
        above200 = (close > ma200).astype(float)
        nh20 = (close >= hi20).astype(float)
        nl20 = (close <= lo20).astype(float)
        # 窗口不足时 rolling 为 NaN，比较会得到 False→0，必须显式还原为 NaN，
        # 否则 2010 年初会伪造“0% 站上 200 日线”的极端底部信号。
        above200[ma200.isna()] = np.nan
        nh20[hi20.isna()] = np.nan
        nl20[lo20.isna()] = np.nan
        frames.append(pd.DataFrame({
            "date": df["date"],
            "above200": above200,
            "nh20": nh20,
            "nl20": nl20,
        }))
    if not frames:
        raise RuntimeError("no sector data")
    all_df = pd.concat(frames, ignore_index=True)
    g = all_df.groupby("date")
    out = pd.DataFrame({
        "breadth_ma200": g["above200"].mean(),
        "breadth_nh20": g["nh20"].mean(),
        "breadth_nl20": g["nl20"].mean(),
        "breadth_n": g["above200"].count(),
    }).reset_index()
    out = out[out["breadth_n"] >= 20].copy()
    out["breadth_nh_nl"] = out["breadth_nh20"] - out["breadth_nl20"]
    out["breadth_ma200_chg20"] = out["breadth_ma200"] - out["breadth_ma200"].shift(20)
    return out.sort_values("date").reset_index(drop=True)


def merge_asof(df, right, right_on, cols, suffix=""):
    right = right[[right_on] + cols].dropna(subset=[right_on]).sort_values(right_on)
    out = pd.merge_asof(df.sort_values("date"), right,
                        left_on="date", right_on=right_on,
                        direction="backward")
    return out


def build_for_index(index_name, pe_name, breadth):
    df = load_index(index_name)
    close = df["close"]
    df["ma20"] = close.rolling(20).mean()
    df["ma50"] = close.rolling(50).mean()
    df["ma200"] = close.rolling(200).mean()
    df["trend_up"] = (close > df["ma200"]).astype(float)
    df["ma_cross"] = (df["ma50"] > df["ma200"]).astype(float)
    df["extension"] = close / df["ma200"] - 1
    df["drawdown_252"] = close / close.rolling(252, min_periods=60).max() - 1
    df["ret_60"] = close / close.shift(60) - 1
    df["ret_120"] = close / close.shift(120) - 1
    df["ret_1d"] = close.pct_change()
    df["realized_vol"] = df["ret_1d"].rolling(20).std() * np.sqrt(252)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    amt_z = ((df["amount"] - df["amount"].rolling(60).mean())
             / df["amount"].rolling(60).std())
    # 创业板指没有可用的每日成交额源，新日期 amount 为空；
    # 用成交量 z 兜底（同为活跃度标准化，阈值含义近似），并标记来源。
    vol_z = ((df["volume"] - df["volume"].rolling(60).mean())
             / df["volume"].rolling(60).std())
    df["turnover_z"] = amt_z.where(amt_z.notna(), vol_z)
    df["turnover_src"] = np.where(amt_z.notna(), "amount", "volume")

    # 估值：月频 PE，按月末值向后填充；ERP 用同一月末的 10Y
    pe = load_pe(pe_name)
    bond = load_bond()
    bond_m = (bond.set_index("date")["cn_10y"].resample("ME").last()
              .reset_index().rename(columns={"date": "month"}))
    pe = pd.merge_asof(pe.sort_values("date"), bond_m.sort_values("month"),
                       left_on="date", right_on="month", direction="backward")
    pe["erp"] = 100.0 / pe["pe_ttm"] - pe["cn_10y"]
    pe["erp_pct"] = pct_rank(pe["erp"], 120, 36)
    pe_cols = ["date", "pe_ttm", "pe_pct", "erp", "erp_pct"]
    df = merge_asof(df, pe[pe_cols], "date", ["pe_ttm", "pe_pct", "erp", "erp_pct"])

    df = merge_asof(df, bond, "date", ["cn_10y"])
    df["cn_10y"] = df["cn_10y"].ffill()
    df["cn_10y_chg60"] = df["cn_10y"] - df["cn_10y"].shift(60)

    money = load_money()
    df = merge_asof(df, money[["avail", "gap", "gap_chg3"]],
                    "avail", ["gap", "gap_chg3"])
    social = load_social()
    df = merge_asof(df, social[["avail", "sz_yoy"]], "avail", ["sz_yoy"])

    margin = load_margin()
    df = merge_asof(df, margin[["avail", "balance", "margin_20d_chg", "margin_pct"]],
                    "avail", ["balance", "margin_20d_chg", "margin_pct"])
    df = df.rename(columns={"balance": "margin_balance"})

    qvix = load_qvix()
    df = merge_asof(df, qvix[["date", "qvix", "qvix_pct", "qvix_20d_chg"]],
                    "date", ["qvix", "qvix_pct", "qvix_20d_chg"])

    df = merge_asof(df, breadth[["date", "breadth_ma200", "breadth_ma200_chg20",
                                 "breadth_nh_nl", "breadth_n"]],
                    "date", ["breadth_ma200", "breadth_ma200_chg20",
                             "breadth_nh_nl", "breadth_n"])

    top_flags = []
    bottom_flags = []
    top_dims = []
    bottom_dims = []
    for _, r in df.iterrows():
        t = []
        b = []
        # T1 / B1 估值
        if pd.notna(r["pe_pct"]):
            t.append(r["pe_pct"] >= 0.80)
            b.append(r["pe_pct"] <= 0.20)
        elif pd.notna(r["erp_pct"]):
            t.append(r["erp_pct"] <= 0.20)
            b.append(r["erp_pct"] >= 0.80)
        # T2 / B2 信用与利率
        credit_t = []
        credit_b = []
        if pd.notna(r["gap_chg3"]):
            credit_t.append(r["gap_chg3"] < 0 and r["cn_10y_chg60"] > 0)
            credit_b.append(r["gap_chg3"] > 0)
        if pd.notna(r["sz_yoy"]):
            credit_t.append(r["sz_yoy"] < 0)
            credit_b.append(r["sz_yoy"] > 0)
        if credit_t:
            t.append(any(credit_t))
            b.append(any(credit_b))
        # T3 / B3 广度
        breadth_t = []
        breadth_b = []
        if pd.notna(r["breadth_ma200_chg20"]):
            breadth_t.append(r["ret_60"] > 0.05 and r["breadth_ma200_chg20"] < -0.10)
        if pd.notna(r["breadth_ma200"]):
            breadth_t.append(r["drawdown_252"] > -0.05 and r["breadth_ma200"] < 0.45)
            breadth_b.append(r["breadth_ma200"] < 0.25)
            breadth_b.append(r["breadth_ma200"] < 0.40 and r["breadth_ma200_chg20"] > 0.10)
        if breadth_t:
            t.append(any(breadth_t))
            b.append(any(breadth_b))
        # T4 / B4 情绪与仓位
        sent_t = []
        sent_b = []
        if pd.notna(r["margin_20d_chg"]):
            sent_t.append(r["margin_20d_chg"] > 0.05 and pd.notna(r["turnover_z"]) and r["turnover_z"] > 1.0)
            sent_b.append(r["margin_20d_chg"] < -0.05 or (pd.notna(r["turnover_z"]) and r["turnover_z"] < -0.5))
        if pd.notna(r["margin_pct"]):
            sent_t.append(r["margin_pct"] > 0.90 and r["ret_60"] > 0.05)
        if pd.notna(r["qvix_pct"]):
            sent_b.append(r["qvix_pct"] > 0.80 and r["qvix_20d_chg"] < 0)
        if sent_t:
            t.append(any(sent_t))
            b.append(any(sent_b))
        # T5 / B5 趋势与位置
        if pd.notna(r["extension"]) or pd.notna(r["ret_120"]):
            t.append(r["extension"] > 0.20 or r["ret_120"] > 0.25)
        if pd.notna(r["drawdown_252"]) or pd.notna(r["ma200"]):
            b.append(r["drawdown_252"] < -0.20 or r["close"] / r["ma200"] < 0.85)

        top_flags.append(np.mean(t) if len(t) >= 3 else np.nan)
        bottom_flags.append(np.mean(b) if len(b) >= 3 else np.nan)
        top_dims.append(len(t))
        bottom_dims.append(len(b))

    df["top_score"] = top_flags
    df["bottom_score"] = bottom_flags
    df["top_dims"] = top_dims
    df["bottom_dims"] = bottom_dims
    return df.sort_values("date").reset_index(drop=True)


def main():
    breadth = build_breadth()
    breadth.to_csv(OUT / "breadth.csv", index=False, encoding="utf-8-sig")
    print(f"breadth: {breadth.shape}, {breadth.date.min()} -> {breadth.date.max()}")
    for index_name, pe_name in [
        ("hs300", "hs300"), ("zz500", "zz500"),
        ("zz1000", "zz1000"), ("cyb", "cyb50"), ("sse", "hs300"),
    ]:
        df = build_for_index(index_name, pe_name, breadth)
        path = OUT / f"signals_{index_name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        valid = df.dropna(subset=["top_score"])
        print(f"{index_name}: {df.shape}, top_score valid {valid.date.min()} -> {valid.date.max()}")


if __name__ == "__main__":
    main()
