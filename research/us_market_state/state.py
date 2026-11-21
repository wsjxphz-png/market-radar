# -*- coding: utf-8 -*-
"""状态机：把价格/宏观数据翻译成“现在处于什么阶段 + 该怎么做”。

判断顺序（从强到弱）：
  6 趋势健康 → 5 趋势确认 → 4 修复中 → 3 止跌观察 → 2 深度下跌 → 1 下跌中
只用收盘价和均线，不预测；所有动作都是规则输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field, field

import pandas as pd

import data
from config import (
    CHANGE_BY_STAGE,
    HEALTHY_DRAWDOWN,
    MAG7,
    MARKET_MEANING,
    MARKETS,
    RISK_CREDIT_BP,
    RISK_MAG7_BREADTH,
    RISK_RSP_SPY_20D,
    RISK_VIX,
    STAGE2_DRAWDOWN,
    STAGE2_VIX,
    STAGE3_RECLAIM_DAYS,
    STAGE4_RECLAIM_DAYS,
    STAGE_LABELS,
    STAGE_MEANING,
    Market,
)


def build_frame(close: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"close": close.astype(float)})
    df["ret"] = df["close"].pct_change()
    for n in (20, 50, 200):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    df["hi252"] = df["close"].rolling(252, min_periods=60).max()
    df["dd"] = df["close"] / df["hi252"] - 1
    return df


def _reclaim(close: pd.Series, ma: pd.Series, days: int) -> bool:
    """当前在均线上方，且本轮上穿之前至少连续 days 天在均线下方。

    注意：这是“持续状态”，不是“交叉当天”。只要价格还在均线上方，
    上穿后的第 1/2/5/10 天都返回 True；跌回均线下方才变成 False。
    """
    if len(close) < days + 1 or pd.isna(ma.iloc[-1]):
        return False
    above = close > ma
    if not bool(above.iloc[-1]):
        return False
    i = len(close) - 1
    while i >= 0 and bool(above.iloc[i]):
        i -= 1
    count = 0
    while i >= 0 and not bool(above.iloc[i]):
        count += 1
        i -= 1
    return count >= days


def _above_run(close: pd.Series, ma: pd.Series) -> int:
    """当前连续在均线上方的天数。"""
    above = close > ma
    count = 0
    for v in above.iloc[::-1]:
        if bool(v):
            count += 1
        else:
            break
    return count


def classify_stage(df: pd.DataFrame, vix: float | None = None) -> int:
    if df is None or df.empty:
        return 0
    r = df.iloc[-1]
    if pd.isna(r["ma200"]):
        return 0

    close, ma20, ma50, ma200 = r["close"], r["ma20"], r["ma50"], r["ma200"]
    dd = r["dd"] if pd.notna(r["dd"]) else 0.0

    # 多头排列才算 5/6；死叉后的反弹只能算 4，不能叫“趋势确认”
    golden = pd.notna(ma50) and pd.notna(ma200) and ma50 > ma200
    if close > ma200 and close > ma50 and golden:
        return 6 if dd > HEALTHY_DRAWDOWN else 5
    if close > ma200:
        return 4
    if _reclaim(df["close"], df["ma50"], STAGE4_RECLAIM_DAYS):
        return 4
    if (_reclaim(df["close"], df["ma20"], STAGE3_RECLAIM_DAYS)
            and _above_run(df["close"], df["ma20"]) >= STAGE3_RECLAIM_DAYS):
        return 3
    if dd <= STAGE2_DRAWDOWN or (vix is not None and pd.notna(vix) and vix >= STAGE2_VIX):
        return 2
    return 1


def action_for(market: Market, stage: int, branch: str = "") -> str:
    if stage == 0:
        return "数据不足，先观察，不操作。"

    if market.kind == "china":
        if stage <= 1:
            return "中概防守：不新买；跌破 200 日线的持仓减半；中概总仓位降到 5-10%；等连续 5 天站稳 20 日线再试。"
        if stage == 2:
            return ("中概防守：不抄底；不摊平；跌破 200 日线的清掉；"
                    "中概总仓位不超过 10–15%（弱市 5–10%）；等连续 5 天站稳 20 日线再试。")
        if stage == 3:
            return ("中考试探：最多用中概计划仓位的 1/3；只买 200 日线上方最强的 1-2 只；"
                    "单票不超过 5–8%；止损 60 日低点下方 2-3%。")
        if stage == 4:
            return ("中概修复：加到中概计划仓位的 50-60%；只加站上 50 日线的；"
                    "单票不超过 5–8%；止损 50 日线/60 日低点较低者。")
        if stage == 5:
            return "中概右侧确认：加到中概计划仓位的 70-80%；单票不超过 5–8%；止损 200 日线。"
        return "中概持有：中概计划仓位 80-100%；单票不超过 5–8%；跌破 200 日线减半。"

    if market.kind == "semis":
        if stage <= 2:
            return "不碰；等 SOXQ 连续 5 天站稳 20 日线再谈。真要做，半导体仓位减半，单票不超过 10%。"
        if stage == 3:
            return "小仓试（半导体仓位减半，最多 1/3），止损放 60 日低点下方 2-3%；跌破就走。"
        if stage == 4:
            return "观察/持有，不新加；等 50/200 日线走好。"
        return "持有；跌破 200 日线减半。"

    if stage == 1:
        base = ("防守：不新买；跌破 200 日线的持仓减半；现金提高到 30-50%；"
                "等这个市场连续 5 天站稳 20 日线再考虑试仓。")
    elif stage == 2:
        base = ("防守：不抄底；已有仓位不摊平；单票跌破 200 日线的清掉；"
                "美股组合总仓位不超过 30-50%，现金至少 50%；等连续 5 天站稳 20 日线再试。")
    elif stage == 3:
        base = ("试探：先用计划仓位的 1/3（约 20-30%）；只买 200 日线上方、相对最强的 1-3 只；"
                "单票不超过 10%；止损放 60 日低点下方 2-3%；跌破就走，不补仓。")
    elif stage == 4:
        if branch == "pullback":
            base = ("回调：持有 200 日线上方的强票，不新加；跌破 200 日线的减半；"
                    "现金 20-30%；等重新站上 50 日线再考虑加。")
        else:
            base = ("修复：把美股组合仓位加到 50-60%；只加重新站上 50 日线的；单票不超过 10%；"
                    "止损放 50 日线或 60 日低点中较低者；跌破就减回。")
    elif stage == 5:
        base = ("右侧确认：美股组合仓位加到 70-80%；只加 50 日线在 200 日线上方的；"
                "单票不超过 15%；止损放 200 日线；跌破就减半。")
    elif stage == 6:
        base = ("持有：美股组合仓位 80-100%；不追高；单票不超过 15%；"
                "跌破 200 日线减半，跌破 50 日线先观察。")
    else:
        base = "观察，等数据。"
    return base


@dataclass
class MarketState:
    market: Market
    close: float
    ma20: float
    ma50: float
    ma200: float
    drawdown: float
    stage: int
    low60: float = float("nan")

    @property
    def label(self) -> str:
        return STAGE_LABELS.get(self.stage, "未知")

    @property
    def gap200(self) -> float:
        return self.close / self.ma200 - 1 if pd.notna(self.ma200) else float("nan")

    @property
    def branch(self) -> str:
        """阶段 4 分两种：200 日线上方的回调（pullback），和 200 日线下方的修复（repair）。"""
        if self.stage != 4:
            return ""
        return "pullback" if self.close > self.ma200 else "repair"

    @property
    def action(self) -> str:
        return action_for(self.market, self.stage, self.branch)

    @property
    def meaning(self) -> str:
        if self.stage == 0:
            return "数据不足/异常，先观察；不根据缺失数据做判断。"
        if self.stage == 4:
            if self.branch == "pullback":
                head = "价格在 200 日线上方、50 日线下方，是上升趋势中的回调；先观察，不急着加。"
            else:
                head = "价格还在 200 日线下方，但重新站上 50 日线，中期在修复；可以小仓，但还没确认。"
            return head + MARKET_MEANING.get(self.market.key, "")
        return STAGE_MEANING.get(self.stage, "") + MARKET_MEANING.get(self.market.key, "")

    @property
    def change(self) -> str:
        if self.stage == 4:
            return CHANGE_BY_STAGE["4_repair" if self.branch == "repair" else "4_pullback"]
        return CHANGE_BY_STAGE.get(self.stage, "等价格给出方向。")


@dataclass
class Mag7State:
    symbol: str
    name: str
    close: float
    ma200: float
    stage: int

    @property
    def label(self) -> str:
        return STAGE_LABELS.get(self.stage, "未知")

    @property
    def gap200(self) -> float:
        return self.close / self.ma200 - 1 if pd.notna(self.ma200) else float("nan")

    @property
    def action(self) -> str:
        if self.stage == 0:
            return "数据不足，先观察"
        if self.stage >= 5:
            return "可持有"
        if self.stage == 4:
            return "观察，不新买"
        if self.stage == 3:
            return "等确认"
        return "减/不碰"


@dataclass
class RiskTrendItem:
    label: str
    detail: str
    worsening: bool
    note: str = ""


@dataclass
class WeeklyItem:
    name: str
    week_return: float
    week_high: float
    week_low: float
    close_pos: float
    ma10w: float
    close: float


@dataclass
class Analysis:
    states: list[MarketState]
    mag7: list[Mag7State]
    risk_flags: list[tuple[str, str]]
    mag7_breadth: int
    rsp_spy_20d: float
    data_date: pd.Timestamp
    risk_trend: list[RiskTrendItem] = field(default_factory=list)
    risk_worsening: int = 0
    quality_warnings: list[str] = field(default_factory=list)
    weekly: list[WeeklyItem] = field(default_factory=list)
    vix_week_chg: float = float("nan")
    credit_week_chg: float = float("nan")

    @property
    def by_key(self) -> dict[str, MarketState]:
        return {s.market.key: s for s in self.states}

    @property
    def us_block(self) -> list[MarketState]:
        return [s for s in self.states if s.market.kind in ("us", "semis")]

    @property
    def headline(self) -> str:
        us = self.us_block
        if not us or all(s.stage == 0 for s in us):
            return "数据不足，先不判断；等数据恢复再看。"
        if all(s.stage <= 2 for s in us):
            return "美股还在下跌/防守阶段，不是全面底部。现在最重要的是别急着抄底，等确认信号。"
        if any(s.stage == 3 for s in us) and not any(s.stage >= 4 for s in us):
            return "有市场开始止跌，但还没确认。可以小仓试探，不要重仓。"
        if any(s.stage >= 4 for s in us):
            return "有市场在修复/确认，但不是全面转好；只做修复的那个市场，其他继续防守。"
        return "趋势健康。持有，跌破 200 日线再减。"


def _risk_flags(states: list[MarketState], macro: pd.DataFrame,
                mag7_breadth: int, rsp_spy_20d: float) -> list[tuple[str, str]]:
    by = {s.market.key: s for s in states}
    flags: list[tuple[str, str]] = []

    vix = float(macro["VIXCLS"].iloc[-1]) if "VIXCLS" in macro and pd.notna(macro["VIXCLS"].iloc[-1]) else float("nan")
    vxv = float(macro["VXVCLS"].iloc[-1]) if "VXVCLS" in macro and pd.notna(macro["VXVCLS"].iloc[-1]) else float("nan")
    baa = macro["BAA10Y"].dropna() if "BAA10Y" in macro else pd.Series(dtype=float)
    credit_chg20 = (baa.iloc[-1] - baa.iloc[-21]) * 100 if len(baa) >= 21 else float("nan")

    if by["spy"].close < by["spy"].ma200 or by["qqq"].close < by["qqq"].ma200:
        flags.append(("趋势", "大盘或纳指在 200 日线下方"))
    if mag7_breadth < RISK_MAG7_BREADTH:
        flags.append(("广度", f"七姐妹只有 {mag7_breadth}/7 在 200 日线上方"))
    if pd.notna(rsp_spy_20d) and rsp_spy_20d < RISK_RSP_SPY_20D:
        flags.append(("广度", "等权指数跑输市值权，上涨只靠少数大票"))
    if pd.notna(credit_chg20) and credit_chg20 >= RISK_CREDIT_BP:
        flags.append(("信用", f"企业债利差 20 日走阔 {credit_chg20:.0f}bp"))
    vol: list[str] = []
    if pd.notna(vix) and vix >= RISK_VIX:
        vol.append(f"VIX {vix:.0f}")
    if pd.notna(vix) and pd.notna(vxv) and vxv > 0 and vix / vxv >= 1.0:
        vol.append("短期恐慌高于中期")
    if vol:
        flags.append(("波动", "，".join(vol)))
    if by["soxq"].close < by["soxq"].ma200:
        flags.append(("半导体", "半导体在 200 日线下方"))
    return flags


def _risk_trend(frames: dict[str, pd.DataFrame], mag7_frames: dict[str, pd.DataFrame],
                macro: pd.DataFrame, rsp_ratio: pd.Series) -> list[RiskTrendItem]:
    """过去 20 天，风险相关指标是在恶化还是在改善。不是预测，是方向。"""
    items: list[RiskTrendItem] = []

    vix = macro["VIXCLS"].dropna() if "VIXCLS" in macro else pd.Series(dtype=float)
    if len(vix) >= 21:
        now, prev = float(vix.iloc[-1]), float(vix.iloc[-21])
        delta = now - prev
        items.append(RiskTrendItem(
            "VIX",
            f"从 {prev:.0f} 到 {now:.0f}（{delta:+.0f}，截至 {vix.index[-1].strftime('%m/%d')}）",
            delta > 1,
            "恐慌温度计：20 以下平静，30 以上紧张，40 以上极度恐慌",
        ))

    baa = macro["BAA10Y"].dropna() if "BAA10Y" in macro else pd.Series(dtype=float)
    if len(baa) >= 41:
        cur = (float(baa.iloc[-1]) - float(baa.iloc[-21])) * 100
        prev = (float(baa.iloc[-21]) - float(baa.iloc[-41])) * 100
        items.append(RiskTrendItem(
            "信用利差",
            f"20 日 {cur:+.0f}bp（前 20 日 {prev:+.0f}bp，截至 {baa.index[-1].strftime('%m/%d')}）",
            cur > prev and cur > 0,
            "企业借钱比国债贵多少：走阔说明市场在担心违约，收窄说明风险在释放",
        ))

    if mag7_frames:
        denom = len(mag7_frames)
        now_b = sum(1 for df in mag7_frames.values()
                    if len(df) > 21 and pd.notna(df["ma200"].iloc[-1]) and df["close"].iloc[-1] > df["ma200"].iloc[-1])
        prev_b = sum(1 for df in mag7_frames.values()
                     if len(df) > 21 and pd.notna(df["ma200"].iloc[-21]) and df["close"].iloc[-21] > df["ma200"].iloc[-21])
        items.append(RiskTrendItem(
            "七姐妹广度", f"{prev_b}/{denom} → {now_b}/{denom}", now_b < prev_b,
            "7 只里有几只在 200 日线上方：变少说明上涨只靠少数大票",
        ))

    for key, name in (("qqq", "QQQ"), ("soxq", "半导体")):
        df = frames.get(key)
        if df is None or len(df) <= 21 or pd.isna(df["ma200"].iloc[-1]) or pd.isna(df["ma200"].iloc[-21]):
            continue
        now_g = float(df["close"].iloc[-1] / df["ma200"].iloc[-1] - 1)
        prev_g = float(df["close"].iloc[-21] / df["ma200"].iloc[-21] - 1)
        note = ("价格离一年平均成本多远：变负说明中期趋势转弱"
                if name == "QQQ" else "AI/芯片是最敏感的部分：跌破 200 日线说明这条线在出清")
        items.append(RiskTrendItem(f"{name} 距 200 日线", f"{prev_g:+.1%} → {now_g:+.1%}", now_g < prev_g, note))

    if len(rsp_ratio) >= 41:
        now_r = float(rsp_ratio.iloc[-1] / rsp_ratio.iloc[-21] - 1)
        prev_r = float(rsp_ratio.iloc[-21] / rsp_ratio.iloc[-41] - 1)
        items.append(RiskTrendItem(
            "等权/市值权", f"{prev_r:+.1%} → {now_r:+.1%}", now_r < prev_r and now_r < 0,
            "等权跑赢=广度在修复；等权跑输=上涨只靠少数大票",
        ))

    return items


def _nan_state(m: Market) -> MarketState:
    n = float("nan")
    return MarketState(m, n, n, n, n, n, 0, n)


def _nan_mag7(symbol: str, name: str) -> Mag7State:
    n = float("nan")
    return Mag7State(symbol, name, n, n, 0)


def analyze(force: bool = False) -> Analysis:
    frames: dict[str, pd.DataFrame | None] = {}
    quality: list[str] = []
    for m in MARKETS:
        try:
            df = build_frame(data.fetch_us(m.symbol, force=force))
            if (df["ret"].tail(250) < -0.35).any():
                quality.append(f"{m.symbol} 近 250 天有单日跌超 35%，可能是拆股/数据异常，本次不判断")
                df = None
            frames[m.key] = df
        except Exception:
            quality.append(f"{m.symbol} 数据抓取失败，本次不判断")
            frames[m.key] = None

    valid = [df for df in frames.values() if df is not None]
    if not valid:
        raise RuntimeError("所有市场数据都不可用")
    spy_index = frames["spy"].index if frames.get("spy") is not None else valid[0].index

    macro = data.load_macro(spy_index, force=force)
    if "VIXCLS" in macro and macro["VIXCLS"].isna().all():
        quality.append("VIX 不可用，波动判断已跳过")
    if "BAA10Y" in macro and macro["BAA10Y"].isna().all():
        quality.append("信用利差不可用，信用判断已跳过")

    vix = float(macro["VIXCLS"].iloc[-1]) if "VIXCLS" in macro and pd.notna(macro["VIXCLS"].iloc[-1]) else float("nan")
    baa = macro["BAA10Y"].dropna() if "BAA10Y" in macro else pd.Series(dtype=float)

    states: list[MarketState] = []
    for m in MARKETS:
        df = frames[m.key]
        if df is None or df.empty:
            states.append(_nan_state(m))
            continue
        r = df.iloc[-1]
        states.append(MarketState(
            market=m,
            close=float(r["close"]),
            ma20=float(r["ma20"]),
            ma50=float(r["ma50"]),
            ma200=float(r["ma200"]),
            drawdown=float(r["dd"]) if pd.notna(r["dd"]) else float("nan"),
            stage=classify_stage(df, vix),
            low60=float(df["close"].rolling(60, min_periods=20).min().iloc[-1]),
        ))

    mag7: list[Mag7State] = []
    mag7_frames: dict[str, pd.DataFrame] = {}
    for symbol, name in MAG7:
        try:
            df = build_frame(data.fetch_us(symbol, force=force))
            if (df["ret"].tail(250) < -0.35).any():
                quality.append(f"{symbol} 近 250 天有单日跌超 35%，可能是拆股/数据异常，本次不判断")
                df = None
        except Exception:
            quality.append(f"{symbol} 数据抓取失败，本次不判断")
            df = None
        if df is None or df.empty:
            mag7.append(_nan_mag7(symbol, name))
            continue
        mag7_frames[symbol] = df
        r = df.iloc[-1]
        mag7.append(Mag7State(
            symbol=symbol,
            name=name,
            close=float(r["close"]),
            ma200=float(r["ma200"]),
            stage=classify_stage(df, vix),
        ))
    mag7_breadth = sum(1 for x in mag7 if pd.notna(x.ma200) and x.close > x.ma200)

    data_date = pd.Timestamp(spy_index[-1]).date()
    for m in MARKETS:
        df = frames[m.key]
        if df is not None and not df.empty and df.index[-1].date() < data_date:
            quality.append(f"{m.symbol} 数据截至 {df.index[-1].date()}，比大盘晚")
    for symbol, df in mag7_frames.items():
        if not df.empty and df.index[-1].date() < data_date:
            quality.append(f"{symbol} 数据截至 {df.index[-1].date()}，比大盘晚")

    weekly: list[WeeklyItem] = []
    for m in MARKETS:
        df = frames[m.key]
        if df is None or len(df) < 6:
            continue
        c = df["close"]
        hi = float(c.iloc[-5:].max())
        lo = float(c.iloc[-5:].min())
        pos = (float(c.iloc[-1]) - lo) / (hi - lo) if hi > lo else 0.5
        wk = c.resample("W-FRI").last().dropna()
        ma10w = float(wk.rolling(10).mean().iloc[-1]) if len(wk) >= 10 else float("nan")
        weekly.append(WeeklyItem(
            m.name, float(c.iloc[-1] / c.iloc[-6] - 1), hi, lo, pos, ma10w, float(c.iloc[-1]),
        ))

    vix_series = macro["VIXCLS"].dropna() if "VIXCLS" in macro else pd.Series(dtype=float)
    vix_week_chg = float(vix_series.iloc[-1] - vix_series.iloc[-6]) if len(vix_series) >= 6 else float("nan")
    credit_week_chg = float((baa.iloc[-1] - baa.iloc[-6]) * 100) if len(baa) >= 6 else float("nan")

    ratio = pd.Series(dtype=float)
    try:
        ratio = data.load_rsp_spy_ratio(force=force)
        rsp_spy_20d = float(ratio.iloc[-1] / ratio.iloc[-21] - 1) if len(ratio) >= 21 else float("nan")
    except Exception:
        quality.append("RSP/SPY 广度数据不可用，广度判断已跳过")
        rsp_spy_20d = float("nan")

    trend = _risk_trend(frames, mag7_frames, macro, ratio)
    worsening = sum(1 for x in trend if x.worsening)
    risk_flags = _risk_flags(states, macro, mag7_breadth, rsp_spy_20d)
    return Analysis(
        states=states,
        mag7=mag7,
        risk_flags=risk_flags,
        mag7_breadth=mag7_breadth,
        rsp_spy_20d=rsp_spy_20d,
        data_date=pd.Timestamp(spy_index[-1]),
        risk_trend=trend,
        risk_worsening=worsening,
        quality_warnings=quality,
        weekly=weekly,
        vix_week_chg=vix_week_chg,
        credit_week_chg=credit_week_chg,
    )
