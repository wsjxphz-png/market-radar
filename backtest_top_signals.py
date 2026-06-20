#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股大顶监测框架 — 历史回测 + 本轮回调验证
============================================
目的:
  1. 检验五层框架在 2007/2015/2021 三次大顶是否发出信号（命中率）
  2. 检验在 2024.9.24 以来五次回调中是否误报（误报率）
  3. 找出能区分"真顶"和"中途回调"的指标组合

核心问题: 如果指标在每次 10% 回调都叫，那它没用。
          只有当指标能在真顶前集中触发、在回调中保持安静，才是有效框架。
"""
import os
os.environ["PYTHONIOENCODING"] = "utf-8"

import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# 事件定义
# ============================================================

# 历史大顶（真顶）
TOPS = {
    "2007-全球牛市顶": {
        "peak_date": "2007-10-16", "is_top": True,
        "index": "sh000001", "narrative": "工业化+城镇化+股改",
        "result": "后续跌72.8%，真顶",
    },
    "2015-杠杆牛顶": {
        "peak_date": "2015-06-12", "is_top": True,
        "index": "sh000001", "narrative": "互联网++杠杆+场外配资",
        "result": "后续跌49%，真顶",
    },
    "2021-核心资产顶": {
        "peak_date": "2021-02-18", "is_top": True,
        "index": "sh000300", "narrative": "消费升级+公募爆发+外资",
        "result": "后续跌42%，真顶",
    },
}

# 2024.9.24 以来的主要回调（非顶）
CORRECTIONS = {
    "2024-10 首轮急调": {
        "start": "2024-10-08", "end": "2024-10-17",
        "is_top": False,
        "index": "sh000001",
        "narrative": "924暴涨后获利回吐",
        "result": "上证-13.8%，回调后创新高",
    },
    "2024-11 二次回调": {
        "start": "2024-11-08", "end": "2024-11-27",
        "is_top": False,
        "index": "sh000001",
        "narrative": "政策预期兑现后调整",
        "result": "上证-7.3%，回调后继续上行",
    },
    "2024-12~2025-01 年底调整": {
        "start": "2024-12-12", "end": "2025-01-13",
        "is_top": False,
        "index": "sh000001",
        "narrative": "年末资金面收紧+春节前避险",
        "result": "上证-9.4%，节后回升",
    },
    "2025-04 关税冲击": {
        "start": "2025-04-03", "end": "2025-04-09",
        "is_top": False,
        "index": "sh000001",
        "narrative": "美国关税升级冲击",
        "result": "上证-10.6%，快速修复",
    },
    "2025-05 近期调整": {
        "start": "2025-05-15", "end": "2025-05-28",
        "is_top": False,
        "index": "sh000001",
        "narrative": "科技股获利回吐+外部不确定性",
        "result": "上证-5.8%，调整中",
    },
}

ALL_EVENTS = {**TOPS, **CORRECTIONS}

# ============================================================
# 数据获取
# ============================================================

def fetch_index_history(index_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        return None


def fetch_margin_history() -> Optional[pd.DataFrame]:
    """两融余额（仅2010年后可用）"""
    for func_name in ["stock_margin_sse", "stock_margin_detail_sse"]:
        try:
            import akshare as ak
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            df = fn(start_date="20100101", end_date="20261231")
            if df is not None and len(df) > 0:
                df["date"] = pd.to_datetime(df["date"])
                return df
        except Exception:
            continue
    return None


def fetch_macro_money_supply() -> Optional[pd.DataFrame]:
    """M1/M2 月度同比"""
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        df = df.rename(columns={df.columns[0]: "month"})
        m1_cols = [c for c in df.columns if "m1" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        m2_cols = [c for c in df.columns if "m2" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        keep = ["month"]
        if m1_cols: keep.append(m1_cols[0])
        if m2_cols: keep.append(m2_cols[0])
        result = df[keep].copy()
        result.columns = (["month", "m1_yoy", "m2_yoy"])[:len(result.columns)]
        result["month"] = pd.to_datetime(
            result["month"].astype(str).str.strip().str.replace("年", "-").str.replace("月份", ""),
            errors="coerce"
        )
        result = result.dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
        result["m1_yoy"] = pd.to_numeric(result["m1_yoy"], errors="coerce")
        result["m2_yoy"] = pd.to_numeric(result["m2_yoy"], errors="coerce")
        return result
    except Exception:
        return None


def fetch_social_financing() -> Optional[pd.DataFrame]:
    """社融月度增量"""
    try:
        import akshare as ak
        df = ak.macro_china_shrzgm()
        df = df.rename(columns={df.columns[0]: "month"})
        sz_cols = [c for c in df.columns if "增量" in str(c) or "规模" in str(c)]
        if not sz_cols:
            sz_cols = [df.columns[1]]
        result = df[["month", sz_cols[0]]].copy()
        result.columns = ["month", "sz_inc"]
        result["month"] = pd.to_datetime(
            result["month"].astype(str).str.strip().str.replace("年", "-").str.replace("月", ""),
            errors="coerce"
        )
        result = result.dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
        result["sz_inc"] = pd.to_numeric(result["sz_inc"], errors="coerce")
        return result
    except Exception:
        return None


def fetch_market_breadth() -> Optional[pd.DataFrame]:
    """获取 A 股涨跌家数（仅 2020 年后较全）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        # 这个是当天快照，做不了历史。尝试 market breadth
        return None
    except Exception:
        return None


# ============================================================
# 单事件信号检测
# ============================================================

def detect_signals(
    event_date_or_start: pd.Timestamp,
    idx_df: pd.DataFrame,
    margin_df: Optional[pd.DataFrame],
    money_df: Optional[pd.DataFrame],
    sz_df: Optional[pd.DataFrame],
    event_name: str,
    is_top: bool,
) -> Dict:
    """
    对一个事件（顶或回调），检测各层指标是否发出信号。
    is_top=True: 检测窗口在事件日前 12 个月，看信号累积
    is_top=False: 检测窗口在回调期间，看信号是否误触
    """
    signals = {}

    # 对于真顶: 观察窗口在顶部前12个月
    # 对于回调: 观察窗口在回调起点前6个月到回调终点
    if is_top:
        window_start = event_date_or_start - pd.DateOffset(months=12)
        window_end = event_date_or_start + pd.DateOffset(months=1)
    else:
        window_start = event_date_or_start - pd.DateOffset(months=6)
        window_end = event_date_or_start + pd.DateOffset(months=1)  # event_date is start of correction

    idx_window = idx_df[(idx_df["date"] >= window_start) & (idx_df["date"] <= window_end)].copy()
    if len(idx_window) < 30:
        return {"error": f"指数数据不足 ({len(idx_window)}天)"}

    idx_window = idx_window.sort_values("date")
    idx_window["ret"] = idx_window["close"].pct_change()
    idx_window["vol_ma20"] = idx_window["volume"].rolling(20).mean()
    idx_window["vol_ma60"] = idx_window["volume"].rolling(60).mean()

    # ──────────── 第一层：资金结构 ────────────
    l1 = {}

    # 1.1 成交量变化（放量后缩量）
    pre = idx_window[idx_window["date"] <= event_date_or_start]
    post = idx_window[idx_window["date"] > event_date_or_start]
    if len(pre) >= 20 and len(post) >= 5:
        pre_vol = pre["volume"].tail(20).mean()
        post_vol = post["volume"].head(5).mean()
        if pre_vol > 0:
            ratio = post_vol / pre_vol
            l1["量能萎缩"] = {
                "triggered": ratio < 0.65,
                "value": f"缩至{ratio:.1%}",
                "detail": f"事件前20日均量→事件后5日均量: {ratio:.1%}",
            }

    # 1.2 两融余额趋势（仅 2015 后）
    if margin_df is not None:
        mrg = margin_df.copy()
        mrg["date"] = pd.to_datetime(mrg["date"])
        mrg_w = mrg[(mrg["date"] >= window_start) & (mrg["date"] <= window_end)]
        if len(mrg_w) > 10:
            bal_cols = [c for c in mrg_w.columns if "余额" in str(c) or "balance" in str(c).lower()]
            if bal_cols:
                mrg_pre = mrg_w[mrg_w["date"] <= event_date_or_start]
                mrg_post = mrg_w[mrg_w["date"] > event_date_or_start]
                if len(mrg_pre) >= 3 and len(mrg_post) >= 3:
                    pre_bal = mrg_pre[bal_cols[0]].tail(3).mean()
                    post_bal = mrg_post[bal_cols[0]].head(3).mean()
                    if pre_bal > 0:
                        bal_chg = (post_bal - pre_bal) / pre_bal
                        l1["两融余额收缩"] = {
                            "triggered": bal_chg < -0.03,
                            "value": f"{bal_chg:+.1%}",
                            "detail": f"两融余额: {bal_chg:+.1%}",
                        }

    signals["L1_资金结构"] = l1

    # ──────────── 第二层：基本面 ────────────
    signals["L2_产业基本面"] = {
        "财报验证": {"triggered": None, "value": "需人工", "detail": "自动回测无法获取当时产业财报数据"}
    }

    # ──────────── 第三层：宏观流动性 ────────────
    l3 = {}

    # 3.1 M1 同比变化（事件日前 12 个月趋势）
    if money_df is not None:
        m = money_df[(money_df["month"] >= window_start) & (money_df["month"] <= event_date_or_start)]
        if len(m) >= 6:
            m = m.sort_values("month")
            early = m["m1_yoy"].head(3).mean()
            late = m["m1_yoy"].tail(3).mean()
            m1_change = late - early
            l3["M1同比下滑"] = {
                "triggered": m1_change < -1.0,
                "value": f"{m1_change:+.1f}pp ({early:.1f}%→{late:.1f}%)",
                "detail": f"M1同比从{early:.1f}%降至{late:.1f}%，变化{m1_change:+.1f}pp",
            }

    # 3.2 社融趋势
    if sz_df is not None:
        sz = sz_df[(sz_df["month"] >= window_start) & (sz_df["month"] <= event_date_or_start)]
        if len(sz) >= 6:
            sz = sz.sort_values("month")
            early = sz["sz_inc"].head(3).mean()
            late = sz["sz_inc"].tail(3).mean()
            if abs(early) > 0:
                sz_chg = (late - early) / abs(early)
                l3["社融收缩"] = {
                    "triggered": sz_chg < -0.25,
                    "value": f"{sz_chg:+.0%} ({early:.0f}亿→{late:.0f}亿)",
                    "detail": f"社融3月均: {early:.0f}亿→{late:.0f}亿",
                }

    # 3.3 指数本身动能衰减
    if len(idx_window) >= 120:
        # 3个月涨幅
        three_m_ago = event_date_or_start - pd.DateOffset(months=3)
        early_data = idx_window[idx_window["date"] <= three_m_ago]
        if len(early_data) > 0:
            p_start = early_data["close"].iloc[-1]
            p_end = idx_window[idx_window["date"] <= event_date_or_start]["close"].iloc[-1]
            ret_3m = (p_end - p_start) / p_start
            l3["3月加速赶顶"] = {
                "triggered": ret_3m > 0.15,
                "value": f"{ret_3m:.1%}",
                "detail": f"事件前3个月涨幅{ret_3m:.1%} (>15%是加速赶顶迹象)",
            }

    signals["L3_宏观流动性"] = l3

    # ──────────── 第四层：市场结构 ────────────
    l4 = {}
    pre_data = idx_window[idx_window["date"] <= event_date_or_start]
    if len(pre_data) >= 60:
        # 4.1 波动率剧烈放大
        pre_data["ret"] = pre_data["close"].pct_change()
        pre_data["vol_20d"] = pre_data["ret"].rolling(20).std()
        early_vol = pre_data["vol_20d"].iloc[20:40].mean() if len(pre_data) >= 40 else pre_data["vol_20d"].iloc[10:20].mean()
        late_vol = pre_data["vol_20d"].tail(20).mean()
        vol_ratio = late_vol / early_vol if early_vol > 0 else 1
        l4["波动率激增"] = {
            "triggered": vol_ratio > 1.5,
            "value": f"{vol_ratio:.1f}x",
            "detail": f"波动率从{early_vol:.3f}升至{late_vol:.3f}",
        }

        # 4.2 上涨比例衰减（近似：最近20日阳线比例）
        if "up" in pre_data.columns or "open" in pre_data.columns:
            pre_data["is_up"] = pre_data["close"] > pre_data["open"]
            up_ratio = pre_data.tail(20)["is_up"].mean()
            l4["上涨乏力"] = {
                "triggered": up_ratio < 0.45,
                "value": f"{up_ratio:.0%}",
                "detail": f"最近20日上涨天数比例: {up_ratio:.0%}",
            }

    signals["L4_市场结构"] = l4

    # ──────────── 第五层：情绪 ────────────
    l5 = {}
    if len(idx_window) >= 60:
        # 5.1 成交量极端化（z-score）
        pre_for_vol = idx_window[idx_window["date"] <= event_date_or_start]
        if len(pre_for_vol) >= 60:
            vol_mean = pre_for_vol["volume"].rolling(60).mean()
            vol_std = pre_for_vol["volume"].rolling(60).std()
            if vol_std.iloc[-1] > 0:
                vol_z = (pre_for_vol["volume"].iloc[-1] - vol_mean.iloc[-1]) / vol_std.iloc[-1]
                l5["成交量极端"] = {
                    "triggered": abs(vol_z) > 1.8,
                    "value": f"z={vol_z:.1f}",
                    "detail": f"事件日成交量z-score: {vol_z:.1f}",
                }

        # 5.2 12个月累计涨幅（仅对顶部有意义）
        if is_top and len(pre_for_vol) >= 200:
            p_start = pre_for_vol["close"].iloc[0]
            p_peak = pre_for_vol["close"].max()
            total_ret = (p_peak - p_start) / p_start
            l5["12月累计大涨"] = {
                "triggered": total_ret > 0.5,
                "value": f"{total_ret:.1%}",
                "detail": f"12个月最大涨幅{total_ret:.1%}",
            }

    signals["L5_情绪估值"] = l5

    return signals


# ============================================================
# 汇总与对比分析
# ============================================================

def count_layer_signals(signals: Dict) -> Dict[str, int]:
    """统计每层触发数 / 总可检测数"""
    counts = {}
    for layer_name, layer in signals.items():
        if layer_name == "error":
            continue
        trig = sum(1 for v in layer.values() if v.get("triggered") is True)
        total = sum(1 for v in layer.values() if v.get("triggered") is not None)
        counts[layer_name] = {"triggered": trig, "detectable": total}
    return counts


def run_backtest():
    print("=" * 70)
    print("  A 股大顶监测框架 — 历史回测 + 本轮回调误报检验")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # 加载共享数据
    print("\n📡 加载宏观数据...")
    money_df = fetch_macro_money_supply()
    print(f"   {'[OK]' if money_df is not None else '[!!]'} M1/M2: {len(money_df) if money_df is not None else 0} 个月")

    sz_df = fetch_social_financing()
    print(f"   {'[OK]' if sz_df is not None else '[!!]'} 社融: {len(sz_df) if sz_df is not None else 0} 个月")

    margin_df = fetch_margin_history()
    print(f"   {'[OK]' if margin_df is not None else '[!]'} 两融: {len(margin_df) if margin_df is not None else 0} 条 (2010年前不可用)")

    # ============================================================
    # Part 1: 历史大顶回测
    # ============================================================
    print("\n\n" + "█" * 70)
    print("█  Part 1: 历史大顶信号命中率（指标应该触发）")
    print("█" * 70)

    top_results = {}
    for name, info in TOPS.items():
        peak = pd.Timestamp(info["peak_date"])
        start_str = (peak - pd.DateOffset(months=15)).strftime("%Y%m%d")
        end_str = (peak + pd.DateOffset(months=3)).strftime("%Y%m%d")

        print(f"\n{'─'*60}")
        print(f"📌 {name} | 峰值日期: {info['peak_date']} | {info['narrative']}")
        print(f"   指数: {info['index']} | {info['result']}")
        print(f"{'─'*60}")

        idx_df = fetch_index_history(info["index"], start_str, end_str)
        if idx_df is None or len(idx_df) < 30:
            print(f"   [!!] 指数数据不足")
            top_results[name] = {"error": "指数数据不足", "is_top": True}
            continue

        signals = detect_signals(peak, idx_df, margin_df, money_df, sz_df, name, is_top=True)
        top_results[name] = {"signals": signals, "is_top": True}

        for layer_name in ["L1_资金结构", "L3_宏观流动性", "L4_市场结构", "L5_情绪估值"]:
            layer = signals.get(layer_name, {})
            if not layer:
                continue
            for k, v in layer.items():
                if v.get("triggered") is None:
                    icon = "[?]"
                elif v["triggered"]:
                    icon = "[X]"
                else:
                    icon = "[O]"
                print(f"   {icon} [{layer_name[1:3]}] {k}: {v.get('value','?')}")

    # ============================================================
    # Part 2: 924 以来回调误报检验
    # ============================================================
    print("\n\n" + "█" * 70)
    print("█  Part 2: 924 以来回调误报率（指标应该安静）")
    print("█" * 70)

    correction_results = {}
    for name, info in CORRECTIONS.items():
        start_dt = pd.Timestamp(info["start"])
        end_dt = pd.Timestamp(info["end"])
        # 获取足够的历史数据
        fetch_start = (start_dt - pd.DateOffset(months=9)).strftime("%Y%m%d")
        fetch_end = (end_dt + pd.DateOffset(days=10)).strftime("%Y%m%d")

        print(f"\n{'─'*60}")
        print(f"📌 {name} | {info['start']}~{info['end']} | {info['narrative']}")
        print(f"   {info['result']}")
        print(f"{'─'*60}")

        idx_df = fetch_index_history(info["index"], fetch_start, fetch_end)
        if idx_df is None or len(idx_df) < 30:
            print(f"   [!!] 指数数据不足")
            correction_results[name] = {"error": "指数数据不足", "is_top": False}
            continue

        # 用回调起点检测
        signals = detect_signals(start_dt, idx_df, margin_df, money_df, sz_df, name, is_top=False)
        correction_results[name] = {"signals": signals, "is_top": False}

        for layer_name in ["L1_资金结构", "L3_宏观流动性", "L4_市场结构", "L5_情绪估值"]:
            layer = signals.get(layer_name, {})
            if not layer:
                continue
            for k, v in layer.items():
                if v.get("triggered") is None:
                    icon = "[?]"
                elif v["triggered"]:
                    icon = "[X]"
                else:
                    icon = "[O]"
                print(f"   {icon} [{layer_name[1:3]}] {k}: {v.get('value','?')}")

    # ============================================================
    # Part 3: 对比分析
    # ============================================================
    print("\n\n" + "█" * 70)
    print("█  Part 3: 真顶 vs 回调 — 信号模式对比")
    print("█" * 70)

    # 汇总每个事件每层的信号数
    def aggregate(results_dict):
        agg = {}
        for name, r in results_dict.items():
            if "error" in r:
                continue
            counts = count_layer_signals(r["signals"])
            agg[name] = counts
        return agg

    top_agg = aggregate(top_results)
    corr_agg = aggregate(correction_results)

    # 计算平均每层触发率
    print("\n📊 各层平均触发率对比:")
    print(f"   {'层':<20} {'真顶(n='+str(len(top_agg))+')':<16} {'回调(n='+str(len(corr_agg))+')':<16} {'区分度'}")
    print(f"   {'─'*20} {'─'*16} {'─'*16} {'─'*10}")

    layers = ["L1_资金结构", "L3_宏观流动性", "L4_市场结构", "L5_情绪估值"]
    layer_labels = {"L1_资金结构":"L1 资金结构","L3_宏观流动性":"L3 宏观流动性","L4_市场结构":"L4 市场结构","L5_情绪估值":"L5 情绪估值"}

    for layer in layers:
        # 真顶触发率
        top_trig = 0
        top_total = 0
        for name, counts in top_agg.items():
            l = counts.get(layer, {"triggered": 0, "detectable": 0})
            top_trig += l["triggered"]
            top_total += l["detectable"]

        # 回调触发率
        corr_trig = 0
        corr_total = 0
        for name, counts in corr_agg.items():
            l = counts.get(layer, {"triggered": 0, "detectable": 0})
            corr_trig += l["triggered"]
            corr_total += l["detectable"]

        top_rate = f"{top_trig}/{top_total}" if top_total > 0 else "N/A"
        corr_rate = f"{corr_trig}/{corr_total}" if corr_total > 0 else "N/A"

        # 区分度 = 真顶触发率 - 回调触发率（越高越好）
        if top_total > 0 and corr_total > 0:
            top_pct = top_trig / top_total
            corr_pct = corr_trig / corr_total
            discrimination = top_pct - corr_pct
            if discrimination > 0.4:
                grade = "[OK] 优秀"
            elif discrimination > 0.2:
                grade = "[~] 尚可"
            elif discrimination > 0:
                grade = "[!] 一般"
            else:
                grade = "[!!] 无效"
        else:
            discrimination = None
            grade = "数据不足"

        disc_str = f"{discrimination:+.0%} {grade}" if discrimination is not None else grade
        print(f"   {layer_labels.get(layer,layer):<20} {top_rate:<16} {corr_rate:<16} {disc_str}")

    # ── 关键发现 ──
    print("\n\n" + "=" * 70)
    print("📋 关键发现")
    print("=" * 70)

    # 分别统计每个指标的区分度
    print("\n🔍 逐指标分析 — 哪些指标能区分真顶和回调:\n")

    all_indicators = {}
    for layer in layers:
        for name, r in top_agg.items():
            for ind_name, ind_val in top_results.get(name, {}).get("signals", {}).get(layer, {}).items():
                if ind_name not in all_indicators:
                    all_indicators[ind_name] = {"top_trig": 0, "top_det": 0, "corr_trig": 0, "corr_det": 0}
                if ind_val.get("triggered") is not None:
                    all_indicators[ind_name]["top_det"] += 1
                    if ind_val["triggered"]:
                        all_indicators[ind_name]["top_trig"] += 1

        for name, r in corr_agg.items():
            for ind_name, ind_val in correction_results.get(name, {}).get("signals", {}).get(layer, {}).items():
                if ind_name not in all_indicators:
                    all_indicators[ind_name] = {"top_trig": 0, "top_det": 0, "corr_trig": 0, "corr_det": 0}
                if ind_val.get("triggered") is not None:
                    all_indicators[ind_name]["corr_det"] += 1
                    if ind_val["triggered"]:
                        all_indicators[ind_name]["corr_trig"] += 1

    # 按区分度排序输出
    ranked = []
    for ind_name, stats in all_indicators.items():
        if stats["top_det"] < 2 and stats["corr_det"] < 2:
            continue
        tp = stats["top_trig"] / stats["top_det"] if stats["top_det"] > 0 else 0
        fp = stats["corr_trig"] / stats["corr_det"] if stats["corr_det"] > 0 else 0
        disc = tp - fp
        ranked.append((ind_name, disc, tp, fp, stats))

    ranked.sort(key=lambda x: x[1], reverse=True)

    for ind_name, disc, tp, fp, stats in ranked:
        disc_str = "[OK]" if disc > 0.5 else ("[~]" if disc > 0.2 else ("[!]" if disc > 0 else "[!!]"))
        print(f"   {disc_str} {ind_name}:")
        print(f"      真顶触发: {stats['top_trig']}/{stats['top_det']} ({tp:.0%}) | 回调触发: {stats['corr_trig']}/{stats['corr_det']} ({fp:.0%}) | 区分度: {disc:+.0%}")

    print(f"\n💡 结论:")
    print(f"   [OK] 区分度 > 0.5 = 能有效区分真顶和回调，适合作为核心指标")
    print(f"   [~] 区分度 0.2~0.5 = 有一定区分能力，需结合其他指标使用")
    print(f"   [!] 区分度 < 0.2 = 区分能力不足,容易误报")
    print(f"   [!!] 区分度 ≤ 0 = 无用指标，真顶反而不如回调触发多")
    print()


if __name__ == "__main__":
    run_backtest()
