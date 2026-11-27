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
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from dateutil.easter import easter

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


# --- NYSE 交易日历 ---------------------------------------------------------
# 只用来回答“现在该拿到哪天的收盘”。半日市（如感恩节次日）照常算交易日。

# 常规规则之外的临时休市（国葬、飓风等，发生频率很低）
_SPECIAL_CLOSURES = frozenset({
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),                        # 里根国葬
    date(2007, 1, 2),                         # 福特国葬
    date(2012, 10, 29), date(2012, 10, 30),   # 飓风桑迪
    date(2018, 12, 5),                        # 老布什国葬
    date(2025, 1, 9),                         # 卡特国葬
})


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """NYSE 补休规则：周六休市提前到周五，周日休市顺延到周一。"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _us_market_holidays(year: int) -> dict[date, str]:
    days = {
        _observed(date(year, 1, 1)): "元旦",
        _nth_weekday(year, 1, 0, 3): "马丁·路德·金日",
        _nth_weekday(year, 2, 0, 3): "总统日",
        easter(year) - timedelta(days=2): "耶稣受难日",
        _last_weekday(year, 5, 0): "阵亡将士纪念日",
        _observed(date(year, 7, 4)): "独立日",
        _nth_weekday(year, 9, 0, 1): "劳动节",
        _nth_weekday(year, 11, 3, 4): "感恩节",
        _observed(date(year, 12, 25)): "圣诞节",
    }
    if year >= 2022:  # NYSE 自 2022 年起六月节休市
        days[_observed(date(year, 6, 19))] = "六月节"
    # 若次年元旦在周六，按补休规则当年 12/31 也休市
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        days[next_new_year] = "元旦"
    return days


def _us_holiday_name(d: date) -> str | None:
    """该日若是美股休市日，返回节日名；周末和交易日返回 None。"""
    if d.weekday() >= 5:
        return None
    name = _us_market_holidays(d.year).get(d)
    if name:
        return name
    return "临时休市" if d in _SPECIAL_CLOSURES else None


def _is_us_trading_day(d: date) -> bool:
    return d.weekday() < 5 and _us_holiday_name(d) is None


def _now_et() -> pd.Timestamp:
    return pd.Timestamp.now(tz="America/New_York")


def _last_session_date(today: date | None = None,
                       now_et: pd.Timestamp | None = None) -> date:
    """按“周一至周五都有盘”的日历，现在应该已经收盘的那一天（不扣节假日）。

    用来判断“本该有的这一场”是不是休市。传 today（测试用）时按该日早上处理；
    now_et 用于测试时注入固定的纽约时刻。
    """
    if today is not None:
        d = today - timedelta(days=1)
    else:
        now = now_et if now_et is not None else _now_et()
        d = now.date()
        if not (now.hour > 16 or (now.hour == 16 and now.minute >= 30)):
            d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _expected_us_date(today: date | None = None,
                      now_et: pd.Timestamp | None = None) -> pd.Timestamp:
    """现在应该已经收盘、可以拿到数据的最新美股交易日（跳过休市日）。

    不传 today 时看纽约时间；传 today（测试用）时按“该日早上、前一交易日已收盘”处理。
    """
    d = _last_session_date(today, now_et=now_et)
    for _ in range(14):
        if _is_us_trading_day(d):
            return pd.Timestamp(d)
        d -= timedelta(days=1)
    raise RuntimeError("连续 14 天找不到美股交易日，交易日历异常")


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
