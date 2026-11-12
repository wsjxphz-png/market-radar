#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取宽基 ETF 日频份额，作为“国家队/大资金”日频代理。

公开数据里没有按主体（汇金/证金/国新/诚通）披露的每日买卖，
但交易所每天披露 ETF 份额。份额增加只能来自一级市场申购（用一篮子
股票或现金换份额），因此“下跌中份额逆势大增”是大资金托底的高概率信号。
本脚本只做数据采集，不做归因。
"""
from __future__ import annotations

import time
from pathlib import Path

import akshare as ak
import pandas as pd

BASE = Path(__file__).parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)
SIG = BASE / "signals"

BASKETS: dict[str, list[str]] = {
    "沪深300": ["510300", "510310", "510330", "159919"],
    "上证50": ["510050"],
    "中证500": ["510500", "159922"],
    "中证1000": ["512100", "159845"],
    "创业板": ["159915"],
    "科创50": ["588000"],
}
NAMES = {
    "510300": "华泰柏瑞沪深300ETF",
    "510310": "易方达沪深300ETF",
    "510330": "华夏沪深300ETF",
    "159919": "嘉实沪深300ETF",
    "510050": "华夏上证50ETF",
    "510500": "南方中证500ETF",
    "159922": "嘉实中证500ETF",
    "512100": "南方中证1000ETF",
    "159845": "华夏中证1000ETF",
    "159915": "易方达创业板ETF",
    "588000": "华夏科创50ETF",
}
SSE_CODES = {"510300", "510310", "510330", "510050", "510500", "512100", "588000"}
SZSE_CODES = {"159919", "159922", "159845", "159915"}


def trading_dates(start: str, end: str) -> list[pd.Timestamp]:
    # 用指数缓存而不是信号文件：信号文件可能几天没重建，
    # 中间交易日不会出现在任何一版信号里，SSE 逐日抓取就会永久漏抓。
    s = pd.read_csv(CACHE / "index_hs300.csv", parse_dates=["date"])
    d = s.loc[(s.date >= start) & (s.date <= end), "date"].drop_duplicates().sort_values()
    return list(d)


def load_cache() -> pd.DataFrame:
    path = CACHE / "etf_shares.csv"
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"], dtype={"code": str})
        return df.drop_duplicates(subset=["date", "code"], keep="last")
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "code": pd.Series(dtype="object"),
        "name": pd.Series(dtype="object"),
        "exchange": pd.Series(dtype="object"),
        "shares": pd.Series(dtype="float64"),
    })


def save_cache(df: pd.DataFrame) -> None:
    df = (df.drop_duplicates(subset=["date", "code"], keep="last")
          .sort_values(["date", "code"]).reset_index(drop=True))
    df.to_csv(CACHE / "etf_shares.csv", index=False, encoding="utf-8-sig")
    print(f"  saved etf_shares.csv: {df.shape}, {df.date.min().date()} -> {df.date.max().date()}")


def fetch_sse(dates: list[pd.Timestamp], have: pd.DataFrame) -> pd.DataFrame:
    """SSE 按单日查询；只补缺失的交易日。"""
    # 只有该日 7 只 SSE ETF 全部到齐才算完成，避免某次部分抓取后永久留缺口
    if have.empty:
        done = set()
    else:
        h = have[have.exchange == "SSE"]
        cnt = h.groupby(h["date"].dt.normalize())["code"].nunique()
        done = set(cnt[cnt >= len(SSE_CODES)].index)
    todo = [d for d in dates if d.normalize() not in done]
    print(f"SSE: {len(todo)} dates to fetch ({len(dates)} total)")
    rows = []
    for i, d in enumerate(todo, 1):
        try:
            raw = ak.fund_etf_scale_sse(date=d.strftime("%Y%m%d"))
        except Exception as e:
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {d.date()}: {type(e).__name__} (skip)")
            continue
        if raw is None or len(raw) == 0:
            continue
        hit = raw[raw["基金代码"].astype(str).isin(SSE_CODES)]
        for _, r in hit.iterrows():
            code = str(r["基金代码"])
            rows.append({
                "date": pd.Timestamp(d).normalize(),
                "code": code,
                "name": NAMES.get(code, str(r["基金简称"])),
                "exchange": "SSE",
                "shares": float(r["基金份额"]),
            })
        if i % 25 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] {d.date()} rows={len(hit)}")
        time.sleep(0.03)
    return pd.DataFrame(rows)


def fetch_szse(start: str, end: str) -> pd.DataFrame:
    """SZSE 支持区间查询，单次不超过 6 个月。"""
    print(f"SZSE: {start} -> {end}")
    raw = ak.fund_scale_daily_szse(start_date=start, end_date=end, symbol="ETF")
    if raw is None or len(raw) == 0:
        return pd.DataFrame()
    raw["基金代码"] = raw["基金代码"].astype(str).str.zfill(6)
    hit = raw[raw["基金代码"].isin(SZSE_CODES)].copy()
    return pd.DataFrame({
        "date": pd.to_datetime(hit["日期"]).dt.normalize(),
        "code": hit["基金代码"],
        "name": hit["基金代码"].map(NAMES),
        "exchange": "SZSE",
        "shares": pd.to_numeric(hit["基金份额"], errors="coerce"),
    }).dropna(subset=["shares"])


def fetch_prices() -> pd.DataFrame:
    """ETF 日线收盘价，用于把份额变化换算成金额。"""
    path = CACHE / "etf_prices.csv"
    if path.exists():
        old = pd.read_csv(path, parse_dates=["date"], dtype={"code": str})
    else:
        old = pd.DataFrame(columns=["date", "code", "close", "amount"])
    rows = []
    for code in NAMES:
        sym = ("sh" if code in SSE_CODES else "sz") + code
        try:
            df = ak.fund_etf_hist_sina(symbol=sym)
            df["date"] = pd.to_datetime(df["date"])
            df["code"] = code
            rows.append(df[["date", "code", "close", "amount"]])
        except Exception as exc:
            print(f"  price {code} {sym} FAILED {type(exc).__name__} {str(exc)[:100]}")
    if not rows:
        return old
    out = pd.concat([old] + rows, ignore_index=True)
    out = (out.dropna(subset=["close"])
           .drop_duplicates(subset=["date", "code"], keep="last")
           .sort_values(["date", "code"]).reset_index(drop=True))
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  saved etf_prices.csv: {out.shape}, {out.date.min().date()} -> {out.date.max().date()}")
    return out


def update(start: str = "2025-11-01", end: str | None = None) -> pd.DataFrame:
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    dates = trading_dates(start, end)
    if not dates:
        raise RuntimeError("no trading dates in signal file")
    cache = load_cache()

    # SZSE: 按 <=6 个月切窗，区间查询便宜
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    frames = [cache]
    while s <= e:
        win_end = min(s + pd.DateOffset(months=6) - pd.Timedelta(days=1), e)
        try:
            frames.append(fetch_szse(s.strftime("%Y%m%d"), win_end.strftime("%Y%m%d")))
        except Exception as exc:
            print(f"  SZSE {s.date()}->{win_end.date()} FAILED {type(exc).__name__} {str(exc)[:120]}")
        s = win_end + pd.Timedelta(days=1)

    # SSE: 逐日补缺
    frames.append(fetch_sse(dates, cache))

    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["shares"]).drop_duplicates(subset=["date", "code"], keep="last")
    save_cache(out)
    fetch_prices()
    return out


def main() -> None:
    update()


if __name__ == "__main__":
    main()
