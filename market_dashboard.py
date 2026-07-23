#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股市场全景仪表盘 — 基于《趋势交易论》(710页)
================================================================
数据源: 《趋势交易论》(710页)

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

from portfolio import Portfolio
from stock_data import StockData

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

# ═══════════════════════════════════════════════════════════
# 交易手册 — 《趋势交易论》五大模块速查
# 每条规则三段：📚 理论 → 📋 规则 → 🔍 验证
# ═══════════════════════════════════════════════════════════

TRADING_MANUAL = """
## 📖 交易手册 — 《趋势交易论》核心规则

---

### 1. 仓位管理 — 多少钱下注

📚 **理论**：三周期共振定仓位，趋势不明宁可休息（第265-269节）。月线方向决定你是否应该在场内，日线信号决定你具体什么时候动手。两者是主从关系，不能平起平坐。

📋 **规则**：
- 三周期共振向上 → 仓位 **7-8成**，可以重仓持有
- 级别不统一 → 仓位 **3-5成**，不做新买入，等方向明确
- 三周期共振向下 → 仓位 **0-2成**，空仓或极轻仓

🔍 **自检**：打开今日信号，看「三周期趋势」指标——三个级别方向一致吗？模拟账户仓位是否在规则范围内？

---

### 2. 选股 — 什么能买

📚 **理论**：「日线只做所有均线都在250日线之上的个股。买横买坑不买竖——横盘或回踩时入场，追涨必亏」（第128-130节）。核心逻辑：年线上方代表多数人赚钱、趋势向上；年线下方代表多数人被套、趋势向下。在向上的趋势里找机会，不要逆着大方向硬做。

📋 **规则**：
- 板块必须满足：年线上方 + 均线多头排列（价格>MA5>MA20>MA60）
- 排除：已翻倍（涨幅透支）、均线空头排列、日线在年线下方

🔍 **自检**：打开板块操作信号，被列为🟢的板块——是否都满足「站上月线+季线」？有没有年线下方的板块被误判为可买？

---

### 3. 入场 — 什么时候买

📚 **理论**：「金叉+放量是最可靠的入场信号。没有量的突破是假的」（第128-129节）。量的背后是钱——有人真金白银在买，价格才撑得住。缩量上涨是买家不够多，随时可能跌回来。

📋 **规则**：以下三个条件**缺一不可**——
- 520金叉（5日均线上穿20日均线）
- 放量（当日成交量 > 20日均量的1.2倍）
- 不追高（BIAS 乖离率 < 5%，价格没有远离均线）

🔍 **自检**：今天被列为🟢可买入的板块——三个条件全满足了吗？少一个就是在赌。

---

### 4. 止损 — 什么时候认错

📚 **理论**：「死叉后还持有的唯一理由是希望——而希望不是策略」（第128节）。止损不是承认你错了，是承认市场不按你设想的走。保住本金永远比证明自己正确更重要。

📋 **规则**：
- 收盘跌破5日均线 → **减仓一半**（趋势可能转弱）
- 5日线下穿20日线（死叉）→ **全部清仓**（趋势确认反转）
- 单笔亏损达到仓位5% → **无条件止损**（不管指标怎么说）

🔍 **自检**：模拟账户里有持仓吗？有没有已经跌破5日线但还没减仓的？有没有死叉了还在持有的？

---

### 5. 止盈 — 什么时候收手

📚 **理论**：「高位放量滞涨，坚决出局观望」（口诀第4条 + 筑顶特征）。庄家在高位出货的典型手法：成交量很大但价格不涨——说明买的人在减少、卖的人在增加。等你看到大跌，已经晚了。

📋 **规则**：
- 放量滞涨（量>2倍均量但涨幅<0.3%）→ **减仓一半**
- 高位顶分型 + 放量下跌 → **清仓**
- RSI > 80（极端超买）→ **分批减仓**，每次减1/3

🔍 **自检**：持仓中有没有放量滞涨的？有没有RSI>80还在继续持有的？有没有涨幅已翻倍还没动的？

---

### 📖 量价八诀速查（第127-139节）

量在价先——成交量的变化领先于价格的变化。以下八种量价组合是《趋势交易论》最核心的看盘技巧：

| # | 形态 | 量 | 价 | 含义 | 操作 |
|---|------|----|----|------|------|
| 1 | 放量上涨 | ↑↑ | ↑ | 资金主动买入，上涨有持续性 | 🟢 持仓/加仓 |
| 2 | 缩量下跌 | ↓ | ↓ | 抛压枯竭，卖的人越来越少 | 🟡 关注止跌信号 |
| 3 | 放量下跌 | ↑↑ | ↓ | 资金出逃，恐慌或主力出货 | 🔴 减仓/清仓 |
| 4 | 缩量上涨 | ↓ | ↑ | 买盘不足，上涨不可持续 | 🟡 警惕见顶，不加仓 |
| 5 | 放量滞涨 | ↑↑ | → | 主力高位出货，最危险信号 | 🔴 立即减仓 |
| 6 | 缩量止跌 | ↓ | → | 卖压耗尽，可能见底 | 🟡 等放量确认后入场 |
| 7 | 量平价升 | → | ↑ | 温和上涨，趋势健康 | 🟢 继续持有 |
| 8 | 量平价跌 | → | ↓ | 阴跌，没恐慌但也没人买 | 🟡 观望为主 |

> 💡 **这五条规则+八种量价，每次买卖前过一遍。对就是对，错就是错。不管市场怎么走，规则不变。**
"""


def fetch_index(name: str, code: str, days: int = 300) -> Optional[pd.DataFrame]:
    # 主数据源: akshare
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=code)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(days).reset_index(drop=True)
        if len(df) >= 10:
            return df
    except Exception as e:
        print(f"  [!] akshare {name}: {e}")

    # 备用数据源: yfinance (GitHub Actions 从美国访问更稳定)
    yf_map = {
        "sh000001": "000001.SS", "sz399001": "399001.SZ",
        "sz399006": "399006.SZ", "sh000688": "000688.SS",
    }
    yf_ticker = yf_map.get(code)
    if yf_ticker:
        try:
            import yfinance as yf
            df = yf.download(yf_ticker, period=f"{days+30}d", progress=False, auto_adjust=True)
            if df is not None and len(df) >= 10:
                df = df.reset_index()
                df = df.rename(columns={
                    "Date": "date", "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume"
                })
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").tail(days).reset_index(drop=True)
                print(f"  [i] {name}: 使用 yfinance 备用数据源")
                return df
        except Exception as e2:
            print(f"  [!] yfinance {name}: {e2}")

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
                "250日均线（年线）是《趋势交易论》的牛熊分界线。书里第18节明确写了核心规则：「日线只做所有均线都在250日线之上的个股，且多头顺次排列」。为什么？因为股价在年线上方，意味着大多数人都在赚钱——赚钱效应会吸引更多资金入场，形成正向循环。当前上证指数稳稳站在年线上方，说明牛市的条件已经具备。这时候应该积极寻找年线上方、均线多头排列的个股——这些才是主升浪的候选标的。",
                "「日线只做所有均线都在250日线之上的个股，且多头顺次排列」—《趋势交易论》第18节")
        elif dev250 > 0:
            s = Signal("年线位置", f"年线上方 {dev250:+.1%}", "healthy",
                "上证指数目前在年线上方，处于牛市区域——这是《趋势交易论》认为可以操作的环境。不过价格离年线不远，说明市场虽然方向向上，但还没有拉开距离形成强烈的趋势行情。这就像春天刚来，气温在零度以上但还不暖和——方向是对的，但力度还不够。此时可以持仓，但不要因为「牛市来了」就满仓冲进去。等价格继续上行、年线开始上翘（说明长期资金在持续流入），再逐步加大仓位。",
                "")
        elif dev250 > -0.05:
            s = Signal("年线位置", f"年线附近 {dev250:+.1%}", "caution",
                "250日均线俗称「年线」，代表过去一年所有买入者的平均持仓成本。上证指数当前在年线附近（偏离不超过±5%），处于牛熊分界的模糊地带。年线是《趋势交易论》最看重的长期趋势标尺——书里反复强调「日线只做所有均线都在250日线之上的个股」。为什么年线这么重要？因为年线上方代表多数人赚钱、趋势向上；年线下方代表多数人被套、趋势向下。当前在年线附近徘徊，意味着市场还没有选择方向——既不是明确的牛市也不是明确的熊市。这时候应该「宁可休息」，减少操作，等市场自己走出来。具体操作：仓位降到3-5成，不追涨不杀跌，等指数明确站上年线且年线走平或上翘后再加仓。",
                "「趋势不明，宁可休息」—《趋势交易论》口诀第6条")
        else:
            s = Signal("年线位置", f"年线下方 {dev250:+.1%}", "danger",
                "250日均线是《趋势交易论》的牛熊分界线。上证指数当前在年线下方运行——这意味着过去一年买入的人整体处于被套状态，市场处于熊市区域。书里说得很清楚：「年线之下重防守」。这时候的核心策略是保住本金，而不是追求收益。具体的纪律：①不要重仓（仓位控制在2成以下）；②不要追任何反弹（熊市反弹是诱多）；③不要因为「跌多了」就去抄底（底下还有底）。等两个条件满足再考虑重新入场：指数重新站上年线，且年线从下降转为走平或上翘——这意味着长期趋势开始反转。",
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
            "「520战法」是《趋势交易论》里最简洁的趋势跟踪交易系统，只用两条均线：5日均线（代表最近一周的市场平均成本）和20日均线（代表最近一个月的市场平均成本）。当5日均线从下方上穿20日均线时，叫做「金叉」——这意味着短期买入力量开始强于中期买入力量，是明确的入场信号。当前上证指数出现520金叉，按照战法规则应该买入。但书里特别强调了：金叉需要配合成交量放大才可靠——有量支撑的金叉是真金叉，无量金叉可能是假突破。",
            "「MA5金叉MA20+放量=买入」—《趋势交易论》520战法")
    elif dead_520:
        s = Signal("520战法", "MA5死叉MA20 ⚠", "danger",
            "5日均线从上方下穿20日均线，叫做「死叉」——短期力量开始弱于中期力量。这是520战法的离场信号。当前上证指数出现死叉，按照纪律应该清仓或大幅减仓。书里特别强调：不要在死叉出现后「再等等看」——很多人亏损的根源就是在信号出现后犹豫不决。死叉如果伴随着放量下跌（说明资金在主动卖出），离场的紧迫性更高。",
            "「5日线下穿20日线死叉离场」—《趋势交易论》520战法")
    elif last > ma5_now:
        s = Signal("520战法", "多头运行中", "healthy",
            "当前上证指数价格在5日均线上方，短期趋势向上。520战法的持仓纪律是：只要每天收盘价不跌破5日均线，就继续持有——不要因为盘中波动或者「涨多了」就提前卖出。书里教的新手入门方法就是这句话：「买入点5日线，收盘跌破5日线减仓，20日线加回来，直到5日线下穿20日线死叉离场」。这套纪律看起来简单，能严格执行的人却很少——因为大多数人会被日内的涨跌吓出去。",
            "「收盘跌破5日线减仓，20日线加回来」—《趋势交易论》第18节")
    else:
        s = Signal("520战法", "偏弱运行", "caution",
            "上证指数当前价格在5日均线下方，说明最近一周买入的人处于浮亏状态——短期走势偏弱。但520战法还没有给出死叉信号（5日线还在20日线上方），所以趋势不算完全转空。这时候的关键观察点是：20日均线能否提供支撑？如果价格回踩20日均线后缩量止跌，说明只是正常调整——可以按纪律加仓。如果放量跌破20日线且形成死叉，那就是离场信号。",
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
            "「三周期共振」是《趋势交易论》最核心的分析框架。它把市场走势分为三个时间维度：月线（观察周期：半年到一年，判断大方向是牛是熊）、周线（观察周期：两到三个月，判断中期结构是在上升通道还是下降通道）、日线（观察周期：两到三周，判断短期走势是在涨还是跌）。当前上证指数的月线、周线、日线全部向上——这意味着：长期资金在做多、中期资金在做多、短期资金也在做多。三种力量同时推着一个方向走，这就是《趋势交易论》所说的「三者共振出黄金点」。历史上每一轮大牛市的主升浪，都伴随着三周期共振向上。此时应该重仓持有，不要被日内的回调吓出来——你看到的「大跌」在月线级别上不过是一根小阴线。",
            "「大周期定方向，中周期选结构，小周期抓节奏。三者共振出黄金点」—《趋势交易论》三周期选股")
    elif monthly == "bear" and weekly in ("bear","neut") and daily == "↓":
        s = Signal("三周期趋势", f"{mtf_val} — 共振向下 ⚠", "danger",
            "上证指数当前出现了《趋势交易论》认为最危险的信号：月线向下、周线向下、日线向下——三周期共振向下。这意味着：长期趋势在走弱（月线）、中期结构在恶化（周线）、短期也在继续跌（日线）。三个时间维度的力量都在把你往下拉——这不是「回调」，这是「趋势反转」。书里的原话是「果断放弃，空仓看戏」。为什么不能抄底？因为当三个级别都向下时，你以为的「底」很可能只是下跌中继——底下还有底。要等至少日线先走平不再创新低、月线的下跌速度开始放缓（斜率变小），才能开始考虑重新入场。",
            "「大级别下跌趋势里不要抄底博弈反弹——一套一个不吱声」—《趋势交易论》")
    elif monthly == "bull" and daily == "↓":
        s = Signal("三周期趋势", f"{mtf_val} — 大级别向上，小级别调整", "caution",
            "当前月线和周线向上（大方向没变），但日线在下跌——这是「牛市中的正常回踩」。就像一辆上坡的车偶尔松一下油门，不是要掉头。如何确认是回踩而不是反转？看两点：①日线回踩是否在20日均线或60日均线获得支撑；②回调时成交量是否缩小（说明卖的人越来越少，不是恐慌出逃）。如果这两个条件都满足，那就是加仓的好时机——「回踩不破是机会不是风险」。",
            "「回踩不破是机会不是风险。好股票每次回踩关键均线都是加仓点」—《趋势交易论》")
    elif monthly == "bear" and daily == "↑":
        s = Signal("三周期趋势", f"{mtf_val} — 大级别向下，小级别反弹", "caution",
            "月线向下意味着大趋势还是空头——就像河流的方向是往下游走的。日线的上涨只是河面上的一个小浪花，改变不了河水的流向。《趋势交易论》的多空循环理论明确指出：空头市场中的反弹是卖出机会。如果你手里还有持仓，趁反弹减仓；如果你空仓，不要被几天的上涨诱惑进去——熊市中最大的亏损往往来自「抢反弹」。",
            "「空头市场中，反弹往往是卖出或做空的机会」—《趋势交易论》多空循环")
    else:
        s = Signal("三周期趋势", f"{mtf_val} — 级别不统一", "caution",
            "三个时间级别的方向不一致（可能月线向上但日线向下，或者月线走平日线在涨），说明市场内部存在分歧——长期资金和短期资金在「打架」。这种情况在《趋势交易论》里叫做「级别不统一」——是最应该休息的时候。口诀第6条：「趋势不明，宁可休息」。具体做法：把仓位降到3成以下，不做新的买入，持有现金观望。等三个级别重新统一方向之后，再决定是加仓还是清仓。不要赌方向——赌对了是运气，赌错了是灾难。",
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
            "「缩量上涨+高乖离，量价背离风险」—《趋势交易论》")
    elif vr < VOL_SHRINK and chg < -0.005:
        s = Signal("量价结构", f"缩量下跌 (vol={vr:.1f}x)", "caution",
            "上证指数在下跌，但成交量在缩小——这就是「缩量下跌」。它说明市场上主动卖出的人不多，不是恐慌性出逃，更像是买家暂时休假了。这是止跌的必要条件，但不是反弹的充分条件。要确认反弹开始，需要等两个信号：①成交量从萎缩转为放大（有资金开始主动买入）；②价格企稳不再创新低（买方开始占优）。两个信号同时出现，才是安全入场点。在此之前继续观望。",
            "「下跌过程中成交量持续萎缩=抛压耗尽」—底部判断方法")
    else:
        s = Signal("量价结构", f"量价正常 (vol={vr:.1f}x)", "healthy",
            "当前上证指数的成交量和价格变动在健康范围内——没有出现放量滞涨（最危险的出货信号）、没有出现缩量上涨（背离预警）、没有出现放量下跌（恐慌信号）。成交量在60日均量的正常区间内波动，说明买卖力量处于平衡状态。《趋势交易论》里说：「短线看量，长线看势」——量是一切分析的基础。没有异常量价信号的时候，趋势就是最可靠的指引。",
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
            "「打到压力位但量能不足，可能回踩，等突破确认」—《趋势交易论》")
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

def diagnose_sector_mi(s: Dict) -> Dict:
    """用《趋势交易论》框架诊断单个板块。"""
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
    # ── 纯理论规则判定 ──
    if status == "entry" and any(t.startswith("✅") or t in ("金叉","底分型","周线↑") for t in tags):
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
        # 携带原始技术数据，供 generate_sector_ops 做硬校验
        "_tech": tech,
    }


# ═══════════════════════════════════════════════════════════
# AI 解读
# ═══════════════════════════════════════════════════════════

def ai_audit(cycle: Dict, signals: List[Signal], sectors: List[Dict],
             idx_tail: pd.DataFrame, temp_data: Dict, flow_data: Dict) -> Optional[str]:
    """AI 决策审计 — 不发表观点，只做合规检查。为五大模块逐项评分。"""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        sig_text = "\n".join(f"- {s.name}: {s.value} [{s.status}]" for s in signals)
        sec_text = "\n".join(
            f"- {s['rating']} {s['name']}({s['category']}): {s['phase']} | {s['tags']}"
            for s in sorted(sectors, key=lambda x: x['rating'])
        )
        temp_text = f"涨{temp_data.get('up_count',0)}家/跌{temp_data.get('down_count',0)}家, 涨停{temp_data.get('limit_up',0)}, 跌停{temp_data.get('limit_down',0)}"
        flow_text = f"北向{flow_data.get('net_flow',0):+.0f}亿"

        prompt = f"""你是交易纪律审计员。不要发表个人观点，只根据《趋势交易论》五大模块规则逐项评分。

当前状态:
{cycle['emoji']} {cycle['name']} | 仓位{cycle['position']} | {cycle['action']}
市场温度: {temp_text}
资金: {flow_text}

大盘信号:
{sig_text}

板块概览:
{sec_text}

请按以下格式输出（只输出这个格式，不要废话）:

**决策审计:**

趋势: [X/2分] — 理由
量价: [X/2分] — 理由
资金: [X/2分] — 理由
板块: [X/2分] — 理由
风险: [X/2分] — 理由

**总分: X/10**

**规则违例:**
- [若有违例列出，无则写"无违例"]
"""

        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [
                {"role": "system", "content": "你是交易纪律审计员。严格根据《趋势交易论》五大模块规则评判，不打感情分。只说事实，不发表投资建议。"},
                {"role": "user", "content": prompt}
            ], "temperature": 0.3, "max_tokens": 400},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[!] AI审计: {e}")
    return None


# ═══════════════════════════════════════════════════════════
# 信号冲突裁决
# ═══════════════════════════════════════════════════════════

# 优先级：三周期趋势 > 量价结构 > 520战法 > MACD > RSI > 其他
CONFLICT_PRIORITY = {
    "三周期趋势": 1, "量价结构": 2, "520战法": 3,
    "MACD动能": 4, "RSI": 5, "年线位置": 6,
    "乖离率": 7, "支撑压力": 7, "波动率": 7, "M1宏观锚": 7,
}

CONFLICT_EXPLANATIONS = {
    ("三周期趋势", "danger", "520战法", "healthy"):
        "三周期共振向下时，日线520金叉只是反弹而非反转。**月线/周线向下压倒日线信号**——520多头让位于三周期。只能轻仓快进快出（不超过2成），不可重仓持有。",
    ("三周期趋势", "danger", "量价结构", "healthy"):
        "三周期向下时，即使量价正常也不能做多。**趋势是主、量价是辅**——量价正常只说明抛压不大，不代表要涨。",
    ("量价结构", "danger", "520战法", "healthy"):
        "放量滞涨/放量下跌 > 520多头信号。**量在价先**——主力出货时即使520还没死叉，也应立即减仓。等520死叉才走就晚了。",
    ("量价结构", "danger", "RSI", "healthy"):
        "放量滞涨时RSI正常只是暂时的。量价结构是领先指标，RSI是滞后指标——**量价信号优先**。",
    ("520战法", "danger", "RSI", "healthy"):
        "520死叉是明确的离场信号。RSI正常不能对抗死叉——死叉后即使RSI没超买也应按纪律离场。",
    ("520战法", "danger", "MACD动能", "healthy"):
        "520死叉 > MACD金叉。520战法纪律：死叉即离场，不等MACD确认。",
}

STATUS_LABELS = {"healthy": "🟢", "caution": "🟡", "danger": "🔴"}


def detect_conflicts(signals: List[Signal]) -> List[str]:
    """检测指标间的冲突，返回裁决说明列表。按优先级从高到低排列。"""
    conflicts = []
    sig_map = {s.name: s for s in signals}
    names = list(sig_map.keys())

    # 两两检查
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            a, b = names[i], names[j]
            sa, sb = sig_map[a], sig_map[b]
            # 只检查状态不同的指标对
            if sa.status == sb.status:
                continue

            # 确定谁压倒谁
            pa = CONFLICT_PRIORITY.get(a, 10)
            pb = CONFLICT_PRIORITY.get(b, 10)
            high_sig, low_sig = (sa, sb) if pa < pb else (sb, sa)
            high_name, low_name = high_sig.name, low_sig.name

            # 只报告高优 danger vs 低优 healthy 或相反的有意义冲突
            if high_sig.status == "danger" and low_sig.status == "healthy":
                pass  # 有意义的冲突：高优指标看空 vs 低优指标看多
            elif high_sig.status == "healthy" and low_sig.status == "danger":
                pass  # 同上但方向相反
            else:
                continue  # caution级的就不报告了

            # 查找解释模板
            key = (high_name, high_sig.status, low_name, low_sig.status)
            explanation = CONFLICT_EXPLANATIONS.get(key)

            if not explanation:
                # 反向查找
                rev_key = (low_name, low_sig.status, high_name, high_sig.status)
                explanation = CONFLICT_EXPLANATIONS.get(rev_key)

            if explanation:
                conflicts.append(
                    f"**{STATUS_LABELS.get(high_sig.status, '')} {high_name}** ({high_sig.value[:30]}) "
                    f"vs **{STATUS_LABELS.get(low_sig.status, '')} {low_name}** ({low_sig.value[:30]})\n"
                    f"> {explanation}"
                )

    if not conflicts:
        # 检查一致性方向
        danger_count = sum(1 for s in signals if s.status == "danger")
        healthy_count = sum(1 for s in signals if s.status == "healthy")
        if danger_count > healthy_count:
            direction = "偏空——多数指标指向风险"
        elif healthy_count > danger_count:
            direction = "偏多——多数指标指向机会"
        else:
            direction = "中性——信号均衡，方向不明"
        conflicts.append(f"**今日指标一致，无冲突**。整体方向：{direction}。")

    return conflicts


# ═══════════════════════════════════════════════════════════
# 板块操作信号
# ═══════════════════════════════════════════════════════════

def generate_sector_ops(sectors: List[Dict]) -> List[str]:
    """
    将模糊的板块评级转换为具体的交易操作信号。
    三类：🟢 可考虑买入、🔴 应考虑减仓、🟡 继续等待。
    每条输出：具体条件 + 动作 + 止损/止盈 + 理论依据。
    """
    lines = []
    buy_candidates = []
    sell_candidates = []
    wait_candidates = []

    for s in sectors:
        rating = s.get("rating", "")
        tags = s.get("tags", "")
        tech = s.get("_tech", {})

        # ── 选股条件验证（手册第2节：五选五） ──
        above_ma20 = tech.get("above_ma20", False)
        above_ma60 = tech.get("above_ma60", False)
        above_ma250 = tech.get("above_ma250", False) or above_ma60  # 60日线上方近似年线上方
        is_doubled = tech.get("is_doubled", False)
        suppressed = tech.get("suppressed_by_ma", False)
        weekly_bull = tech.get("weekly_trend", "") == "bullish"
        meets_stock_selection = (
            above_ma20 and above_ma60 and above_ma250 and
            not is_doubled and not suppressed and
            weekly_bull  # ⑤周线趋势向上
        )

        # ── 入场条件验证（手册第3节：金叉+放量+不追高，三条件缺一不可） ──
        golden = tech.get("any_golden_cross", False) or "金叉" in tags
        vol_ratio = tech.get("vol_ratio", 0)
        vol_price = tech.get("vol_price", "")
        bias = tech.get("bias_ma5_pct", 0)
        meets_entry = (
            golden and
            vol_ratio > 1.2 and vol_price == "healthy_up" and
            abs(bias) < 5
        )

        # ── 止损条件（手册第4节） ──
        dead_cross = tech.get("dead_cross_5_20", False) or "死叉" in tags
        below_ma5 = not tech.get("above_ma5", True)

        # ── 止盈条件（手册第5节） ──
        vol_stagnant = vol_ratio > 2.0 and abs(tech.get("change_pct", 0)) < 0.3
        top_with_dist = tech.get("top_fractal", False) and vol_price == "distribution"
        extreme_overbought = False  # RSI > 80 from indicator signals

        if "可入场" in rating and meets_stock_selection and meets_entry:
            buy_candidates.append(s)
            s["_entry_check"] = f"✅ 金叉({golden})+放量({vol_ratio:.1f}x)+不追高(bias{bias:+.1f}%)"
        elif "可入场" in rating and not meets_entry:
            # downgrade: rating says enter but conditions not met
            s["rating"] = "🟡 等条件满足"
            reasons = []
            if not golden: reasons.append("未金叉")
            if vol_ratio <= 1.2 or vol_price != "healthy_up": reasons.append(f"未放量(vol={vol_ratio:.1f}x,{vol_price})")
            if abs(bias) >= 5: reasons.append(f"乖离过高(bias{bias:+.1f}%)")
            s["_entry_check"] = f"❌ {'; '.join(reasons)}——手册第3节：金叉+放量+不追高，缺一不可"
            wait_candidates.append(s)
            continue
        elif "可入场" in rating and not meets_stock_selection:
            s["rating"] = "🟡 选股不达标"
            reasons = []
            if not above_ma20: reasons.append("未站上20日线")
            if not above_ma60: reasons.append("未站上60日线")
            if not above_ma250: reasons.append("未站上年线")
            if is_doubled: reasons.append("已翻倍")
            if suppressed: reasons.append("均线压制")
            if not weekly_bull: reasons.append("周线未走好")
            s["_entry_check"] = f"❌ {'; '.join(reasons)}——手册第2节：选股需五条件全满足"
            wait_candidates.append(s)
            continue
        elif any(x in rating for x in ("持有不动",)):
            buy_candidates.append(s)
        elif any(x in rating for x in ("明确回避", "反弹就撤", "高位风险", "继续回避")):
            sell_candidates.append(s)
        elif dead_cross or below_ma5 or vol_stagnant or top_with_dist:
            # 量价八诀: 死叉/破5日线/放量滞涨/顶分+放量跌 = 卖出
            if "持有" in rating:
                s["_sell_reason"] = (
                    "死叉" if dead_cross else
                    "破5日线" if below_ma5 else
                    "放量滞涨（八诀#5: 主力高位出货）" if vol_stagnant else
                    "顶分型+放量跌（八诀#3: 资金出逃）"
                )
                sell_candidates.append(s)
            else:
                wait_candidates.append(s)
        elif vol_price == "weak_up" and abs(bias) > 5:
            # 量价八诀 #4: 缩量上涨+高乖离 = 警惕见顶
            if "持有" in rating:
                s["_sell_reason"] = "缩量上涨+乖离过高（八诀#4: 买盘不足，上涨不可持续）"
                sell_candidates.append(s)
            else:
                s["_wait_reason"] = "缩量上涨（八诀#4: 等放量确认或回踩再入）"
                wait_candidates.append(s)
        elif vol_price == "weak_down":
            # 量价八诀 #2/#6: 缩量下跌 = 抛压耗尽，可能见底
            s["_wait_reason"] = "缩量下跌（八诀#2: 抛压枯竭，等放量止跌确认后可能反转）"
            wait_candidates.append(s)
        elif any(x in tags for x in ("已翻倍", "顶分+放量跌", "持续背离")):
            if "持有" in rating:
                sell_candidates.append(s)
            else:
                wait_candidates.append(s)
        else:
            wait_candidates.append(s)

    lines.append("## 🔍 板块操作信号")
    lines.append("")

    if buy_candidates:
        lines.append("**🟢 可考虑买入的板块**:")
        lines.append("")
        for s in buy_candidates[:5]:
            tags = s.get("tags", "")
            tech = s.get("_tech", {})
            vol_ratio = tech.get("vol_ratio", 0)
            bias = tech.get("bias_ma5_pct", 0)
            golden = tech.get("any_golden_cross", False) or "金叉" in tags
            rating = s.get("rating", "")
            is_holding = "持有不动" in rating

            check = s.get("_entry_check", f"✅ 金叉({golden})+放量({vol_ratio:.1f}x)+不追高(bias{bias:+.1f}%)")

            if is_holding:
                action = "👉 **继续持有。**不破5日线就拿着，破5日线减半仓。"
                why = "📖 为什么：手册第1/4节——趋势未破，持仓纪律是「不破不走」。"
            else:
                action = "👉 **建1/3仓位入场**。止损：近期低点下方3%。"
                why = "📖 为什么：手册第2-3节——选股需年线上方+多头排列，入场需金叉+放量+不追高。三个条件全部验证通过。"

            lines.append(f"- **{s['name']}** ({s.get('category','')}) | {s.get('phase','')}")
            lines.append(f"  {s.get('tags','指标正常')}")
            if not is_holding:
                lines.append(f"  {check}")
            lines.append(f"  {action}")
            lines.append(f"  {why}")
        lines.append("")

    if sell_candidates:
        lines.append("**🔴 应考虑减仓/清仓的板块**:")
        lines.append("")
        for s in sell_candidates[:5]:
            tags = s.get("tags", "")
            sell_reason = s.get("_sell_reason", "")
            if sell_reason:
                action = f"👉 **减仓/清仓。**原因：{sell_reason}。"
            elif "已翻倍" in tags and ("死叉" in tags or "顶分" in tags):
                action = "👉 **清仓。**涨幅已透支+顶部信号出现。全部卖出，锁定利润。"
            elif "死叉" in tags:
                action = "👉 **减仓至少一半。**死叉=按520战法纪律离场。破5日线清掉剩下的。"
            elif "顶分+放量跌" in tags:
                action = "👉 **减仓一半。**顶分型+放量跌=主力在出货。不等死叉，先走一半。"
            elif "持续背离" in tags:
                action = "👉 **逐步减仓。**量价持续背离=上涨动力衰竭。每次反弹减1/3。"
            else:
                action = "👉 **不要加仓，设好止损。**若破5日线→减半仓；破20日线→全清。"
            lines.append(f"- **{s['name']}** ({s.get('category','')}) | {s.get('phase','')}")
            if tags:
                lines.append(f"  {tags}")
            lines.append(f"  {action}")
            lines.append(f"  📖 依据：量价八诀 + 筑顶特征（第127-139节）")
        lines.append("")

    if wait_candidates:
        lines.append("**🟡 继续等待的板块**:")
        lines.append("")
        for s in wait_candidates[:8]:
            tags = s.get("tags", "")
            check = s.get("_entry_check", "")
            wait_reason = s.get("_wait_reason", "")
            if check:
                cond = check
            elif wait_reason:
                cond = wait_reason
            elif "均线压制" in tags:
                cond = "等价格站上20日均线+放量确认"
            elif "周线↓" in tags:
                cond = "等周线走平不再创新低，再考虑入场"
            elif "死叉" in tags:
                cond = "等死叉修复+重新金叉+放量，三条件缺一不可"
            else:
                cond = "等520金叉或底分型出现"
            lines.append(f"- **{s['name']}** ({s.get('category','')}) | {s.get('phase','')} → {cond}")
        lines.append("")

    return lines


# ═══════════════════════════════════════════════════════════
# 信号翻转检测 — 对比昨日信号，标注状态翻转
# ═══════════════════════════════════════════════════════════

def detect_signal_flips(signals: List[Signal]) -> List[str]:
    """从 trade_log.jsonl 加载昨日信号，对比今日，检测翻转向。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, "trade_log.jsonl")
    if not os.path.exists(log_path):
        return []

    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yest_sigs = {}
        with open(log_path, encoding='utf-8') as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                    if e.get("date") == yesterday and e.get("type") == "index" and "signals" in e:
                        yest_sigs = e["signals"]
                        break
                except Exception:
                    continue

        if not yest_sigs:
            return []

        flips = []
        today_sigs = {s.name: s.status for s in signals}
        for name, today_status in today_sigs.items():
            yest_status = yest_sigs.get(name, "")
            if yest_status and yest_status != today_status:
                arrow = "↑" if today_status == "healthy" and yest_status != "healthy" else \
                        "↓" if today_status == "danger" and yest_status != "danger" else "→"
                flips.append(f"{name}: {yest_status}→{today_status} {arrow}")

        return flips
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# 每日一得 — 从《趋势交易论》按市场状态选摘
# ═══════════════════════════════════════════════════════════

DAILY_INSIGHTS = {
    "三周期向下": [
        "「大级别下跌趋势里不要抄底博弈反弹——一套一个不吱声」—《趋势交易论》三周期共振",
        "「空头市场中，反弹往往是卖出或做空的机会」—《趋势交易论》多空循环",
        "「趋势不明，宁可休息」—口诀第6条",
    ],
    "三周期向上": [
        "「三者共振出黄金点。历史上每一轮大牛市的主升浪，都伴随着三周期共振向上」—《趋势交易论》三周期选股",
        "「月线级别的主升浪中，日线下跌只是小浪花——不要被日内回调吓出来」—《趋势交易论》持仓纪律",
    ],
    "放量滞涨": [
        "「高位放量滞涨，坚决出局观望」—口诀第4条",
        "「放巨量但价格基本不动——往往是主力在高位出货。历史上多次大顶前都出现过放量滞涨」—《趋势交易论》筑顶特征",
    ],
    "缩量": [
        "「下跌过程中成交量持续萎缩=抛压耗尽」—《趋势交易论》底部判断",
        "「缩量回踩不破是机会不是风险。好股票每次回踩关键均线都是加仓点」—《趋势交易论》量价八诀",
    ],
    "震荡": [
        "「横盘的时候最考验耐心。着急的人买在最高点、卖在最低点——因为他们等不起」—《趋势交易论》中枢震荡",
        "「趋势不明时，做的越多错的越多。等待本身就是一种策略」—《趋势交易论》交易心理",
    ],
    "default": [
        "「短线看量，长线看势——量是一切分析的基础」—《趋势交易论》量价关系",
        "「买横买坑不买竖。横盘或回踩时入场，追涨必亏」—《趋势交易论》入场条件",
        "「跌出来的机会，涨出来的风险」—口诀第3条",
        "「收盘跌破5日线减仓，20日线加回来」—《趋势交易论》第18节",
        "「震仓：打压后有承接，股价能很快企稳。出货：放量下跌后无人承接」—《趋势交易论》震仓vs出货",
    ],
}


def pick_daily_insight(cycle: Dict, signals: List[Signal]) -> str:
    """根据当日市场状态，从摘录库中选一条最相关的。"""
    cycle_name = cycle.get("name", "")

    # 匹配规则：优先匹配更具体的状态
    sig_map = {s.name: s for s in signals}
    if "下跌" in cycle_name:
        pool = DAILY_INSIGHTS.get("三周期向下", DAILY_INSIGHTS["default"])
    elif "上涨" in cycle_name:
        pool = DAILY_INSIGHTS.get("三周期向上", DAILY_INSIGHTS["default"])
    elif "震荡" in cycle_name:
        pool = DAILY_INSIGHTS.get("震荡", DAILY_INSIGHTS["default"])
    else:
        pool = DAILY_INSIGHTS["default"]

    # 叠加量价状态
    vp = sig_map.get("量价结构")
    if vp and vp.status == "danger":
        pool = DAILY_INSIGHTS.get("放量滞涨", pool)
    elif vp and vp.status == "caution":
        pool = DAILY_INSIGHTS.get("缩量", pool)

    # 选一条（用日期做种子，同一天同一句）
    day_seed = int(datetime.now().strftime("%d")) % len(pool)
    return pool[day_seed]


# ═══════════════════════════════════════════════════════════
# 格式化输出
# ═══════════════════════════════════════════════════════════

def format_dashboard(cycle: Dict, signals: List[Signal], sectors: List[Dict],
                     ai_text: Optional[str], idx: pd.DataFrame, sector_text: str = "",
                     indices: dict = None, portfolio: Portfolio = None,
                     temp_data: Dict = None, flow_data: Dict = None) -> str:
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
    ]

    # ── 🔥 市场温度 ──
    if temp_data:
        up = temp_data.get("up_count", 0)
        dn = temp_data.get("down_count", 0)
        lu = temp_data.get("limit_up", 0)
        ld = temp_data.get("limit_down", 0)
        total = temp_data.get("total", up+dn)
        breadth_ok = up > dn
        vol_info = temp_data.get("volume", {})
        vol_ratio = vol_info.get("ratio", 1.0)

        # 赚钱效应判断
        if up > total * 0.7:
            breadth_label = "🟢 极好"
        elif up > total * 0.5:
            breadth_label = "🟡 分化"
        elif up > total * 0.3:
            breadth_label = "🔴 较差"
        else:
            breadth_label = "🔴 极差"

        vol_label = '🟢 放量' if vol_ratio > 1.2 else '🟡 正常' if vol_ratio > 0.8 else '🔴 缩量'
        lim_label = '🟢 活跃' if lu >= 40 else '🟡 一般' if lu >= 20 else '🔴 冷清'

        temp_lines = [
            f"## 🔥 市场温度",
            f"",
            f"| 指标 | 数值 | 判断 |",
            f"|------|------|------|",
            f"| 涨跌比 | {up}↑ / {dn}↓ | {breadth_label} |",
            f"| 涨停/跌停 | {lu}涨停 / {ld}跌停 | {lim_label} |",
            f"| 成交额 | {vol_info.get('total_amount',0)/1e8:.0f}亿 (vs 20日均) | {vol_label} |",
            f"",
            f"> 📖 **怎么看**：涨跌比反映赚钱效应——指数可能不跌但你手里的票全在跌（分化）。涨停数反映短线资金活跃度——少于20说明游资休息了。成交额是最诚实的指标——放量才有行情，缩量就是大家在等。三者结合判断：指数趋势告诉你方向，市场温度告诉你能不能赚钱。",
            f"",
        ]
        lines.extend(temp_lines)

    # ── 💰 资金地图 ──
    if flow_data:
        north = flow_data.get("north", {})
        flows = flow_data.get("sectors", [])
        north_flow = north.get('net_flow', 0)
        north_label = '🟢 大幅流入' if north_flow > 50 else '🟢 小幅流入' if north_flow > 0 else '🔴 流出' if north_flow < -50 else '🟡 小幅流出'
        lines.extend([
            f"## 💰 资金地图",
            f"",
            f"北向资金: **{north_flow:+.0f}亿** {north_label}",
            f"",
            f"> 📖 **怎么看**：北向资金是外资通过沪深港通买卖A股的钱。持续流入=外资看好中国资产；持续流出=外资在撤退或调仓。单日几十亿进出意义不大，要看趋势。如果北向连续3天流入+技术面走好=资金+趋势共振，可靠性大幅提升。",
            f"",
        ])
        if flows:
            lines.append("主力净流入 TOP5:")
            for f in flows[:5]:
                lines.append(f"- {f}")
            lines.append("")
            lines.append("> 📖 **怎么看**：主力资金流入的板块=大钱正在布局的方向。把主力流入TOP5和你的板块操作信号对照——如果半导体同时出现在「主力流入TOP5」和「可买入」= 技术面和资金面共振，信号更可靠。只在一侧出现要谨慎。')
            lines.append("")

    lines.extend([
        f"**{cycle['emoji']} {cycle['name']}**  |  建议仓位 **{cycle['position']}**",
        f"",
    ])

    # ── 📖 交易手册速查 ──
    lines.append(TRADING_MANUAL)

    # ── ⚠️ 信号翻转 ──
    flips = detect_signal_flips(signals)
    if flips:
        lines.extend([
            "---",
            "",
            "## ⚠️ 信号翻转 — 与昨日对比",
            "",
        ])
        for f in flips:
            lines.append(f"- ⚠️ {f}")
        lines.append("")
        lines.append("> 信号翻转日是最容易犯错的时刻——确认翻转是真实的趋势变化还是盘中噪声。")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 📊 今日信号 — 手册规则 × 当前数据",
        "",
        f"**仓位模块** → 手册第1节：当前 {cycle['emoji']} {cycle['name']} → 理论仓位 **{cycle['position']}**",
        f"> 📖 为什么：{cycle.get('quote','')}",
        "",
    ])

    # ── 每个关键指标：理论 + 当前数据 + 操作建议 + 手册归属 ──
    key_indicators = ["年线位置", "三周期趋势", "MACD动能", "量价结构", "RSI", "520战法"]
    # 每个指标所属的手册模块
    indicator_module = {
        "年线位置": "2.选股", "三周期趋势": "1.仓位管理", "MACD动能": "3.入场",
        "量价结构": "3.入场 / 5.止盈", "RSI": "5.止盈", "520战法": "3.入场 / 4.止损",
    }
    action_map = {
        "年线位置": {
            "healthy": "👉 **操作**：年线上方可以积极选股。重点找年线上方、均线多头排列的个股——这些是主升浪的候选标的。仓位可到5-7成。",
            "caution": "👉 **操作**：年线附近方向不明，仓位控制在3-5成。不追涨不杀跌，等指数明确站上年线且年线走平或上翘后，再逐步加仓。",
            "danger": "👉 **操作**：年线下方以防守为主，仓位控制在2成以下。不要抄底、不要追反弹。等指数重新站上年线且年线走平或上翘，才能考虑重新入场。",
        },
        "三周期趋势": {
            "healthy": "👉 **操作**：三周期共振向上=可以重仓（7-8成）。不要被日内的回调吓出来——月线级别的主升浪中，日线下跌只是小浪花。",
            "caution": "👉 **操作**：三个级别方向不一致时，仓位降到3-5成。不做新的买入，等三个级别重新统一方向后再动手。",
            "danger": "👉 **操作**：三周期共振向下=空仓或极轻仓（2成以下）。不抄底、不抢反弹、不博弈短线。至少等日线走平不再创新低，才能重新评估。",
        },
        "MACD动能": {
            "healthy": "👉 **操作**：MACD金叉或零轴上多头运行=可以持仓。如果是金叉刚出现，可以买入；如果已运行一段时间，持有不动，关注是否出现顶背离。",
            "caution": "👉 **操作**：MACD空头运行或死叉=观望为主。零轴上死叉不用恐慌，但不要加仓；零轴下死叉要果断离场。有底背离时加入自选观察，等金叉确认再入场。",
            "danger": "👉 **操作**：零轴下死叉=空头加速，应立即离场。不要抱「等反弹再走」的侥幸心理——零轴下的死叉往往意味着加速下跌。",
        },
        "量价结构": {
            "healthy": "👉 **操作**：量价正常=按趋势信号操作即可。放量上涨时可以加仓，缩量回踩时可以持有。",
            "caution": "👉 **操作**：缩量上涨或缩量下跌=不要追高，也不要恐慌。等成交量恢复正常再决定方向。持续缩量上涨3天以上要警惕见顶。",
            "danger": "👉 **操作**：放量滞涨或放量下跌=立即减仓或离场。放量滞涨是《趋势交易论》最危险的信号——主力在出货。不要等暴跌再后悔。",
        },
        "RSI": {
            "healthy": "👉 **操作**：RSI正常=按趋势信号操作。40-70区间是正常的波动范围。",
            "caution": "👉 **操作**：RSI超卖（<30）=关注错杀机会，但不急于抄底——等MACD金叉+放量企稳再入场。RSI超买（>70）=不加仓，等回踩。",
            "danger": "👉 **操作**：RSI极端超买（>80）=应分批减仓，「高潮减仓」。RSI极端超卖（<20）=冰点期，留意新题材萌芽，但不要裸抄底。",
        },
        "520战法": {
            "healthy": "👉 **操作**：金叉或5日线上方=可以持仓。收盘不破5日线就继续持有。金叉配合放量是最可靠的入场信号。",
            "caution": "👉 **操作**：5日线下方但未死叉=观望。关注20日线能否提供支撑。如果缩量止跌可以试探性买入，放量跌破则继续等。",
            "danger": "👉 **操作**：死叉=按纪律离场。不要犹豫，不要「再等等看」。死叉后如果伴随放量下跌，更要果断清仓。",
        },
    }
    for name in key_indicators:
        s = sig_map.get(name)
        if not s: continue
        mod = indicator_module.get(name, "")
        lines.append(f"**{icon[s.status]} {s.name}**（📖 手册 {mod}）: {s.value}")
        lines.append(f"> {s.meaning}")
        if s.rule:
            lines.append(f"> 📖 {s.rule}")
        # 操作建议
        act = action_map.get(name, {}).get(s.status, "")
        if act:
            lines.append(f"> {act}")
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

    # 综合判断入场/出场 — 明确标注适用指数
    signals_bull = []
    signals_bear = []

    if m520:
        if "金叉" in m520.value: signals_bull.append("[上证] 520金叉 → 可入场买入")
        elif "死叉" in m520.value: signals_bear.append("[上证] 520死叉 → 应离场清仓")
        elif "多头" in m520.value: signals_bull.append("[上证] 520多头运行 → 可继续持有")
        else: signals_bear.append("[上证] 520偏弱 → 暂不宜入场，等金叉")

    if macd_s:
        if "金叉" in macd_s.value: signals_bull.append("[上证] MACD金叉 → 买入信号")
        elif "死叉" in macd_s.value and "零轴上" in macd_s.value:
            signals_bull.append("[上证] MACD零轴上死叉 → 正常调整，不必恐慌卖出")
        elif "死叉" in macd_s.value: signals_bear.append("[上证] MACD死叉 → 应离场")
        if "底背离" in macd_s.value: signals_bull.append("[上证] MACD底背离 → 关注见底机会（等金叉确认后入场）")
        if "顶背离" in macd_s.value: signals_bear.append("[上证] MACD顶背离 → 上涨动力衰竭，逐步减仓")

    if rsi_s:
        rsi_val = rsi_s.value
        if "极端超买" in rsi_val: signals_bear.append("[上证] RSI极端超买(>80) → 高潮期，分批减仓")
        elif "超买" in rsi_val: signals_bear.append("[上证] RSI超买(>70) → 不加仓，等回踩")
        elif "超卖" in rsi_val or "极端超卖" in rsi_val:
            signals_bull.append("[上证] RSI超卖(<30) → 恐慌中有错杀机会，但需等MACD金叉+放量企稳双确认再入场")

    if sr_s:
        import re
        if re.search(r"近.*支撑", sr_s.value):
            signals_bull.append(f"[上证] {sr_s.value} → 若缩量企稳可低吸")

    # 理论仓位
    theory_pos = cycle.get("position", "N/A")

    # 各指数单独策略
    idx_lines = []
    for tag, df in indices.items():
        if df is not None and len(df) >= 2:
            c = df["close"].iloc[-1]; ch = (c/df["close"].iloc[-2]-1)
            idx_lines.append(f"{tag} {c:.0f} ({ch:+.2%})")

    lines.append(f"**以上信号基于**: 上证指数（{' | '.join(idx_lines)}）")
    lines.append(f"**理论仓位**（上证）: {theory_pos}")
    lines.append("")

    if signals_bull:
        lines.append("**🟢 入场/持有信号**:")
        for s in signals_bull: lines.append(f"- {s}")
        lines.append("")
    if signals_bear:
        lines.append("**🔴 出场/谨慎信号**:")
        for s in signals_bear: lines.append(f"- {s}")
        lines.append("")

    # 综合操作总结 — 三周期趋势权重最高，压倒一切
    mtf_s = sig_map.get("三周期趋势")
    mtf_danger = mtf_s and mtf_s.status == "danger"  # 共振向下
    mtf_healthy = mtf_s and mtf_s.status == "healthy"  # 共振向上

    # 致命信号：三周期共振向下 + 520死叉 = 无条件空仓
    fatal_520 = m520 and "死叉" in m520.value
    fatal_macd = macd_s and "死叉" in macd_s.value and "零轴下" in macd_s.value

    if mtf_danger and (fatal_520 or fatal_macd):
        summary = "🔴 三周期共振向下 + 关键死叉信号同时出现。**理论建议：无条件空仓。不要抄底、不要抢反弹。** 等至少日线走平且月线不再加速下跌，再重新评估。"
    elif mtf_danger:
        summary = "🔴 三周期共振向下——这是《趋势交易论》最危险的信号。**理论建议：空仓或极轻仓（2成以下）。** 虽然有底背离等积极信号，但三周期向下的力量更大，不宜逆势操作。等三周期中至少两个级别转向上方，再考虑入场。"
    elif mtf_healthy:
        summary = "🟢 三周期共振向上——这是最强的做多信号。**理论建议：可以重仓持有（7-8成）。** 按520战法纪律操作：不破5日线就拿着，破5日线减半仓，回踩20日线加回。"
    else:
        # 无共振，按信号数量判断
        bear_count = len(signals_bear); bull_count = len(signals_bull)
        if bear_count > bull_count:
            summary = f"🟡 空头信号略占优（空{bear_count} vs 多{bull_count}）。**理论建议：轻仓观望（3-5成），等方向明确。**"
        elif bull_count > bear_count:
            summary = f"🟢 多头信号略占优（多{bull_count} vs 空{bear_count}）。**理论建议：可以持仓或逐步入场，按520战法纪律操作。**"
        else:
            summary = "🟡 信号均衡。**理论建议：轻仓观望，等方向明确后再行动。**"

    lines.append(f"**综合判断**: {summary}")
    lines.append("")

    # 520战法具体规则
    lines.append(f"**📖 520战法纪律速查（《趋势交易论》第128-129节）**:")
    lines.append(f"- 入场：5日均线上穿20日均线（金叉）+ 成交量放大 → 买入")
    lines.append(f"- 减仓：收盘价跌破5日均线 → 减掉一半仓位")
    lines.append(f"- 加仓：回踩20日均线不破 + 缩量止跌 → 把减掉的仓位加回来")
    lines.append(f"- 离场：5日均线下穿20日均线（死叉） → 全部清仓")
    lines.append("")

    # ── ⚔️ 信号冲突裁决 ──
    lines.append("---")
    lines.append("")
    lines.append("## ⚔️ 信号冲突裁决")
    lines.append("")
    conflict_lines = detect_conflicts(signals)
    for cl in conflict_lines:
        lines.append(cl)
        lines.append("")
    lines.append(f"**裁决优先级**: 三周期趋势 > 量价结构 > 520战法 > MACD > RSI > 其他指标")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ── 🔍 板块操作信号 ──
    sector_ops = generate_sector_ops(sectors)
    lines.extend(sector_ops)

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

    # ── 👀 板块观察池 ──
    close_calls = [s for s in sectors if s.get("_entry_check", "").startswith("❌") and "可入场" not in s.get("rating","")]
    if close_calls:
        lines.append("---")
        lines.append("")
        lines.append("## 👀 板块观察池 — 差一个条件就满足入场")
        lines.append("")
        for s in close_calls[:5]:
            check = s.get("_entry_check", "")
            lines.append(f"- **{s['name']}** ({s.get('category','')}): {check}")
        lines.append("")

    if ai_text:
        lines.append("---")
        lines.append("")
        lines.append(f"{ai_text}")
        lines.append("")

    # ── 💰 模拟账户 ──
    if portfolio:
        pf_lines = portfolio.format_for_dashboard()
        lines.extend(pf_lines)
        lines.append("")

    # ── 📖 每日一得 ──
    lines.append("---")
    lines.append("")
    insight = pick_daily_insight(cycle, signals)
    lines.append(f"## 📖 每日一得")
    lines.append("")
    lines.append(f"> {insight}")
    lines.append("")

    lines.append("---")
    lines.append("*《趋势交易论》(710页) | 每日自动*")

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

    today = datetime.now()
    weekday = today.weekday()

    # ── 开市检查 ──
    if not args.force:
        if weekday >= 5:
            print(f"[i] 周末休市 (周{['一','二','三','四','五','六','日'][weekday]})，不推送"); return
        if weekday == 0 and today.hour < 10:
            print(f"[i] 周一早于10点，数据可能未更新，不推送"); return
        if 1 <= weekday <= 4 and today.hour < 16:
            print(f"[i] 交易日未收盘 (当前{today.hour}:{today.minute:02d})，不推送"); return

    print("=" * 50)
    print("  A股全景仪表盘")
    print(f"  {today.strftime('%Y-%m-%d %H:%M')}")
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

    print("[3/4] 板块诊断...")
    sector_data = None; sector_text = ""; sectors_diag = []
    try:
        from sector_monitor import fetch_sector_monitor_data, format_sector_for_prompt
        sector_data = fetch_sector_monitor_data()
        sector_text = format_sector_for_prompt(sector_data)
        sectors_diag = [diagnose_sector_mi(s) for s in sector_data.get("sectors", [])]
        sm = sector_data.get("summary", {})
        print(f"  板块: {sm.get('entry_count',0)}入 {sm.get('hold_count',0)}持 "
              f"{sm.get('watch_count',0)}观 {sm.get('avoid_count',0)}避")
    except Exception as e:
        print(f"  [!] 板块: {e}")

    cycle = assess_sentiment(signals)
    print(f"\n[4/6] 情绪周期: {cycle['emoji']} {cycle['name']}")

    # ── 市场温度 + 资金流向 ──
    print("\n[5/6] 市场温度+资金...")
    temp_data = {"up_count": 0, "down_count": 0, "limit_up": 0, "limit_down": 0, "volume": {}}
    flow_data = {"north": {}, "sectors": []}
    try:
        sd = StockData()
        breadth = sd.get_market_breadth()
        vol_info = sd.get_market_volume()
        north = sd.get_north_flow()
        temp_data = {
            "up_count": breadth["up_count"], "down_count": breadth["down_count"],
            "limit_up": breadth["limit_up"], "limit_down": breadth["limit_down"],
            "total": breadth["total"],
            "volume": {"total_amount": vol_info["total_amount"], "ratio": vol_info["ratio"]},
        }
        flow_data["north"] = north
        ff = sd.get_sector_fund_flow()
        if len(ff) > 0:
            name_col = [c for c in ff.columns if "名称" in str(c) or "板块" in str(c) or "name" in str(c).lower()]
            flow_col = [c for c in ff.columns if "净流入" in str(c) or "主力" in str(c)]
            if name_col and flow_col:
                for _, row in ff.iterrows():
                    flow_data["sectors"].append(f"{row[name_col[0]]}: {row[flow_col[0]]}")
        print(f"  温度: {breadth['up_count']}↑/{breadth['down_count']}↓ 涨停{breadth['limit_up']} | 北向{north['net_flow']:+.0f}亿")
    except Exception as e:
        print(f"  [!] 温度数据: {e}")

    ai = ai_audit(cycle, signals, sectors_diag, idx, temp_data,
                  {"north": flow_data.get("north", {}), "net_flow": flow_data.get("north", {}).get("net_flow", 0),
                   "sectors": flow_data.get("sectors", [])})

    # ── 模拟账户 ──
    print("\n[6/6] 模拟账户...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pf = Portfolio(script_dir, initial_cash=1_000_000)

    # 执行上次建议
    price_map = {}
    for tag, df in indices.items():
        if df is not None and len(df) >= 1:
            price_map[tag] = float(df["close"].iloc[-1])
    # 板块价格从 sector_monitor 获取（用指数近似）
    if sector_data:
        for s in sector_data.get("sectors", []):
            sname = s.get("name", "")
            sp = s.get("technical", {}).get("last_price", 0)
            if sp > 0:
                price_map[sname] = sp
    pf.update_prices(price_map)
    pf.execute_prior_recommendations(price_map)

    # 记录本次信号日志
    pf.log_signal({
        "type": "index", "target": "上证",
        "cycle": cycle["name"], "position": cycle["position"],
        "signals": {s.name: s.status for s in signals},
    })

    # 构建本次建议（供明天执行——明天会自动执行这些交易）
    index_rec = f"{cycle['name']} → 仓位{cycle['position']} | {cycle['action']}"
    sector_recs = {}

    # 买入建议：只有通过入场三条件验证的才写入（明天自动买入）
    for s in sectors_diag:
        rating = s.get("rating", "")
        entry_check = s.get("_entry_check", "")
        if "可入场" in rating and entry_check.startswith("✅"):
            sector_recs[s["name"]] = {
                "action": "建仓入场",
                "position_pct": 0.33,  # 1/3仓位
                "rating": rating,
                "entry_check": entry_check,
            }

    # 卖出建议
    for s in sectors_diag:
        rating = s.get("rating", "")
        tags = s.get("tags", "")
        if any(x in rating for x in ("明确回避", "反弹就撤", "高位风险", "继续回避")):
            action = "清仓" if "明确回避" in rating else "减仓一半"
        elif "死叉" in tags:
            action = "减仓一半"
        elif "顶分+放量跌" in tags:
            action = "减仓一半"
        else:
            continue
        sector_recs[s["name"]] = {"action": action, "rating": rating, "tags": tags}

    pf.save_position_state(index_rec, sector_recs)

    msg = format_dashboard(cycle, signals, sectors_diag, ai, idx, sector_text, indices, pf, temp_data, flow_data)

    # 飞书内容可能超长，分段发送
    max_chars = 25000
    if len(msg) > max_chars and not args.dry_run:
        parts = []
        remaining = msg
        while len(remaining) > max_chars:
            split_at = remaining.rfind("---", 0, max_chars)
            if split_at < max_chars // 2:
                split_at = remaining.rfind("\n\n", 0, max_chars)
            if split_at < 1000:
                split_at = max_chars
            parts.append(remaining[:split_at])
            remaining = remaining[split_at:]
        parts.append(remaining)
        for i, part in enumerate(parts):
            ok = send_feishu(part)
            print(f"[>] 飞书({i+1}/{len(parts)}): {'OK' if ok else 'FAIL'}")
    else:
        print("\n" + msg)
        if args.dry_run: print("\n[i] Dry run"); return
        ok = send_feishu(msg)
        print(f"\n[>] 飞书: {'OK' if ok else 'FAIL'}")


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    main()
