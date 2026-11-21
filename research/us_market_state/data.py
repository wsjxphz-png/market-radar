# -*- coding: utf-8 -*-
"""数据层：美股 ETF/个股日线 + FRED 宏观。

- 所有数据落盘到 cache/，同一份数据 4 天内不重复抓。
- 抓取失败时回退到旧缓存，并在调用方给出数据日期，避免静默用错数据。
- FRED 优先 requests，失败时回退 curl（本机 SSL 偶发问题）。
"""
from __future__ import annotations

import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)

FRED_SERIES = ("VIXCLS", "VXVCLS", "BAA10Y")
MAX_AGE_DAYS = 4
SPLIT_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20)


def _cache_path(name: str) -> Path:
    return CACHE / f"{name}.csv"


def _read_cached(name: str) -> pd.Series | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        if df.empty:
            return None
        return df.set_index("date").iloc[:, 0].sort_index()
    except Exception:
        return None


def _read_cached_df(symbol: str) -> pd.DataFrame | None:
    """读美股原始 OHLC；旧缓存只有 date/close 时视为不可用，强制重抓。"""
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        if df.empty or not {"date", "open", "high", "low", "close"}.issubset(df.columns):
            return None
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


def _adjust_splits(df: pd.DataFrame) -> pd.DataFrame:
    """识别最近 250 个交易日内的拆股，并把拆股前的价格复权。

    为什么只看最近 250 天：更早的拆股不影响当前 200 日线、52 周高点和 10 周线；
    而老数据里的真实暴跌（例如 AAPL 2000-09-29）长得也像拆股，全历史扫描会误伤。
    拆股当天 open/high/low/close 相对昨收都会落在 1/k 附近（k 是拆股比例）；
    真实的单日暴跌通常不会四个价格都紧贴同一个整比例，所以用“四个价格都在
    中位数 ±10% 内 + 比例在常见拆股列表里”来判定，而不是只看收盘价。
    """
    df = df.sort_values("date").reset_index(drop=True).copy()
    prev_close = df["close"].shift(1)
    events: list[tuple[int, int]] = []
    cutoff = max(0, len(df) - 250)
    for i in range(len(df) - 1, cutoff - 1, -1):
        pc = prev_close.iloc[i]
        if pd.isna(pc) or pc <= 0:
            continue
        ratios = [
            df["open"].iloc[i] / pc, df["high"].iloc[i] / pc,
            df["low"].iloc[i] / pc, df["close"].iloc[i] / pc,
        ]
        if any(pd.isna(x) for x in ratios):
            continue
        med = sorted(ratios)[1]
        if not (0.02 < med < 0.6):
            continue
        factor = 1 / med
        nearest = min(SPLIT_RATIOS, key=lambda f: abs(factor - f))
        if abs(factor - nearest) / nearest > 0.10:
            continue
        base = 1 / nearest
        if all(abs(x - base) / base < 0.10 for x in ratios):
            events.append((i, nearest))
    for i, k in events:
        df.loc[:i - 1, ["open", "high", "low", "close"]] *= (1 / k)
        print(f"[data] 检测到拆股：{df['date'].iloc[i].date()} 1:{k}，已复权")
    return df


def _expected_us_date(today: date | None = None) -> pd.Timestamp:
    """今天早上应该已经拿到的最新美股交易日（近似处理周末；节假日会多抓一次）。"""
    ts = pd.Timestamp(today or date.today())
    if ts.weekday() == 0:       # 周一 -> 上周五
        return ts - pd.Timedelta(days=3)
    if ts.weekday() == 5:       # 周六 -> 周五
        return ts - pd.Timedelta(days=1)
    if ts.weekday() == 6:       # 周日 -> 周五
        return ts - pd.Timedelta(days=2)
    return ts - pd.Timedelta(days=1)


def _is_fresh(series: pd.Series | None) -> bool:
    """美股日线：必须已经更新到“今天该有的最近交易日”，不能用旧缓存冒充最新。"""
    if series is None or series.empty:
        return False
    last = pd.Timestamp(series.index.max()).normalize()
    return last >= _expected_us_date()


def _is_fresh_fred(series: pd.Series | None) -> bool:
    """FRED 日频序列常晚 1 天，给 4 天宽限；卡片里会标 as-of 日期。"""
    if series is None or series.empty:
        return False
    last = pd.Timestamp(series.index.max()).normalize()
    return (pd.Timestamp.today().normalize() - last).days <= MAX_AGE_DAYS


def _ak_daily(symbol: str) -> pd.DataFrame:
    import akshare as ak
    return ak.stock_us_daily(symbol=symbol)


def _fetch_with_timeout(symbol: str, timeout: int = 60) -> pd.DataFrame:
    """akshare 内部的 requests 没有 timeout；用线程包一层，避免一次挂起吃满 CI。"""
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_ak_daily, symbol)
        return fut.result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


def fetch_us(symbol: str, force: bool = False) -> pd.Series:
    """美股日线收盘价（已按拆股复权）。返回的 index 是交易日。"""
    cached_df = _read_cached_df(symbol)
    if cached_df is not None and _is_fresh(cached_df.set_index("date")["close"]) and not force:
        return _adjust_splits(cached_df).set_index("date")["close"]

    last_exc: Exception | None = None
    for _ in range(2):
        try:
            raw = _fetch_with_timeout(symbol)
            if raw is None or len(raw) == 0:
                raise ValueError("返回空数据")
            raw = raw.copy()
            raw["date"] = pd.to_datetime(raw["date"])
            raw = raw[["date", "open", "high", "low", "close"]].dropna().sort_values("date")
            if len(raw) < 200:
                raise ValueError("历史太短（少于 200 个交易日）")
            raw.to_csv(_cache_path(symbol), index=False)
            adjusted = _adjust_splits(raw)
            series = adjusted.set_index("date")["close"]
            ret = series.pct_change()
            if (ret.tail(250) < -0.35).any():
                print(f"[data] 警告：{symbol} 复权后仍有单日跌超 35%，可能是真实暴跌/数据异常")
            return series
        except Exception as exc:
            last_exc = exc

    if cached_df is not None:
        print(f"[data] {symbol} 抓取失败，沿用缓存（最后 {cached_df['date'].max().date()}）: "
              f"{type(last_exc).__name__} {last_exc}")
        return _adjust_splits(cached_df).set_index("date")["close"]
    raise RuntimeError(f"{symbol} 数据抓取失败，且没有缓存") from last_exc


def _fred_via_requests(series_id: str) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _fred_via_curl(series_id: str) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    out = subprocess.run(
        ["curl", "-sL", "--max-time", "60", url],
        capture_output=True, text=True, check=True,
    )
    if not out.stdout.strip():
        raise RuntimeError("curl 返回空数据")
    return out.stdout


def fetch_fred(series_id: str, force: bool = False) -> pd.Series:
    cached = _read_cached(f"fred_{series_id}")
    if cached is not None and _is_fresh_fred(cached) and not force:
        return cached

    text = None
    errors = []
    for fetcher in (_fred_via_requests, _fred_via_curl):
        try:
            candidate = fetcher(series_id)
            if "observation_date" not in candidate[:200]:
                raise ValueError("返回内容不是 FRED CSV（可能是拦截页）")
            text = candidate
            break
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {type(exc).__name__} {exc}")
    if text is None:
        if cached is not None:
            print(f"[data] FRED {series_id} 抓取失败，沿用缓存: {'; '.join(errors)}")
            return cached
        raise RuntimeError(f"FRED {series_id} 抓取失败: {'; '.join(errors)}")

    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        raise RuntimeError(f"FRED {series_id} 返回格式异常")
    df.columns = ["date", series_id]
    df["date"] = pd.to_datetime(df["date"])
    df[series_id] = pd.to_numeric(df[series_id], errors="coerce")
    df = df.dropna().sort_values("date")
    df.to_csv(_cache_path(f"fred_{series_id}"), index=False)
    return df.set_index("date")[series_id]


def load_macro(index: pd.DatetimeIndex, force: bool = False) -> pd.DataFrame:
    """把 FRED 日频序列对齐到给定交易日；缺失值用前值填充。"""
    out = pd.DataFrame(index=index)
    for series_id in FRED_SERIES:
        try:
            s = fetch_fred(series_id, force=force)
            out[series_id] = s.reindex(index, method="ffill")
        except Exception as exc:
            print(f"[data] 宏观 {series_id} 不可用: {type(exc).__name__} {exc}")
            out[series_id] = float("nan")
    return out


def load_rsp_spy_ratio(force: bool = False) -> pd.Series:
    """等权/市值权相对强弱；用来判断“指数涨是不是只靠少数大票”。"""
    rsp = fetch_us("RSP", force=force)
    spy = fetch_us("SPY", force=force)
    pair = pd.concat([rsp.rename("rsp"), spy.rename("spy")], axis=1).dropna()
    return (pair["rsp"] / pair["spy"]).sort_index()
