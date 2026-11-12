#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频面板的数据层。

只做计算，不渲染。所有信号只用当日收盘及以前可用的数据；
数据过期时显式标记，不把旧值当新信号。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
SIG = BASE / "signals"
CACHE = BASE / "cache"
OUT = BASE / "results"

INDEXES = [
    ("hs300", "沪深300", "sh000300"),
    ("zz500", "中证500", "sh000905"),
    ("zz1000", "中证1000", "sh000852"),
    ("cyb", "创业板指", "sz399006"),
]

ETF_BASKETS = {
    "沪深300": ["510300", "510310", "510330", "159919"],
    "上证50": ["510050"],
    "中证500": ["510500", "159922"],
    "中证1000": ["512100", "159845"],
    "创业板": ["159915"],
    "科创50": ["588000"],
}
ETF_NAMES = {
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


# ──────────────────────────────────────────────────────────────
# 基础工具
# ──────────────────────────────────────────────────────────────

def load_signal(name: str) -> pd.DataFrame:
    s = pd.read_csv(SIG / f"signals_{name}.csv", parse_dates=["date"])
    return s.sort_values("date").reset_index(drop=True)


def attach_credit_ok(s: pd.DataFrame, max_bdays: int = 45) -> pd.DataFrame:
    """逐日判断：当时能拿到的最新社融，是否仍在有效期内。

    这样历史区间按“当时的可得数据”计算，而不是用今天的停更状态回改历史。
    """
    social = pd.read_csv(CACHE / "social_financing.csv", parse_dates=["month"])
    months = social["month"].sort_values().values.astype("datetime64[D]")
    dates = s["date"].values.astype("datetime64[D]")
    cal = trading_calendar().astype("datetime64[D]")
    pos = np.searchsorted(months, dates, side="right") - 1
    row_pos = np.searchsorted(cal, dates, side="right")
    month_pos = np.searchsorted(cal, months, side="right")
    gap = row_pos - month_pos[np.clip(pos, 0, None)]
    ok = (pos >= 0) & (gap <= max_bdays)
    out = s.copy()
    out["credit_ok"] = ok
    return out


_CAL: np.ndarray | None = None


def trading_calendar() -> np.ndarray:
    global _CAL
    if _CAL is None:
        _CAL = pd.read_csv(SIG / "signals_hs300.csv", usecols=["date"],
                           parse_dates=["date"])["date"].values
    return _CAL


def bdays_between(a, b) -> int | None:
    """a 之后到 b（含）之间的实际交易日数；用信号文件的交易日历，不用工作日近似。"""
    if pd.isna(a) or pd.isna(b):
        return None
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    if a >= b:
        return 0
    cal = trading_calendar()
    return int(((cal > np.datetime64(a)) & (cal <= np.datetime64(b))).sum())


def pct_of_last(s: pd.Series, window: int = 250) -> float:
    s = s.dropna()
    if len(s) < 30:
        return float("nan")
    h = s.iloc[-window:]
    # 全零/恒定窗口没有“分位”可言；严格小于会返回 0，被误读成“持续赎回/派发”
    if float(h.max() - h.min()) <= 1e-12:
        return float("nan")
    return float((h < h.iloc[-1]).mean())


def fmt(v, spec: str, dash: str = "—") -> str:
    """NaN 安全的格式化；面板里不允许出现 'nan%'。"""
    if v is None:
        return dash
    try:
        if pd.isna(v):
            return dash
    except (TypeError, ValueError):
        return dash
    return format(v, spec)


def confirm(cond: pd.Series, k: int) -> pd.Series:
    if k <= 1:
        return cond.astype(bool)
    return cond.astype(int).rolling(k, min_periods=k).sum().eq(k)


# ──────────────────────────────────────────────────────────────
# 分数重算（信用维度停更时剔除）
# ──────────────────────────────────────────────────────────────

def recompute_flags(r: pd.Series, stale: set[str]) -> tuple[dict[str, bool], dict[str, bool]]:
    """与 build_signals.py 的逐行逻辑保持一致；stale 维度不参与。"""
    top: dict[str, bool] = {}
    bottom: dict[str, bool] = {}

    if "valuation" not in stale:
        if pd.notna(r["pe_pct"]):
            top["估值"] = bool(r["pe_pct"] >= 0.80)
            bottom["估值"] = bool(r["pe_pct"] <= 0.20)
        elif pd.notna(r["erp_pct"]):
            top["估值"] = bool(r["erp_pct"] <= 0.20)
            bottom["估值"] = bool(r["erp_pct"] >= 0.80)

    if "credit" not in stale and bool(r.get("credit_ok", True)):
        ct, cb = [], []
        if pd.notna(r["gap_chg3"]):
            ct.append(r["gap_chg3"] < 0 and r["cn_10y_chg60"] > 0)
            cb.append(r["gap_chg3"] > 0)
        if pd.notna(r["sz_yoy"]):
            ct.append(r["sz_yoy"] < 0)
            cb.append(r["sz_yoy"] > 0)
        if ct:
            top["信用"] = bool(any(ct))
            bottom["信用"] = bool(any(cb))

    if "breadth" not in stale:
        bt, bb = [], []
        if pd.notna(r["breadth_ma200_chg20"]):
            bt.append(r["ret_60"] > 0.05 and r["breadth_ma200_chg20"] < -0.10)
        if pd.notna(r["breadth_ma200"]):
            bt.append(r["drawdown_252"] > -0.05 and r["breadth_ma200"] < 0.45)
            bb.append(r["breadth_ma200"] < 0.25)
            bb.append(r["breadth_ma200"] < 0.40 and r["breadth_ma200_chg20"] > 0.10)
        if bt:
            top["广度"] = bool(any(bt))
            bottom["广度"] = bool(any(bb))

    if "sentiment" not in stale:
        st, sb = [], []
        if pd.notna(r["margin_20d_chg"]):
            st.append(r["margin_20d_chg"] > 0.05 and pd.notna(r["turnover_z"]) and r["turnover_z"] > 1.0)
            sb.append(r["margin_20d_chg"] < -0.05 or (pd.notna(r["turnover_z"]) and r["turnover_z"] < -0.5))
        if pd.notna(r["margin_pct"]):
            st.append(r["margin_pct"] > 0.90 and r["ret_60"] > 0.05)
        if pd.notna(r["qvix_pct"]):
            sb.append(r["qvix_pct"] > 0.80 and r["qvix_20d_chg"] < 0)
        if st:
            top["情绪"] = bool(any(st))
            bottom["情绪"] = bool(any(sb))

    if "trend" not in stale:
        if pd.notna(r["extension"]) or pd.notna(r["ret_120"]):
            top["趋势"] = bool(r["extension"] > 0.20 or r["ret_120"] > 0.25)
        if pd.notna(r["drawdown_252"]) or pd.notna(r["ma200"]):
            bottom["趋势"] = bool(r["drawdown_252"] < -0.20 or r["close"] / r["ma200"] < 0.85)

    return top, bottom


def score(flags: dict[str, bool]) -> float:
    return float(np.mean(list(flags.values()))) if len(flags) >= 3 else float("nan")


# ──────────────────────────────────────────────────────────────
# 区间统计
# ──────────────────────────────────────────────────────────────

def episodes(s: pd.DataFrame, col: str, threshold: float, min_len: int = 5, gap: int = 20):
    """连续满足条件的区间。

    间隔不超过 gap 个交易日（按行号，即真实交易日）的两段合并为一段：
    信号中途短暂跌破阈值不应把一轮顶部/底部拆成两段，更不能把更长的一段丢掉。
    """
    mask = (s[col] >= threshold).values
    runs = []
    i = 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(mask) and mask[j + 1]:
            j += 1
        if j - i + 1 >= min_len:
            runs.append((i, j))
        i = j + 1
    picked: list[tuple[int, int]] = []
    for a, b in runs:
        if picked and a - picked[-1][1] <= gap:
            picked[-1] = (picked[-1][0], b)
        else:
            picked.append((a, b))
    return picked


def zone_stats(s: pd.DataFrame, col: str, threshold: float) -> dict[str, Any]:
    """区间的事后统计。只用结束后仍有完整 120 个交易日的区间，
    避免把进行中/被截断的窗口混进“120日”中位数和概率。"""
    close = s["close"].values
    n = len(close)
    rows = []
    for a, b in episodes(s, col, threshold):
        row = {
            "zone_days": b - a + 1,
            "zone_ret": close[b] / close[a] - 1,
            "max_dd_120": np.nan,
            "max_up_120": np.nan,
            "days_to_low": np.nan,
            "post_dd_120": np.nan,
        }
        if b + 120 <= n - 1:
            fut = close[a + 1:b + 121]
            lo = int(np.argmin(fut)) + 1
            hi = int(np.argmax(fut)) + 1
            row["max_dd_120"] = close[a + lo] / close[a] - 1
            row["max_up_120"] = close[a + hi] / close[a] - 1
            row["days_to_low"] = lo
            row["post_dd_120"] = min(close[b + 1:b + 121]) / close[b] - 1
        rows.append(row)
    z = pd.DataFrame(rows)
    if z.empty:
        return {"n": 0}
    full = z.dropna(subset=["post_dd_120"])
    return {
        "n": len(z),
        "n_full": len(full),
        "days_med": float(z.zone_days.median()),
        "days_p25": float(z.zone_days.quantile(.25)),
        "days_p75": float(z.zone_days.quantile(.75)),
        "zone_ret_med": float(z.zone_ret.median()),
        "fwd_dd_med": float(full.max_dd_120.median()) if len(full) else np.nan,
        "fwd_up_med": float(full.max_up_120.median()) if len(full) else np.nan,
        "fwd_dd_10": float((full.max_dd_120 < -0.10).mean()) if len(full) else np.nan,
        "post_dd_med": float(full.post_dd_120.median()) if len(full) else np.nan,
        "post_dd_10": float((full.post_dd_120 < -0.10).mean()) if len(full) else np.nan,
        "days_low_med": float(full.days_to_low.median()) if len(full) else np.nan,
    }


def last_episode(s: pd.DataFrame, col: str, threshold: float,
                 min_len: int = 5) -> dict[str, Any] | None:
    mask = (s[col] >= threshold).values
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(mask) and mask[j + 1]:
            j += 1
        if j - i + 1 >= min_len:
            runs.append((i, j))
        i = j + 1
    if not runs:
        return None
    start, end = runs[-1]
    close = s["close"].values
    fut = close[start:min(start + 121, len(close))]
    return {
        "start": s.date.iloc[start].date(),
        "end": s.date.iloc[end].date(),
        "days": end - start + 1,
        "close_start": float(close[start]),
        "max_dd": float(np.min(fut) / close[start] - 1),
        "max_up": float(np.max(fut) / close[start] - 1),
        "is_current": end == len(s) - 1,
    }


def zone_days(s: pd.DataFrame, col: str, threshold: float) -> int:
    mask = (s[col] >= threshold).values
    if len(mask) == 0 or not mask[-1]:
        return 0
    i = len(mask) - 1
    while i > 0 and mask[i - 1]:
        i -= 1
    return len(mask) - i


# ──────────────────────────────────────────────────────────────
# 指数面板
# ──────────────────────────────────────────────────────────────

@dataclass
class IndexPanel:
    key: str
    name: str
    date: pd.Timestamp
    close: float
    chg1: float
    ma20: float
    ma50: float
    ma200: float
    above200: bool
    ma50_above200: bool
    drawdown_252: float
    ret_60: float
    ret_120: float
    extension: float
    pe_ttm: float
    pe_pct: float
    erp: float
    erp_pct: float
    top_raw: float
    top_adj: float
    bottom_raw: float
    bottom_adj: float
    top_flags: dict[str, bool]
    bottom_flags: dict[str, bool]
    state: str
    state_tone: str
    zone_days: int
    action: str
    reentry_ready: bool
    reentry_need: str
    last_top: dict[str, Any] | None
    last_bottom: dict[str, Any] | None
    top_zone: dict[str, Any]
    bottom_zone: dict[str, Any]
    above200_5: bool


def classify(close: float, ma200: float, top: float, bottom: float) -> tuple[str, str]:
    if top >= 0.6 and close > ma200:
        return "顶部风险区", "risk"
    if bottom >= 0.8:
        return "底部观察区（强）", "bottom"
    if bottom >= 0.6 and close <= ma200:
        return "底部观察区（弱）", "watch"
    if close > ma200:
        return "趋势正常", "ok"
    return "下跌趋势", "down"


def adjusted_series(s: pd.DataFrame, stale: set[str]) -> pd.DataFrame:
    """逐行重算分数，保证历史区间与当日口径一致（停更维度全程剔除）。"""
    tops, bots = [], []
    for _, row in s.iterrows():
        t, b = recompute_flags(row, stale)
        tops.append(score(t))
        bots.append(score(b))
    out = s.copy()
    out["top_adj"] = tops
    out["bottom_adj"] = bots
    return out


def build_index_panel(key: str, name: str, s: pd.DataFrame, stale: set[str]) -> IndexPanel:
    r = s.iloc[-1]
    eff = stale - {"credit"}  # 信用维度改为逐日 credit_ok 判断
    top_flags, bottom_flags = recompute_flags(r, eff)
    top_adj, bottom_adj = score(top_flags), score(bottom_flags)
    sa = adjusted_series(s, eff)
    state, tone = classify(r["close"], r["ma200"], top_adj, bottom_adj)

    above5 = bool(confirm(s["close"] > s["ma200"], 5).iloc[-1])
    if top_adj >= 0.6:
        action = "退出/防守：top≥0.6"
    elif r["close"] <= r["ma200"]:
        action = ("底部观察：等站上200日线5日+top<0.4"
                  if bottom_adj >= 0.8 else "防守：跌破200日线，未到强底部区")
    else:
        action = "持有：趋势向上且top<0.6"

    need = []
    if r["close"] <= r["ma200"]:
        need.append("站上200日线并连续5日")
    if top_adj >= 0.4:
        need.append("top<0.4")
    reentry_ready = bool(above5 and top_adj < 0.4)

    zone_days_now = 0
    if state.startswith("顶部"):
        zone_days_now = zone_days(sa, "top_adj", 0.6)
    elif state.startswith("底部"):
        zone_days_now = zone_days(sa, "bottom_adj", 0.8 if bottom_adj >= 0.8 else 0.6)

    return IndexPanel(
        key=key, name=name, date=r["date"], close=float(r["close"]),
        chg1=float(r["ret_1d"]) if pd.notna(r["ret_1d"]) else float("nan"),
        ma20=float(r["ma20"]), ma50=float(r["ma50"]), ma200=float(r["ma200"]),
        above200=bool(r["close"] > r["ma200"]),
        ma50_above200=bool(r["ma50"] > r["ma200"]),
        drawdown_252=float(r["drawdown_252"]),
        ret_60=float(r["ret_60"]), ret_120=float(r["ret_120"]),
        extension=float(r["extension"]),
        pe_ttm=float(r["pe_ttm"]), pe_pct=float(r["pe_pct"]),
        erp=float(r["erp"]), erp_pct=float(r["erp_pct"]),
        top_raw=float(r["top_score"]), top_adj=top_adj,
        bottom_raw=float(r["bottom_score"]), bottom_adj=bottom_adj,
        top_flags=top_flags, bottom_flags=bottom_flags,
        state=state, state_tone=tone, zone_days=zone_days_now, action=action,
        reentry_ready=reentry_ready,
        reentry_need="；".join(need) if need else "已满足",
        last_top=last_episode(sa, "top_adj", 0.6, min_len=5),
        last_bottom=last_episode(sa, "bottom_adj", 0.8, min_len=5),
        top_zone=zone_stats(sa, "top_adj", 0.6),
        bottom_zone=zone_stats(sa, "bottom_adj", 0.8),
        above200_5=above5,
    )


# ──────────────────────────────────────────────────────────────
# 五维读数（以沪深300为主）
# ──────────────────────────────────────────────────────────────

@dataclass
class DimItem:
    label: str
    value: str
    change: str
    meaning: str


@dataclass
class Dimension:
    name: str
    tone: str
    summary: str
    items: list[DimItem] = field(default_factory=list)
    # 给推送用的白话解释：指标名可以专业，冒号后的说明要让新手看懂“这意味着什么”
    plain: str = ""


def build_dimensions(s: pd.DataFrame, stale: set[str], social_date, margin_date) -> list[Dimension]:
    r = s.iloc[-1]
    top, bottom = recompute_flags(r, stale)
    dims: list[Dimension] = []
    pe_chg20 = (r["pe_pct"] - s["pe_pct"].iloc[-21]
                if len(s) > 21 and pd.notna(s["pe_pct"].iloc[-21]) else float("nan"))
    erp_chg20 = (r["erp_pct"] - s["erp_pct"].iloc[-21]
                 if len(s) > 21 and pd.notna(s["erp_pct"].iloc[-21]) else float("nan"))

    # 1 估值（tone 由实际 flag 决定，避免卡片结论与分数/触发列表矛盾）
    chg_txt = f"20日分位变化 {pe_chg20*100:+.0f}pp，" if pd.notna(pe_chg20) else ""
    if top.get("估值"):
        tone = "risk"
        summary = ("估值分位高位继续抬升，拥挤度上升，赔率下降。"
                   if pd.notna(pe_chg20) and pe_chg20 > 0
                   else "估值处于高分位，赔率偏低；分位变化不改变这一判断。")
    elif bottom.get("估值"):
        tone = "cheap"
        summary = ("估值分位从低位回升，赔率改善，但需要趋势确认。"
                   if pd.notna(pe_chg20) and pe_chg20 > 0
                   else "估值处于低分位，便宜但仍在左侧；低估值可以更低。")
    else:
        tone = "neutral"
        summary = f"估值中性，{chg_txt}不构成独立的买卖理由。"
    pe_txt = fmt(r["pe_ttm"], ".2f")
    pe_pct_txt = fmt(r["pe_pct"], ".0%")
    erp_pct_txt = fmt(r["erp_pct"], ".0%")
    if top.get("估值"):
        plain = (f"股票整体偏贵：市盈率 {pe_txt} 倍，比历史上 {pe_pct_txt} 的时间更贵。"
                 "市盈率可以粗略理解为“按现在的赚钱速度，多少年能赚回买入价”，越高说明价格里含的乐观预期越多。"
                 "偏贵不代表马上跌，但这时候买入，未来赚钱的空间相对小、亏钱的概率相对大。")
    elif bottom.get("估值"):
        plain = (f"股票整体偏便宜：市盈率 {pe_txt} 倍，比历史上 {pe_pct_txt} 的时间更便宜，"
                 "说明市场的悲观预期已经反映得比较多。但便宜可以更便宜，单凭便宜不能买，要等趋势修复。")
    else:
        plain = (f"估值不贵也不便宜：市盈率 {pe_txt} 倍，处在历史上 {pe_pct_txt} 的位置（100% 最贵、0% 最便宜）；"
                 f"风险溢价（股票相对债券的划算程度）处在历史 {erp_pct_txt} 的位置（越高越划算）。"
                 "单看估值，现在不构成买卖理由。")
    dims.append(Dimension("估值/风险溢价", tone, summary, [
        DimItem("PE(TTM)", fmt(r["pe_ttm"], ".2f"),
                f"历史分位 {fmt(r['pe_pct'], '.0%')}"
                + (f"（20日 {pe_chg20*100:+.0f}pp）" if pd.notna(pe_chg20) else ""),
                "分位越高，价格越依赖盈利增长；分位低说明悲观预期已计入较多。20日分位变化看的是估值在修复还是继续恶化。"),
        DimItem("ERP", fmt(r["erp"], ".2f") + "%",
                f"历史分位 {fmt(r['erp_pct'], '.0%')}"
                + (f"（20日 {erp_chg20*100:+.0f}pp）" if pd.notna(erp_chg20) else ""),
                "ERP=1/PE−10Y国债。越高，股票相对债券越便宜；极高分位常见于中长期底部区域，但不代表马上涨。"),
    ], plain))

    # 2 信用
    gap_txt = fmt(r["gap"], "+.1f") + "pp"
    gap_read = ("资金活化偏弱" if pd.notna(r["gap"]) and r["gap"] < 0
                else "资金活化改善" if pd.notna(r["gap"]) else "资金活化未知")
    bond_chg = (fmt(r["cn_10y_chg60"] * 100, "+.0f") + "bp"
                if pd.notna(r["cn_10y_chg60"]) else "—")
    if "credit" in stale:
        tone = "watch"
        summary = (f"社融数据源过期/停更（截止 {social_date}），信用维度不参与打分；"
                   f"M1−M2 {gap_txt}（{gap_read}），10Y {fmt(r['cn_10y'], '.2f')}%（60日 {bond_chg}）。")
    elif (pd.notna(r["gap_chg3"]) and r["gap_chg3"] > 0) or (pd.notna(r["sz_yoy"]) and r["sz_yoy"] > 0):
        tone, summary = "ok", "信用条件边际改善：资金活化或社融扩张至少一项为正。"
    else:
        tone, summary = "risk", "信用条件偏紧：M1-M2 未改善、社融同比为负，宽货币尚未转成宽信用。"
    gap_abs_txt = fmt(abs(r["gap"]), ".1f") if pd.notna(r["gap"]) else "—"
    if pd.notna(r["gap"]):
        if r["gap"] < 0:
            gap_plain = (f"企业活期存款增速比总存款增速低 {gap_abs_txt} 个百分点——"
                         "这个数为负，说明钱更多躺在定期和理财里，企业不愿意投资、大家的风险偏好低。")
        else:
            gap_plain = (f"企业活期存款增速比总存款增速高 {gap_abs_txt} 个百分点——"
                         "这个数为正，说明钱在变活、更愿意投资。")
    else:
        gap_plain = "企业活期存款增速（M1−M2）暂时没有数据。"
    bond_plain = (f"10年期国债利率 {fmt(r['cn_10y'], '.2f')}%，最近60个交易日"
                  f"{'下降' if pd.notna(r['cn_10y_chg60']) and r['cn_10y_chg60'] < 0 else '上升'}了 "
                  f"{fmt(abs(r['cn_10y_chg60']), '.2f') if pd.notna(r['cn_10y_chg60']) else '—'} 个百分点；"
                  "利率低对股票估值是支撑，但也可能说明经济预期偏弱。")
    if "credit" in stale:
        plain = (f"信用数据（社融）只更新到 {social_date}，已经很久没更新，这次判断不使用它。"
                 + gap_plain + bond_plain)
    elif tone == "ok":
        plain = ("资金和信用条件在改善：要么企业活期存款在变多（钱变活了），要么企业融资（社融）同比转正，"
                 "说明钱开始愿意进入实体经济，对股票是支撑。" + gap_plain + bond_plain)
    else:
        plain = ("资金和信用条件偏紧：钱没有明显变活，企业借钱扩张的意愿也弱，"
                 "央行放的宽松资金还没真正流到实体经济。" + gap_plain + bond_plain)
    dims.append(Dimension("流动性/信用", tone, summary, [
        DimItem("M1−M2 剪刀差", gap_txt,
                f"3个月变化 {fmt(r['gap_chg3'], '+.1f')}pp",
                "剪刀差回升=企业活期存款增加、资金活化；持续为负=资金淤积在定期/理财，风险偏好低。"),
        DimItem("社融3M同比", fmt(r["sz_yoy"], "+.1%"),
                f"数据截止 {social_date}（{'已停更，不参与' if 'credit' in stale else '可用'}）",
                "社融扩张=实体信用需求回升；收缩=宽货币未转化为宽信用。"),
        DimItem("10Y国债", fmt(r["cn_10y"], ".2f") + "%", f"60日 {bond_chg}",
                "利率上行压低估值分母；利率下行抬升估值分母，但衰退式下行无效。"),
    ], plain))

    # 3 广度
    bchg = (f"20日 {r['breadth_ma200_chg20']*100:+.0f}pp"
            if pd.notna(r["breadth_ma200_chg20"]) else "20日 —")
    if top.get("广度"):
        tone, summary = "risk", "指数可能还没跌，但内部已经恶化，是顶部背离的典型形态。"
    elif bottom.get("广度"):
        tone = "bottom"
        if pd.notna(r["breadth_ma200"]) and r["breadth_ma200"] < 0.25:
            summary = "行业广度极低，市场处于洗盘/熊市内部；这是底部区域的必要条件，不是充分条件。"
        else:
            summary = "广度从低位回升超过10pp，内部在修复；还需要趋势确认。"
    else:
        tone, summary = "neutral", "广度中性，内部结构没有明显恶化或改善。"
    breadth_txt = fmt(r["breadth_ma200"], ".0%")
    bchg_abs = (fmt(abs(r["breadth_ma200_chg20"]) * 100, ".0f")
                if pd.notna(r["breadth_ma200_chg20"]) else "—")
    bdir = ("多" if pd.notna(r["breadth_ma200_chg20"]) and r["breadth_ma200_chg20"] > 0
            else "少")
    if bottom.get("广度"):
        plain = (f"市场内部很弱：只有 {breadth_txt} 的行业，价格在200日线上方"
                 "（200日线≈过去一年的平均成本），"
                 f"比20个交易日前{bdir}了 {bchg_abs} 个百分点，说明绝大多数行业还在下跌。"
                 "这是底部区域常见的特征，但不代表马上见底；只有从低位明显回升（超过10个百分点），才算真正在修复。")
    elif top.get("广度"):
        plain = ("指数可能还没跌，但多数行业已经走弱——这是顶部常见的“背离”信号："
                 "表面指数撑着，内部资金已经在撤。")
    else:
        plain = "大多数行业和指数的方向差不多，市场内部没有明显的恶化或改善。"
    dims.append(Dimension("广度/趋势", tone, summary, [
        DimItem("站上200日线行业", fmt(r["breadth_ma200"], ".0%"), bchg,
                "比例越低，市场内部越弱；从低位回升 >10pp 才算修复。"),
        DimItem("20日新高−新低", fmt(r["breadth_nh_nl"], "+.1%"),
                f"覆盖 {fmt(r['breadth_n'], '.0f')} 个行业",
                "新低多于新高=短线仍在恶化；新高多于新低=短线企稳。"),
    ], plain))

    # 4 情绪
    margin_lag = bdays_between(margin_date, r["date"])
    margin_note = f"两融截止 {margin_date}（T-{margin_lag}）"
    lag_prefix = f"两融数据 T-{margin_lag}，情绪结论降级；" if margin_lag and margin_lag > 2 else ""
    if top.get("情绪"):
        tone = "risk"
        summary = lag_prefix + "杠杆/仓位处于高位（两融大增或余额高分位），注意反向。"
    elif bottom.get("情绪"):
        tone = "bottom"
        if pd.notna(r["margin_20d_chg"]) and r["margin_20d_chg"] < -0.05:
            summary = lag_prefix + "杠杆资金去化，抛压释放中；但去化本身不是买点。"
        elif pd.notna(r["turnover_z"]) and r["turnover_z"] < -0.5:
            summary = lag_prefix + "缩量下跌，抛压衰竭中；缩量本身不是买点。"
        else:
            summary = lag_prefix + "QVIX 高位回落，恐慌正在释放。"
    else:
        tone, summary = "neutral", lag_prefix + "情绪中性：没有拥挤，也没有极端恐慌。"
    margin_chg = r["margin_20d_chg"]
    lag_plain = (f"两融数据比行情晚 {margin_lag} 个交易日，这一项要打折扣看。"
                 if margin_lag and margin_lag > 2 else "")
    if pd.notna(margin_chg):
        margin_plain = (f"两融余额（借钱炒股的总金额，越高说明杠杆越重）最近20个交易日"
                        f"{'减少' if margin_chg < 0 else '增加'}了 {fmt(abs(margin_chg), '.1%')}")
    else:
        margin_plain = "两融余额变化暂时没有数据"
    qvix_plain = (f"恐慌指数 QVIX 是 {fmt(r['qvix'], '.2f')}，"
                  f"处在历史 {fmt(r['qvix_pct'], '.0%')} 的位置（100% 最恐慌、0% 最平静）")
    if bottom.get("情绪"):
        plain = ("投资者在去杠杆、恐慌在释放：" + margin_plain + "；" + qvix_plain + "。"
                 "意思是抛压在减轻，但“跌到没人敢买”本身不是买入信号。" + lag_plain)
    elif top.get("情绪"):
        plain = ("杠杆资金偏多、交易偏热：" + margin_plain + "；" + qvix_plain + "。"
                 "大家越拥挤，越容易出现反向波动，规则上要注意风险。" + lag_plain)
    else:
        plain = ("情绪不狂热也不恐慌：" + margin_plain + "；" + qvix_plain + "。单看情绪没有明显信号。" + lag_plain)
    dims.append(Dimension("情绪/仓位", tone, summary, [
        DimItem("两融余额20日", fmt(r["margin_20d_chg"], "+.1%"),
                f"历史分位 {fmt(r['margin_pct'], '.0%')}；{margin_note}",
                "两融20日增 >5% 且成交额 z>1，说明杠杆资金拥挤；20日降 >5%，说明去杠杆。"),
        DimItem("成交额 z-score" if r.get("turnover_src", "amount") != "volume"
                else "成交量 z-score（成交额缺失，降级）",
                fmt(r["turnover_z"], "+.2f"),
                "60日标准化",
                "z>1 放量，z<-0.5 缩量；放量下跌是出货，缩量下跌是抛压衰竭。"),
        DimItem("QVIX", fmt(r["qvix"], ".2f"),
                (f"历史分位 {fmt(r['qvix_pct'], '.0%')}；20日 {r['qvix_20d_chg']:+.2f}"
                 if pd.notna(r["qvix_20d_chg"]) else f"历史分位 {fmt(r['qvix_pct'], '.0%')}"),
                "QVIX 是 300ETF 期权隐含波动率；高位回落=恐慌释放，低位=缺少反向支撑。"),
    ], plain))

    # 5 趋势
    if top.get("趋势"):
        tone = "risk"
        summary = ("趋势向上但偏离200日线过大，透支；需要时间或回调消化。"
                   if pd.notna(r["extension"]) and r["extension"] > 0.20
                   else "120日涨幅超过25%，趋势可能透支，追高赔率低。")
    elif bottom.get("趋势"):
        tone = "down"
        if r["close"] <= r["ma200"] and r["drawdown_252"] <= -0.20:
            summary = "中期趋势向下且回撤超过20%，处于熊市区间；反弹先按反弹处理，等趋势修复。"
        elif r["close"] <= r["ma200"]:
            summary = "跌破200日线，中期趋势转弱；未站上并站稳5日前，不按反转对待。"
        else:
            summary = "已站上200日线，但距52周高点回撤仍超20%，按修复初期处理。"
    elif r["close"] <= r["ma200"]:
        tone, summary = "watch", "跌破200日线，中期趋势转弱；未站上并站稳5日前，不按反转对待。"
    else:
        tone, summary = "ok", "站上200日线且偏离不大，趋势健康。"
    ma_gap = r["close"] / r["ma200"] - 1 if pd.notna(r["ma200"]) else float("nan")
    if bottom.get("趋势"):
        plain = (f"中期趋势向下：价格比200日线（过去一年的平均成本）低 {fmt(abs(ma_gap), '.1%')}，"
                 f"比一年内最高点低 {fmt(abs(r['drawdown_252']), '.1%')}。"
                 "规则上以防守为主；反弹先当反弹看，等价格重新站上200日线、并连续5个交易日站稳，再谈反转。")
    elif top.get("趋势"):
        plain = (f"涨得太多、离200日线太远：价格比200日线高 {fmt(abs(ma_gap), '.1%')}，"
                 f"过去120个交易日涨了 {fmt(r['ret_120'], '.1%')}。短期透支，追高不划算，规则上要防回调。")
    elif tone == "watch":
        plain = ("价格跌破200日线，中期趋势转弱；在重新站上、并且连续5个交易日站稳之前，"
                 "不把反弹当反转。")
    else:
        plain = "价格在200日线上方、而且离得不远，趋势健康，可以按规则持有。"
    dims.append(Dimension("趋势/位置", tone, summary, [
        DimItem("收盘 vs 200日线", fmt(r["close"] / r["ma200"] - 1, "+.1%"), "站上/跌破",
                "200日线是中期牛熊分界；站上且50日线在200日线上方，趋势更可靠。"),
        DimItem("距52周高点", fmt(r["drawdown_252"], "+.1%"), f"120日收益 {fmt(r['ret_120'], '+.1%')}",
                "回撤 >20% 进入熊市区间；回撤 <5% 且广度差，是顶部背离。"),
        DimItem("偏离200日线", fmt(r["extension"], "+.1%"), f"60日收益 {fmt(r['ret_60'], '+.1%')}",
                "偏离 >20% 为透支；偏离 <−15% 为超跌，但超跌可以更超跌。"),
    ], plain))

    labels = {"valuation": "估值/风险溢价", "credit": "流动性/信用",
              "breadth": "广度/趋势", "sentiment": "情绪/仓位"}
    for d in dims:
        for k, name in labels.items():
            if name == d.name and k in stale and "不参与打分" not in d.summary:
                d.tone = "watch"
                d.summary = "该维度数据源过期，不参与打分；" + d.summary
    return dims


# ──────────────────────────────────────────────────────────────
# ETF 资金代理
# ──────────────────────────────────────────────────────────────

@dataclass
class BasketFlow:
    name: str
    today: float
    d5: float
    d20: float
    d5_pct: float
    d20_pct: float
    price5: float
    price20: float
    read: str
    tone: str


@dataclass
class EtfFlowPanel:
    date: pd.Timestamp
    total_today: float
    total_d5: float
    total_d20: float
    total_d5_pct: float
    total_d20_pct: float
    baskets: list[BasketFlow]
    top_in: list[tuple[str, str, float]]
    top_out: list[tuple[str, str, float]]
    recent: pd.DataFrame
    history_days: int


def build_etf_flows() -> EtfFlowPanel:
    sh = pd.read_csv(CACHE / "etf_shares.csv", parse_dates=["date"], dtype={"code": str})
    px = pd.read_csv(CACHE / "etf_prices.csv", parse_dates=["date"], dtype={"code": str})
    wide = sh.pivot_table(index="date", columns="code", values="shares", aggfunc="last").sort_index()
    pwide = (px.pivot_table(index="date", columns="code", values="close", aggfunc="last")
             .sort_index().reindex(wide.index).ffill())
    flow = (wide.diff() * pwide).fillna(0.0) / 1e8  # 亿元

    baskets: list[BasketFlow] = []
    for bname, codes in ETF_BASKETS.items():
        # 有份额但价格抓取失败的代码必须剔除，否则 pwide[codes] 直接 KeyError 崩掉整个面板
        codes = [c for c in codes if c in flow.columns and c in pwide.columns]
        if not codes:
            continue
        f = flow[codes].sum(axis=1)
        d5 = f.rolling(5, min_periods=3).sum()
        d20 = f.rolling(20, min_periods=10).sum()
        p5 = pwide[codes].pct_change(5).mean(axis=1)
        p20 = pwide[codes].pct_change(20).mean(axis=1)
        today, f5, f20 = float(f.iloc[-1]), float(d5.iloc[-1]), float(d20.iloc[-1])
        pr5 = float(p5.iloc[-1]) if pd.notna(p5.iloc[-1]) else float("nan")
        pr20 = float(p20.iloc[-1]) if pd.notna(p20.iloc[-1]) else float("nan")
        pct = pct_of_last(d5)
        pct20 = pct_of_last(d20)
        if pct20 >= 0.90 and f5 < 0:
            read, tone = "20日大额申购，近5日暂停/转弱", "pause"
        elif pct20 >= 0.90 and pr20 < 0:
            read, tone = "20日逆势大额申购，托底概率高", "support"
        elif pct20 <= 0.10 and pr20 > 0:
            read, tone = "20日上涨中持续赎回，疑似派发", "distribution"
        elif pct >= 0.95 and pr5 < 0:
            read, tone = "连续逆势大额申购，托底概率高", "support"
        elif pct >= 0.95:
            read, tone = "连续大额申购，配置资金流入", "inflow"
        elif pct <= 0.05:
            read, tone = "连续赎回，去杠杆/恐慌", "outflow"
        else:
            read, tone = "无明显大额申赎", "neutral"
        baskets.append(BasketFlow(bname, today, f5, f20, pct, pct20, pr5, pr20, read, tone))

    total = flow.sum(axis=1)
    d5 = total.rolling(5, min_periods=3).sum()
    d20 = total.rolling(20, min_periods=10).sum()
    last_day = flow.index[-1]
    fd = flow.iloc[-1]
    top_in = [(c, ETF_NAMES.get(c, c), float(v)) for c, v in fd[fd > 0].sort_values(ascending=False).head(3).items()]
    top_out = [(c, ETF_NAMES.get(c, c), float(v)) for c, v in fd[fd < 0].sort_values().head(3).items()]

    recent = pd.DataFrame({
        "date": flow.index[-10:],
        "total": total.iloc[-10:].values,
    })
    return EtfFlowPanel(
        date=last_day,
        total_today=float(total.iloc[-1]),
        total_d5=float(d5.iloc[-1]),
        total_d20=float(d20.iloc[-1]),
        total_d5_pct=pct_of_last(d5),
        total_d20_pct=pct_of_last(d20),
        baskets=baskets, top_in=top_in, top_out=top_out,
        recent=recent, history_days=len(flow),
    )


# ──────────────────────────────────────────────────────────────
# 三个底
# ──────────────────────────────────────────────────────────────

def _parse_cn_date(s) -> pd.Timestamp:
    return pd.to_datetime(str(s).replace("年", "-").replace("月", "-").replace("日", ""),
                          errors="coerce")


def _cache_fresh(path: Path, days: int = 7) -> bool:
    return path.exists() and (pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")) < pd.Timedelta(days=days)


def _load_lpr() -> pd.DataFrame:
    path = CACHE / "lpr.csv"
    if _cache_fresh(path):
        return pd.read_csv(path, parse_dates=["date"])
    try:
        import akshare as ak
        raw = ak.macro_china_lpr()
        df = pd.DataFrame({"date": pd.to_datetime(raw["TRADE_DATE"]),
                           "lpr1": pd.to_numeric(raw["LPR1Y"], errors="coerce"),
                           "lpr5": pd.to_numeric(raw["LPR5Y"], errors="coerce")}).dropna(subset=["lpr1"])
        df = df.sort_values("date").reset_index(drop=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return df
    except Exception:
        if path.exists():
            return pd.read_csv(path, parse_dates=["date"])
        raise


def _load_rrr() -> pd.DataFrame:
    path = CACHE / "rrr.csv"
    if _cache_fresh(path):
        return pd.read_csv(path, parse_dates=["公布"])
    try:
        import akshare as ak
        raw = ak.macro_china_reserve_requirement_ratio()
        df = pd.DataFrame({
            "公布": raw["公布时间"].map(_parse_cn_date),
            "幅度": pd.to_numeric(raw["大型金融机构-调整幅度"], errors="coerce"),
        }).dropna(subset=["公布"])
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return df
    except Exception:
        if path.exists():
            return pd.read_csv(path, parse_dates=["公布"])
        raise


@dataclass
class BottomState:
    name: str
    status: str
    tone: str
    evidence: str
    meaning: str
    detail: list[tuple[str, str]]
    # 推送用白话：不出现 top<0.4、分位、pp 这类黑话
    plain: str = ""


@dataclass
class BottomPanel:
    policy: BottomState
    market: BottomState
    earnings: BottomState
    sequence: str
    phase: str


def lpr_cuts(lpr: pd.DataFrame) -> pd.DataFrame:
    """所有降息事件，含只降 5Y 的情形（如 2024-02-20）。"""
    lpr = lpr.copy()
    lpr["chg1"] = lpr["lpr1"].diff()
    lpr["chg5"] = lpr["lpr5"].diff()
    return lpr[(lpr["chg1"] < 0) | (lpr["chg5"] < 0)]


def lpr_tenor(row: pd.Series) -> str:
    if row["chg1"] < 0 and row["chg5"] < 0:
        return "1Y+5Y"
    return "1Y" if row["chg1"] < 0 else "5Y"


def market_bottom_status(above200_5: bool, top_adj: float,
                         strong: list[str], weak: list[str]) -> tuple[str, str]:
    """市场底是一次性事件：趋势修复（连续5日站上200日线）就确认，之后不会再退回“观察区”。

    顶部风险只影响“现在能不能追买”，不影响“底是否成立”。旧逻辑把 top<0.4 写进确认条件，
    导致牛市里涨出顶部风险时反而说“市场底未确认、下跌可能没走完”——方向完全错了。
    """
    if above200_5:
        if top_adj < 0.4:
            return "确认（趋势修复）", "ok"
        return "确认（趋势修复；顶部风险升高，不宜追买）", "watch"
    if strong:
        return f"观察（未确认；强：{'、'.join(strong)}）", "bottom"
    if weak:
        return "观察（未确认）", "watch"
    return "未出现", "neutral"


def build_bottoms(s: pd.DataFrame, indices: list, etf: EtfFlowPanel,
                  panel_date: pd.Timestamp) -> BottomPanel:
    # 政策底（LPR/降准缓存 7 天内复用，过期自动重抓，避免漏掉新一次宽松）
    # 重抓可能带回 panel_date 之后的事件，必须截断，否则面板会“看见未来”
    cuts = lpr_cuts(_load_lpr())
    cuts = cuts[cuts["date"] <= panel_date]
    lpr_cut = cuts.iloc[-1] if len(cuts) else None
    lpr_days = bdays_between(lpr_cut["date"], panel_date) if lpr_cut is not None else None
    lpr_tenor_txt = lpr_tenor(lpr_cut) if lpr_cut is not None else ""

    rrr_raw = _load_rrr()
    rcuts = rrr_raw[rrr_raw["幅度"] < 0].sort_values("公布")
    rcuts = rcuts[rcuts["公布"] <= panel_date]
    rrr_cut = rcuts.iloc[-1] if len(rcuts) else None
    rrr_days = bdays_between(rrr_cut["公布"], panel_date) if rrr_cut is not None else None

    easing = (lpr_days is not None and lpr_days <= 90) or (rrr_days is not None and rrr_days <= 90)
    support = bool(pd.notna(etf.total_d20_pct) and etf.total_d20_pct >= 0.95 and etf.total_d20 > 0)
    stress = bool(s["drawdown_252"].iloc[-1] <= -0.10)
    n = sum([easing, support, stress])
    p_status = "确认" if n == 3 else ("疑似" if n == 2 else "未出现")
    p_tone = "ok" if n == 3 else ("watch" if n == 2 else "neutral")
    d5_read = ("仍在流入" if pd.notna(etf.total_d5) and etf.total_d5 > 0
               else "转为流出/暂停" if pd.notna(etf.total_d5) else "数据不足")
    p_evidence = (
        f"降准/降息：{'90日内有' if easing else '90日内无'}；"
        f"宽基ETF 20日净申购 {fmt(etf.total_d20, '+.0f')} 亿元"
        f"（历史分位 {fmt(etf.total_d20_pct, '.0%')}），"
        f"近5日 {fmt(etf.total_d5, '+.0f')} 亿元（{d5_read}）；"
        f"沪深300距52周高点 {fmt(s['drawdown_252'].iloc[-1], '+.1%')}。"
    )
    lpr_txt = (f"{lpr_cut['date'].date()}（{lpr_days} 个交易日前）"
               if lpr_cut is not None else "没有记录")
    rrr_txt = (f"{rrr_cut['公布'].date()}（{rrr_days} 个交易日前）"
               if rrr_cut is not None else "没有记录")
    if n == 3:
        p_plain = ("三个迹象都出现了：最近3个月有降息或降准、国家托底资金在大额买入、市场也跌得够多。"
                   "政策底通常已经出现，但它是“政策开始托底”，不是价格最低点；"
                   "历史上从政策底到市场底，一般还要1到3个月。")
    elif n == 2:
        p_plain = ("三个迹象里出现了两个，还差一个。可以理解为“政策已经在托底，但组合还不完整”，"
                   "它不代表价格已经见底。")
    else:
        p_plain = f"三个迹象里只出现了 {n} 个，政策托底的信号还不明显。"
    d20_flow = ("—" if pd.isna(etf.total_d20)
                else f"净买入 {abs(etf.total_d20):,.0f} 亿元" if etf.total_d20 >= 0
                else f"净卖出 {abs(etf.total_d20):,.0f} 亿元")
    d5_flow = ("—" if pd.isna(etf.total_d5)
               else f"净买入 {abs(etf.total_d5):,.0f} 亿元" if etf.total_d5 >= 0
               else f"净卖出 {abs(etf.total_d5):,.0f} 亿元")
    p_plain += ("判断政策底只看三件事：①最近3个月有没有降息/降准；"
                "②宽基ETF有没有大额净买入（代表国家队等大资金在买）；③市场是不是已经跌了10%以上。"
                f"最近一次降息是 {lpr_txt}，最近一次降准是 {rrr_txt}；"
                f"宽基ETF最近20个交易日{d20_flow}，最近5个交易日{d5_flow}。")
    policy = BottomState(
        "政策底", p_status, p_tone, p_evidence,
        "政策底是“政策转向/托底动作”，不是价格低点。历史上政策底到市场底通常还要 1-3 个月，"
        "到盈利底 2-4 个季度；它的意义是降低系统性风险，不是让你立刻满仓。"
        "本面板只用降准/降息、宽基ETF份额、回撤三项做代理，不含印花税、IPO节奏、汇金公告等事件——"
        "真实政策底可能早于或晚于这里的读数。",
        [("最近降息", f"{lpr_cut['date'].date()}（{lpr_tenor_txt}，{lpr_days} 个交易日前）" if lpr_cut is not None else "—"),
         ("最近降准", f"{rrr_cut['公布'].date()}（{rrr_days} 个交易日前）" if rrr_cut is not None else "—"),
         ("托底资金", f"20日 {fmt(etf.total_d20, '+.0f')} 亿元（分位 {fmt(etf.total_d20_pct, '.0%')}）；"
                      f"近5日 {fmt(etf.total_d5, '+.0f')} 亿元"),
         ("市场压力", f"回撤 {fmt(s['drawdown_252'].iloc[-1], '+.1%')}")],
        p_plain,
    )

    # 市场底
    close = s["close"].values
    win = close[-60:]
    days_since_low = len(win) - 1 - int(np.argmin(win))
    above_ma20_5 = bool((s["close"].iloc[-5:] > s["ma20"].iloc[-5:]).all())
    breadth = float(s["breadth_ma200"].iloc[-1])
    breadth_chg = float(s["breadth_ma200_chg20"].iloc[-1])
    breadth_recovery = bool(breadth > 0.25 and breadth_chg > 0.10)
    above200_5 = bool(confirm(s["close"] > s["ma200"], 5).iloc[-1])
    top_adj = float(indices[0].top_adj)
    strong = [i.name for i in indices if i.bottom_adj >= 0.8]
    weak = [i.name for i in indices if 0.6 <= i.bottom_adj < 0.8]
    m_status, m_tone = market_bottom_status(above200_5, top_adj, strong, weak)
    bz = indices[0].bottom_zone
    m_stats = ""
    if bz.get("n") and bz.get("n_full"):
        n_part = (f"{bz['n']} 次" if bz["n"] == bz["n_full"]
                  else f"{bz['n']} 次（{bz['n_full']} 次有完整120日窗口）")
        m_stats = (f"本指数历史 bottom≥0.8 区间 {n_part}，"
                   f"结束后120日再跌中位 {bz['post_dd_med']*100:.1f}%，"
                   f"跌超10%概率 {bz['post_dd_10']:.0%}。")
    if m_status.startswith("确认") and top_adj < 0.4:
        m_plain = ("市场底已经确认：价格连续5个交易日站在200日线上方，同时顶部风险很低。"
                   "意思是下跌趋势已经修复，可以按趋势规则持有；但“确认”不等于不会再跌，防守线还是200日线。")
    elif m_status.startswith("确认"):
        lit = sum(1 for v in indices[0].top_flags.values() if v)
        m_plain = ("市场底已经确认：价格连续5个交易日站在200日线上方，下跌趋势已经修复。"
                   f"但顶部风险也在升高（风险条件亮了 {lit} 项），说明这轮已经涨了一段；"
                   "现在不是加仓的时候，跌破200日线就防守。")
    elif strong:
        m_plain = (f"市场底还在“观察区”，没有确认。强观察的指数是：{'、'.join(strong)}——"
                   "意思是这些指数已经跌到便宜、超跌的位置，5项见底条件满足了大部分；"
                   "但趋势还没修复，价格仍在200日线下方。观察区不是买点，历史上观察区之后还可能继续跌。")
    elif weak:
        m_plain = ("市场底还在“观察区（弱）”：只有一部分指数跌到了便宜位置，见底条件还不齐，趋势也没修复。"
                   "这不是买点，确定性比“强观察”低。")
    else:
        m_plain = "市场底还没出现：价格没有进入“便宜+超跌”的观察区，下跌可能还没走完。"
    m_plain += (f"最近60个交易日的最低点出现在 {days_since_low} 个交易日前；"
                f"价格连续站上20日线5天：{'是' if above_ma20_5 else '否'}；"
                f"站上200日线连续5天：{'是' if above200_5 else '否'}。"
                "要确认市场底，看价格能不能连续5个交易日站上200日线；"
                "确认之后，顶部风险分数低于0.4（0到1，越低越安全）才适合加仓，分数高说明涨得太急，先别追。")
    market = BottomState(
        "市场底", m_status, m_tone,
        f"60日低点距今 {days_since_low} 个交易日；站上MA20连续5日：{'是' if above_ma20_5 else '否'}；"
        f"广度 {fmt(breadth, '.0%')}"
        f"（20日 {fmt(breadth_chg*100, '+.0f')}pp）；"
        f"站上200日线5日：{'是' if above200_5 else '否'}。",
        "市场底是价格低点，通常滞后政策底 1-3 个月。底部分数只说明“进入观察区”，不是买点。"
        + m_stats + "确认看趋势修复（连续5日站上200日线）；top<0.4 只决定确认后能否加仓。",
        [("强/弱观察", f"强：{'、'.join(strong) or '无'}；弱：{'、'.join(weak) or '无'}"),
         ("60日低点", f"{days_since_low} 个交易日前"),
         ("站上MA20", "连续5日" if above_ma20_5 else "未满足"),
         ("广度修复", f"{fmt(breadth, '.0%')}，20日 {fmt(breadth_chg*100, '+.0f')}pp")],
        m_plain,
    )

    # 盈利底
    pe = pd.read_csv(CACHE / "pe_hs300.csv", parse_dates=["date"]).sort_values("date")
    idx = pd.read_csv(CACHE / "index_hs300.csv", parse_dates=["date"]).sort_values("date")
    pe = pe.merge(idx[["date", "close"]], on="date", how="left")
    pe["close"] = pe["close"].ffill()
    pe["eps"] = pe["close"] / pe["pe_ttm"]
    pe["eps_yoy"] = pe["eps"] / pe["eps"].shift(12) - 1
    m = pe.dropna(subset=["eps_yoy"]).reset_index(drop=True)
    # 当月未走完时，用上个月末作为“最后完整月”
    if len(m) >= 2 and m["date"].iloc[-1].month == panel_date.month and panel_date.day < 25:
        last_i = len(m) - 2
    else:
        last_i = len(m) - 1
    yoy = float(m["eps_yoy"].iloc[last_i])
    prev = float(m["eps_yoy"].iloc[last_i - 1]) if last_i >= 1 else float("nan")
    chg3 = float(m["eps_yoy"].iloc[last_i] - m["eps_yoy"].iloc[last_i - 3]) if last_i >= 3 else float("nan")
    if yoy > 0 and yoy > prev:
        e_status, e_tone = "确认（改善）", "ok"
    elif yoy > prev or (pd.notna(chg3) and chg3 > 0):
        e_status, e_tone = "疑似（边际改善）", "watch"
    else:
        e_status, e_tone = "未出现（仍在恶化）", "risk"
    if e_status.startswith("确认"):
        e_plain = ("盈利底已经确认：沪深300成分股的整体盈利（每股盈利）同比转正，而且比上个月更好。"
                   "意思是企业赚钱能力开始回升，基本面接棒，行情会更可靠。")
    elif e_status.startswith("疑似"):
        e_plain = ("盈利底可能正在出现：企业整体盈利同比还在跌，但跌得比之前少了（边际改善），还没转正。"
                   "基本面开始有起色，但还没到确认的时候。")
    else:
        e_plain = ("盈利底还没出现：企业整体盈利还在变差，同比仍在下降，而且比3个月前更差。"
                   "这意味着现在的反弹缺少基本面支撑，更容易反复。")
    e_plain += (f"具体数字：沪深300成分股的整体盈利同比 {fmt(yoy, '+.1%')}，"
                f"上月 {fmt(prev, '+.1%')}，3个月变化 {fmt(chg3*100, '+.1f')} 个百分点。"
                "这个盈利数是用指数价格和市盈率反推出来的，不是官方财报，会有误差。")
    earnings = BottomState(
        "盈利底", e_status, e_tone,
        f"沪深300隐含EPS同比 {fmt(yoy, '+.1%')}（{m['date'].iloc[last_i].date()}），"
        f"上月 {fmt(prev, '+.1%')}，3个月变化 {fmt(chg3*100, '+.1f')}pp。",
        "盈利底通常滞后市场底 1-2 个季度。它出现时市场往往已经涨了一段，意义是确认基本面接棒，"
        "而不是买点；它没出现时，反弹更容易反复。",
        [("隐含EPS同比", fmt(yoy, "+.1%")),
         ("上月", fmt(prev, "+.1%")),
         ("3个月变化", fmt(chg3*100, "+.1f") + "pp"),
         ("数据", f"{m['date'].iloc[last_i].date()}（由指数/PE反推，非官方盈利）")],
        e_plain,
    )

    phase = phase_text(p_status, m_status, e_status)
    sequence = "政策底 → 市场底（滞后 1-3 个月）→ 盈利底（再滞后 1-2 个季度）"
    return BottomPanel(policy, market, earnings, sequence, phase)


def phase_text(p_status: str, m_status: str, e_status: str) -> str:
    """由三个底的实际状态拼阶段结论。

    状态先按 state_key 归一化，避免把“未出现（仍在恶化）”整段塞进括号；
    “观察（未确认；强：X）”仍按观察处理，强名单由市场底卡片单独展示。
    """
    pk, mk, ek = state_key(p_status), state_key(m_status), state_key(e_status)
    watching = mk.startswith("观察")
    if pk == "确认" and mk == "未出现":
        return f"政策底已确认，市场底未确认，盈利底{ek}：处于“托底—磨底”阶段。"
    if pk in ("确认", "疑似") and watching:
        return (f"政策底{pk}，市场底在观察区但未确认（盈利底{ek}）："
                "处于“磨底”阶段。")
    if mk.startswith("确认"):
        if ek.startswith("确认"):
            return "市场底与盈利底均已确认：基本面接棒，趋势更可靠。"
        if ek.startswith("疑似"):
            return "市场底确认，盈利底边际改善：基本面开始接棒，趋势可靠性提高。"
        return "市场底确认，盈利底未现：趋势修复，但基本面尚未接棒，反弹可能反复。"
    if pk == "未出现" and mk == "未出现":
        return "三个底均未确认：仍在下跌/观察阶段。"
    return f"政策底{pk}，市场底{mk}，盈利底{ek}：信号未共振，等待确认。"


# ──────────────────────────────────────────────────────────────
# 数据健康
# ──────────────────────────────────────────────────────────────

@dataclass
class SourceHealth:
    name: str
    date: pd.Timestamp | None
    bdays: int | None
    max_ok: int
    status: str
    note: str


def build_health(panel_date: pd.Timestamp) -> list[SourceHealth]:
    def last(path, col, parse=None):
        df = pd.read_csv(path)
        if df.empty:
            return None
        s = pd.to_datetime(df[col], errors="coerce") if parse is None else df[col].map(parse)
        return s.max()

    def last_amount(path):
        df = pd.read_csv(path)
        s = df.loc[pd.to_numeric(df["amount"], errors="coerce").notna(), "date"]
        return pd.to_datetime(s).max() if len(s) else None

    items = [
        ("指数行情", last(CACHE / "index_hs300.csv", "date"), 3),
        ("成交额(沪深300)", last_amount(CACHE / "index_hs300.csv"), 3),
        ("PE估值", last(CACHE / "pe_hs300.csv", "date"), 5),
        ("10Y国债", last(CACHE / "bond_10y.csv", "date"), 3),
        ("M1/M2", last(CACHE / "money_supply.csv", "month"), 45),
        ("社融", last(CACHE / "social_financing.csv", "month"), 45),
        ("两融", last(CACHE / "margin.csv", "date"), 3),
        ("QVIX", last(CACHE / "qvix.csv", "date"), 3),
        ("行业广度", last(SIG / "breadth.csv", "date"), 3),
        ("ETF份额", last(CACHE / "etf_shares.csv", "date"), 3),
        ("ETF价格", last(CACHE / "etf_prices.csv", "date"), 3),
    ]
    out = []
    for name, d, max_ok in items:
        b = bdays_between(d, panel_date) if d is not None else None
        if b is None:
            status, note = "unknown", "无数据"
        elif b <= max_ok:
            status, note = "ok", f"T-{b}" if b else "当日"
        elif b <= max_ok * 2:
            status, note = "lag", f"滞后 {b} 个交易日"
        else:
            status, note = "dead", f"停更/过期 {b} 个交易日，不参与判断"
        out.append(SourceHealth(name, d, b, max_ok, status, note))
    return out


# ──────────────────────────────────────────────────────────────
# 状态变化提醒
# ──────────────────────────────────────────────────────────────

STATE_PATH = CACHE / "panel_state.json"


def state_key(s: str) -> str:
    s = str(s)
    # 市场底的“强”名单变化本身是信号，不能都压成“观察”
    m = re.match(r"观察（未确认；强：(.+?)）", s)
    if m:
        return f"观察（强：{m.group(1)}）"
    for k in ("强观察", "观察", "确认", "疑似"):
        if s.startswith(k):
            return k
    return "未出现"


_STATE_LOAD_NOTE = ""


def load_prev_state() -> dict | None:
    global _STATE_LOAD_NOTE
    _STATE_LOAD_NOTE = ""
    if not STATE_PATH.exists():
        return None
    try:
        import json
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        # 损坏的基线不能静默覆盖：先留备份，再从头建立
        bak = STATE_PATH.with_name("panel_state.corrupt.json")
        try:
            STATE_PATH.replace(bak)
            _STATE_LOAD_NOTE = f"状态基线损坏，已备份为 {bak.name} 并重新建立基线。"
        except Exception:
            _STATE_LOAD_NOTE = "状态基线损坏且无法备份，已重新建立基线。"
        return None


def save_state(pan: "Panel") -> None:
    import json
    data = {
        "date": str(pan.date.date()),
        "bottoms": {
            b.name: {"key": state_key(b.status), "status": b.status}
            for b in (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings)
        },
        "indices": {i.key: i.state for i in pan.indices},
        "headline": pan.headline,
    }
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def diff_alerts(pan: "Panel", prev: dict | None) -> list[str]:
    if prev is None:
        return ["首次运行：无历史基线；本次作为基线，状态变化提醒从下次运行开始。"]
    out = []
    for b in (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings):
        old = prev.get("bottoms", {}).get(b.name, {}).get("key")
        new = state_key(b.status)
        if old and old != new:
            out.append(f"{b.name}：{old} → {new}")
    for i in pan.indices:
        old = prev.get("indices", {}).get(i.key)
        if old and old != i.state:
            out.append(f"{i.name}：{old} → {i.state}")
    if not out:
        out.append(f"状态无变化（上次基线 {prev.get('date', '—')}）。")
    return out


# ──────────────────────────────────────────────────────────────
# 总装
# ──────────────────────────────────────────────────────────────

@dataclass
class Panel:
    date: pd.Timestamp
    generated: pd.Timestamp
    indices: list[IndexPanel]
    dims: list[Dimension]
    bottoms: BottomPanel
    etf: EtfFlowPanel
    health: list[SourceHealth]
    headline: str
    stale_dims: set[str]
    alerts: list[str] = field(default_factory=list)
    prev_date: str = ""


def build_panel() -> Panel:
    sigs = {k: attach_credit_ok(load_signal(k)) for k, _, _ in INDEXES}
    panel_date = max(s["date"].iloc[-1] for s in sigs.values())

    social_last = pd.to_datetime(pd.read_csv(CACHE / "social_financing.csv")["month"]).max()
    margin_last = pd.to_datetime(pd.read_csv(CACHE / "margin.csv")["date"]).max()
    health = build_health(panel_date)
    by = {h.name: h for h in health}
    stale: set[str] = set()
    if (bdays_between(social_last, panel_date) > 45
            or by["社融"].status == "dead" or by["M1/M2"].status == "dead"):
        stale.add("credit")
    if by["PE估值"].status == "dead":
        stale.add("valuation")
    if by["行业广度"].status == "dead":
        stale.add("breadth")
    last_row = sigs["hs300"].iloc[-1]
    if (by["两融"].status == "dead" and by["QVIX"].status == "dead"
            and pd.isna(last_row["turnover_z"])):
        stale.add("sentiment")

    indices = [build_index_panel(k, n, sigs[k], stale) for k, n, _ in INDEXES]
    dims = build_dimensions(sigs["hs300"], stale, social_last.date(), margin_last.date())
    etf = build_etf_flows()
    bottoms = build_bottoms(sigs["hs300"], indices, etf, panel_date)

    strong_bottom = [i.name for i in indices if i.state_tone == "bottom"]
    weak_bottom = [i.name for i in indices if i.state_tone == "watch"]
    top_risk = [i.name for i in indices if i.state_tone == "risk"]
    if top_risk:
        headline = f"顶部风险区：{'、'.join(top_risk)}。规则状态为退出/防守。"
    elif strong_bottom:
        parts = [f"底部观察（强）：{'、'.join(strong_bottom)}"]
        others = [i for i in indices if i.name not in strong_bottom]
        if weak_bottom:
            parts.append(f"弱观察：{'、'.join(weak_bottom)}")
        rest = [i.name for i in others if i.name not in weak_bottom]
        if rest:
            parts.append(f"未进入底部区：{'、'.join(rest)}")
        below = [i.name for i in others if i.close < i.ma200]
        if below:
            parts.append(f"{'、'.join(below)}仍在200日线下，趋势未修复；等站上200日线5日+top<0.4")
        headline = "；".join(parts) + "。"
    elif weak_bottom:
        headline = f"下跌趋势中的底部观察（弱）：{'、'.join(weak_bottom)}；未确认。"
    else:
        headline = "趋势正常或下跌趋势，未触发顶部/底部区间。"

    prev = load_prev_state()
    pan = Panel(
        date=panel_date, generated=pd.Timestamp.now(),
        indices=indices, dims=dims, bottoms=bottoms, etf=etf, health=health,
        headline=headline, stale_dims=stale,
        alerts=[], prev_date=(prev or {}).get("date", ""),
    )
    pan.alerts = diff_alerts(pan, prev)
    if _STATE_LOAD_NOTE:
        pan.alerts.insert(0, _STATE_LOAD_NOTE)
    now = pd.Timestamp.now()
    expected = _expected_last_trade_date(now)
    if expected is not None:
        if panel_date.normalize() < expected:
            pan.alerts.insert(
                0, f"数据日 {panel_date.date()} 落后于最近交易日 {expected.date()}，可能未更新；"
                   f"请运行 panel.py --refresh。")
    else:
        days_old = (now.normalize() - panel_date.normalize()).days
        if days_old > 4:
            pan.alerts.insert(0, f"数据日 {panel_date.date()} 距今 {days_old} 天，可能未更新；"
                                 f"请运行 panel.py --refresh。")
    return pan


def _expected_last_trade_date(now: pd.Timestamp) -> pd.Timestamp | None:
    """最近一个“按时间应该已收盘”的交易日；用它判断面板数据是否落后，
    避免长假后被自然日差误报。取不到交易日历时返回 None，调用方退化处理。"""
    path = CACHE / "trade_dates.csv"
    try:
        if _cache_fresh(path, days=30):
            df = pd.read_csv(path)
        else:
            import akshare as ak
            raw = ak.tool_trade_date_hist_sina()
            df = pd.DataFrame({"trade_date": pd.to_datetime(raw["trade_date"])})
            df.to_csv(path, index=False, encoding="utf-8-sig")
    except Exception:
        if path.exists():
            df = pd.read_csv(path)
        else:
            return None
    cal = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    # 收盘前（16:00）不能用当天要求数据更新
    cutoff = now.normalize() if now.hour >= 16 else now.normalize() - pd.Timedelta(days=1)
    cal = cal[cal <= cutoff]
    return cal.max() if len(cal) else None
