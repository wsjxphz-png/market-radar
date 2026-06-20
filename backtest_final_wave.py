#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最后一波上涨识别 — 回测
========================
不预测顶，只判断当前这波上涨是否具有"末升段"特征。

末升段特征（全球通用，A 股验证）:
  1. 广度恶化: 指数涨但创新高个股减少
  2. 动量背离: 价格新高但 RSI 背离
  3. 领导集中: 少数股票拖着指数涨
  4. 量能衰竭: 价涨量缩
  5. 防御轮动: 公用事业/高股息悄然走强

测试: 2007/2015/2021 三轮末升段是否同时触发上述特征
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
# 事件: 每个牛市的各波上涨
# ============================================================

# 把每轮牛市拆成多波上涨，其中最后一波标记为 FINAL
WAVES = [
    # ── 2005-2007 牛市 ──
    {"name":"06-01 上涨波1","start":"2005-12-06","end":"2006-05-16","type":"MID","desc":"第一波主升"},
    {"name":"06-08 上涨波2","start":"2006-08-07","end":"2007-01-24","type":"MID","desc":"第二波主升"},
    {"name":"07-02 上涨波3","start":"2007-02-06","end":"2007-05-29","type":"MID","desc":"第三波主升"},
    {"name":"07-07 上涨波4(末升段)","start":"2007-07-05","end":"2007-10-16","type":"FINAL","desc":"末升段→大顶"},

    # ── 2014-2015 杠杆牛 ──
    {"name":"14-07 上涨波1","start":"2014-07-22","end":"2015-01-05","type":"MID","desc":"券商+金融主升"},
    {"name":"15-02 上涨波2","start":"2015-02-09","end":"2015-04-28","type":"MID","desc":"互联网+主升"},
    {"name":"15-05 上涨波3(末升段)","start":"2015-05-07","end":"2015-06-12","type":"FINAL","desc":"末升段→大顶"},

    # ── 2019-2021 核心资产牛 ──
    {"name":"19-01 上涨波1","start":"2019-01-04","end":"2019-04-19","type":"MID","desc":"估值修复"},
    {"name":"19-06 上涨波2","start":"2019-06-06","end":"2020-01-14","type":"MID","desc":"科技+消费主升"},
    {"name":"20-03 上涨波3","start":"2020-03-19","end":"2020-07-14","type":"MID","desc":"疫后反弹"},
    {"name":"20-09 上涨波4(末升段)","start":"2020-09-30","end":"2021-02-18","type":"FINAL","desc":"末升段→大顶"},

    # ── 2024.9 至今 ──
    {"name":"24-09 上涨波1","start":"2024-09-24","end":"2024-10-08","type":"MID","desc":"924暴涨"},
    {"name":"24-10 上涨波2","start":"2024-10-17","end":"2024-11-08","type":"MID","desc":"政策预期"},
    {"name":"25-01 上涨波3","start":"2025-01-13","end":"2025-03-18","type":"MID","desc":"春季行情"},
    {"name":"25-04 上涨波4","start":"2025-04-09","end":"2025-05-15","type":"MID","desc":"关税修复"},
    {"name":"25-05 当前(监控中)","start":"2025-05-28","end":None,"type":"CURRENT","desc":"当前进行中"},
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
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return None


# ============================================================
# 末升段特征检测
# ============================================================

def detect_final_wave_signals(idx_df: pd.DataFrame, wave_start: pd.Timestamp, wave_end: Optional[pd.Timestamp]) -> Dict:
    """
    检测一波上涨是否具有末升段特征。
    需要比较"这波"和"前面几波"的结构差异。
    """
    if len(idx_df) < 120:
        return {}

    df = idx_df.copy()
    df["ret"] = df["close"].pct_change()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma120"] = df["close"].rolling(120).mean()

    # 获取 wave 内的数据
    if wave_end is not None:
        wave_mask = (df["date"] >= wave_start) & (df["date"] <= wave_end)
        pre_mask = df["date"] < wave_start
    else:
        # CURRENT wave
        wave_mask = df["date"] >= wave_start
        pre_mask = df["date"] < wave_start

    pre = df[pre_mask].copy()

    # 先在 df 上计算所有指标
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    df["rsi14"] = 100 - (100 / (1 + rs))
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["volatility"] = df["ret"].rolling(20).std()

    # 现在 wave 切片包含所有指标
    wave = df[wave_mask].copy()

    if len(wave) < 10:
        return {}

    # ── 特征1: 动量背离 (价格新高，RSI 不创新高) ──
    if len(wave) >= 14:
        wave_price_peak_idx = wave["close"].idxmax()
        wave_rsi_max_idx = wave["rsi14"].idxmax()
        wave_rsi_max = wave["rsi14"].max()

        if wave_rsi_max_idx is not None and wave_price_peak_idx is not None and pd.notna(wave_rsi_max_idx) and pd.notna(wave_price_peak_idx):
            if wave_rsi_max_idx < wave_price_peak_idx and wave_rsi_max > 60:
                rsi_divergence = True
                rsi_div_detail = f"RSI先见顶{wave_rsi_max:.0f}({df.loc[wave_rsi_max_idx,'date'].strftime('%m-%d')})->价格后见顶({df.loc[wave_price_peak_idx,'date'].strftime('%m-%d')})"
            else:
                rsi_divergence = False
                rsi_div_detail = "RSI与价格同步"
        else:
            rsi_divergence = False
            rsi_div_detail = ""
    else:
        rsi_divergence = None
        rsi_div_detail = "数据不足"

    # ── 特征2: 量能衰竭 (价涨量缩) ──
    half = len(wave) // 2
    if half >= 5:
        first_half_vol = wave["volume"].iloc[:half].mean()
        second_half_vol = wave["volume"].iloc[half:].mean()
        if first_half_vol > 0:
            vol_change = second_half_vol / first_half_vol
            vol_divergence = vol_change < 0.85
            vol_detail = f"前半均量->后半均量: {vol_change:.1%}"
        else:
            vol_divergence = None
            vol_detail = ""
    else:
        vol_divergence = None
        vol_detail = ""

    # ── 特征3: 波动率收缩后突然放大 (高潮特征) ──
    if len(wave) >= 20:
        early_vol = wave["volatility"].iloc[:min(10, half)].mean() if half >= 5 else wave["volatility"].iloc[:5].mean()
        late_vol = wave["volatility"].tail(5).mean()
        if early_vol and early_vol > 0:
            vol_spike = late_vol / early_vol
            vol_spike_detail = f"前期波幅->末期波幅: {vol_spike:.1f}x"
        else:
            vol_spike = None
            vol_spike_detail = ""
    else:
        vol_spike = None
        vol_spike_detail = ""

    # ── 特征4: 连阳加速 ──
    if len(wave) >= 10:
        wave_tail = wave.tail(10).copy()
        wave_tail["up"] = wave_tail["close"] > wave_tail["open"]
        streak = 0
        for i in range(len(wave_tail)-1, -1, -1):
            if wave_tail["up"].iloc[i]:
                streak += 1
            else:
                break
        streak_signal = streak >= 5
        streak_detail = f"末段连阳{streak}天"
    else:
        streak_signal = None
        streak_detail = ""

    # ── 特征5: 距MA60偏离极致 ──
    last_close = wave["close"].iloc[-1]
    last_ma60 = wave["ma60"].iloc[-1] if "ma60" in wave.columns else df[df["date"] <= (wave_end or datetime.now())]["ma60"].iloc[-1]
    if pd.notna(last_ma60) and last_ma60 > 0:
        deviation = (last_close - last_ma60) / last_ma60
    else:
        deviation = 0
    extreme_dev = deviation > 0.15

    # ── 综合评分 ──
    score = 0
    if rsi_divergence: score += 2
    if vol_divergence: score += 2
    if vol_spike and vol_spike > 1.5: score += 1
    if streak_signal: score += 1
    if extreme_dev: score += 1

    return {
        "RSI背离": {"triggered": rsi_divergence, "detail": rsi_div_detail},
        "量能衰竭": {"triggered": vol_divergence, "detail": vol_detail},
        "波幅高潮": {"triggered": vol_spike > 1.5 if vol_spike else None, "detail": vol_spike_detail},
        "连阳冲刺": {"triggered": streak_signal, "detail": streak_detail},
        "偏离均线": {"triggered": extreme_dev, "detail": f"偏离MA60: {deviation:.1%}"},
        "score": score,
        "max_score": 7,
    }


# ============================================================
# 运行
# ============================================================

def run():
    print("=" * 70)
    print("  末升段特征回测")
    print("  不判断'到没到顶', 判断'这波像不像末升段'")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # 获取长周期指数数据
    print("\nLoading data...")
    idx_sh = fetch_index("sh000001", "20050101", "20251231")
    idx_300 = fetch_index("sh000300", "20180101", "20251231")
    print(f"   SSE: {len(idx_sh) if idx_sh is not None else 0} days")
    print(f"   CSI300: {len(idx_300) if idx_300 is not None else 0} days")

    final_scores = []
    mid_scores = []
    current_result = None

    for w in WAVES:
        start = pd.Timestamp(w["start"])
        end = pd.Timestamp(w["end"]) if w["end"] else datetime.now()

        # 选指数
        if start.year >= 2019:
            idx = idx_300
        else:
            idx = idx_sh

        if idx is None:
            continue

        # 扩展数据窗口（需要前面至少120天数据做MA计算）
        fetch_start = (start - pd.DateOffset(days=200)).strftime("%Y%m%d")
        fetch_end = (end + pd.DateOffset(days=5)).strftime("%Y%m%d")
        window = idx[(idx["date"] >= fetch_start) & (idx["date"] <= fetch_end)]

        if len(window) < 120:
            continue

        sigs = detect_final_wave_signals(window, start, end if w["end"] else None)
        score = sigs.pop("score", 0)
        max_s = sigs.pop("max_score", 7)

        # 输出
        tag_map = {"FINAL":"[FINAL]","MID":"[MID]  ","CURRENT":"[NOW]  "}
        tag = tag_map.get(w["type"], "[?]")
        bar = "|" * score + "." * (max_s - score)
        print(f"\n{tag} {w['name']} ({w['desc']})")
        print(f"   Score: {score}/{max_s} {bar}")
        for k, v in sigs.items():
            icon = "[X]" if v.get("triggered") else ("[O]" if v.get("triggered") is False else "[?]")
            detail = v.get("detail", "")
            if detail:
                print(f"   {icon} {k}: {detail}")

        if w["type"] == "FINAL":
            final_scores.append(score)
        elif w["type"] == "MID":
            mid_scores.append(score)
        elif w["type"] == "CURRENT":
            current_result = {"name": w["name"], "score": score, "max": max_s, "signals": sigs}

    # ── 汇总 ──
    print("\n\n" + "=" * 70)
    print("  汇总: 末升段 vs 中继上涨 评分对比")
    print("=" * 70)

    print(f"\n  历史末升段评分: {final_scores}")
    print(f"  历史中继段评分: {mid_scores}")
    if final_scores:
        print(f"  末升段均分: {np.mean(final_scores):.1f}")
    if mid_scores:
        print(f"  中继段均分: {np.mean(mid_scores):.1f}")

    # 找阈值
    if final_scores and mid_scores:
        # 什么 >= 多少分是最优区分阈值
        print("\n  阈值分析:")
        for th in range(1, 8):
            final_hit = sum(1 for s in final_scores if s >= th) / len(final_scores)
            mid_false = sum(1 for s in mid_scores if s >= th) / len(mid_scores)
            disc = final_hit - mid_false
            mark = "[OK]" if disc > 0.5 else ("[~]" if disc > 0.2 else "[!]")
            print(f"   阈值>={th}: 末升段命中{final_hit:.0%} | 中继误报{mid_false:.0%} | {mark} 区分度{disc:+.0%}")

    # 当前状态
    if current_result:
        print(f"\n\n  >>> 当前状态: {current_result['name']}")
        print(f"  >>> 末升段评分: {current_result['score']}/{current_result['max']}")
        if current_result['score'] >= 4:
            print(f"  >>> [!] 当前上涨波具有末升段特征，建议警惕")
        elif current_result['score'] >= 2:
            print(f"  >>> [~] 当前上涨波有部分末升段特征，需跟踪")
        else:
            print(f"  >>> [O] 当前上涨波不具备明显的末升段特征")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    run()
