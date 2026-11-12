#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 probe_data.py 抓到的原始缓存整理成回测用的统一格式。
东财 push2 接口在本机代理下不稳定，probe 已成功抓到的数据直接复用，避免重复请求。"""
import re
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).parent / "cache"


def read(name):
    return pd.read_csv(CACHE / name)


def write(df, name):
    df.to_csv(CACHE / name, index=False, encoding="utf-8-sig")
    print(f"{name}: {df.shape}")


def parse_cn_month(s):
    s = str(s).strip()
    m = re.match(r"(\d{4})年(\d{1,2})月", s)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0)
    m = re.match(r"(\d{4})(\d{2})", s)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0)
    return pd.NaT


def main():
    for src, dst in [
        ("index_hs300_em.csv", "index_hs300.csv"),
        ("index_zz500_em.csv", "index_zz500.csv"),
        ("index_zz1000_em.csv", "index_zz1000.csv"),
        ("index_cyb_em.csv", "index_cyb.csv"),
        ("index_sse_em.csv", "index_sse.csv"),
    ]:
        df = read(src)
        df["date"] = pd.to_datetime(df["date"])
        write(df.sort_values("date"), dst)

    for src, dst in [
        ("pe_hs300_lg.csv", "pe_hs300.csv"),
        ("pe_zz500_lg.csv", "pe_zz500.csv"),
        ("pe_zz1000_lg.csv", "pe_zz1000.csv"),
        ("pe_cyb50_lg.csv", "pe_cyb50.csv"),
    ]:
        raw = read(src)
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["日期"]),
            "pe_ttm": pd.to_numeric(raw["滚动市盈率"], errors="coerce"),
            "pe_median": pd.to_numeric(raw["滚动市盈率中位数"], errors="coerce"),
        }).dropna(subset=["pe_ttm"]).sort_values("date")
        write(out, dst)

    raw = read("bond_cn_us.csv")
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["日期"]),
        "cn_10y": pd.to_numeric(raw["中国国债收益率10年"], errors="coerce"),
    }).dropna(subset=["cn_10y"]).sort_values("date")
    write(out, "bond_10y.csv")

    raw = read("money_supply.csv")
    out = pd.DataFrame({
        "month": raw["月份"].map(parse_cn_month),
        "m1_yoy": pd.to_numeric(raw["货币(M1)-同比增长"], errors="coerce"),
        "m2_yoy": pd.to_numeric(raw["货币和准货币(M2)-同比增长"], errors="coerce"),
    }).dropna(subset=["month"]).sort_values("month")
    write(out, "money_supply.csv")

    raw = read("social_financing.csv")
    out = pd.DataFrame({
        "month": raw["月份"].map(parse_cn_month),
        "sz_inc": pd.to_numeric(raw["社会融资规模增量"], errors="coerce"),
    }).dropna(subset=["month"]).sort_values("month")
    write(out, "social_financing.csv")

    frames = []
    for src, label in [("margin_sh.csv", "sh"), ("margin_sz.csv", "sz")]:
        raw = read(src)
        tmp = pd.DataFrame({
            "date": pd.to_datetime(raw["日期"]),
            "balance": pd.to_numeric(raw["融资融券余额"], errors="coerce"),
        }).dropna(subset=["balance"])
        tmp["market"] = label
        frames.append(tmp)
    margin = (pd.concat(frames, ignore_index=True)
              .groupby("date", as_index=False)["balance"].sum()
              .sort_values("date"))
    write(margin, "margin.csv")

    raw = read("qvix_300etf.csv")
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]),
        "qvix": pd.to_numeric(raw["close"], errors="coerce"),
    }).dropna(subset=["qvix"]).sort_values("date")
    write(out, "qvix.csv")

    raw = read("new_funds_em.csv")
    raw["成立日期"] = pd.to_datetime(raw["成立日期"], errors="coerce")
    raw["募集份额"] = pd.to_numeric(raw["募集份额"], errors="coerce")
    raw = raw.dropna(subset=["成立日期"])
    raw = raw[raw["基金类型"].astype(str).str.contains("股票|混合", regex=True)]
    raw["month"] = raw["成立日期"].dt.to_period("M").dt.to_timestamp("M")
    out = (raw.groupby("month")
           .agg(fund_count=("基金代码", "count"), fund_size=("募集份额", "sum"))
           .reset_index().sort_values("month"))
    write(out, "new_funds.csv")


if __name__ == "__main__":
    main()
