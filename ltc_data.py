# ltc_data.py
"""数据抓取层：新浪交易日/南向/板块资金流/回购/板块K线/估值位置"""
import logging, random, time
from datetime import datetime, timedelta
from typing import List, Optional
import pandas as pd
import akshare as ak
from ltc_config import BOARDS, PHASE_LABEL

logger = logging.getLogger(__name__)
REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX = 0.8, 2.0

def _safe_call(func, *args, retries=2, **kwargs):
    for i in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning("call failed %s try %d: %s", getattr(func, "__name__", func), i + 1, str(e)[:80])
            time.sleep(2 + i * 2)
    return None

def get_trading_date() -> Optional[str]:
    """新浪上证指数日K末行日期 = 最近交易日（稳定源，交易日判定用）"""
    df = _safe_call(ak.stock_zh_index_daily, symbol="sh000001", retries=2)
    if df is None or len(df) == 0:
        return None
    return str(df.iloc[-1]["date"])[:10]

def fetch_southbound() -> Optional[dict]:
    """南向资金当日净买入（东财实时摘要，trade_date 为数据日期）"""
    df = _safe_call(ak.stock_hsgt_fund_flow_summary_em)
    if df is None or len(df) < 2:
        return None
    cols = df.columns.tolist()
    sub = df[df[cols[2]].astype(str).str.contains("港股通", na=False)]
    if len(sub) == 0:
        return None
    total = pd.to_numeric(sub[cols[5]], errors="coerce").sum()
    return {"date": str(df.iloc[0][cols[0]])[:10],
            "southbound_net_yi": round(float(total), 2)}

def parse_sector_flow(df_raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    """东财行业资金流：位置列映射（0:序号 1:行业 2:指数 3:涨跌幅 4:主力 5:超大单 6:大单）"""
    cols = df_raw.columns.tolist()
    pos = {1: "industry", 3: "chg_pct", 4: "main_net_yi", 5: "super_large_net_yi", 6: "large_net_yi"}
    rename = {}
    for p, name in pos.items():
        if p < len(cols):
            rename[cols[p]] = name
    df = df_raw.rename(columns=rename)
    if "industry" not in df.columns:
        return None
    for c in ["chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]]

def fetch_sector_flow() -> Optional[pd.DataFrame]:
    df = _safe_call(ak.stock_fund_flow_industry)
    if df is None or len(df) == 0:
        return None
    return parse_sector_flow(df)

def parse_repurchase(df_raw: pd.DataFrame, weeks: int = 4) -> dict:
    """东财回购：0:序号 1:代码 2:名称 9:计划金额上限 10:计划金额下限 12:实施进度 16:已回购金额 17:最新公告日期"""
    cols = df_raw.columns.tolist()
    rename = {}
    for p, name in {1: "code", 2: "name", 9: "plan_max", 10: "plan_min", 12: "progress", 16: "done_amount", 17: "ann_date"}.items():
        if p < len(cols):
            rename[cols[p]] = name
    df = df_raw.rename(columns=rename)
    if "name" not in df.columns:
        return {"period": f"近{weeks}周", "items": []}
    df["ann_date"] = pd.to_datetime(df["ann_date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(weeks=weeks)
    df = df[df["ann_date"] >= cutoff].copy()
    if len(df) == 0:
        return {"period": f"近{weeks}周", "items": []}
    # 已回购为空(NaN)或为0(尚未回购)→ 用计划金额下限兜底，保证预案阶段卡片显示计划金额而非0
    done = pd.to_numeric(df["done_amount"], errors="coerce")
    df["amount"] = done.where(done > 0).fillna(
        pd.to_numeric(df["plan_min"], errors="coerce")).fillna(0)
    items = []
    for _, r in df.sort_values("amount", ascending=False).head(20).iterrows():
        items.append({
            "name": str(r["name"]), "code": str(r["code"]),
            "amount_yi": round(float(r["amount"]) / 1e8, 2) if r["amount"] else 0.0,
            "phase": PHASE_LABEL(r["progress"]),
        })
    return {"period": f"近{weeks}周", "items": items}

def fetch_repurchase(weeks: int = 4) -> dict:
    df = _safe_call(ak.stock_repurchase_em)
    if df is None or len(df) == 0:
        return {"period": f"近{weeks}周", "items": []}
    return parse_repurchase(df, weeks)

def _pick_kline_cols(df: pd.DataFrame) -> Optional[tuple]:
    """按列名取 日期/收盘/成交量/成交额 — 东财(收盘=第3列)与同花顺(收盘=第5列)列序不同，按名不按位"""
    cols = df.columns.tolist()
    def find(*keys):
        for c in cols:
            if any(k in str(c) for k in keys):
                return c
        return None
    date_col, close_col = find("日期"), find("收盘")
    if date_col is None or close_col is None:
        return None
    return date_col, close_col, find("成交量"), find("成交额")

def fetch_board_kline(board_name: str, days: int = 1300) -> Optional[pd.DataFrame]:
    """板块K线：东财优先，同花顺兜底；返回 date/close/volume/amount（升序）"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    df = _safe_call(ak.stock_board_industry_hist_em,
                    symbol=board_name, period="日k", start_date=start, end_date=end, retries=2)
    if df is None or len(df) < 60:
        df = _safe_call(ak.stock_board_industry_index_ths,
                        symbol=board_name, start_date=start, end_date=end, retries=2)
    if df is None or len(df) < 60:
        return None
    picked = _pick_kline_cols(df)
    if picked is None:
        return None
    date_col, close_col, vol_col, amt_col = picked
    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col]),
        "close": pd.to_numeric(df[close_col], errors="coerce"),  # 收盘价
        "volume": pd.to_numeric(df[vol_col], errors="coerce") if vol_col else 0,
        "amount": pd.to_numeric(df[amt_col], errors="coerce") if amt_col else 0,
    }).dropna(subset=["close"]).sort_values("date")
    return out if len(out) >= 60 else None

def fetch_valuation() -> List[dict]:
    """估值温度 = 5年价格位置分位 + vs MA60（板块K线无PE列，已实测；标注基于价格）"""
    results = []
    for name, code in BOARDS:
        k = fetch_board_kline(name, days=1300)
        if k is None:
            results.append({"board": name, "ok": False})
            continue
        close = k["close"].values
        pos = (close[-1] - close.min()) / (close.max() - close.min()) * 100 if close.max() > close.min() else 50
        ma60 = float(close[-60:].mean())
        results.append({
            "board": name, "ok": True,
            "position_pct": round(float(pos), 1),
            "price_vs_ma60_pct": round(float(close[-1] / ma60 - 1) * 100, 1),
        })
        time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))
    return results
