#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股顶部分层回测：大顶 vs 阶段顶 + 再入场信号
==============================================
三类事件:
  大顶  (CYCLE_TOP):  牛市终结，该清仓
  阶段顶(PHASE_TOP):  牛市中继回调，该减仓等再入场
  再入场(REENTRY):    回调充分，可以再加仓

回测覆盖:
  - 2005-2007 牛市: 4次阶段顶 + 最终大顶 + 4次再入场
  - 2014-2015 牛市: 2次阶段顶 + 最终大顶 + 2次再入场
  - 2019-2021 牛市: 3次阶段顶 + 最终大顶 + 3次再入场
  - 2024.9至今:    5次回调(阶段顶候选) + 已发生的再入场
"""
import os
os.environ["PYTHONIOENCODING"] = "utf-8"

import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# 事件定义
# ============================================================

EVENTS = [
    # ── 2005-2007 大牛市 ──
    {"name":"06-05 阶段顶", "peak":"2006-05-16", "trough":"2006-08-07", "type":"PHASE",
     "index":"sh000001", "desc":"中继回调-12%"},
    {"name":"07-01 阶段顶", "peak":"2007-01-24", "trough":"2007-02-06", "type":"PHASE",
     "index":"sh000001", "desc":"中继回调-15%"},
    {"name":"07-05 阶段顶(印花税)", "peak":"2007-05-29", "trough":"2007-07-05", "type":"PHASE",
     "index":"sh000001", "desc":"加印花税急跌-19%"},
    {"name":"2007-10 大顶", "peak":"2007-10-16", "trough":None, "type":"CYCLE",
     "index":"sh000001", "desc":"牛市终结，跌72.8%"},

    # ── 2014-2015 杠杆牛 ──
    {"name":"15-01 阶段顶", "peak":"2015-01-05", "trough":"2015-02-09", "type":"PHASE",
     "index":"sh000001", "desc":"中继回调-10%"},
    {"name":"15-04 阶段顶", "peak":"2015-04-28", "trough":"2015-05-07", "type":"PHASE",
     "index":"sh000001", "desc":"中继回调-10%"},
    {"name":"2015-06 大顶", "peak":"2015-06-12", "trough":None, "type":"CYCLE",
     "index":"sh000001", "desc":"杠杆牛终结，跌49%"},

    # ── 2019-2021 核心资产牛 ──
    {"name":"19-04 阶段顶", "peak":"2019-04-19", "trough":"2019-06-06", "type":"PHASE",
     "index":"sh000300", "desc":"中继回调-16%"},
    {"name":"20-01 阶段顶(新冠)", "peak":"2020-01-14", "trough":"2020-03-19", "type":"PHASE",
     "index":"sh000300", "desc":"新冠冲击-17%"},
    {"name":"20-07 阶段顶", "peak":"2020-07-14", "trough":"2020-09-30", "type":"PHASE",
     "index":"sh000300", "desc":"中继回调-8%"},
    {"name":"2021-02 大顶", "peak":"2021-02-18", "trough":None, "type":"CYCLE",
     "index":"sh000300", "desc":"核心资产顶，跌42%"},

    # ── 2024.9 至今 ──
    {"name":"24-10 回调", "peak":"2024-10-08", "trough":"2024-10-17", "type":"PHASE",
     "index":"sh000001", "desc":"924暴涨后急调-13.8%"},
    {"name":"24-11 回调", "peak":"2024-11-08", "trough":"2024-11-27", "type":"PHASE",
     "index":"sh000001", "desc":"政策兑现后调整-7.3%"},
    {"name":"24-12~25-01 回调", "peak":"2024-12-12", "trough":"2025-01-13", "type":"PHASE",
     "index":"sh000001", "desc":"年底调整-9.4%"},
    {"name":"25-04 回调(关税)", "peak":"2025-04-03", "trough":"2025-04-09", "type":"PHASE",
     "index":"sh000001", "desc":"关税冲击-10.6%"},
    {"name":"25-05 回调", "peak":"2025-05-15", "trough":"2025-05-28", "type":"PHASE",
     "index":"sh000001", "desc":"科技获利回吐-5.8%"},
]

# ============================================================
# 数据获取
# ============================================================

def fetch_index(index_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None


def fetch_macro_money() -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        df = df.rename(columns={df.columns[0]: "month"})
        m1c = [c for c in df.columns if "m1" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        m2c = [c for c in df.columns if "m2" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        keep = ["month"] + m1c[:1] + m2c[:1]
        result = df[keep].copy()
        result.columns = (["month","m1","m2"])[:len(result.columns)]
        result["month"] = pd.to_datetime(
            result["month"].astype(str).str.strip().str.replace("年","-").str.replace("月份",""),
            errors="coerce")
        result = result.dropna(subset=["month"]).sort_values("month")
        result["m1"] = pd.to_numeric(result["m1"], errors="coerce")
        result["m2"] = pd.to_numeric(result["m2"], errors="coerce")
        return result
    except Exception:
        return None


# ============================================================
# 指标计算
# ============================================================

def compute_indicators(df: pd.DataFrame, peak_date: pd.Timestamp) -> Dict:
    """
    在一个顶部日期前计算所有相关指标。
    返回 {指标名: {value, zscore, percentile}}
    """
    indicators = {}
    pre = df[df["date"] <= peak_date].copy()
    if len(pre) < 60:
        return indicators

    pre = pre.sort_values("date")
    pre["ret"] = pre["close"].pct_change()

    # ── 动量类 ──
    # 距60日均线偏离
    pre["ma60"] = pre["close"].rolling(60).mean()
    pre["ma120"] = pre["close"].rolling(120).mean()
    last = pre.iloc[-1]
    indicators["偏离MA60"] = (last["close"] - last["ma60"]) / last["ma60"] if pd.notna(last["ma60"]) else None
    indicators["偏离MA120"] = (last["close"] - last["ma120"]) / last["ma120"] if pd.notna(last["ma120"]) else None

    # 3个月涨幅
    three_m = peak_date - pd.DateOffset(months=3)
    early = pre[pre["date"] <= three_m]
    if len(early) > 0:
        indicators["3月涨幅"] = (last["close"] - early["close"].iloc[-1]) / early["close"].iloc[-1]

    # 1个月涨幅
    one_m = peak_date - pd.DateOffset(months=1)
    early_1m = pre[pre["date"] <= one_m]
    if len(early_1m) > 0:
        indicators["1月涨幅"] = (last["close"] - early_1m["close"].iloc[-1]) / early_1m["close"].iloc[-1]

    # ── 波动率类 ──
    pre["vol20"] = pre["ret"].rolling(20).std()
    pre["vol60"] = pre["ret"].rolling(60).std()
    if pd.notna(pre["vol20"].iloc[-1]) and pd.notna(pre["vol60"].iloc[-2]):
        indicators["波动率比率"] = pre["vol20"].iloc[-1] / pre["vol60"].iloc[-2]  # 近期/中期

    # ── 成交量类 ──
    pre["vol_ma20"] = pre["volume"].rolling(20).mean()
    pre["vol_ma60"] = pre["volume"].rolling(60).mean()
    # 量能极端度
    if pd.notna(pre["vol_ma20"].iloc[-1]) and pre["vol_ma60"].iloc[-1] > 0:
        indicators["量能比率"] = pre["volume"].iloc[-1] / pre["vol_ma60"].iloc[-1]
    if pd.notna(pre["vol_ma20"].iloc[-1]) and pre["vol_ma60"].iloc[-1] > 0:
        indicators["量能趋势"] = pre["vol_ma20"].iloc[-1] / pre["vol_ma60"].iloc[-1]

    # ── RSI类 ──
    delta = pre["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    pre["rsi14"] = 100 - (100 / (1 + rs))
    indicators["RSI14"] = pre["rsi14"].iloc[-1] if pd.notna(pre["rsi14"].iloc[-1]) else None

    # ── 连续上涨天数 ──
    pre["up"] = pre["close"] > pre["open"]
    streak = 0
    for i in range(len(pre)-1, -1, -1):
        if pre["up"].iloc[i]:
            streak += 1
        else:
            break
    indicators["连阳天数"] = streak

    # ── 近期上涨比例 ──
    indicators["20日上涨比"] = pre.tail(20)["up"].mean()

    return indicators


def compute_reentry_indicators(df: pd.DataFrame, peak_date: pd.Timestamp, trough_date: pd.Timestamp) -> Dict:
    """在回调底部计算再入场指标"""
    indicators = {}
    trough = df[df["date"] <= trough_date].copy()
    if len(trough) < 60:
        return indicators

    trough = trough.sort_values("date")
    trough["ret"] = trough["close"].pct_change()

    last = trough.iloc[-1]

    # 从顶部跌了多少
    peak_row = df[df["date"] == peak_date]
    if len(peak_row) == 0:
        peak_row = df[df["date"] <= peak_date].tail(1)
    if len(peak_row) > 0:
        indicators["最大回撤"] = (last["close"] - peak_row["close"].iloc[0]) / peak_row["close"].iloc[0]

    # 距60日均线
    trough["ma60"] = trough["close"].rolling(60).mean()
    indicators["偏离MA60"] = (last["close"] - trough["ma60"].iloc[-1]) / trough["ma60"].iloc[-1] if pd.notna(trough["ma60"].iloc[-1]) else None

    # RSI
    delta = trough["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    trough["rsi14"] = 100 - (100 / (1 + rs))
    indicators["RSI14"] = trough["rsi14"].iloc[-1] if pd.notna(trough["rsi14"].iloc[-1]) else None

    # 量能萎缩
    trough["vol_ma20"] = trough["volume"].rolling(20).mean()
    trough["vol_ma60"] = trough["volume"].rolling(60).mean()
    if pd.notna(trough["vol_ma20"].iloc[-1]) and trough["vol_ma60"].iloc[-1] > 0:
        indicators["量能比率"] = trough["vol_ma20"].iloc[-1] / trough["vol_ma60"].iloc[-1]

    # 下跌天数
    indicators["下跌天数"] = (trough_date - peak_date).days

    # 是否企稳（最近5日波动缩小）
    if len(trough) >= 10:
        recent_std = trough["ret"].tail(5).std()
        prior_std = trough["ret"].iloc[-10:-5].std()
        if prior_std > 0:
            indicators["波动率收缩"] = recent_std / prior_std

    return indicators


# ============================================================
# 回测主逻辑
# ============================================================

def run():
    print("=" * 70)
    print("  A 股顶部分层回测：大顶 vs 阶段顶 + 再入场信号")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    money_df = fetch_macro_money()
    print(f"\nM1/M2 data: {len(money_df) if money_df is not None else 0} months")

    # ── Part 1: 顶部分类回测 ──
    print("\n" + "=" * 70)
    print("  Part 1: 顶部信号 — 大顶 vs 阶段顶")
    print("=" * 70)

    top_results = []

    for ev in EVENTS:
        peak = pd.Timestamp(ev["peak"])
        start = (peak - pd.DateOffset(months=15)).strftime("%Y%m%d")
        end = (peak + pd.DateOffset(days=10)).strftime("%Y%m%d")

        idx = fetch_index(ev["index"], start, end)
        if idx is None or len(idx) < 30:
            continue

        inds = compute_indicators(idx, peak)
        if not inds:
            continue

        # 汇总为可排序的分数
        score = 0
        triggers = []

        # 动量过热
        dev60 = inds.get("偏离MA60")
        if dev60 is not None and dev60 > 0.12:
            score += 1; triggers.append(f"偏离MA60={dev60:.1%}")
        ret3m = inds.get("3月涨幅")
        if ret3m is not None and ret3m > 0.15:
            score += 1; triggers.append(f"3月涨{ret3m:.1%}")
        ret1m = inds.get("1月涨幅")
        if ret1m is not None and ret1m > 0.08:
            score += 1; triggers.append(f"1月涨{ret1m:.1%}")

        # 波动率
        vr = inds.get("波动率比率")
        if vr is not None and vr > 1.3:
            score += 1; triggers.append(f"波动率={vr:.1f}x")

        # 量能
        vr_vol = inds.get("量能趋势")
        if vr_vol is not None and vr_vol > 1.3:
            score += 1; triggers.append(f"放量{ vr_vol:.1f}x")

        # RSI
        rsi = inds.get("RSI14")
        if rsi is not None and rsi > 75:
            score += 1; triggers.append(f"RSI={rsi:.0f}")

        # 连阳
        streak = inds.get("连阳天数", 0)
        if streak >= 5:
            score += 1; triggers.append(f"连阳{streak}天")

        # M1
        if money_df is not None and peak.year >= 2005:
            m = money_df[(money_df["month"] <= peak) & (money_df["month"] >= peak - pd.DateOffset(months=12))]
            if len(m) >= 6:
                m = m.sort_values("month")
                m1_early = m["m1"].head(3).mean()
                m1_late = m["m1"].tail(3).mean()
                m1_chg = m1_late - m1_early
                if m1_chg < -1.0:
                    score += 2; triggers.append(f"M1下滑{m1_chg:+.1f}pp")
                elif m1_chg < 0:
                    score += 1; triggers.append(f"M1微降{m1_chg:+.1f}pp")

        ev["score"] = score
        ev["triggers"] = triggers
        ev["indicators"] = {k: round(v, 4) if isinstance(v, float) and not np.isnan(v) else v
                           for k, v in inds.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}
        top_results.append(ev)

        # 输出
        tag = "[CYCLE]" if ev["type"] == "CYCLE" else "[PHASE]"
        score_bar = "|" * score
        print(f"\n{tag} {ev['name']} ({ev['desc']})")
        print(f"   Score: {score} {score_bar}")
        print(f"   Triggers: {', '.join(triggers)}")
        for k, v in ev["indicators"].items():
            if isinstance(v, float):
                print(f"   {k}: {v:.3f}" if abs(v) < 10 else f"   {k}: {v:.1f}")

    # ── Part 2: 再入场信号 ──
    print("\n\n" + "=" * 70)
    print("  Part 2: 再入场信号 — 回调底部特征")
    print("=" * 70)

    reentry_results = []

    for ev in EVENTS:
        if ev["trough"] is None:
            continue  # 大顶没有 trough
        peak = pd.Timestamp(ev["peak"])
        trough = pd.Timestamp(ev["trough"])

        start = (peak - pd.DateOffset(months=15)).strftime("%Y%m%d")
        end = (trough + pd.DateOffset(days=10)).strftime("%Y%m%d")

        idx = fetch_index(ev["index"], start, end)
        if idx is None or len(idx) < 30:
            continue

        inds = compute_reentry_indicators(idx, peak, trough)
        if not inds:
            continue

        score = 0
        triggers = []

        dd = inds.get("最大回撤")
        if dd is not None and dd < -0.05:
            score += 1; triggers.append(f"回撤{dd:.1%}")

        dev60 = inds.get("偏离MA60")
        if dev60 is not None and dev60 < 0:
            score += 1; triggers.append(f"跌破MA60({dev60:.1%})")

        rsi = inds.get("RSI14")
        if rsi is not None and rsi < 35:
            score += 2; triggers.append(f"RSI超卖={rsi:.0f}")
        elif rsi is not None and rsi < 45:
            score += 1; triggers.append(f"RSI偏低={rsi:.0f}")

        vol_ratio = inds.get("量能比率")
        if vol_ratio is not None and vol_ratio < 0.7:
            score += 2; triggers.append(f"地量{vol_ratio:.1%}")
        elif vol_ratio is not None and vol_ratio < 0.9:
            score += 1; triggers.append(f"缩量{vol_ratio:.1%}")

        vol_contract = inds.get("波动率收缩")
        if vol_contract is not None and vol_contract < 0.8:
            score += 1; triggers.append(f"波动收缩{vol_contract:.1%}")

        days = inds.get("下跌天数", 0)
        if days >= 20:
            score += 1; triggers.append(f"跌{days}天")

        ev["reentry_score"] = score
        ev["reentry_triggers"] = triggers
        ev["reentry_indicators"] = {k: round(v, 4) if isinstance(v, float) and not np.isnan(v) else v
                                    for k, v in inds.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}
        reentry_results.append(ev)

        tag = "[CYCLE]" if ev["type"] == "CYCLE" else "[PHASE]"
        score_bar = "|" * score
        print(f"\n{tag} {ev['name']} -> trough: {ev['trough']}")
        print(f"   Re-entry Score: {score} {score_bar}")
        print(f"   Triggers: {', '.join(triggers)}")
        for k, v in ev["reentry_indicators"].items():
            if isinstance(v, float):
                print(f"   {k}: {v:.3f}" if abs(v) < 10 else f"   {k}: {v:.1f}")

    # ── Part 3: 阈值分析 ──
    print("\n\n" + "=" * 70)
    print("  Part 3: 阈值分析 — 什么阈值能区分大顶和阶段顶")
    print("=" * 70)

    # 分组统计
    cycle_tops = [e for e in top_results if e["type"] == "CYCLE"]
    phase_tops = [e for e in top_results if e["type"] == "PHASE"]

    def mean_indicator(events, key):
        vals = [e["indicators"].get(key) for e in events
                if e["indicators"].get(key) is not None and not (isinstance(e["indicators"].get(key), float) and np.isnan(e["indicators"].get(key)))]
        return np.mean(vals) if vals else None

    print("\n大顶 vs 阶段顶 — 各指标均值对比:")
    print(f"   {'指标':<18} {'大顶均值':<12} {'阶段顶均值':<12} {'差异'}")
    print(f"   {'─'*18} {'─'*12} {'─'*12} {'─'*10}")

    key_metrics = ["偏离MA60", "偏离MA120", "3月涨幅", "1月涨幅", "波动率比率", "量能比率", "量能趋势", "RSI14", "连阳天数", "20日上涨比"]

    for km in key_metrics:
        cv = mean_indicator(cycle_tops, km)
        pv = mean_indicator(phase_tops, km)
        if cv is not None and pv is not None:
            diff = cv - pv
            sig = ">>" if diff > 0.5 else (">" if diff > 0.1 else ("~" if abs(diff) < 0.05 else "<"))
            print(f"   {km:<18} {cv:<12.3f} {pv:<12.3f} {sig} ({diff:+.3f})")

    # 分数分布
    print("\n\n顶部评分分布:")
    print(f"   大顶: {[e['score'] for e in cycle_tops]}")
    print(f"   阶段顶: {[e['score'] for e in phase_tops]}")

    cycle_scores = [e["score"] for e in cycle_tops]
    phase_scores = [e["score"] for e in phase_tops]
    print(f"   大顶均分: {np.mean(cycle_scores):.1f} | 阶段顶均分: {np.mean(phase_scores):.1f}")

    # ── Part 4: 再入场阈值 ──
    print("\n\n" + "=" * 70)
    print("  Part 4: 再入场信号汇总")
    print("=" * 70)

    for ev in reentry_results:
        if ev["type"] == "CYCLE":
            continue  # 大顶没有再入场
        print(f"\n{ev['name']}: Re-entry={ev['reentry_score']}分")
        print(f"   Triggers: {', '.join(ev.get('reentry_triggers',[]))}")

    good_reentry = [e for e in reentry_results if e["type"] == "PHASE" and e.get("reentry_score", 0) >= 4]
    weak_reentry = [e for e in reentry_results if e["type"] == "PHASE" and e.get("reentry_score", 0) < 4]
    print(f"\n   >=4分 (强再入场信号): {len(good_reentry)}次")
    print(f"   <4分 (弱信号): {len(weak_reentry)}次")

    print("\n" + "=" * 70)
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    run()
