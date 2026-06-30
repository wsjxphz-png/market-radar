"""
A股板块异动监测模块
基于 2026-06-29 会议分析框架，每日自动判断板块 entry/hold/watch/avoid 状态。
"""

import json
import time
import random
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# 反爬加固：替换 AKShare 默认 User-Agent + 增加超时
# ============================================================
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_original_request_with_retry = None


def _install_ua_patch():
    """给 AKShare 内部的 requests.Session 注入浏览器 User-Agent"""
    global _original_request_with_retry
    try:
        from akshare.utils import request as _akreq
        _original_request_with_retry = _akreq.request_with_retry

        def patched_request(url, params=None, timeout=15, max_retries=3,
                            base_delay=1.0, random_delay_range=(0.5, 1.5)):
            import requests
            from requests.adapters import HTTPAdapter
            last_exception = None
            for attempt in range(max_retries):
                try:
                    with requests.Session() as session:
                        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1)
                        session.mount("http://", adapter)
                        session.mount("https://", adapter)
                        session.headers.update({
                            "User-Agent": _BROWSER_UA,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                            "Accept-Encoding": "gzip, deflate, br",
                        })
                        resp = session.get(url, params=params, timeout=timeout)
                        resp.raise_for_status()
                        return resp
                except (requests.RequestException, ValueError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(*random_delay_range)
                        time.sleep(delay)
            raise last_exception

        _akreq.request_with_retry = patched_request
        logger.info("AKShare UA patch installed")
    except Exception:
        pass  # 静默失败，不影响主流程


_install_ua_patch()

# ============================================================
# 板块规则配置（来源：6.29 会议）
# ============================================================

SECTOR_RULES = [
    # ---- ENTRY: 可低吸入场 ----
    {"name": "仪器仪表", "category": "机器人方向", "status": "entry",
     "rule": "dip_buy_oscillation",
     "note": "震荡中枢，未拉过主升，上涨趋势中可低吸"},
    {"name": "通用机械", "category": "机器人方向", "status": "entry",
     "rule": "dip_buy_oscillation",
     "note": "同仪器仪表，机器人方向，震荡中枢可低吸"},
    {"name": "工业机械", "category": "机器人方向", "status": "entry",
     "rule": "dip_buy_oscillation",
     "note": "同仪器仪表，机器人方向，震荡中枢可低吸"},
    {"name": "农林牧渔", "category": "消费", "status": "entry",
     "rule": "bottom_fishing_pullback",
     "note": "底部资金抄底，连续3日阳线，等回踩买入，机构票当天涨超3%不追"},
    {"name": "家电行业", "category": "消费", "status": "entry",
     "rule": "bottom_fishing",
     "note": "主力抄底格力，机构票只能买超跌不追涨"},
    {"name": "煤炭行业", "category": "周期", "status": "entry",
     "rule": "support_rebound",
     "note": "跌到位到支撑，可加仓等反弹降成本"},
    {"name": "船舶制造", "category": "制造", "status": "entry",
     "rule": "double_bottom",
     "note": "做双底，拉上去就好"},

    # ---- HOLD: 趋势完好，持有不追 ----
    {"name": "半导体", "category": "科技", "status": "hold",
     "rule": "uptrend_selective",
     "note": "趋势完好但不可追，选底部未翻倍个股短线参与，下半年存储IPO有持续性"},

    # ---- WATCH: 观察中，需要确认信号 ----
    {"name": "食品饮料", "category": "消费", "status": "watch",
     "rule": "bottom_confirmation",
     "note": "见底标志，需明日继续给阳线确认底分"},
    {"name": "日用化工", "category": "消费", "status": "watch",
     "rule": "yang_confirmation",
     "note": "需明日持续阳线确认"},
    {"name": "医疗保健", "category": "医药", "status": "watch",
     "rule": "resistance_test",
     "note": "打到压力位，若继续放量上涨则见底确认"},
    {"name": "医药", "category": "医药", "status": "watch",
     "rule": "resistance_test",
     "note": "大级别趋势未扭转，短期可能回踩，震荡看待"},
    {"name": "证券", "category": "金融", "status": "watch",
     "rule": "5week_ma_dip",
     "note": "打到压力，与科技跷跷板，等5周线低吸不追"},
    {"name": "保险", "category": "金融", "status": "watch",
     "rule": "consolidating",
     "note": "在这个位置横住了，等方向选择"},
    {"name": "文教休闲", "category": "消费", "status": "avoid",
     "rule": "just_stopped_falling",
     "note": "弱势板块刚止跌，下影线只是止跌信号，还没到入场时机"},
    {"name": "旅游酒店", "category": "消费", "status": "avoid",
     "rule": "just_stopped_falling",
     "note": "同文教休闲，弱势板块刚止跌，需要更多确认信号"},

    # ---- AVOID: 回避，不入场 ----
    {"name": "酿酒行业", "category": "消费", "status": "avoid",
     "rule": "prolonged_consolidation",
     "note": "大级别支撑但需长时间横盘震荡，暂时不是机会"},
    {"name": "有色金属", "category": "周期", "status": "avoid",
     "rule": "downtrend_line",
     "note": "下降趋势线压制，假突破后回落，下降趋势未改"},
    {"name": "电气设备", "category": "新能源", "status": "avoid",
     "rule": "trend_broken",
     "note": "走势非常不好，阶段性筑底但立刻反转不可能"},
    {"name": "新能源", "category": "新能源", "status": "avoid",
     "rule": "trend_broken",
     "note": "同电气设备，走势不好"},
    {"name": "互联网服务", "category": "科技", "status": "avoid",
     "rule": "multi_month_adjustment",
     "note": "太弱，还需3-5个月调整，熬不到那个时候"},
    {"name": "电力行业", "category": "公用事业", "status": "avoid",
     "rule": "multi_month_consolidation",
     "note": "跌透但需横几个月，短期反弹会被压力位压回"},
    {"name": "仓储物流", "category": "物流", "status": "avoid",
     "rule": "avoid",
     "note": "垃圾板块，不参与"},
]

# 会议中提到的非板块指数/标的
MARKET_INDICES = [
    {"name": "上证指数", "code": "000001", "category": "大盘"},
    {"name": "科创50", "code": "000688", "category": "科技"},
    {"name": "创业板指", "code": "399006", "category": "成长"},
]

# 关键参考票
KEY_STOCKS = {
    "农林牧渔": {"code": "002714", "name": "牧原股份", "type": "机构票"},
    "家电行业": {"code": "000651", "name": "格力电器", "type": "机构票"},
    "农林牧渔_游资": {"code": "603336", "name": "宏辉果蔬", "type": "游资票"},
}

# ============================================================
# 数据获取（AKShare）
# ============================================================

REQUEST_INTERVAL_MIN = 0.8   # AKShare 最小请求间隔
REQUEST_INTERVAL_MAX = 2.0   # AKShare 最大请求间隔
BATCH_PAUSE_EVERY = 5        # 每 N 个请求暂停一次
BATCH_PAUSE_SECS = 3.0       # 批次间暂停秒数
LOOKBACK_DAYS = 120     # 历史K线回溯天数（需覆盖周线计算）
FUND_FLOW_DAYS = 20     # 资金流向历史回溯天数


def _safe_call(func, *args, **kwargs):
    """安全调用 AKShare，失败返回 None"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"AKShare call failed: {func.__name__} | {str(e)[:80]}")
        return None


def fetch_industry_board_overview() -> Optional[pd.DataFrame]:
    """获取所有行业板块当日涨跌排名（东方财富）"""
    import akshare as ak
    df = _safe_call(ak.stock_board_industry_name_em)
    if df is not None and len(df) > 0:
        cols_map = {
            "排名": "rank", "板块名称": "board_name", "板块代码": "board_code",
            "最新价": "price", "涨跌额": "change_amount", "涨跌幅": "change_pct",
            "总市值": "total_mv", "换手率": "turnover",
            "上涨家数": "up_count", "下跌家数": "down_count",
            "领涨股票": "lead_stock", "领涨股票-涨跌幅": "lead_change_pct",
        }
        df = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
    return df


def fetch_sector_fund_flow_5d() -> Optional[pd.DataFrame]:
    """获取5日板块资金流向排名"""
    import akshare as ak
    df = _safe_call(ak.stock_sector_fund_flow_rank, indicator="5日", sector_type="行业资金流")
    if df is not None and len(df) > 0:
        df = df.rename(columns={
            "名称": "board_name",
            "5日涨跌幅": "chg_5d",
            "5日主力净流入-净额": "main_inflow_5d",
            "5日主力净流入-净占比": "main_inflow_pct_5d",
            "5日超大单净流入-净额": "super_large_inflow_5d",
            "5日大单净流入-净额": "large_inflow_5d",
            "5日中单净流入-净额": "mid_inflow_5d",
            "5日小单净流入-净额": "small_inflow_5d",
            "5日主力净流入最大股": "top_inflow_stock_5d",
        })
    return df


def fetch_sector_fund_flow_10d() -> Optional[pd.DataFrame]:
    """获取10日板块资金流向排名"""
    import akshare as ak
    df = _safe_call(ak.stock_sector_fund_flow_rank, indicator="10日", sector_type="行业资金流")
    if df is not None and len(df) > 0:
        df = df.rename(columns={
            "名称": "board_name",
            "10日涨跌幅": "chg_10d",
            "10日主力净流入-净额": "main_inflow_10d",
            "10日主力净流入-净占比": "main_inflow_pct_10d",
            "10日超大单净流入-净额": "super_large_inflow_10d",
            "10日大单净流入-净额": "large_inflow_10d",
            "10日中单净流入-净额": "mid_inflow_10d",
            "10日小单净流入-净额": "small_inflow_10d",
            "10日主力净流入最大股": "top_inflow_stock_10d",
        })
    return df


def fetch_board_kline(symbol: str, lookback: int = LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """获取单个板块历史日K线"""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback + 10)).strftime("%Y%m%d")
    df = _safe_call(ak.stock_board_industry_hist_em, symbol=symbol, period="日k",
                    start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        cols_map = {
            "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
            "最低": "low", "涨跌幅": "change_pct", "涨跌额": "change_amount",
            "成交量": "volume", "成交额": "amount", "振幅": "amplitude", "换手率": "turnover",
        }
        df = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(lookback)
    return df


def fetch_index_kline(code: str, lookback: int = LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """获取指数历史日K线"""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback + 10)).strftime("%Y%m%d")
    df = _safe_call(ak.index_zh_a_hist, symbol=code, period="daily",
                    start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        cols_map = {
            "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
            "最低": "low", "成交量": "volume", "成交额": "amount",
            "涨跌幅": "change_pct", "涨跌额": "change_amount", "振幅": "amplitude",
        }
        df = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(lookback)
    return df


def fetch_sector_fund_flow_hist(symbol: str) -> Optional[pd.DataFrame]:
    """获取单个板块历史资金流向"""
    import akshare as ak
    df = _safe_call(ak.stock_sector_fund_flow_hist, symbol=symbol)
    if df is not None and len(df) > 0:
        cols_map = {
            "日期": "date", "主力净流入-净额": "main_inflow",
            "主力净流入-净占比": "main_inflow_pct",
            "超大单净流入-净额": "super_large_inflow",
            "超大单净流入-净占比": "super_large_inflow_pct",
            "大单净流入-净额": "large_inflow",
            "大单净流入-净占比": "large_inflow_pct",
            "中单净流入-净额": "mid_inflow",
            "中单净流入-净占比": "mid_inflow_pct",
            "小单净流入-净额": "small_inflow",
            "小单净流入-净占比": "small_inflow_pct",
        }
        df = df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
    return df


# ============================================================
# 技术指标计算
# ============================================================

def compute_ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _resample_weekly(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """日线 → 周线 resample（用于计算周线趋势）"""
    if df is None or "date" not in df.columns or len(df) < 10:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    w = df.resample("W").agg({
        "open": "first", "close": "last", "high": "max", "low": "min",
        "volume": "sum",
    }).dropna()
    return w


def _detect_golden_cross(ma_short: np.ndarray, ma_long: np.ndarray,
                         lookback: int = 5) -> Tuple[bool, int]:
    """检测金叉（短均线上穿长均线），返回(是否金叉, 天数前)"""
    for i in range(1, min(lookback + 1, len(ma_short) - 1)):
        s_now = ma_short[-i]
        s_prev = ma_short[-i-1]
        l_now = ma_long[-i]
        l_prev = ma_long[-i-1]
        if not (np.isnan(s_now) or np.isnan(s_prev) or np.isnan(l_now) or np.isnan(l_prev)):
            if s_prev <= l_prev and s_now > l_now:
                return True, i
    return False, 0


def _detect_dead_cross(ma_short: np.ndarray, ma_long: np.ndarray,
                       lookback: int = 5) -> Tuple[bool, int]:
    """检测死叉（短均线下穿长均线），返回(是否死叉, 天数前)"""
    for i in range(1, min(lookback + 1, len(ma_short) - 1)):
        s_now = ma_short[-i]
        s_prev = ma_short[-i-1]
        l_now = ma_long[-i]
        l_prev = ma_long[-i-1]
        if not (np.isnan(s_now) or np.isnan(s_prev) or np.isnan(l_now) or np.isnan(l_prev)):
            if s_prev >= l_prev and s_now < l_now:
                return True, i
    return False, 0


def calc_technical_indicators(df: pd.DataFrame) -> Dict:
    """从板块K线计算技术指标（含周线、金叉、均线压制、量价背离、缺口等）"""
    min_days = 60  # 至少需要60日数据做周线换算
    if df is None or len(df) < 20:
        return {"error": "数据不足", "available_days": len(df) if df is not None else 0}

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values if "volume" in df.columns else np.zeros(len(close))
    opens = df["open"].values if "open" in df.columns else np.zeros(len(close))

    ma5 = compute_ma(pd.Series(close), 5).values
    ma10 = compute_ma(pd.Series(close), 10).values
    ma20 = compute_ma(pd.Series(close), 20).values
    ma60 = compute_ma(pd.Series(close), 60).values

    last = -1
    close_now = close[last]
    ma5_now = ma5[last] if not np.isnan(ma5[last]) else close_now
    ma10_now = ma10[last] if not np.isnan(ma10[last]) else close_now
    ma20_now = ma20[last] if not np.isnan(ma20[last]) else close_now
    ma60_now = ma60[last] if not np.isnan(ma60[last]) else close_now

    # ---- 均线位置与方向 ----
    above_ma5 = close_now > ma5_now
    above_ma20 = close_now > ma20_now
    above_ma60 = close_now > ma60_now

    ma5_5d_ago = ma5[-6] if len(ma5) >= 6 and not np.isnan(ma5[-6]) else ma5_now
    ma5_direction = "up" if ma5_now > ma5_5d_ago else "down"
    ma20_10d_ago = ma20[-11] if len(ma20) >= 11 and not np.isnan(ma20[-11]) else ma20_now
    ma20_direction = "up" if ma20_now > ma20_10d_ago else "down"

    # ---- 均线排列与压制 ----
    mas = sorted([ma5_now, ma10_now, ma20_now, ma60_now])
    ma_range_pct = (mas[-1] - mas[0]) / close_now * 100 if close_now > 0 else 0
    if ma5_now > ma10_now > ma20_now:
        ma_alignment = "bullish"
    elif ma5_now < ma10_now < ma20_now:
        ma_alignment = "bearish"
    elif ma_range_pct < 3:
        ma_alignment = "converging"
    else:
        ma_alignment = "mixed"

    # 均线压制：价格在 MA20 和 MA60 下方，且均线在向下 → "上方有均线压制，不可能拉主升"
    suppressed_by_ma = (not above_ma20) and (not above_ma60) and ma20_direction == "down"

    # ---- 乖离率 ----
    bias_ma5 = (close_now - ma5_now) / ma5_now * 100 if ma5_now > 0 else 0
    bias_ma20 = (close_now - ma20_now) / ma20_now * 100 if ma20_now > 0 else 0

    # ---- 量价分析（当日 + 多日背离） ----
    vol_ma5 = np.mean(volume[-6:-1]) if len(volume) >= 6 else np.mean(volume)
    vol_ratio = volume[last] / vol_ma5 if vol_ma5 > 0 else 1.0
    if close_now > opens[last] and vol_ratio > 1.2:
        vol_price = "healthy_up"
    elif close_now < opens[last] and vol_ratio > 1.2:
        vol_price = "distribution"
    elif close_now > opens[last] and vol_ratio < 0.8:
        vol_price = "weak_up"
    elif close_now < opens[last] and vol_ratio < 0.8:
        vol_price = "weak_down"
    else:
        vol_price = "normal"

    # 持续量价背离：价格创新高但成交量持续萎缩 ≥3日
    persistent_divergence = False
    if len(close) >= 5 and len(volume) >= 5:
        div_count = 0
        for i in range(-1, -6, -1):
            if close[i] > close[i-1] and volume[i] < volume[i-1]:
                div_count += 1
            else:
                break
        persistent_divergence = div_count >= 3

    # ---- 底分型 / 顶分型 ----
    bottom_fractal = False
    top_fractal = False
    if len(low) >= 5:
        for i in range(-4, -1):
            if low[i] < low[i-1] and low[i] < low[i+1] and close[i] > opens[i]:
                if abs(i + 1) - 1 <= 3:
                    bottom_fractal = True
                    break
        for i in range(-4, -1):
            if high[i] > high[i-1] and high[i] > high[i+1] and close[i] < opens[i]:
                if abs(i + 1) - 1 <= 3:
                    top_fractal = True
                    break

    # ---- 连续阳线 ----
    consecutive_yang = 0
    for i in range(last, max(last-8, -len(close)), -1):
        if close[i] > opens[i]:
            consecutive_yang += 1
        else:
            break

    # ---- 跳空缺口 ----
    gap_up = False
    gap_down = False
    if len(low) >= 2:
        if low[-1] > high[-2]:
            gap_up = True
        if high[-1] < low[-2]:
            gap_down = True

    # ---- 长上影线检测（放量冲高回落） ----
    upper_shadow_ratio = 0
    candle_range = high[last] - low[last]
    if candle_range > 0:
        body_top = max(close[last], opens[last])
        upper_shadow_ratio = (high[last] - body_top) / candle_range
    long_upper_shadow = upper_shadow_ratio > 0.6 and vol_ratio > 1.0  # 长上影 + 放量

    # ---- 趋势线与趋势阶段 ----
    has_downtrend = False
    has_uptrend = False
    if len(high) >= 20:
        recent_highs = high[-20:]
        recent_lows = low[-20:]
        x = np.arange(len(recent_highs))
        hs, _ = np.polyfit(x, recent_highs, 1)
        ls, _ = np.polyfit(x, recent_lows, 1)
        has_downtrend = hs < 0
        has_uptrend = ls > 0

    # 趋势阶段识别：单边上涨 / 中枢震荡 / 筑顶 / 下降趋势
    if has_downtrend and not above_ma20:
        trend_phase = "downtrend"
    elif long_upper_shadow and persistent_divergence:
        trend_phase = "topping"         # 筑顶信号
    elif ma_alignment == "bullish" and not persistent_divergence:
        trend_phase = "rally"           # 单边上涨
    elif ma_alignment == "converging" or (above_ma5 and not above_ma20):
        trend_phase = "oscillation"     # 中枢震荡
    elif ma_alignment == "bearish" and not has_downtrend:
        trend_phase = "bottoming"       # 筑底
    else:
        trend_phase = "mixed"

    # ---- 金叉 / 死叉 ----
    golden_cross_5_20, gc_5_20_days = _detect_golden_cross(ma5, ma20, lookback=5)
    golden_cross_5_60, gc_5_60_days = _detect_golden_cross(ma5, ma60, lookback=5)
    golden_cross_10_20, gc_10_20_days = _detect_golden_cross(ma10, ma20, lookback=5)
    any_golden_cross = golden_cross_5_20 or golden_cross_5_60 or golden_cross_10_20

    dead_cross_5_20, dc_days = _detect_dead_cross(ma5, ma20, lookback=5)
    dead_cross_5_10, _ = _detect_dead_cross(ma5, ma10, lookback=3)

    # ---- 周线趋势（从日线 resample） ----
    weekly_trend = "unknown"
    weekly_ma4 = None
    wdf = _resample_weekly(df)
    if wdf is not None and len(wdf) >= 8:
        w_close = wdf["close"].values
        w_ma4 = compute_ma(pd.Series(w_close), 4).values   # 4周 ≈ 月线
        if len(w_ma4) >= 2 and not np.isnan(w_ma4[-1]):
            weekly_ma4 = round(float(w_ma4[-1]), 2)
            if w_close[-1] > w_ma4[-1] and w_ma4[-1] > w_ma4[-2]:
                weekly_trend = "bullish"
            elif w_close[-1] < w_ma4[-1]:
                weekly_trend = "bearish"
            else:
                weekly_trend = "neutral"

    # ---- 距60日低点涨幅（翻倍检测） ----
    low_60d = float(np.min(low[-60:])) if len(low) >= 60 else float(np.min(low))
    from_low_pct = (close_now / low_60d - 1) * 100 if low_60d > 0 else 0
    is_doubled = from_low_pct > 80  # 接近翻倍（留20%裕度）

    # ---- 支撑/压力位 ----
    support_20d = float(np.min(low[-20:]))
    resistance_20d = float(np.max(high[-20:]))
    resistance_60d = float(np.max(high[-60:])) if len(high) >= 60 else resistance_20d

    at_resistance = close_now >= resistance_20d * 0.97  # 距压力位 ≤3%
    at_support = close_now <= support_20d * 1.03       # 距支撑位 ≤3%

    # ---- 近期涨跌幅 ----
    chg_5d = (close_now / close[-6] - 1) * 100 if len(close) >= 6 else 0
    chg_20d = (close_now / close[-21] - 1) * 100 if len(close) >= 21 else 0

    return {
        "close": round(float(close_now), 2),
        "ma5": round(float(ma5_now), 2),
        "ma10": round(float(ma10_now), 2),
        "ma20": round(float(ma20_now), 2),
        "ma60": round(float(ma60_now), 2),
        "weekly_ma4": weekly_ma4,
        "above_ma5": above_ma5,
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "ma5_direction": ma5_direction,
        "ma20_direction": ma20_direction,
        "ma_alignment": ma_alignment,
        "suppressed_by_ma": suppressed_by_ma,
        "bias_ma5_pct": round(float(bias_ma5), 1),
        "bias_ma20_pct": round(float(bias_ma20), 1),
        "vol_ratio": round(float(vol_ratio), 1),
        "vol_price": vol_price,
        "persistent_divergence": persistent_divergence,
        "bottom_fractal": bottom_fractal,
        "top_fractal": top_fractal,
        "consecutive_yang": consecutive_yang,
        "gap_up": gap_up,
        "gap_down": gap_down,
        "long_upper_shadow": long_upper_shadow,
        "has_downtrend": has_downtrend,
        "has_uptrend": has_uptrend,
        "trend_phase": trend_phase,
        "weekly_trend": weekly_trend,
        "any_golden_cross": any_golden_cross,
        "golden_cross_5_20": golden_cross_5_20,
        "golden_cross_5_60": golden_cross_5_60,
        "golden_cross_10_20": golden_cross_10_20,
        "dead_cross_5_20": dead_cross_5_20,
        "dead_cross_5_10": dead_cross_5_10,
        "at_resistance": at_resistance,
        "at_support": at_support,
        "from_low_60d_pct": round(float(from_low_pct), 1),
        "is_doubled": is_doubled,
        "chg_5d_pct": round(float(chg_5d), 1),
        "chg_20d_pct": round(float(chg_20d), 1),
        "support_20d": support_20d,
        "resistance_20d": resistance_20d,
        "resistance_60d": resistance_60d,
    }


# ============================================================
# 信号生成
# ============================================================

def generate_signal(rule_cfg: Dict, tech: Dict, fund: Dict, board_today: Optional[Dict]) -> Dict:
    """对照会议规则 + 全量技术指标生成板块信号"""
    status = rule_cfg["status"]
    rule = rule_cfg["rule"]

    signal = {"type": "unknown", "label": "❓ 未知", "detail": ""}
    risk_flags = []

    # 数据不可用 → 降级为纯规则判断
    if tech.get("error"):
        no_data_msg = f"数据不可用({tech.get('available_days',0)}日)，基于会议规则判断"
        if status == "entry":
            return {"type": "entry_no_data", "label": "🟡 等数据",
                    "detail": f"{no_data_msg}：{rule_cfg['note'][:30]}", "risk_flags": [no_data_msg]}
        elif status == "avoid":
            return {"type": "avoid_no_data", "label": "🔴 回避",
                    "detail": f"{no_data_msg}，维持回避", "risk_flags": []}
        elif status == "watch":
            return {"type": "watch_no_data", "label": "🟡 观察",
                    "detail": f"{no_data_msg}，等待确认", "risk_flags": []}
        else:
            return {"type": "hold_no_data", "label": "🟡 谨慎",
                    "detail": f"{no_data_msg}", "risk_flags": []}

    # ========== 公共风险检测（所有板块共用） ==========
    if tech.get("vol_price") == "distribution":
        risk_flags.append("放量下跌，可能有资金出逃")
    if tech.get("vol_price") == "weak_up" and tech.get("bias_ma5_pct", 0) > 5:
        risk_flags.append("缩量上涨+高乖离，量价背离风险")
    if tech.get("persistent_divergence"):
        risk_flags.append("持续量价背离（≥3日）：若持续，可能即将出现大阴")
    if tech.get("bias_ma5_pct", 0) > 8:
        risk_flags.append(f"乖离率{tech.get('bias_ma5_pct')}%偏高，短期有回踩风险")
    if tech.get("long_upper_shadow"):
        risk_flags.append("放量长上影：高位出逃信号，需降仓位")
    if tech.get("dead_cross_5_20"):
        risk_flags.append("5日线死叉20日线：短期趋势转弱")
    if tech.get("top_fractal") and tech.get("vol_price") == "distribution":
        risk_flags.append("顶分型+放量下跌：可能阶段性见顶")

    # ========== 分类信号 ==========
    if status == "entry":
        signal = _entry_signal(rule_cfg, tech, fund, board_today, risk_flags)
    elif status == "hold":
        signal = _hold_signal(rule_cfg, tech, fund, board_today, risk_flags)
    elif status == "watch":
        signal = _watch_signal(rule_cfg, tech, fund, board_today, risk_flags)
    elif status == "avoid":
        signal = _avoid_signal(rule_cfg, tech, fund, board_today, risk_flags)

    return {"type": signal["type"], "label": signal["label"], "detail": signal["detail"],
            "risk_flags": risk_flags}


def _entry_signal(rule_cfg: Dict, tech: Dict, fund: Dict, board_today: Optional[Dict],
                  risk_flags: List[str]) -> Dict:
    """ENTRY 板块：可低吸入场"""
    rule = rule_cfg["rule"]
    name = rule_cfg["name"]
    chg_today = board_today.get("change_pct", 0) if board_today else 0

    if rule == "dip_buy_oscillation":
        # 机器人方向：震荡中枢低吸
        if tech.get("trend_phase") == "rally" and tech.get("above_ma5") and tech.get("weekly_trend") == "bullish":
            return {"type": "hold_uptrend", "label": "🟢 趋势向上",
                    "detail": "已出中枢进入单边，持仓持有不追，等中枢回踩再入"}
        if tech.get("trend_phase") in ("oscillation", "bottoming") and tech.get("at_support"):
            if fund.get("main_inflow_5d", 0) > 0:
                return {"type": "entry_dip", "label": "✅ 可低吸",
                        "detail": "震荡中枢下沿+资金流入+近支撑位，逢低布局"}
            else:
                return {"type": "entry_dip", "label": "🟡 等放量",
                        "detail": "震荡中枢下沿但资金未流入，等放量确认再入"}
        if tech.get("any_golden_cross") and tech.get("vol_price") == "healthy_up":
            return {"type": "entry_confirm", "label": "✅ 金叉确认",
                    "detail": "均线金叉+放量上涨，信号增强，可入场"}
        if tech.get("suppressed_by_ma"):
            risk_flags.append("上方均线压制未消除，短期仍以震荡为主")
            return {"type": "watch_wait", "label": "🟡 观望",
                    "detail": "均线压制未消除，等均线从粘合到金叉突破"}
        if tech.get("bias_ma5_pct", 0) > 7:
            return {"type": "entry_wait", "label": "🟡 等回踩",
                    "detail": "乖离偏高，不建议追，等回踩5日线"}
        if tech.get("vol_price") == "healthy_up" and tech.get("bottom_fractal"):
            return {"type": "entry_pullback", "label": "✅ 底部启动",
                    "detail": "底分型确认+放量上涨，可入场"}
        return {"type": "watch_wait", "label": "🟡 观望",
                "detail": "等待震荡中枢下沿放量企稳信号"}

    elif rule == "bottom_fishing_pullback":
        # 农林牧渔：连续3天底部阳线→等回踩买入，机构票当天涨超3%不追
        if tech.get("consecutive_yang", 0) >= 3 and tech.get("above_ma5"):
            if abs(chg_today) > 3:
                return {"type": "entry_wait", "label": "🟡 等回踩",
                        "detail": f"今日涨幅{chg_today:+.1f}%，超3%不追，等回踩5日线"}
            if tech.get("bias_ma5_pct", 0) > 3:
                return {"type": "entry_pullback", "label": "🟡 等回踩",
                        "detail": "连阳已拉但乖离偏高，等回踩5日线"}
            return {"type": "entry_pullback", "label": "✅ 可入场",
                    "detail": "底部连续阳线+站上5日线+乖离适中，回踩买入"}
        if tech.get("bottom_fractal") and tech.get("any_golden_cross"):
            return {"type": "entry_confirm", "label": "✅ 底分+金叉",
                    "detail": "底分型+均线金叉双重确认，可入场"}
        if tech.get("bottom_fractal"):
            return {"type": "entry_pullback", "label": "✅ 底分确认",
                    "detail": "底分型出现，等放量阳线确认后入场"}
        return {"type": "watch_wait", "label": "🟡 等待",
                "detail": "底部尚未确认，等连续阳线+放量信号"}

    elif rule == "double_bottom":
        # 船舶：做双底，拉上去就好
        # 双底特征：两次探底价格接近（10%内），时间间隔2-6周，第二次缩量企稳
        if tech.get("at_support") and tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up":
            return {"type": "entry_confirm", "label": "✅ 双底突破",
                    "detail": "支撑位+底分型+放量，双底形态可能完成"}
        if tech.get("above_ma5") and fund.get("main_inflow_5d", 0) > 0:
            return {"type": "entry_dip", "label": "✅ 可入场",
                    "detail": "站上5日线+资金流入，逢低布局"}
        if tech.get("at_support"):
            return {"type": "entry_dip", "label": "🟡 近支撑",
                    "detail": "接近支撑位，观察能否形成双底"}
        if tech.get("suppressed_by_ma"):
            return {"type": "watch_wait", "label": "🟡 等突破",
                    "detail": "均线压制中，等双底颈线突破确认"}
        return {"type": "watch_wait", "label": "🟡 等待", "detail": "等双底形态确认"}

    elif rule == "support_rebound":
        # 煤炭：跌到位到支撑，可加仓等反弹降成本
        if tech.get("at_support") and tech.get("bottom_fractal"):
            return {"type": "entry_dip", "label": "✅ 支撑+底分",
                    "detail": "跌到支撑位+底分型，可加仓等反弹降成本"}
        if tech.get("at_support"):
            return {"type": "entry_dip", "label": "🟡 近支撑",
                    "detail": "接近支撑位，观察能否企稳反弹"}
        if tech.get("bias_ma5_pct", 0) < -5:
            return {"type": "entry_dip", "label": "🟡 超跌",
                    "detail": "短线超跌，可左侧轻仓试探"}
        if tech.get("suppressed_by_ma"):
            risk_flags.append("均线压制中，反弹空间有限")
        return {"type": "watch_wait", "label": "🟡 等待",
                "detail": "等跌到支撑位再考虑加仓"}

    elif rule == "bottom_fishing":
        # 家电：主力抄底，机构票买超跌不追涨
        if abs(chg_today) > 3:
            return {"type": "entry_wait", "label": "🟡 不追",
                    "detail": f"今日涨{chg_today:+.1f}%，机构票超3%不追，等回踩"}
        if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up":
            return {"type": "entry_confirm", "label": "✅ 底部确认",
                    "detail": "底分型+放量，机构票可逢低布局"}
        if tech.get("above_ma5") and fund.get("main_inflow_5d", 0) > 0:
            return {"type": "entry_dip", "label": "✅ 可入场",
                    "detail": "站上5日线+资金流入，机构票低吸机会"}
        if tech.get("at_support"):
            return {"type": "entry_dip", "label": "🟡 近支撑",
                    "detail": "接近支撑位，等企稳信号"}
        return {"type": "watch_wait", "label": "🟡 等待",
                "detail": "等底部放量确认或回踩支撑位"}

    return {"type": "watch_wait", "label": "🟡 等待", "detail": ""}


def _hold_signal(rule_cfg: Dict, tech: Dict, fund: Dict, board_today: Optional[Dict],
                  risk_flags: List[str]) -> Dict:
    """HOLD 板块：趋势完好但不可开新仓"""
    name = rule_cfg["name"]
    rule = rule_cfg["rule"]

    # 半导体特殊逻辑：选底部未翻倍个股短线参与
    if rule == "uptrend_selective":
        if tech.get("is_doubled"):
            risk_flags.append("板块已整体翻倍，选股需谨慎，只选底部未大涨个股")
        if tech.get("persistent_divergence"):
            risk_flags.append("持续量价背离，可能见顶，不宜追高")
        if tech.get("trend_phase") == "topping":
            return {"type": "hold_caution", "label": "🟡 警惕筑顶",
                    "detail": "出现筑顶信号，只出不进"}

    # 通用 HOLD 逻辑
    if tech.get("dead_cross_5_20"):
        if tech.get("vol_price") == "distribution":
            return {"type": "exit_confirm", "label": "🔴 离场信号",
                    "detail": "死叉+放量下跌，建议减仓或离场"}
        return {"type": "hold_warning", "label": "⚠️ 趋势转弱",
                "detail": "死叉出现但未放量，减仓观察"}
    if tech.get("above_ma5") and tech.get("ma5_direction") == "up":
        if tech.get("persistent_divergence"):
            risk_flags.append("持续量价背离：若不放量修复，可能见顶")
            return {"type": "hold_caution", "label": "🟡 持有但警惕",
                    "detail": "趋势向上但持续缩量，防见顶"}
        if tech.get("vol_price") == "weak_up":
            return {"type": "hold_caution", "label": "🟡 警惕缩量",
                    "detail": "趋势向上但缩量，若持续缩量可能见顶"}
        return {"type": "hold_healthy", "label": "🟢 趋势完好",
                "detail": "站上5日线+均线向上，持有不动"}
    if tech.get("above_ma5") and tech.get("ma5_direction") == "down":
        return {"type": "hold_warning", "label": "⚠️ 趋势转弱",
                "detail": "仍站5日线但均线走平/向下，减仓观望"}
    if not tech.get("above_ma5") and tech.get("vol_price") == "distribution":
        return {"type": "exit_confirm", "label": "🔴 离场信号",
                "detail": "跌破5日线+放量下跌，减仓或清仓"}
    return {"type": "hold_caution", "label": "🟡 谨慎持有",
            "detail": "跌破5日线但缩量，观察能否收回"}


def _watch_signal(rule_cfg: Dict, tech: Dict, fund: Dict, board_today: Optional[Dict],
                  risk_flags: List[str]) -> Dict:
    """WATCH 板块：观察等确认"""
    name = rule_cfg["name"]
    rule = rule_cfg["rule"]

    # 证券特殊逻辑：5周线低吸
    if "证券" in name or rule == "5week_ma_dip":
        if tech.get("at_support") and tech.get("bottom_fractal"):
            return {"type": "watch_upgrade", "label": "⬆️ 接近买点",
                    "detail": "接近支撑+底分型，可在5周线附近低吸"}
        if tech.get("bias_ma5_pct", 0) < -3:
            return {"type": "watch_upgrade", "label": "⬆️ 调整充分",
                    "detail": "短线超跌，可用5周线战法试探"}
        if tech.get("trend_phase") == "oscillation":
            return {"type": "watch_hold", "label": "🟡 调整中",
                    "detail": "与科技跷跷板，等科技休息时证券可能有机会"}

    # 保险：横住了，等方向选择
    if rule == "consolidating":
        if tech.get("ma_alignment") == "converging" and tech.get("any_golden_cross"):
            return {"type": "watch_upgrade", "label": "⬆️ 可能突破",
                    "detail": "均线粘合+金叉，横盘后可能选择向上"}
        if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up":
            return {"type": "watch_upgrade", "label": "⬆️ 底部异动",
                    "detail": "横盘中出现底分+放量，关注方向选择"}
        if tech.get("ma_alignment") == "converging":
            return {"type": "watch_hold", "label": "🟡 横盘整理",
                    "detail": "均线粘合横盘，等方向选择后再行动"}
        return {"type": "watch_hold", "label": "🟡 横盘观察",
                "detail": "持续横盘整理，等待放量突破方向"}

    # 医疗/医药：打到压力位看能否放量突破
    if rule == "resistance_test":
        if tech.get("at_resistance") and tech.get("vol_price") == "healthy_up":
            return {"type": "watch_upgrade", "label": "⬆️ 压力突破中",
                    "detail": "打到压力位但放量上涨，若持续可能升级为入场"}
        if tech.get("at_resistance") and tech.get("vol_price") != "healthy_up":
            return {"type": "watch_wait", "label": "🟡 压力受阻",
                    "detail": "打到压力位但量能不足，可能回踩，等突破确认"}
        if tech.get("long_upper_shadow"):
            return {"type": "watch_wait", "label": "🟡 冲高回落",
                    "detail": "长上影线，短期可能回踩，等震一震横一横"}

    # 食品饮料/日用化工：需要阳线确认底分
    if rule in ("bottom_confirmation", "yang_confirmation"):
        if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up" and tech.get("consecutive_yang", 0) >= 2:
            return {"type": "watch_upgrade", "label": "⬆️ 可能升级",
                    "detail": "底分+放量阳线+连阳，确认见底信号，可升级为入场"}
        if tech.get("consecutive_yang", 0) >= 2:
            return {"type": "watch_positive", "label": "🟢 积极信号",
                    "detail": "连续阳线，若明日继续放量阳线则确认底分"}

    # 通用 WATCH 逻辑
    if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up" and tech.get("any_golden_cross"):
        return {"type": "watch_upgrade", "label": "⬆️ 强烈升级信号",
                "detail": "底分+放量+金叉三重确认，建议升级为入场"}
    if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up":
        return {"type": "watch_upgrade", "label": "⬆️ 可能升级",
                "detail": "底分确认+放量上涨，若持续可能升级为入场"}
    if tech.get("consecutive_yang", 0) >= 2 and tech.get("above_ma5"):
        return {"type": "watch_positive", "label": "🟢 积极信号",
                "detail": "连阳+站上5日线，继续观察确认"}
    if tech.get("above_ma5"):
        return {"type": "watch_hold", "label": "🟡 观察中",
                "detail": "站上5日线但确认信号不足，继续等"}
    if tech.get("suppressed_by_ma"):
        return {"type": "watch_wait", "label": "🟡 均线压制",
                "detail": "上方均线压制，需时间消化，短期不会大幅拉升"}
    return {"type": "watch_wait", "label": "🟡 等待",
            "detail": "尚未满足确认条件，继续等待"}


def _avoid_signal(rule_cfg: Dict, tech: Dict, fund: Dict, board_today: Optional[Dict],
                  risk_flags: List[str]) -> Dict:
    """AVOID 板块：回避"""
    rule = rule_cfg["rule"]

    # 下降趋势线压制的板块（有色金属）
    if rule == "downtrend_line":
        if not tech.get("has_downtrend") and tech.get("above_ma20"):
            return {"type": "avoid_surprise", "label": "⚠️ 趋势可能反转",
                    "detail": "下降趋势线被突破+站上20日线，关注是否假突破"}
        if tech.get("has_downtrend"):
            return {"type": "avoid_confirm", "label": "🔴 继续回避",
                    "detail": "下降趋势线压制确认，未突破前不参与"}
        return {"type": "avoid_confirm", "label": "🔴 继续回避",
                "detail": "趋势未改，维持回避判断"}

    # 文教休闲/旅游：弱势板块刚止跌，下影线只是止跌信号
    if rule == "just_stopped_falling":
        if tech.get("any_golden_cross") and tech.get("above_ma20") and tech.get("vol_price") == "healthy_up":
            return {"type": "avoid_surprise", "label": "⚠️ 可能反转",
                    "detail": "刚止跌即出现金叉+放量站上月线，关注是否趋势反转"}
        if tech.get("bottom_fractal") and tech.get("consecutive_yang", 0) >= 3:
            return {"type": "avoid_surprise", "label": "⚠️ 持续走强",
                    "detail": "底分+连阳，止跌信号在增强，注意跟踪"}
        if tech.get("bottom_fractal"):
            return {"type": "avoid_confirm", "label": "🔴 刚止跌",
                    "detail": "下影线止跌信号出现，但还需更多确认，暂不入场"}
        return {"type": "avoid_confirm", "label": "🔴 继续回避",
                "detail": "弱势板块，止跌≠反转，需要时间和量能确认"}

    # 酿酒：大级别支撑但需长时间横盘震荡
    if rule == "prolonged_consolidation":
        if tech.get("ma_alignment") == "converging" and tech.get("any_golden_cross"):
            return {"type": "avoid_surprise", "label": "⚠️ 横盘末端",
                    "detail": "均线粘合+金叉，横盘可能接近尾声，关注突破方向"}
        if tech.get("at_support") and tech.get("bottom_fractal"):
            return {"type": "avoid_surprise", "label": "⚠️ 支撑企稳",
                    "detail": "大级别支撑位+底分，可能正在筑底"}
        if tech.get("ma_alignment") == "converging":
            return {"type": "avoid_confirm", "label": "🔴 横盘中",
                    "detail": "均线粘合横盘，横多久不确定，继续回避"}
        return {"type": "avoid_confirm", "label": "🔴 继续回避",
                "detail": "大级别支撑但需长时间震荡，暂时不是机会"}

    # 电气设备/新能源/电力：走势不好，缓慢筑底
    if rule in ("trend_broken", "multi_month_consolidation"):
        if tech.get("any_golden_cross") and tech.get("above_ma20") and tech.get("weekly_trend") == "bullish":
            return {"type": "avoid_surprise", "label": "⚠️ 可能反转",
                    "detail": "金叉+站上月线+周线转多，趋势可能正在反转"}
        if tech.get("bottom_fractal") and tech.get("vol_price") == "healthy_up":
            return {"type": "avoid_surprise", "label": "⚠️ 底部异动",
                    "detail": "底分+放量，开始筑底但立刻反转不太可能，持续跟踪"}
        if tech.get("bias_ma5_pct", 0) < -8:
            return {"type": "avoid_confirm", "label": "🔴 弱势超跌",
                    "detail": "跌幅较大但无反转信号，反弹后仍可能继续探底"}

    # 互联网服务：大周期二浪回踩，3-5个月调整
    if rule == "multi_month_adjustment":
        if tech.get("ma_alignment") == "converging" and tech.get("any_golden_cross"):
            return {"type": "avoid_surprise", "label": "⚠️ 提前见底？",
                    "detail": "均线粘合+金叉，可能提前结束调整，跟踪"}
        if tech.get("suppressed_by_ma"):
            return {"type": "avoid_confirm", "label": "🔴 均线压制",
                    "detail": "均线仍在压制，调整时间未到，继续等"}

    # 通用 AVOID 确认
    if tech.get("vol_price") == "healthy_up" and fund.get("main_inflow_5d", 0) > 0 and tech.get("any_golden_cross"):
        return {"type": "avoid_surprise", "label": "⚠️ 强烈异动",
                "detail": "回避板块出现放量+金叉+资金流入，密切跟踪是否趋势反转"}
    if tech.get("has_downtrend") or not tech.get("above_ma5"):
        return {"type": "avoid_confirm", "label": "🔴 继续回避",
                "detail": "符合预期：下降趋势/均线压制，不参与"}
    return {"type": "avoid_confirm", "label": "🔴 继续回避",
            "detail": "趋势未改，维持回避判断"}


# ============================================================
# 资金流向分析
# ============================================================

def analyze_fund_flow(board_name: str,
                       fund_5d_df: Optional[pd.DataFrame],
                       fund_10d_df: Optional[pd.DataFrame],
                       fund_hist_df: Optional[pd.DataFrame]) -> Dict:
    """汇总板块资金流向信号"""
    result = {"main_inflow_5d": 0, "main_inflow_pct_5d": 0,
              "main_inflow_10d": 0, "main_inflow_pct_10d": 0,
              "signal": "neutral"}

    if fund_5d_df is not None:
        row = fund_5d_df[fund_5d_df["board_name"] == board_name]
        if len(row) > 0:
            result["main_inflow_5d"] = float(row.iloc[0].get("main_inflow_5d", 0) or 0)
            result["main_inflow_pct_5d"] = float(row.iloc[0].get("main_inflow_pct_5d", 0) or 0)
            result["top_inflow_stock_5d"] = str(row.iloc[0].get("top_inflow_stock_5d", "") or "")

    if fund_10d_df is not None:
        row = fund_10d_df[fund_10d_df["board_name"] == board_name]
        if len(row) > 0:
            result["main_inflow_10d"] = float(row.iloc[0].get("main_inflow_10d", 0) or 0)
            result["main_inflow_pct_10d"] = float(row.iloc[0].get("main_inflow_pct_10d", 0) or 0)

    # 综合信号
    inflow_5d = result["main_inflow_5d"]
    inflow_10d = result["main_inflow_10d"]

    if inflow_5d > 0 and inflow_10d > 0:
        result["signal"] = "accumulation"    # 持续流入
    elif inflow_5d > 0 and inflow_10d < 0:
        result["signal"] = "turning_bullish" # 近期转多
    elif inflow_5d < 0 and inflow_10d > 0:
        result["signal"] = "turning_bearish" # 近期转空
    elif inflow_5d < 0 and inflow_10d < 0:
        result["signal"] = "distribution"    # 持续流出
    else:
        result["signal"] = "neutral"

    # 历史资金流向趋势（最近10天vs前10天）
    if fund_hist_df is not None and len(fund_hist_df) >= 10:
        recent_10 = fund_hist_df.tail(10)["main_inflow"].sum()
        prev_10 = fund_hist_df.iloc[-20:-10]["main_inflow"].sum() if len(fund_hist_df) >= 20 else 0
        result["recent_10d_sum"] = float(recent_10)
        result["prev_10d_sum"] = float(prev_10)
        if prev_10 < 0 and recent_10 > 0:
            result["flow_anomaly"] = "资金面反转：此前持续流出，近期转为流入"
        elif prev_10 > 0 and recent_10 < 0:
            result["flow_anomaly"] = "资金面反转：此前持续流入，近期转为流出"

    return result


# ============================================================
# 主函数：拉取所有板块数据并生成监测报告
# ============================================================

def fetch_sector_monitor_data() -> Dict:
    """主入口：拉取板块数据 + 计算指标 + 生成信号"""
    import akshare as ak  # noqa: F811

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "market_overview": {},
        "sectors": [],
        "flow_anomalies": [],
        "summary": {"entry": [], "hold": [], "watch": [], "avoid": [], "entry_count": 0,
                    "hold_count": 0, "watch_count": 0, "avoid_count": 0},
    }

    logger.info("📊 板块监测：开始拉取数据...")

    # 1. 获取全行业板块概况
    board_df = fetch_industry_board_overview()
    if board_df is not None:
        logger.info(f"   行业板块概况: {len(board_df)} 个板块")
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 2. 获取资金流向
    fund_5d_df = fetch_sector_fund_flow_5d()
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))
    fund_10d_df = fetch_sector_fund_flow_10d()
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 3. 获取三大指数K线
    for idx_cfg in MARKET_INDICES:
        df_idx = fetch_index_kline(idx_cfg["code"])
        if df_idx is not None and len(df_idx) > 5:
            tech = calc_technical_indicators(df_idx)
            result["market_overview"][idx_cfg["name"]] = {
                "close": tech.get("close"), "ma5": tech.get("ma5"),
                "above_ma5": tech.get("above_ma5"), "bias_pct": tech.get("bias_ma5_pct"),
                "ma5_direction": tech.get("ma5_direction"),
                "vol_price": tech.get("vol_price"),
            }
        time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 4. 逐个板块拉K线+计算信号
    total = len(SECTOR_RULES)
    for i, rule_cfg in enumerate(SECTOR_RULES):
        board_name = rule_cfg["name"]
        logger.info(f"   [{i+1}/{total}] {board_name} ...")

        # 分批停顿：每 BATCH_PAUSE_EVERY 个请求暂停一下
        if i > 0 and i % BATCH_PAUSE_EVERY == 0:
            pause = BATCH_PAUSE_SECS + random.uniform(0, 2)
            logger.info(f"   ⏸ 批次暂停 {pause:.1f}s ...")
            time.sleep(pause)

        # 4a. K线和技术指标
        df_kl = fetch_board_kline(board_name)
        time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

        tech = calc_technical_indicators(df_kl) if df_kl is not None else {}

        # 4b. 资金流向
        fund = analyze_fund_flow(board_name, fund_5d_df, fund_10d_df, None)

        # 4c. 板块当日行情（从board_df取）
        board_today = {}
        if board_df is not None:
            row = board_df[board_df["board_name"] == board_name]
            if len(row) > 0:
                board_today = {
                    "change_pct": float(row.iloc[0].get("change_pct", 0) or 0),
                    "up_count": int(row.iloc[0].get("up_count", 0) or 0),
                    "down_count": int(row.iloc[0].get("down_count", 0) or 0),
                    "lead_stock": str(row.iloc[0].get("lead_stock", "") or ""),
                }

        # 4d. 生成信号
        signal = generate_signal(rule_cfg, tech, fund, board_today)

        sector_entry = {
            "name": board_name,
            "category": rule_cfg["category"],
            "meeting_status": rule_cfg["status"],
            "meeting_rule": rule_cfg["rule"],
            "meeting_note": rule_cfg["note"],
            "today": board_today,
            "technical": tech,
            "fund_flow": {k: v for k, v in fund.items()
                          if k in ("main_inflow_5d", "main_inflow_pct_5d",
                                   "main_inflow_10d", "main_inflow_pct_10d", "signal")},
            "signal": signal,
        }

        # 资金异动检测
        if fund.get("flow_anomaly"):
            result["flow_anomalies"].append({
                "sector": board_name, "anomaly": fund["flow_anomaly"],
                "attention": "⚠️ 关注趋势是否发生实质变化",
            })

        result["sectors"].append(sector_entry)
        result["summary"][rule_cfg["status"]].append(board_name)

    # 汇总统计
    result["summary"]["entry_count"] = len(result["summary"]["entry"])
    result["summary"]["hold_count"] = len(result["summary"]["hold"])
    result["summary"]["watch_count"] = len(result["summary"]["watch"])
    result["summary"]["avoid_count"] = len(result["summary"]["avoid"])

    logger.info("📊 板块监测：完成")
    return result


# ============================================================
# 格式化输出（注入 AI prompt）
# ============================================================

def format_sector_for_prompt(data: Dict) -> str:
    """将监测数据格式化为适合注入 AI prompt 的文本"""
    if not data or not data.get("sectors"):
        return ""

    lines = []

    # 市场概况
    mo = data.get("market_overview", {})
    if mo:
        lines.append("### 主要指数")
        for idx_name, idx_data in mo.items():
            ma5_str = f"MA5={idx_data.get('ma5', '?')}"
            pos_str = "站上" if idx_data.get("above_ma5") else "跌破"
            bias = idx_data.get("bias_pct", 0)
            lines.append(f"- {idx_name}: {idx_data.get('close')} ({pos_str}{ma5_str}, 乖离{bias}%)")

    # 按状态分组输出
    lines.append("\n### 📥 ENTRY（可入场）")
    for s in data["sectors"]:
        if s["meeting_status"] != "entry":
            continue
        _append_sector_line(lines, s)

    lines.append("\n### 🟢 HOLD（持有不追）")
    for s in data["sectors"]:
        if s["meeting_status"] != "hold":
            continue
        _append_sector_line(lines, s)

    lines.append("\n### 🟡 WATCH（观察等确认）")
    for s in data["sectors"]:
        if s["meeting_status"] != "watch":
            continue
        _append_sector_line(lines, s)

    lines.append("\n### 🔴 AVOID（回避）")
    for s in data["sectors"]:
        if s["meeting_status"] != "avoid":
            continue
        _append_sector_line(lines, s)

    # 资金异动
    anomalies = data.get("flow_anomalies", [])
    if anomalies:
        lines.append("\n### ⚠️ 资金异动")
        for a in anomalies:
            lines.append(f"- **{a['sector']}**: {a['anomaly']}")

    # 汇总
    summary = data.get("summary", {})
    lines.append(f"\n### 📋 今日汇总: "
                 f"入场{summary.get('entry_count',0)} | "
                 f"持有{summary.get('hold_count',0)} | "
                 f"观察{summary.get('watch_count',0)} | "
                 f"回避{summary.get('avoid_count',0)}")

    return "\n".join(lines)


def _append_sector_line(lines: List[str], s: Dict):
    tech = s.get("technical", {})
    fund = s.get("fund_flow", {})
    sig = s.get("signal", {})
    today = s.get("today", {})

    # 基本信号
    detail = f"{sig.get('label', '?')} {sig.get('detail', '')}"

    # 技术面摘要（含新指标）
    tech_bits = []
    if tech:
        # 趋势阶段
        phase_label = {"rally": "单边涨", "oscillation": "中枢震", "topping": "筑顶",
                       "bottoming": "筑底", "downtrend": "下降", "mixed": "混合"}.get(
                           tech.get("trend_phase", ""), "")
        if phase_label:
            tech_bits.append(f"阶段:{phase_label}")

        # 均线位置
        ma_pos = "↑5" if tech.get("above_ma5") else "↓5"
        ma_pos += "↑20" if tech.get("above_ma20") else "↓20"
        vol_label = {"healthy_up": "放量涨", "distribution": "放量跌", "weak_up": "缩量涨",
                     "weak_down": "缩量跌", "normal": "量平"}.get(tech.get("vol_price", ""), "")
        tech_bits.append(f"{ma_pos} {vol_label}")

        # 周线
        wt = tech.get("weekly_trend", "unknown")
        wt_label = {"bullish": "周↑", "bearish": "周↓", "neutral": "周→"}.get(wt, "")
        if wt_label:
            tech_bits.append(wt_label)

        # 关键信号
        if tech.get("bottom_fractal"):
            tech_bits.append("底分")
        if tech.get("top_fractal"):
            tech_bits.append("顶分")
        if tech.get("any_golden_cross"):
            cross_labels = []
            if tech.get("golden_cross_5_20"): cross_labels.append("5×20")
            if tech.get("golden_cross_5_60"): cross_labels.append("5×60")
            tech_bits.append(f"金叉({'/'.join(cross_labels)})")
        if tech.get("dead_cross_5_20"):
            tech_bits.append("死叉(5×20)")
        if tech.get("gap_up"):
            tech_bits.append("跳空↑")
        if tech.get("gap_down"):
            tech_bits.append("跳空↓")
        if tech.get("suppressed_by_ma"):
            tech_bits.append("均线压制")
        if tech.get("persistent_divergence"):
            tech_bits.append("持续背离!")
        if tech.get("long_upper_shadow"):
            tech_bits.append("长上影")
        if tech.get("is_doubled"):
            tech_bits.append("已翻倍⚠")
        if tech.get("at_resistance"):
            tech_bits.append(f"近压力{tech.get('resistance_20d', '?')}")
        if tech.get("at_support"):
            tech_bits.append(f"近支撑{tech.get('support_20d', '?')}")
        if tech.get("consecutive_yang", 0) >= 2:
            tech_bits.append(f"{tech['consecutive_yang']}连阳")
        tech_bits.append(f"乖离{tech.get('bias_ma5_pct', '?')}%")

    # 资金面摘要
    fund_bits = []
    if fund:
        inflow_5d = fund.get("main_inflow_5d", 0)
        if inflow_5d > 0:
            fund_bits.append(f"5日流入{inflow_5d/1e8:.1f}亿")
        elif inflow_5d < 0:
            fund_bits.append(f"5日流出{abs(inflow_5d)/1e8:.1f}亿")
        fs = fund.get("signal", "")
        fs_label = {"accumulation": "持续流入", "turning_bullish": "转流入",
                    "turning_bearish": "转流出", "distribution": "持续流出"}.get(fs, "")
        if fs_label:
            fund_bits.append(fs_label)

    # 当日行情
    today_bits = ""
    if today:
        chg = today.get("change_pct", 0)
        today_bits = f" 当日{chg:+.1f}%"

    risk_str = ""
    if sig.get("risk_flags"):
        risk_str = " ⚠️" + " | ".join(sig["risk_flags"])

    lines.append(
        f"- **{s['name']}**（{s['category']}）| {detail} | "
        f"{' '.join(tech_bits)} | {' '.join(fund_bits)}{today_bits}{risk_str}"
    )


# ============================================================
# 独立运行
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    data = fetch_sector_monitor_data()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    print("\n" + "=" * 60)
    print(format_sector_for_prompt(data))
