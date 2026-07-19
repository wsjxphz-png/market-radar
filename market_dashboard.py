#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股市场全景仪表盘 — 基于《趋势交易论》(710页) + 2198条最新推文
================================================================
数据源: 《趋势交易论》(710页) + 2198条推文

核心框架:
  三周期共振 — 大盘定仓位 + 板块定方向 + 个股定买点
  多空循环   — 多头(涨-调-涨) vs 空头(跌-弹-跌)
  情绪周期   — 冰点→启动→加速→高潮→分化→冰点
  520战法    — MA5/MA20金叉买入，死叉离场
  量价八诀   — 放量突破有效、缩量回踩正常、放量滞涨危险、缩量下跌弱势

输出:
  📊 大盘总览 — 周期阶段 + 情绪温度 + 三周期共振信号
  📋 板块诊断 — 23个行业逐项评分
  🎯 操作策略 — 仓位建议 + 买卖信号 + 关键观察点

用法:
  python market_dashboard.py           # 正常模式
  python market_dashboard.py --dry-run # 仅打印
"""

import os, sys, json, argparse
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

import requests
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════
# 配置 — 所有阈值来自《趋势交易论》
# ═══════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID") or "oc_94e85ee81df40d0ac71c358861427b06"

# ── 均线系统 (520战法核心) ──
MA_SHORT, MA_LONG = 5, 20       # 520战法
MA_MID = 60                      # 中期趋势线
MA_YEAR = 250                    # 年线 = 牛熊分界线

# ── RSI (来自书第146-148节) ──
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_EXTREME_OVERBOUGHT = 80
RSI_OVERSOLD = 30
RSI_EXTREME_OVERSOLD = 20
RSI_BULLISH = 50

# ── 乖离率 BIAS (来自书第212-213节) ──
BIAS6_BUY = -0.06       # 个股短线超跌 (< -6%)
BIAS6_SELL = 0.06       # 个股短线止盈 (> +6%)
BIAS_IDX_BUY = -0.04    # 大盘超跌 (< -4%)
BIAS_IDX_SELL = 0.04    # 大盘止盈 (> +4%)

# ── 量价关系 (来自书第127-139节) ──
VOL_BREAKOUT = 1.5      # 放量突破 (量 > 均量1.5倍)
VOL_SHRINK = 0.7        # 缩量回踩 (量 < 均量70%)
VOL_STAGNANT = 2.0      # 放量滞涨 (量 > 均量2倍但不涨)
VOL_DIVERGENCE_DAYS = 3  # 持续量价背离天数

# ── 偏离度 ──
DEV_MA60_EXTREME = 0.20  # 拉直角 (偏离60日线20%+)
DEV_MA60_ELEVATED = 0.12 # 偏高 (12%+)

# ── 仓位管理 (来自书第265-269节) ──
POSITION_STRONG_TREND = "70-80%"     # 强趋势
POSITION_UNCLEAR = "20-40%"          # 趋势模糊
POSITION_BROKEN = "0-20%"            # 趋势破坏
POSITION_OSCILLATION = "30-50%"      # 震荡

# ── M1宏观锚 ──
M1_FALL_WARN = -0.5
M1_FALL_SEVERE = -1.5

# ═══════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════

def fetch_index(name: str, code: str, days: int = 300) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=code)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(days).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  [!] {name}: {e}")
        return None

def fetch_m1() -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        df = df.rename(columns={df.columns[0]: "month"})
        cols = [c for c in df.columns if "m1" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        result = df[["month"] + cols[:1]].copy()
        result.columns = ["month", "m1"]
        result["month"] = pd.to_datetime(
            result["month"].astype(str).str.strip().str.replace("年","-").str.replace("月份",""), errors="coerce")
        result = result.dropna(subset=["month"]).sort_values("month")
        result["m1"] = pd.to_numeric(result["m1"], errors="coerce")
        return result
    except Exception as e:
        print(f"  [!] M1: {e}")
        return None

# ═══════════════════════════════════════════════════════════
# 技术指标 — 全量《趋势交易论》阈值
# ═══════════════════════════════════════════════════════════

def _ma(s, p): return s.rolling(p).mean()
def _ema(s, p): return s.ewm(span=p, adjust=False).mean()

def _resample_w(df):
    if df is None or len(df) < 10: return None
    d = df.copy(); d["date"] = pd.to_datetime(d["date"]); d = d.set_index("date")
    return d.resample("W").agg({"open":"first","close":"last","high":"max","low":"min","volume":"sum"}).dropna()

def _resample_m(df):
    if df is None or len(df) < 30: return None
    d = df.copy(); d["date"] = pd.to_datetime(d["date"]); d = d.set_index("date")
    return d.resample("ME").agg({"open":"first","close":"last","high":"max","low":"min","volume":"sum"}).dropna()


@dataclass
class Signal:
    """一个监测信号 — 理论+当前+含义 三位一体"""
    name: str; value: str; status: str  # healthy/caution/danger (必填)
    meaning: str = ""  # 理论×当前：综合含义 (旧接口的第四个参数)
    rule: str = ""     # 原文引用 (旧接口的第五个参数)
    theory: str = ""   # 趋势交易论里这个指标怎么说的 (新字段，keyword-only)
    current: str = ""  # 当前数据是什么 (新字段，keyword-only)

def compute_signals(df: pd.DataFrame, m1_df: Optional[pd.DataFrame]) -> List[Signal]:
    close = df["close"].values; high = df["high"].values; low = df["low"].values
    volume = df["volume"].values; opens = df["open"].values
    last = close[-1]; last_vol = volume[-1]
    chg = (last / close[-2] - 1) if len(close) >= 2 else 0
    signals = []

    # ═══ 1. 年线位置 (250MA) — 书第18节：只做年线上方个股 ═══
    ma250 = _ma(pd.Series(close), MA_YEAR).iloc[-1]
    if pd.notna(ma250) and ma250 > 0:
        dev250 = (last - ma250) / ma250
        if dev250 > 0.05:
            s = Signal("年线位置", f"年线上方 {dev250:+.1%}", "healthy",
                "指数站稳年线——这是牛市的必要条件。核心规则：日线只做所有均线都在250日线之上的个股。",
                "「日线只做所有均线都在250日线之上的个股，且多头顺次排列」—《趋势交易论》第18节")
        elif dev250 > 0:
            s = Signal("年线位置", f"年线上方 {dev250:+.1%}", "healthy",
                "指数在年线上方但乖离不大，仍在牛市区域内运行。",
                "")
        elif dev250 > -0.05:
            s = Signal("年线位置", f"年线附近 {dev250:+.1%}", "caution",
                "指数在年线附近拉锯——方向不明。这种时候应该'宁可休息'，减少操作。",
                "「趋势不明，宁可休息」—《趋势交易论》口诀第6条")
        else:
            s = Signal("年线位置", f"年线下方 {dev250:+.1%}", "danger",
                "指数在年线下方运行，长期趋势偏弱。年线之下重防守，不要重仓。",
                "「躺在60日线以下的票不要买」—《趋势交易论》口诀第17条")
        signals.append(s)

    # ═══ 2. 520战法 (MA5/MA20) — 书第128-129节 ═══
    ma5_now = _ma(pd.Series(close), 5).iloc[-1]
    ma20_now = _ma(pd.Series(close), 20).iloc[-1]
    ma5_prev = _ma(pd.Series(close), 5).iloc[-6]
    ma20_prev = _ma(pd.Series(close), 20).iloc[-11]
    golden_520 = ma5_now > ma20_now and ma5_prev <= ma20_prev
    dead_520 = ma5_now < ma20_now and ma5_prev >= ma20_prev

    if golden_520:
        s = Signal("520战法", "MA5金叉MA20 ✅", "healthy",
            "520金叉——短期趋势转多。520战法核心信号：金叉买入，持股待涨。",
            "「MA5金叉MA20+放量=买入」—《趋势交易论》520战法")
    elif dead_520:
        s = Signal("520战法", "MA5死叉MA20 ⚠", "danger",
            "520死叉——短期趋势转空。按照520战法，这是离场信号。",
            "「5日线下穿20日线死叉离场」—《趋势交易论》520战法")
    elif last > ma5_now:
        s = Signal("520战法", "多头运行中", "healthy",
            "价格在5日线上方，短期趋势健康。收盘不破5日线就继续持有。",
            "「收盘跌破5日线减仓，20日线加回来」—《趋势交易论》第18节")
    else:
        s = Signal("520战法", "偏弱运行", "caution",
            "价格在5日线下方，短期偏弱。关注是否能在20日线获得支撑。",
            "「站5日线以上的票不轻易卖」—口诀第17条")
    signals.append(s)

    # ═══ 3. MACD + 背离 — 书第140-145节 ═══
    dif = _ema(pd.Series(close), 12).values - _ema(pd.Series(close), 26).values
    dea = _ema(pd.Series(dif), 9).values
    hist = 2 * (dif - dea)
    macd_golden = dif[-1] > dea[-1] and dif[-2] <= dea[-2] if len(dif) >= 2 else False
    macd_dead = dif[-1] < dea[-1] and dif[-2] >= dea[-2] if len(dif) >= 2 else False

    # 背离检测
    diverge = ""
    if len(close) >= 40:
        if np.max(close[-20:]) >= np.max(close[-40:]) and np.max(dif[-20:]) < np.max(dif[-40:]) * 0.9:
            diverge = " ⚠顶背离"
        if np.min(close[-20:]) <= np.min(close[-40:]) and np.min(dif[-20:]) > np.min(dif[-40:]) * 1.1:
            diverge = " ✅底背离"

    # 零轴位置
    dif_now = dif[-1]; dea_now = dea[-1]
    above_zero = dif_now > 0

    if macd_golden:
        zero_note = "零轴上金叉=强势启动" if above_zero else "零轴下金叉=弱势反弹"
        s = Signal("MACD动能", f"金叉{zero_note}{diverge}", "healthy",
            f"MACD刚金叉。{zero_note}——{'这是最可靠的买入信号之一' if above_zero else '需要配合放量确认'}。",
            "「零轴上死叉：牛中歇一歇；零轴下死叉：熊再咬一口」—口诀第14条")
    elif macd_dead:
        zero_note = "零轴上死叉=正常调整" if above_zero else "零轴下死叉=加速下跌"
        s = Signal("MACD动能", f"死叉{zero_note}{diverge}", "danger" if not above_zero else "caution",
            f"MACD刚死叉。{zero_note}。{'这只是牛市中的正常调整，不必恐慌' if above_zero else '这是空头加速信号，要格外警惕'}。",
            "")
    elif dif_now > dea_now:
        s = Signal("MACD动能", f"多头运行{diverge}", "healthy",
            "MACD在多头区域运行，上涨动能还在。关注是否出现顶背离。",
            "「MACD顶背离是机构出货后的慢杀信号」—《趋势交易论》筑顶特征")
    else:
        s = Signal("MACD动能", f"空头运行{diverge}", "caution",
            "MACD在空头区域运行。等待底背离或金叉信号出现。",
            "")
    signals.append(s)

    # ═══ 4. RSI — 书第146-148节 ═══
    delta = pd.Series(close).diff()
    g = delta.where(delta > 0, 0.0); l = (-delta).where(delta < 0, 0.0)
    rsi = (100 - 100 / (1 + g.rolling(14).mean() / l.rolling(14).mean())).iloc[-1]
    if rsi > RSI_EXTREME_OVERBOUGHT:
        s = Signal("RSI", f"RSI={rsi:.0f} 极端超买", "danger",
            "RSI>80=极端超买。高潮=风险，应该减仓。这时候不是追高的时机。",
            "「情绪高潮之后不能恋战，一炸板就要高度警惕」—情绪周期第4阶段")
    elif rsi > RSI_OVERBOUGHT:
        s = Signal("RSI", f"RSI={rsi:.0f} 超买", "caution",
            "RSI>70=短期偏热。不需要马上卖，但不要在这个位置加仓。等回踩。",
            "「RSI>70进入超买区，需警惕短期回调」—《趋势交易论》第147节")
    elif rsi < RSI_EXTREME_OVERSOLD:
        s = Signal("RSI", f"RSI={rsi:.0f} 极端超卖", "caution",
            "RSI<20=极端恐慌。冰点是希望的开始。但不急于抄底——等底部确认信号（底分型+金叉+放量）。",
            "「冰点是赚大钱的埋伏期，留意新题材和换手首板」—情绪周期第6阶段")
    elif rsi < RSI_OVERSOLD:
        s = Signal("RSI", f"RSI={rsi:.0f} 超卖", "caution",
            "RSI<30=市场偏恐慌。好股票可能被错杀，但需要等止跌企稳信号。",
            "「跌出来的机会，涨出来的风险」—口诀第3条")
    else:
        s = Signal("RSI", f"RSI={rsi:.0f} 正常", "healthy",
            "RSI在40-70正常区间，市场情绪不极端。",
            "")
    signals.append(s)

    # ═══ 5. BIAS乖离率 — 书第212-213节 ═══
    bias6 = (last - _ma(pd.Series(close), 6).iloc[-1]) / _ma(pd.Series(close), 6).iloc[-1]
    if bias6 < BIAS6_BUY:
        s = Signal("乖离率", f"BIAS6={bias6:+.1%} 超跌", "caution",
            "短线超跌（大盘<-4%即超跌）。超跌后常有技术性反弹，但需要确认信号，不建议裸抄底。",
            "「BIAS6<-4%大盘超跌买入，BIAS6>+4%大盘止盈」—《趋势交易论》第212节")
    elif bias6 > BIAS6_SELL:
        s = Signal("乖离率", f"BIAS6={bias6:+.1%} 超涨", "caution",
            "短线涨幅过大（大盘>+4%），短期有回调压力。'大涨之后必有回调'。",
            "「大涨之后必有回调，大跌之后必有反弹」—口诀第5条")
    else:
        s = Signal("乖离率", f"BIAS6={bias6:+.1%} 正常", "healthy",
            "短线乖离在正常范围内，价格运行节奏健康。",
            "")
    signals.append(s)

    # ═══ 6. 多级别趋势 (月/周/日) — 书第77节：三周期选股 ═══
    mtf_parts = []
    # 日线
    if last > ma5_now and ma5_now > ma5_prev:
        daily = "↑"; mtf_parts.append("日↑")
    elif last < ma5_now:
        daily = "↓"; mtf_parts.append("日↓")
    else:
        daily = "→"; mtf_parts.append("日→")

    # 周线
    wdf = _resample_w(df); weekly = "unk"
    if wdf is not None and len(wdf) >= 8:
        wc = wdf["close"].values; wma4 = _ma(pd.Series(wc), 4).values
        if len(wma4) >= 3 and not np.isnan(wma4[-1]):
            if wc[-1] > wma4[-1] > wma4[-2]:
                weekly = "bull"; mtf_parts.append("周↑")
            elif wc[-1] < wma4[-1]:
                weekly = "bear"; mtf_parts.append("周↓")
            else:
                weekly = "neut"; mtf_parts.append("周→")

    # 月线
    mdf = _resample_m(df); monthly = "unk"
    if mdf is not None and len(mdf) >= 6:
        mc = mdf["close"].values; mma5 = _ma(pd.Series(mc), 5).values
        if len(mma5) >= 3 and not np.isnan(mma5[-1]):
            if mc[-1] > mma5[-1] > mma5[-2]:
                monthly = "bull"; mtf_parts.append("月↑")
            elif mc[-1] < mma5[-1]:
                monthly = "bear"; mtf_parts.append("月↓")
            else:
                monthly = "neut"; mtf_parts.append("月→")

    mtf_val = " ".join(mtf_parts)

    # 三周期共振判断 (大盘版)
    if monthly == "bull" and weekly == "bull" and daily == "↑":
        s = Signal("三周期趋势", f"{mtf_val} — 共振向上 ✅", "healthy",
            "月线、周线、日线三级别全部向上——这是最强的做多信号！三周期选股法：大周期定方向、中周期定结构、小周期抓节奏。三个级别共振=可以重仓。",
            "「大周期定方向，中周期选结构，小周期抓节奏。三者共振出黄金点」—《趋势交易论》三周期选股")
    elif monthly == "bear" and weekly in ("bear","neut") and daily == "↓":
        s = Signal("三周期趋势", f"{mtf_val} — 共振向下 ⚠", "danger",
            "月线、周线、日线三级别全部向下——最危险的信号。这种时候应该果断放弃，空仓看戏。",
            "「大级别下跌趋势里不要抄底博弈反弹——一套一个不吱声」—推文")
    elif monthly == "bull" and daily == "↓":
        s = Signal("三周期趋势", f"{mtf_val} — 大级别向上，小级别调整", "caution",
            "月线/周线还在上升结构中，但日线在调整。这是'牛市中的正常回踩'，不是趋势反转。等日线企稳后可以加仓。",
            "「回踩不破是机会不是风险。好股票每次回踩关键均线都是加仓点」—推文")
    elif monthly == "bear" and daily == "↑":
        s = Signal("三周期趋势", f"{mtf_val} — 大级别向下，小级别反弹", "caution",
            "月线还在下降趋势中，日线只是反弹——空头市场中的反弹往往是卖出机会，不是买入机会。",
            "「空头市场中，反弹往往是卖出或做空的机会」—《趋势交易论》多空循环")
    else:
        s = Signal("三周期趋势", f"{mtf_val} — 级别不统一", "caution",
            "不同时间级别方向不一致，市场在纠结。趋势不明时，宁可休息——降低仓位，等方向明确。",
            "「趋势不明，宁可休息」—口诀第6条")
    signals.append(s)

    # ═══ 7. 量价结构 — 书第127-139节 ═══
    vol_ma60 = _ma(pd.Series(volume), 60).iloc[-1]
    vr = last_vol / vol_ma60 if pd.notna(vol_ma60) and vol_ma60 > 0 else 1

    # 持续量价背离
    persistent_div = False
    if len(close) >= 4 and len(volume) >= 4:
        dc = sum(1 for i in range(-1,-4,-1) if close[i] > close[i-1] and volume[i] < volume[i-1])
        persistent_div = dc >= VOL_DIVERGENCE_DAYS

    if vr > VOL_STAGNANT and abs(chg) < 0.003:
        s = Signal("量价结构", f"放量滞涨 (vol={vr:.1f}x) ⚠", "danger",
            "放巨量但价格基本不动——这是最危险的信号——往往是主力在高位出货。历史上多次大顶前都出现过放量滞涨。",
            "「高位放量滞涨，坚决出局观望」—口诀第4条 / 《趋势交易论》筑顶特征")
    elif vr > VOL_BREAKOUT and chg > 0.005:
        s = Signal("量价结构", f"放量上涨 (vol={vr:.1f}x)", "healthy",
            "放量上涨——最健康的量价组合。资金在主动买入，上涨有持续性。'短线看量，长线看势'。",
            "「放量突破有效」—量价诊断规则")
    elif vr > VOL_BREAKOUT and chg < -0.005:
        s = Signal("量价结构", f"放量下跌 (vol={vr:.1f}x) ⚠", "danger",
            "放量下跌——资金在出逃。要区分是'震仓'还是'出货'：震仓后有承接，出货后持续走低。",
            "「震仓：打压后有承接，股价能很快企稳。出货：放量下跌后无人承接」—《趋势交易论》震仓vs出货")
    elif vr < VOL_SHRINK and chg > 0.005:
        qual = " ⚠持续背离!" if persistent_div else " ⚠" if vr < 0.6 else ""
        s = Signal("量价结构", f"缩量上涨 (vol={vr:.1f}x){qual}", "caution",
            "缩量上涨=买盘不够强。偶尔一两天没问题，但如果连续3天以上=量价背离，警惕见顶信号。" +
            (" 当前已持续背离≥3天！" if persistent_div else ""),
            "「缩量上涨+高乖离，量价背离风险」—推文")
    elif vr < VOL_SHRINK and chg < -0.005:
        s = Signal("量价结构", f"缩量下跌 (vol={vr:.1f}x)", "caution",
            "缩量下跌说明抛压不大——这是好事，说明不是恐慌性出逃。但不代表会立刻涨，需要等放量企稳信号。",
            "「下跌过程中成交量持续萎缩=抛压耗尽」—底部判断方法")
    else:
        s = Signal("量价结构", f"量价正常 (vol={vr:.1f}x)", "healthy",
            "量价配合正常，没有异常信号。",
            "")
    signals.append(s)

    # ═══ 8. 波动率 ═══
    ret = pd.Series(close).pct_change()
    v20, v60_v = ret.rolling(20).std().iloc[-1], ret.rolling(60).std().iloc[-1]
    vvr = v20 / v60_v if pd.notna(v60_v) and v60_v > 0 else 1
    if vvr > 1.8:
        s = Signal("波动率", f"剧烈放大 {vvr:.1f}x", "danger",
            "波动率急剧放大=市场情绪失控。波动率飙升往往出现在下跌初期或顶部区域。降低仓位。",
            "")
    elif vvr > 1.3:
        s = Signal("波动率", f"在放大 {vvr:.1f}x", "caution",
            "波动率在上升=不确定性增加。关注是什么原因导致的波动加大。",
            "")
    else:
        s = Signal("波动率", f"稳定 {vvr:.1f}x", "healthy",
            "波动率稳定=市场情绪平稳。低波环境有利于趋势延续。",
            "")
    signals.append(s)

    # ═══ 9. 支撑/压力位 ═══
    support = float(np.min(low[-20:])); resist = float(np.max(high[-20:]))
    at_sup = last <= support * 1.03; at_res = last >= resist * 0.97
    if at_sup:
        s = Signal("支撑压力", f"近20日支撑 {support:.0f} (距{last/support-1:+.1%})", "healthy",
            "价格接近20日支撑位——'跌出来的机会'。如果在这里缩量企稳，是低吸的好位置。",
            "「跌出来的机会，涨出来的风险」—口诀第3条")
    elif at_res:
        s = Signal("支撑压力", f"近20日压力 {resist:.0f} (距{last/resist-1:+.1%})", "caution",
            "价格接近20日压力位——突破需要放量配合。如果没有放量突破，可能回踩。",
            "「打到压力位但量能不足，可能回踩，等突破确认」—推文")
    else:
        s = Signal("支撑压力", f"支撑{support:.0f} / 压力{resist:.0f}", "healthy",
            "价格在支撑和压力之间运行，有操作空间。",
            "")
    signals.append(s)

    # ═══ 10. M1宏观锚 ═══
    if m1_df is not None and len(m1_df) >= 6:
        r = m1_df.tail(6); mc = r["m1"].tail(3).mean() - r["m1"].head(3).mean()
        mn = r["m1"].iloc[-1]
        if mc < M1_FALL_SEVERE:
            s = Signal("M1宏观锚", f"快速恶化 {mc:+.1f}pp (当前{mn:.1f}%)", "danger",
                "M1在快速收缩——流动性退潮。2007、2015、2021年大顶前M1都出现过类似恶化。如果叠加价格高位=宏观-价格背离，必须大幅降仓。",
                "")
        elif mc < M1_FALL_WARN:
            s = Signal("M1宏观锚", f"边际走弱 {mc:+.1f}pp (当前{mn:.1f}%)", "caution",
                "M1开始边际走弱。关注下月数据是否继续恶化——如果持续恶化对股市是压力。",
                "")
        elif mc > 0.5:
            s = Signal("M1宏观锚", f"改善中 {mc:+.1f}pp (当前{mn:.1f}%)", "healthy",
                "M1在改善，钱变多了——这是股市上涨的燃料。",
                "")
        else:
            s = Signal("M1宏观锚", f"稳定 {mc:+.1f}pp (当前{mn:.1f}%)", "healthy",
                "M1保持稳定，流动性环境没有明显变化。",
                "")
    else:
        s = Signal("M1宏观锚", "数据不可用", "caution",
            "M1数据缺失，无法判断宏观流动性状态。",
            "")
    signals.append(s)

    return signals


# ═══════════════════════════════════════════════════════════
# 情绪周期判断 (6阶段) — 书第101-103节
# ═══════════════════════════════════════════════════════════

SENTIMENT_CYCLE = {
    "ice": {
        "name": "冰点期", "emoji": "❄️", "position": "20-30%",
        "action": "轻仓观察，不急于抄底。留意新题材的萌芽——'冰点是希望的开始'。",
        "watch": ["下一个情绪周期的首板龙头", "是否有新题材异动", "成交量是否见底回升"],
        "quote": "冰点不动——等确认信号，不要自作聪明去抄底。"
    },
    "startup": {
        "name": "启动期", "emoji": "🌱", "position": "30-50%",
        "action": "识别题材和潜在龙头，小仓位试水。敢打首板是吃肉的第一步。",
        "watch": ["首板股票的数量和质量", "成交量是否放大", "板块效应是否形成"],
        "quote": "启动试水——确认趋势再加大仓位。"
    },
    "acceleration": {
        "name": "加速期", "emoji": "🔥", "position": "70-80%",
        "action": "果断上车核心龙头或前排强势股。这是最赚钱的阶段——但仓位不要打满，留余地。",
        "watch": ["龙头股是否健康换手", "板块内高低切是否活跃", "赚钱效应是否扩散"],
        "quote": "加速重仓——但要控制仓位，防止过热。牛市中最大的风险是没上车，第二大的风险是满仓。"
    },
    "climax": {
        "name": "高潮期", "emoji": "🎢", "position": "30-50%",
        "action": "快进快出，抓最后一波。高潮=风险，最难也最重要的是'见好就收'。开始分批减仓。",
        "watch": ["炸板率是否上升", "高开低走是否增多", "散户是否蜂拥入场"],
        "quote": "高潮减仓——不要恋战。'情绪高潮之后不能恋战，一炸板就要高度警惕'。"
    },
    "divergence": {
        "name": "分化期", "emoji": "🔻", "position": "0-20%",
        "action": "坚决离场，空仓等待。不割就是被割——别做接盘侠。等冰点再考虑入场。",
        "watch": ["龙头是否见顶杀跌", "亏钱效应是否蔓延", "热点题材是否无人接力"],
        "quote": "退潮空仓——'不割就是被割'，保住本金是第一位的。"
    },
    "downtrend": {
        "name": "下跌趋势", "emoji": "🔴", "position": "0-20%",
        "action": "空仓或轻仓。不要抄底！大级别下跌趋势里——果断放弃，空仓看戏。",
        "watch": ["是否跌到长期支撑区", "量能是否持续萎缩", "MACD是否底背离", "是否有板块率先企稳"],
        "quote": "大级别下跌趋势里不要抄底博弈反弹——一套一个不吱声。"
    },
}


def assess_sentiment(signals: List[Signal]) -> Dict:
    """基于指标信号判断当前情绪周期阶段"""
    sig_map = {s.name: s for s in signals}
    dangers = sum(1 for s in signals if s.status == "danger")
    cautions = sum(1 for s in signals if s.status == "caution")

    mtf = sig_map.get("三周期趋势", Signal("","","","",""))
    vp = sig_map.get("量价结构", Signal("","","","",""))
    rsi = sig_map.get("RSI", Signal("","","","",""))
    macd = sig_map.get("MACD动能", Signal("","","","",""))
    m520 = sig_map.get("520战法", Signal("","","","",""))

    mtf_val = mtf.value; vp_val = vp.value

    # 共振向下 + 多个危险 = 下跌趋势
    if "共振向下" in mtf_val and dangers >= 2:
        return SENTIMENT_CYCLE["downtrend"]
    # 危险信号多 + 量价危险 = 分化期
    if dangers >= 2 and "danger" in vp.status:
        return SENTIMENT_CYCLE["divergence"]
    # RSI极端超买 + 量价背离 = 高潮期
    if "极端超买" in rsi.value and ("缩量上涨" in vp_val or "放量滞涨" in vp_val):
        return SENTIMENT_CYCLE["climax"]
    # 共振向上 + 健康量价 + 无危险 = 加速期
    if ("共振向上" in mtf_val or "大级别向上" in mtf_val) and vp.status == "healthy" and dangers == 0:
        if "超买" in rsi.value:
            return SENTIMENT_CYCLE["climax"]
        return SENTIMENT_CYCLE["acceleration"]
    # 金叉 + 量价健康 = 启动期
    if "金叉" in m520.value and vp.status == "healthy":
        return SENTIMENT_CYCLE["startup"]
    # RSI超卖 + MACD底背离 = 冰点期
    if ("超卖" in rsi.value or "极端超卖" in rsi.value) and "底背离" in macd.value:
        return SENTIMENT_CYCLE["ice"]
    # 默认：根据危险信号数量
    if dangers >= 2:
        return SENTIMENT_CYCLE["divergence"]
    if cautions >= 3:
        return SENTIMENT_CYCLE["downtrend"]
    return SENTIMENT_CYCLE["startup"]


# ═══════════════════════════════════════════════════════════
# 板块诊断增强 (利用 sector_monitor)
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 推文权威信号 — 每次运行实时加载，作为最高优先级覆盖一切静态规则
# ═══════════════════════════════════════════════════════════

# 板块关键词映射（覆盖全部23个板块）
SECTOR_KEYWORDS = {
    "半导体": ["半导体", "芯片", "硬科技", "高位科技", "科技抱团"],
    "互联网服务": ["互联网", "软件", "算力"],
    "电气设备": ["电气", "新能源", "光伏", "锂电", "储能"],
    "新能源": ["新能源"],
    "证券": ["证券", "券商", "大金融"],
    "保险": ["保险"],
    "仪器仪表": ["仪器仪表", "机器人", "自动化"],
    "通用机械": ["通用机械", "机器人", "机械"],
    "工业机械": ["工业机械", "机器人", "机械"],
    "农林牧渔": ["农林牧渔", "农业", "种业", "猪肉", "粮食"],
    "家电行业": ["家电", "格力", "美的", "白电"],
    "煤炭行业": ["煤炭", "煤", "焦煤"],
    "船舶制造": ["船舶", "造船", "船"],
    "食品饮料": ["食品饮料", "白酒", "食品", "消费.*底"],
    "日用化工": ["日化", "化工"],
    "医疗保健": ["医疗保健", "医疗", "器械"],
    "医药": ["医药", "创新药", "中药", "CRO", "药"],
    "酿酒行业": ["酿酒", "白酒", "茅台", "酒"],
    "有色金属": ["有色", "铜", "铝", "稀土", "黄金"],
    "文教休闲": ["文教", "教育", "传媒"],
    "旅游酒店": ["旅游", "酒店", "出行"],
    "电力行业": ["电力", "发电", "电网", "绿电"],
    "仓储物流": ["物流", "仓储", "快递"],
}

# 信号规则 — 有序匹配，先匹配到的生效
TWEET_SIGNAL_RULES = [
    # 最强烈的看空
    ("🔴 明确回避", [
        "破势", "放弃幻想", "不要抱有任何幻想", "果断放弃",
        "不要再碰", "坚决不碰", "清仓", "全部出掉"
    ]),
    # 反弹即走
    ("🔴 反弹就撤", [
        "反弹就先出来", "有反弹就出来", "反弹出", "逢反弹",
        "减仓.*反弹", "反弹.*减仓"
    ]),
    # 高位风险/不追
    ("🔴 高位风险不追", [
        "不要追", "不追涨", "高位.*风险", "泡沫",
        "抱团.*结束", "踩踏", "出货.*阶段", "主力.*出货"
    ]),
    # 等待反弹减仓
    ("🟡 等反弹减仓", [
        "等反弹", "回本", "减少亏损", "反弹.*机会",
        "不要恐慌割肉", "被套.*不必.*悲观"
    ]),
    # 等调整/等回踩
    ("🟡 等调整到位", [
        "等回踩", "等调整", "等右侧", "不急于",
        "横盘.*等", "耐心.*等"
    ]),
    # 积极信号
    ("🟢 可以关注", [
        "可以.*低吸", "可以.*布局", "逢低.*买", "可以.*关注",
        "机会.*来了", "底部.*确认", "见底"
    ]),
]


def load_tweet_alerts(json_path: str) -> dict:
    """
    每次运行仪表盘时实时调用。
    从最新推文提取每个板块的权威判断。
    返回 {板块名: {"rating": str, "quote": str, "date": str, "bookmarks": int}}
    """
    alerts = {}
    try:
        import json
        data = json.load(open(json_path, encoding='utf-8'))
    except Exception:
        return alerts

    from datetime import datetime, timedelta
    # 最近4天（覆盖周末空窗期）
    cutoff = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
    recent = [t for t in data if t.get('created_at_iso', '')[:10] >= cutoff]
    if not recent:
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = [t for t in data if t.get('created_at_iso', '')[:10] >= cutoff]

    for board_name, keywords in SECTOR_KEYWORDS.items():
        best = None  # 找最强烈的信号（bookmarks最高的那条）
        for t in recent:
            text = t['text']
            if not any(kw in text for kw in keywords):
                continue
            import re
            for rating, signals in TWEET_SIGNAL_RULES:
                for sig in signals:
                    if re.search(sig, text):
                        bk = t.get('bookmarks', 0)
                        if best is None or bk > best['bookmarks']:
                            best = {
                                "rating": rating,
                                "quote": text[:150].replace('\n', ' '),
                                "date": t.get('created_at_iso', '')[:10],
                                "bookmarks": bk,
                            }
                        break
                if best: break
            if best: break
        if best:
            alerts[board_name] = best

    return alerts


def diagnose_sector_mi(s: Dict, tweet_alerts: dict) -> Dict:
    """用《趋势交易论》框架诊断单个板块。tweet_alerts 为最高优先级权威信号。"""
    tech = s.get("technical", {})
    sig = s.get("signal", {})
    fund = s.get("fund_flow", {})

    phase = tech.get("trend_phase", "mixed")
    phase_cn = {"rally":"单边上涨","oscillation":"中枢震荡","topping":"筑顶",
                "bottoming":"筑底","downtrend":"下降","mixed":"震荡"}.get(phase, phase)

    tags = []
    if tech.get("any_golden_cross"): tags.append("金叉")
    if tech.get("dead_cross_5_20"): tags.append("⚠死叉")
    if tech.get("persistent_divergence"): tags.append("⚠持续背离")
    if tech.get("long_upper_shadow"): tags.append("⚠长上影")
    if tech.get("bottom_fractal"): tags.append("底分型")
    if tech.get("top_fractal") and tech.get("vol_price") == "distribution": tags.append("⚠顶分+放量跌")
    if tech.get("is_doubled"): tags.append("⚠已翻倍")
    if tech.get("suppressed_by_ma"): tags.append("均线压制")
    if tech.get("above_ma20") and tech.get("above_ma60"): tags.append("站月线+季线")

    wt = tech.get("weekly_trend","")
    if wt == "bullish": tags.append("周线↑")
    elif wt == "bearish": tags.append("周线↓")

    inflow = fund.get("main_inflow_5d", 0)
    if inflow > 0: tags.append(f"流入{inflow/1e8:.0f}亿")
    elif inflow < 0: tags.append(f"流出{abs(inflow)/1e8:.0f}亿")

    bias = tech.get("bias_ma5_pct", 0)
    if abs(bias) > 8: tags.append(f"乖离{bias:+.0f}%")

    status = s.get("meeting_status", "watch")
    # ── 权威信号：推文最新表态覆盖一切 ──
    tweet_alert = tweet_alerts.get(s["name"])
    if tweet_alert:
        rating = tweet_alert["rating"]
        tags.insert(0, f"📡 {tweet_alert['date']}: {tweet_alert['quote'][:60]}...")
    # ── 静态规则作为兜底 ──
    elif status == "entry" and any(t.startswith("✅") or t in ("金叉","底分型","周线↑") for t in tags):
        rating = "🟢 可入场"
    elif status == "entry" and any(t.startswith("⚠") for t in tags):
        rating = "🟡 等回踩再入"
    elif status == "hold" and not any(t.startswith("⚠") for t in tags):
        rating = "🟢 持有不动"
    elif status == "hold":
        rating = "🟡 持有但警惕"
    elif status == "watch" and any(t in ("金叉","底分型","周线↑") for t in tags):
        rating = "⬆️ 可能升级"
    elif status == "avoid" and any(t in ("金叉","底分型","周线↑") for t in tags):
        rating = "⚠️ 异动关注"
    elif status == "avoid":
        rating = "🔴 继续回避"
    else:
        rating = "🟡 观察中"

    return {
        "name": s["name"], "category": s.get("category",""),
        "rating": rating, "phase": phase_cn,
        "tags": ", ".join(tags) if tags else ("周末/节假日无实时数据" if not tech else "指标正常"),
        "note": s.get("meeting_note",""),
    }


# ═══════════════════════════════════════════════════════════
# AI 解读
# ═══════════════════════════════════════════════════════════

def ai_interpret(cycle: Dict, signals: List[Signal], sectors: List[Dict],
                 idx_tail: pd.DataFrame) -> Optional[str]:
    if not DEEPSEEK_API_KEY: return None
    try:
        sig_text = "\n".join(f"- {s.name}: {s.value} [{s.status}]" for s in signals)
        sec_text = "\n".join(
            f"- {s['rating']} {s['name']}({s['category']}): {s['phase']} | {s['tags']}"
            for s in sorted(sectors, key=lambda x: x['rating'])
        )
        recent = idx_tail.tail(5)[["date","close","volume"]].to_string()

        prompt = f"""你是A股市场监测系统的AI分析师。你的分析框架来自《趋势交易论》（Mimiwftt著）和2198条推文数据。

当前情绪周期: {cycle['emoji']} {cycle['name']}
仓位建议: {cycle['position']}
核心操作: {cycle['action']}

大盘信号:
{sig_text}

板块概览:
{sec_text}

最近5日:
{recent}

用直接客观的风格说4-5句话：
- 第一句：现在市场处于什么阶段
- 第二句：最值得关注的1个风险或机会
- 第三句：具体该怎么做
- 最后：一句你的经典口诀

要求：直接、接地气、不模棱两可、不用分析师语言。就像在跟朋友聊天。"""

        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [
                {"role": "system", "content": "你是A股市场监测系统的AI分析师。分析风格：直接、接地气、不装腔作势。参考框架：买横买坑不买竖、趋势不明宁可休息、高位放量滞涨坚决出局。"},
                {"role": "user", "content": prompt}
            ], "temperature": 0.4, "max_tokens": 500},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[!] AI: {e}")
    return None


# ═══════════════════════════════════════════════════════════
# 格式化输出
# ═══════════════════════════════════════════════════════════

def format_dashboard(cycle: Dict, signals: List[Signal], sectors: List[Dict],
                     ai_text: Optional[str], idx: pd.DataFrame, sector_text: str = "",
                     tweet_alerts: dict = None, indices: dict = None) -> str:
    d = idx["date"].iloc[-1]
    date_str = d.strftime("%Y.%m.%d") if hasattr(d, 'strftime') else str(d)[:10]
    wd = ["一","二","三","四","五","六","日"][d.weekday()] if hasattr(d, 'weekday') else ""

    # 四大指数概览
    idx_lines = []
    for tag, df in indices.items():
        if df is not None and len(df) >= 2:
            c = df["close"].iloc[-1]; ch = (c/df["close"].iloc[-2]-1)
            idx_lines.append(f"{tag} {c:.0f} ({ch:+.2%})")
    idx_summary = "  |  ".join(idx_lines)

    icon = {"healthy":"🟢","caution":"🟡","danger":"🔴"}
    sig_map = {s.name: s for s in signals}

    lines = [
        f"**{date_str} 周{wd}**",
        f"{idx_summary}",
        f"**{cycle['emoji']} {cycle['name']}**  |  建议仓位 **{cycle['position']}**",
        "",
        "---",
        "",
        "## 📖 理论框架 × 当前数据",
        "",
    ]

    # ── 每个关键指标：理论怎么说 + 当前数据印证 ──
    key_indicators = ["年线位置", "三周期趋势", "MACD动能", "量价结构", "RSI", "520战法"]
    for name in key_indicators:
        s = sig_map.get(name)
        if not s: continue
        lines.append(f"**{icon[s.status]} {s.name}**: {s.value}")
        lines.append(f"> **理论**: {s.meaning}")
        if s.rule:
            lines.append(f"> 📖 {s.rule}")
        lines.append("")

    # ── 辅助指标简表 ──
    aux = [s for s in signals if s.name not in key_indicators]
    if aux:
        lines.append("**其他指标**:")
        for s in aux:
            lines.append(f"- {icon[s.status]} {s.name}: {s.value}")
        lines.append("")

    # ── 入场/出场信号与仓位（理论+表态综合） ──
    lines.append("---")
    lines.append("")
    lines.append("## 🎯 入场/出场信号与仓位")
    lines.append("")

    m520 = sig_map.get("520战法")
    macd_s = sig_map.get("MACD动能")
    rsi_s = sig_map.get("RSI")
    vp_s = sig_map.get("量价结构")
    sr_s = sig_map.get("支撑压力")

    # 综合判断入场/出场
    signals_bull = []
    signals_bear = []

    if m520:
        if "金叉" in m520.value: signals_bull.append("520金叉 → 可入场")
        elif "死叉" in m520.value: signals_bear.append("520死叉 → 应离场")
        elif "多头" in m520.value: signals_bull.append("520多头运行 → 可持有")
        else: signals_bear.append("520偏弱 → 不宜入场")

    if macd_s:
        if "金叉" in macd_s.value: signals_bull.append("MACD金叉 → 买入信号")
        elif "死叉" in macd_s.value and "零轴上" in macd_s.value:
            signals_bull.append("MACD零轴上死叉 → 正常调整，不必恐慌")
        elif "死叉" in macd_s.value: signals_bear.append("MACD死叉 → 卖出信号")
        if "底背离" in macd_s.value: signals_bull.append("MACD底背离 → 关注见底机会")
        if "顶背离" in macd_s.value: signals_bear.append("MACD顶背离 → 减仓预警")

    if rsi_s:
        rsi_val = rsi_s.value
        if "极端超买" in rsi_val: signals_bear.append("RSI极端超买 → 高潮减仓")
        elif "超买" in rsi_val: signals_bear.append("RSI超买 → 不宜追高")
        elif "超卖" in rsi_val or "极端超卖" in rsi_val:
            signals_bull.append("RSI超卖 → 关注企稳后的入场机会（不急抄底）")

    if sr_s and "近.*支撑" in sr_s.value:
        signals_bull.append("接近支撑位 → 若缩量企稳可低吸")

    # 理论仓位
    theory_pos = cycle.get("position", "N/A")

    # 她最近的板块级表态
    tweet_entry = []  # sectors she's bullish on
    tweet_exit = []   # sectors she's bearish on
    if tweet_alerts:
        for name, alert in tweet_alerts.items():
            if "可以关注" in alert["rating"] or "可以入场" in alert["rating"]:
                tweet_entry.append(name)
            elif "回避" in alert["rating"] or "反弹就撤" in alert["rating"] or "高位风险" in alert["rating"]:
                tweet_exit.append(name)

    lines.append(f"**理论仓位**: {theory_pos}")
    lines.append("")

    if signals_bull:
        lines.append("**入场信号**:")
        for s in signals_bull: lines.append(f"- ✅ {s}")
        lines.append("")
    if signals_bear:
        lines.append("**出场/谨慎信号**:")
        for s in signals_bear: lines.append(f"- ⚠ {s}")
        lines.append("")

    if tweet_entry:
        lines.append(f"**她看好的方向**: {', '.join(tweet_entry)}")
    if tweet_exit:
        lines.append(f"**她回避的方向**: {', '.join(tweet_exit)}")
    if tweet_entry or tweet_exit:
        lines.append("")

    # 520战法具体规则
    lines.append(f"**520战法纪律**:")
    lines.append(f"- 买：MA5金叉MA20 + 放量 → 入场")
    lines.append(f"- 减：收盘跌破MA5 → 减半仓")
    lines.append(f"- 加：回踩MA20不破 → 加回")
    lines.append(f"- 卖：MA5死叉MA20 → 全离场")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ── 她最近的明确表态（板块级别） ──
    tweet_sectors = [s for s in sectors if "📡" in s.get("tags", "")]
    other_sectors = [s for s in sectors if "📡" not in s.get("tags", "")]

    lines.append("## 📡 她最近的明确表态")
    lines.append("")

    if tweet_alerts:
        lines.append(f"*最近4天推文中对 {len(tweet_alerts)} 个板块有明确判断：*")
        lines.append("")
        for board_name, alert in tweet_alerts.items():
            lines.append(f"**{alert['rating']} — {board_name}**")
            lines.append(f"> {alert['date']}: *{alert['quote'][:150]}*")
            lines.append("")
    else:
        lines.append("*最近4天推文中未发现对具体板块的明确判断。以下为基于理论框架的板块诊断。*")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 板块全貌（理论框架诊断） ──
    if sectors:
        lines.append(f"## 📋 板块全貌（{len(sectors)}个 · 理论框架诊断）")
        lines.append("")
        for label, filters in [
            ("🟢 可入场/持有", ["🟢 可入场","🟢 持有不动"]),
            ("🟡 等回踩/观察", ["🟡 等回踩再入","🟡 持有但警惕","🟡 观察中","🟡 等反弹减仓","🟡 等调整到位","🟢 可以关注"]),
            ("⬆️ 可能升级", ["⬆️ 可能升级"]),
            ("⚠️ 异动关注", ["⚠️ 异动关注"]),
            ("🔴 回避", ["🔴 继续回避","🔴 明确回避","🔴 反弹就撤","🔴 高位风险不追"]),
        ]:
            matched = [s for s in sectors if s["rating"] in filters]
            if matched:
                lines.append(f"**{label} ({len(matched)}):**")
                for s in matched:
                    lines.append(f"- {s['rating']} **{s['name']}** ({s['category']}) | {s['phase']}")
                    if s.get('tags'): lines.append(f"  {s['tags']}")
                lines.append("")

    lines.append("---")
    lines.append("")

    # ── 综合策略 ──
    lines.append(f"## 🎯 综合策略")
    lines.append("")
    lines.append(f"**理论判断**: {cycle['name']} → 仓位{cycle['position']}")
    lines.append(f"**操作**: {cycle['action']}")
    lines.append("")
    lines.append("**关键观察点**:")
    for w in cycle.get("watch", []): lines.append(f"- {w}")
    lines.append("")
    lines.append(f"> 💬 *{cycle['quote']}*")
    lines.append("")

    if ai_text:
        lines.append("---")
        lines.append("")
        lines.append(f"**🤖 AI分析:**")
        lines.append(f"> {ai_text}")
        lines.append("")

    if sector_text:
        lines.append("---")
        lines.append("")
        lines.append("<details><summary>📋 原始板块数据（6/29会议规则，仅供参考）</summary>")
        lines.append("")
        lines.append(sector_text)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append("---")
    lines.append("*《趋势交易论》(710页) + 实时推文 | 每日自动*")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 飞书推送
# ═══════════════════════════════════════════════════════════

def send_feishu(content: str) -> bool:
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET: return False
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=15)
        if resp.status_code != 200: return False
        token = resp.json().get("tenant_access_token")
    except: return False

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "A股全景仪表盘"}},
        "elements": [{"tag": "markdown", "content": content}]
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            json={"receive_id": FEISHU_CHAT_ID, "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
        return resp.status_code == 200 and resp.json().get("code") == 0
    except: return False


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    print("=" * 50)
    print("  A股全景仪表盘")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    print("\n[1/4] 数据...")
    # 四大指数
    indices = {
        "上证": fetch_index("上证指数", "sh000001", days=300),
        "深证": fetch_index("深证成指", "sz399001", days=300),
        "创业板": fetch_index("创业板指", "sz399006", days=300),
        "科创50": fetch_index("科创50", "sh000688", days=300),
    }
    idx = indices["上证"]  # 主指数用于信号计算
    m1 = fetch_m1()
    if idx is None: print("[!!] 无数据"); return

    today = datetime.now().strftime("%Y-%m-%d")
    if not args.force and idx["date"].iloc[-1].strftime("%Y-%m-%d") != today:
        print(f"[i] 非交易日 (最新: {idx['date'].iloc[-1].strftime('%Y-%m-%d')})"); return

    print("[2/4] 计算信号...")
    signals = compute_signals(idx, m1)
    for s in signals: print(f"  {s.name}: {s.value} [{s.status}]")

    # 加载推文权威信号（每次运行实时读取，必须在诊断板块之前）
    tweet_alerts = load_tweet_alerts(r"C:\Users\Administrator\Mimiwftt_clean.json")
    print(f"  推文信号: {len(tweet_alerts)} 个板块有最新表态")
    for k, v in tweet_alerts.items():
        print(f"    {k}: {v['rating']} ({v['date']})")

    print("[3/4] 板块诊断...")
    sector_data = None; sector_text = ""; sectors_diag = []
    try:
        from sector_monitor import fetch_sector_monitor_data, format_sector_for_prompt
        sector_data = fetch_sector_monitor_data()
        sector_text = format_sector_for_prompt(sector_data)
        sectors_diag = [diagnose_sector_mi(s, tweet_alerts) for s in sector_data.get("sectors", [])]
        sm = sector_data.get("summary", {})
        print(f"  板块: {sm.get('entry_count',0)}入 {sm.get('hold_count',0)}持 "
              f"{sm.get('watch_count',0)}观 {sm.get('avoid_count',0)}避")
    except Exception as e:
        print(f"  [!] 板块: {e}")

    cycle = assess_sentiment(signals)
    print(f"\n[4/4] 情绪周期: {cycle['emoji']} {cycle['name']}")
    ai = ai_interpret(cycle, signals, sectors_diag, idx)
    msg = format_dashboard(cycle, signals, sectors_diag, ai, idx, sector_text, tweet_alerts, indices)

    print("\n" + msg)
    if args.dry_run: print("\n[i] Dry run"); return
    ok = send_feishu(msg)
    print(f"\n[>] 飞书: {'OK' if ok else 'FAIL'}")


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    main()
