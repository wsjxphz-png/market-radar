#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源可用性探测：只读，不修改生产代码。"""
import os
import sys
import traceback
from pathlib import Path

import pandas as pd
import akshare as ak

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def probe(name, fn):
    print(f"\n=== {name} ===")
    try:
        df = fn()
        if df is None:
            print("None")
            return None
        print("shape:", df.shape)
        print("columns:", list(df.columns))
        for col in df.columns:
            if any(k in str(col).lower() for k in ("date", "日期", "month", "月份", "时间")):
                try:
                    s = pd.to_datetime(df[col], errors="coerce")
                    print(f"  {col}: {s.min()} -> {s.max()}")
                except Exception:
                    pass
        print(df.head(3).to_string())
        print(df.tail(3).to_string())
        df.to_csv(CACHE / f"{name}.csv", index=False, encoding="utf-8-sig")
        return df
    except Exception as e:
        print("FAILED:", type(e).__name__, str(e)[:300])
        traceback.print_exc(limit=2)
        return None


def main():
    probe("index_hs300_em", lambda: ak.stock_zh_index_daily_em(
        symbol="sh000300", start_date="20050101", end_date="20261231"))
    probe("index_zz500_em", lambda: ak.stock_zh_index_daily_em(
        symbol="sh000905", start_date="20050101", end_date="20261231"))
    probe("index_zz1000_em", lambda: ak.stock_zh_index_daily_em(
        symbol="sh000852", start_date="20050101", end_date="20261231"))
    probe("index_cyb_em", lambda: ak.stock_zh_index_daily_em(
        symbol="sz399006", start_date="20050101", end_date="20261231"))
    probe("index_sse_em", lambda: ak.stock_zh_index_daily_em(
        symbol="sh000001", start_date="20050101", end_date="20261231"))
    probe("pe_hs300_lg", lambda: ak.stock_index_pe_lg(symbol="沪深300"))
    probe("pe_zz500_lg", lambda: ak.stock_index_pe_lg(symbol="中证500"))
    probe("pe_zz1000_lg", lambda: ak.stock_index_pe_lg(symbol="中证1000"))
    probe("pe_cyb50_lg", lambda: ak.stock_index_pe_lg(symbol="创业板50"))
    probe("high_low_all", lambda: ak.stock_a_high_low_statistics(symbol="all"))
    probe("bond_cn_us", lambda: ak.bond_zh_us_rate(start_date="20050101"))
    probe("money_supply", lambda: ak.macro_china_money_supply())
    probe("social_financing", lambda: ak.macro_china_shrzgm())
    probe("shibor_all", lambda: ak.macro_china_shibor_all())
    probe("margin_sh", lambda: ak.macro_china_market_margin_sh())
    probe("margin_sz", lambda: ak.macro_china_market_margin_sz())
    probe("qvix_300etf", lambda: ak.index_option_300etf_qvix())
    probe("all_a_pb", lambda: ak.stock_a_all_pb())
    probe("new_funds_em", lambda: ak.fund_new_found_em())


if __name__ == "__main__":
    main()
