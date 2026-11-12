#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并缓存周期择时回测所需数据。仅研究目录，不碰生产代码。"""
import re
import time
from pathlib import Path

import akshare as ak
import pandas as pd

BASE = Path(__file__).parent
CACHE = BASE / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
SECTOR_DIR = CACHE / "sectors"
SECTOR_DIR.mkdir(parents=True, exist_ok=True)

START = "20050101"
END = "20261231"


def save(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  saved {path.name}: {df.shape}")


def cached(path):
    if path.exists():
        df = pd.read_csv(path)
        if len(df) > 0:
            print(f"  cached {path.name}: {df.shape}")
            return df
    return None


def fetch_index(symbol, name):
    path = CACHE / f"index_{name}.csv"
    df = cached(path)
    if df is not None:
        return df
    print(f"fetch index {name} ({symbol})")
    df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=START, end_date=END)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    save(df, path)
    return df


def fetch_pe(symbol, name):
    path = CACHE / f"pe_{name}.csv"
    df = cached(path)
    if df is not None:
        return df
    print(f"fetch PE {name}")
    raw = ak.stock_index_pe_lg(symbol=symbol)
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["日期"]),
        "pe_ttm": pd.to_numeric(raw["滚动市盈率"], errors="coerce"),
        "pe_median": pd.to_numeric(raw["滚动市盈率中位数"], errors="coerce"),
    }).dropna(subset=["pe_ttm"]).sort_values("date").reset_index(drop=True)
    save(out, path)
    return out


def fetch_bond():
    path = CACHE / "bond_10y.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch 10Y bond")
    raw = ak.bond_zh_us_rate(start_date=START)
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["日期"]),
        "cn_10y": pd.to_numeric(raw["中国国债收益率10年"], errors="coerce"),
    }).dropna(subset=["cn_10y"]).sort_values("date").reset_index(drop=True)
    save(out, path)
    return out


def parse_cn_month(s):
    s = str(s).strip()
    m = re.match(r"(\d{4})年(\d{1,2})月", s)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0)
    m = re.match(r"(\d{4})(\d{2})", s)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) + pd.offsets.MonthEnd(0)
    return pd.NaT


def fetch_money():
    path = CACHE / "money_supply.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch M1/M2")
    raw = ak.macro_china_money_supply()
    out = pd.DataFrame({
        "month": raw["月份"].map(parse_cn_month),
        "m1_yoy": pd.to_numeric(raw["货币(M1)-同比增长"], errors="coerce"),
        "m2_yoy": pd.to_numeric(raw["货币和准货币(M2)-同比增长"], errors="coerce"),
    }).dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
    save(out, path)
    return out


def fetch_social():
    path = CACHE / "social_financing.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch social financing")
    raw = ak.macro_china_shrzgm()
    out = pd.DataFrame({
        "month": raw["月份"].map(parse_cn_month),
        "sz_inc": pd.to_numeric(raw["社会融资规模增量"], errors="coerce"),
    }).dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
    save(out, path)
    return out


def fetch_margin():
    path = CACHE / "margin.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch margin SSE/SZSE")
    frames = []
    for fn, label in [(ak.macro_china_market_margin_sh, "sh"),
                      (ak.macro_china_market_margin_sz, "sz")]:
        raw = fn()
        tmp = pd.DataFrame({
            "date": pd.to_datetime(raw["日期"]),
            "balance": pd.to_numeric(raw["融资融券余额"], errors="coerce"),
        }).dropna(subset=["balance"])
        tmp["market"] = label
        frames.append(tmp)
    long = pd.concat(frames, ignore_index=True)
    out = (long.groupby("date", as_index=False)["balance"].sum()
           .sort_values("date").reset_index(drop=True))
    save(out, path)
    return out


def fetch_qvix():
    path = CACHE / "qvix.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch QVIX")
    raw = ak.index_option_300etf_qvix()
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]),
        "qvix": pd.to_numeric(raw["close"], errors="coerce"),
    }).dropna(subset=["qvix"]).sort_values("date").reset_index(drop=True)
    save(out, path)
    return out


def fetch_new_funds():
    path = CACHE / "new_funds.csv"
    df = cached(path)
    if df is not None:
        return df
    print("fetch new funds")
    raw = ak.fund_new_found_em()
    raw["成立日期"] = pd.to_datetime(raw["成立日期"], errors="coerce")
    raw["募集份额"] = pd.to_numeric(raw["募集份额"], errors="coerce")
    raw["is_equity"] = raw["基金类型"].astype(str).str.contains("股票|混合", regex=True)
    raw = raw.dropna(subset=["成立日期"])
    raw["month"] = raw["成立日期"].dt.to_period("M").dt.to_timestamp("M")
    eq = raw[raw["is_equity"]]
    out = (eq.groupby("month")
           .agg(fund_count=("基金代码", "count"), fund_size=("募集份额", "sum"))
           .reset_index().sort_values("month").reset_index(drop=True))
    save(out, path)
    return out


def fetch_sectors():
    print("fetch sector list")
    raw = ak.stock_board_industry_name_ths()
    names = raw["name"].tolist()
    save(raw, CACHE / "sector_names.csv")
    for i, name in enumerate(names, 1):
        path = SECTOR_DIR / f"{name}.csv"
        if path.exists() and path.stat().st_size > 100:
            continue
        try:
            df = ak.stock_board_industry_index_ths(
                symbol=name, start_date="20100101", end_date=END)
            if df is None or len(df) < 60:
                print(f"  [{i}/{len(names)}] {name}: empty")
                continue
            out = pd.DataFrame({
                "date": pd.to_datetime(df["日期"]),
                "close": pd.to_numeric(df["收盘价"], errors="coerce"),
            }).dropna().sort_values("date").reset_index(drop=True)
            save(out, path)
        except Exception as e:
            print(f"  [{i}/{len(names)}] {name}: FAILED {type(e).__name__} {str(e)[:80]}")
        time.sleep(0.15)


# ──────────────────────────────────────────────────────────────
# 日频增量更新：面板用。main() 保留首次全量抓取。
# 指数 OHLC 走 Sina；成交额走 CSIndex（沪深300/中证500/中证1000）。
# 创业板指无 CSIndex 成交额，保留已有东财成交额，新日期留空。
# ──────────────────────────────────────────────────────────────

INDEX_SYMBOLS = [
    ("hs300", "sh000300", "000300"),
    ("zz500", "sh000905", "000905"),
    ("zz1000", "sh000852", "000852"),
    ("cyb", "sz399006", None),
    ("sse", "sh000001", None),
]


def _replace(path, df, cols=None):
    if cols is not None:
        df = df[cols]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  updated {path.name}: {df.shape}")


def update_indexes():
    for name, symbol, cs in INDEX_SYMBOLS:
        path = CACHE / f"index_{name}.csv"
        old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame()
        new = ak.stock_zh_index_daily(symbol=symbol)
        new["date"] = pd.to_datetime(new["date"])
        new = new.sort_values("date").drop_duplicates("date").set_index("date")
        new["amount"] = float("nan")
        if cs:
            try:
                c = ak.stock_zh_index_hist_csindex(
                    symbol=cs, start_date=START, end_date=END)
                c["date"] = pd.to_datetime(c["日期"])
                c["amount"] = pd.to_numeric(c["成交金额"], errors="coerce") * 1e8
                new["amount"] = (c.drop_duplicates("date").set_index("date")["amount"]
                                 .reindex(new.index))
            except Exception as e:
                print(f"  {name}: CSIndex amount failed {type(e).__name__}")
        if not old.empty:
            old = old.drop_duplicates("date").set_index("date")
            for col in ("open", "close", "high", "low", "volume"):
                if col in old.columns:
                    new[col] = new[col].combine_first(old[col].reindex(new.index))
            if "amount" in old.columns:
                new["amount"] = new["amount"].combine_first(old["amount"].reindex(new.index))
            extra = old[~old.index.isin(new.index)]
            if not extra.empty:
                new = pd.concat([extra, new]).sort_index()
        out = new.reset_index()
        _replace(path, out, ["date", "open", "close", "high", "low", "volume", "amount"])


def update_pe():
    for symbol, name in [("沪深300", "hs300"), ("中证500", "zz500"),
                         ("中证1000", "zz1000"), ("创业板50", "cyb50")]:
        raw = ak.stock_index_pe_lg(symbol=symbol)
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["日期"]),
            "pe_ttm": pd.to_numeric(raw["滚动市盈率"], errors="coerce"),
            "pe_median": pd.to_numeric(raw["滚动市盈率中位数"], errors="coerce"),
        }).dropna(subset=["pe_ttm"]).sort_values("date").reset_index(drop=True)
        _replace(CACHE / f"pe_{name}.csv", out)


def update_bond():
    raw = ak.bond_zh_us_rate(start_date=START)
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["日期"]),
        "cn_10y": pd.to_numeric(raw["中国国债收益率10年"], errors="coerce"),
    }).dropna(subset=["cn_10y"]).sort_values("date").reset_index(drop=True)
    _replace(CACHE / "bond_10y.csv", out)


def update_money():
    raw = ak.macro_china_money_supply()
    out = pd.DataFrame({
        "month": raw["月份"].map(parse_cn_month),
        "m1_yoy": pd.to_numeric(raw["货币(M1)-同比增长"], errors="coerce"),
        "m2_yoy": pd.to_numeric(raw["货币和准货币(M2)-同比增长"], errors="coerce"),
    }).dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
    _replace(CACHE / "money_supply.csv", out)


def update_social():
    try:
        raw = ak.macro_china_shrzgm()
        out = pd.DataFrame({
            "month": raw["月份"].map(parse_cn_month),
            "sz_inc": pd.to_numeric(raw["社会融资规模增量"], errors="coerce"),
        }).dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
        if len(out):
            _replace(CACHE / "social_financing.csv", out)
    except Exception as e:
        print(f"  social financing unavailable: {type(e).__name__}")


def update_margin():
    frames = []
    for fn, label in [(ak.macro_china_market_margin_sh, "sh"),
                      (ak.macro_china_market_margin_sz, "sz")]:
        raw = fn()
        tmp = pd.DataFrame({
            "date": pd.to_datetime(raw["日期"]),
            "balance": pd.to_numeric(raw["融资融券余额"], errors="coerce"),
        }).dropna(subset=["balance"])
        tmp["market"] = label
        frames.append(tmp)
    long = pd.concat(frames, ignore_index=True)
    out = (long.groupby("date", as_index=False)["balance"].sum()
           .sort_values("date").reset_index(drop=True))
    _replace(CACHE / "margin.csv", out)


def update_qvix():
    raw = ak.index_option_300etf_qvix()
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]),
        "qvix": pd.to_numeric(raw["close"], errors="coerce"),
    }).dropna(subset=["qvix"]).sort_values("date").reset_index(drop=True)
    _replace(CACHE / "qvix.csv", out)


def update_sectors(days: int = 10):
    names_path = CACHE / "sector_names.csv"
    try:
        raw = ak.stock_board_industry_name_ths()
        names = raw["name"].tolist()
        save(raw, names_path)
    except Exception:
        names = pd.read_csv(names_path)["name"].tolist() if names_path.exists() else []
    for i, name in enumerate(names, 1):
        path = SECTOR_DIR / f"{name}.csv"
        old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame()
        start = "20100101"
        if not old.empty:
            start = (old["date"].max() - pd.Timedelta(days=days)).strftime("%Y%m%d")
        try:
            df = ak.stock_board_industry_index_ths(
                symbol=name, start_date=start, end_date=END)
            if df is None or len(df) == 0:
                continue
            out = pd.DataFrame({
                "date": pd.to_datetime(df["日期"]),
                "close": pd.to_numeric(df["收盘价"], errors="coerce"),
            }).dropna().sort_values("date").reset_index(drop=True)
            if not old.empty:
                out = (pd.concat([old, out])
                       .drop_duplicates("date", keep="last").sort_values("date"))
            _replace(path, out)
        except Exception as e:
            print(f"  sector {name}: {type(e).__name__}")
        time.sleep(0.05)


def update():
    print("update indexes")
    update_indexes()
    print("update PE")
    update_pe()
    print("update bond")
    update_bond()
    print("update money")
    update_money()
    print("update social")
    update_social()
    print("update margin")
    update_margin()
    print("update QVIX")
    update_qvix()
    print("update sectors")
    update_sectors()


def main():
    fetch_index("sh000300", "hs300")
    fetch_index("sh000905", "zz500")
    fetch_index("sh000852", "zz1000")
    fetch_index("sz399006", "cyb")
    fetch_index("sh000001", "sse")
    fetch_pe("沪深300", "hs300")
    fetch_pe("中证500", "zz500")
    fetch_pe("中证1000", "zz1000")
    fetch_pe("创业板50", "cyb50")
    fetch_bond()
    fetch_money()
    fetch_social()
    fetch_margin()
    fetch_qvix()
    fetch_new_funds()
    fetch_sectors()


if __name__ == "__main__":
    main()
