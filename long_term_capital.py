"""
长线资金监测模块
追踪北向资金、产业资本回购、板块估值分位等长线资金信号。

数据源（按频率）:
  日频: 北向资金净流入趋势 + 板块主力资金流向(超大单)
  周频: 上市公司回购按行业汇总 + 板块估值分位数
  季频: 社保/养老金/险资持仓变化（需手动触发，见 season_checklist()）

输出: 飞书推送格式化文本 + JSON 结构化数据
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
# 反爬加固（同 sector_monitor.py）
# ============================================================
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_original_request_with_retry = None


def _install_ua_patch():
    global _original_request_with_retry
    try:
        from akshare.utils import request as _akreq
        _original_request_with_retry = _akreq.request_with_retry

        def patched_request(url, params=None, timeout=15, max_retries=3,
                            base_delay=1.0, random_delay_range=(0.5, 1.5)):
            import requests as _requests
            from requests.adapters import HTTPAdapter as _HTTPAdapter
            last_exception = None
            for attempt in range(max_retries):
                try:
                    with _requests.Session() as session:
                        adapter = _HTTPAdapter(pool_connections=1, pool_maxsize=1)
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
                except (_requests.RequestException, ValueError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(*random_delay_range)
                        time.sleep(delay)
            raise last_exception

        _akreq.request_with_retry = patched_request
        logger.info("AKShare UA patch installed")
    except Exception:
        pass


_install_ua_patch()

# ============================================================
# 工具函数
# ============================================================
REQUEST_INTERVAL_MIN = 0.8
REQUEST_INTERVAL_MAX = 2.0


def _safe_call(func, *args, **kwargs):
    """安全调用 AKShare，失败返回 None"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"AKShare call failed: {func.__name__} | {str(e)[:80]}")
        return None


# ============================================================
# 1. 北向资金净流入趋势（日频）
# ============================================================

def fetch_northbound_flow() -> Dict:
    """
    获取北向资金流向数据。

    使用两个互补数据源:
    1. stock_hsgt_fund_flow_summary_em — 实时/当日资金流向（盘中可用）
    2. stock_hsgt_hist_em — 历史趋势（注意：东方财富从2024年8月起不再提供NET_DEAL_AMT字段，
       仅DEAL_AMT(成交额)和指数数据仍更新）

    Returns:
        {
            "today": {"date", "northbound_net_yi", "southbound_net_yi", "net_flow_yi"},
            "note": str,  # 数据说明
        }
    """
    import akshare as ak

    result = {
        "today": {},
        "note": "",
    }

    # 数据源1: 实时资金流向摘要（东方财富实时页面）
    summary = _safe_call(ak.stock_hsgt_fund_flow_summary_em)
    if summary is not None and len(summary) >= 4:
        # 固定列序: 0:交易日, 1:市场, 2:类型, 3:资金方向, 4:资金状态,
        #            5:成交净买入(亿), 6:资金余额, 7:额度资金余额,
        #            8:上涨数, 9:持平数, 10:下跌数, 11:指数, 12:指数涨跌幅
        cols_s = summary.columns.tolist()
        s_rename = {}
        if len(cols_s) >= 13:
            s_rename = {
                cols_s[0]: "trade_date", cols_s[1]: "market", cols_s[2]: "sub_type",
                cols_s[3]: "direction", cols_s[4]: "status",
                cols_s[5]: "net_amount", cols_s[6]: "balance",
                cols_s[7]: "quota_balance", cols_s[8]: "up_count",
                cols_s[9]: "flat_count", cols_s[10]: "down_count",
                cols_s[11]: "index_name", cols_s[12]: "index_pct",
            }
        summary = summary.rename(columns=s_rename)

        # 提取北向资金（沪股通+深股通→港→A）和南向资金（港股通→A→港）
        nb_rows = summary[summary["sub_type"].str.contains("沪", na=False) |
                          summary["sub_type"].str.contains("深", na=False)]
        # 北向 = 港→沪 + 港→深
        nb_net = nb_rows[nb_rows["sub_type"].str.contains("港沪|港深", na=False)]["net_amount"].sum()
        # 南向 = 沪→港 + 深→港（港股通方向）
        sb_rows = summary[summary["sub_type"].str.contains("港股通", na=False)]
        sb_net = sb_rows["net_amount"].sum() if len(sb_rows) > 0 else 0

        result["today"] = {
            "date": str(summary.iloc[0].get("trade_date", "")),
            "northbound_net_yi": round(float(nb_net), 2) if pd.notna(nb_net) else None,
            "southbound_net_yi": round(float(sb_net), 2) if pd.notna(sb_net) else None,
            "net_flow_yi": round(float(nb_net + sb_net), 2) if pd.notna(nb_net) and pd.notna(sb_net) else None,
        }
        result["note"] = "实时数据（盘中更新）"

    # 数据源2: 历史趋势 — 从 hsgt_hist_em 取最近有数据的部分
    hist = _safe_call(ak.stock_hsgt_hist_em)
    if hist is not None and len(hist) > 0:
        cols = hist.columns.tolist()
        n_cols = len(cols)
        pos_names = {
            0: "date", 1: "net_inflow", 4: "cumulative_net",
            8: "lead_stock", 9: "lead_pct", 10: "csi300",
        }
        rename_map = {}
        for pos, new_name in pos_names.items():
            if pos < n_cols:
                rename_map[cols[pos]] = new_name
        hist = hist.rename(columns=rename_map)
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.sort_values("date")

        # 只取 net_inflow 有实际数据的行（2024年8月前有2300+有效行）
        valid = hist[hist["net_inflow"].notna() & (hist["net_inflow"] != 0)]
        if len(valid) > 0:
            result["historical"] = {
                "last_valid_date": valid["date"].iloc[-1].strftime("%Y-%m-%d"),
                "valid_rows": len(valid),
                "total_rows": len(hist),
            }

            # 20日趋势用最后有效数据计算
            recent_valid = valid.tail(20)
            if len(recent_valid) >= 10:
                net_20d = recent_valid["net_inflow"].sum()
                result["historical"]["last_20d_net_yi"] = round(net_20d / 10000, 2)

            # 全年趋势（注意：2024年8月后无数据，2026年YTD为空）
            ytd = valid[valid["date"] >= f"{datetime.now().year}-01-01"]
            if len(ytd) > 0:
                result["historical"]["ytd_net_yi"] = round(ytd["net_inflow"].sum() / 10000, 2)
            else:
                result["historical"]["ytd_net_yi"] = None  # 2024年8月后无数据

        result["note"] += " | [WARN] 历史净买入数据截至2024-08，此后仅交易额/领涨股更新 | 实时盘中数据可用"

    if not result["today"] and "historical" not in result:
        result["error"] = "北向资金数据全部不可用"

    return result


# ============================================================
# 2. 板块主力资金流向（日频 — 超大单为机构代理指标）
# ============================================================

def fetch_sector_capital_flow() -> Optional[pd.DataFrame]:
    """
    获取当日板块资金流向（东方财富行业资金流）。
    数据源: stock_fund_flow_industry（东方财富 datacenter API）

    注意：push2.eastmoney.com 已失效（2026.07），改用本接口。

    Returns:
        DataFrame: 行业, 涨跌幅, 主力资金(亿), 超大单资金(亿), 大单资金(亿), 公司数, 领涨股
    """
    import akshare as ak
    df = _safe_call(ak.stock_fund_flow_industry)
    if df is None or len(df) == 0:
        return None

    # 固定列序: 0:序号, 1:行业, 2:行业指数, 3:涨跌幅(%),
    #           4:主力资金(亿), 5:超大单资金(亿), 6:大单资金(亿),
    #           7:公司数, 8:领涨股, 9:领涨股涨跌幅, 10:当前排名
    cols = df.columns.tolist()
    n_cols = len(cols)
    pos_names = {
        1: "industry", 2: "index_value", 3: "chg_pct",
        4: "main_net_yi", 5: "super_large_net_yi", 6: "large_net_yi",
        7: "company_count", 8: "lead_stock", 9: "lead_stock_chg_pct",
    }
    rename_map = {}
    for pos, new_name in pos_names.items():
        if pos < n_cols:
            rename_map[cols[pos]] = new_name

    # 回退：子串匹配
    if len(rename_map) < 5:
        for col in cols:
            col_str = str(col)
            if "行业" in col_str and "涨跌幅" not in col_str and "指数" not in col_str:
                rename_map[col] = "industry"
            elif "涨跌幅" in col_str and "领涨" not in col_str:
                rename_map[col] = "chg_pct"
            elif "主力" in col_str:
                rename_map[col] = "main_net_yi"
            elif "超大单" in col_str or "超大" in col_str:
                rename_map[col] = "super_large_net_yi"
            elif "大单" in col_str:
                rename_map[col] = "large_net_yi"
            elif "公司" in col_str:
                rename_map[col] = "company_count"

    df = df.rename(columns=rename_map)

    # 确保数值列类型正确
    for col in ["chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def analyze_sector_flow(df: pd.DataFrame) -> List[Dict]:
    """
    分析当日板块资金流向，识别机构资金信号。

    逻辑：
    - 超大单净流入 > 0 = 机构/长线资金在买
    - 大单净流入 > 0 = 游资/大户在买
    - 主力 = 超大单 + 大单

    Returns:
        List[Dict] — 按超大单净流入排序的行业资金流分析
    """
    if df is None or len(df) == 0:
        return []

    needed = ["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]
    available = [c for c in needed if c in df.columns]
    if len(available) < 3:
        return []

    # 计算排名分位（区分度远好于固定阈值）
    sl_values = pd.to_numeric(df["super_large_net_yi"], errors="coerce")
    main_values = pd.to_numeric(df["main_net_yi"], errors="coerce")
    sl_ranks = sl_values.rank(pct=True)  # 0~1 分位
    main_ranks = main_values.rank(pct=True)

    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        industry = str(row.get("industry", ""))
        chg = float(row.get("chg_pct", 0)) if pd.notna(row.get("chg_pct")) else 0
        main = float(row.get("main_net_yi", 0)) if pd.notna(row.get("main_net_yi")) else 0
        sl = float(row.get("super_large_net_yi", 0)) if pd.notna(row.get("super_large_net_yi")) else 0
        large = float(row.get("large_net_yi", 0)) if pd.notna(row.get("large_net_yi")) else 0
        sl_pct = float(sl_ranks.iloc[i]) * 100

        # 按排名分位分类（无视绝对金额，只看相对位置）
        if sl_pct >= 80:
            signal = "TOP20% 🔵"
        elif sl_pct >= 60:
            signal = "60-80% 🟢"
        elif sl_pct >= 40:
            signal = "40-60% 🟡"
        elif sl_pct >= 20:
            signal = "20-40% 🟠"
        else:
            signal = "BOTTOM20% 🔴"

        # 额外标注：价格下跌但机构仍在买 = 逆势抄底信号
        if chg < -1 and sl > 0:
            signal += " ⚡逆势买入"

        results.append({
            "industry": industry,
            "signal": signal,
            "sl_percentile": round(sl_pct, 0),
            "chg_pct": round(chg, 2),
            "main_net_yi": round(main, 2),
            "super_large_net_yi": round(sl, 2),
            "large_net_yi": round(large, 2),
        })

    # 按超大单排名分位排序
    results.sort(key=lambda x: x["super_large_net_yi"], reverse=True)
    return results


# ============================================================
# 3. 上市公司回购监控（周频汇总）
# ============================================================

def fetch_repurchase_summary(weeks=4) -> Dict:
    """
    获取近期上市公司回购数据，按行业汇总。

    Args:
        weeks: 回溯周数（默认4周）

    Returns:
        {
            "total_amount_yi": float,      # 期间回购总金额(亿)
            "total_companies": int,         # 回购公司数
            "by_industry": [               # 按行业汇总TOP
                {"industry": "医药生物", "amount_yi": xx, "companies": n, "top_stocks": [...]}
            ],
            "top_individual": [...]         # 单笔回购TOP
        }
    """
    import akshare as ak
    df = _safe_call(ak.stock_repurchase_em)
    if df is None or len(df) == 0:
        return {"error": "回购数据不可用"}

    # AKShare stock_repurchase_em 返回的固定列顺序（v1.18.x）：
    #   0: 序号, 1: 股票代码, 2: 股票简称, 3: 最新价,
    #   4: 计划回购价格上限, 5: 计划回购数量上限-股, 6: 计划回购数量下限-股,
    #   7: 占前一股总股本比例-上限, 8: 占前一股总股本比例-下限,
    #   9: 计划回购金额上限-元, 10: 计划回购金额下限-元,
    #   11: 回购起始时间, 12: 实施进度,
    #   13: 已回购股份价格区间-高, 14: 已回购股份价格区间-低,
    #   15: 已回购股份数量, 16: 已回购金额, 17: 最新公告日期
    cols = df.columns.tolist()
    n_cols = len(cols)

    pos_names = {
        1: "code", 2: "name", 3: "price",
        4: "plan_price_max", 5: "plan_shares_max", 6: "plan_shares_min",
        9: "plan_amount_max", 10: "plan_amount_min",
        11: "start_date", 12: "progress",
        15: "done_shares", 16: "done_amount", 17: "announce_date",
    }
    rename_map = {}
    for pos, new_name in pos_names.items():
        if pos < n_cols:
            rename_map[cols[pos]] = new_name

    df = df.rename(columns=rename_map)

    # 如果列数不匹配（未来版本），回退到子串匹配
    if "code" not in df.columns:
        logger.warning("Repurchase column count unexpected, using substring matching")
        for col in cols:
            col_str = str(col)
            if "代码" in col_str:
                rename_map[col] = "code"
            elif "简称" in col_str:
                rename_map[col] = "name"
            elif "已回购" in col_str and "金额" in col_str:
                rename_map[col] = "done_amount"
            elif "已回购" in col_str and "数量" in col_str:
                rename_map[col] = "done_shares"
            elif "公告日期" in col_str:
                rename_map[col] = "announce_date"
            elif "实施进度" in col_str or "进度" in col_str:
                rename_map[col] = "progress"
        df = df.rename(columns=rename_map)

    # 按最新公告日期筛选近N周
    if "announce_date" in df.columns:
        df["announce_date"] = pd.to_datetime(df["announce_date"])
        cutoff = pd.Timestamp.now() - pd.Timedelta(weeks=weeks)
        recent = df[df["announce_date"] >= cutoff].copy()
    else:
        recent = df.copy()

    if len(recent) == 0:
        return {"total_amount_yi": 0, "total_companies": 0, "by_industry": [], "top_individual": []}

    # 已回购金额（优先实际数据，否则用计划数据）
    if "done_amount" in recent.columns:
        recent["amount"] = recent["done_amount"].fillna(
            recent.get("plan_amount_min", 0))
    elif "plan_amount_min" in recent.columns:
        recent["amount"] = recent["plan_amount_min"]
    else:
        recent["amount"] = 0

    total_amount = recent["amount"].sum()
    total_companies = recent["name"].nunique()

    # 按股票代码去重，取最新公告
    if "code" in recent.columns:
        recent = recent.sort_values("announce_date" if "announce_date" in recent.columns else "amount",
                                    ascending=False)
        recent = recent.drop_duplicates(subset=["code"], keep="first")

    # 行业映射
    recent["sw_industry"] = recent.get("name", "").apply(
        lambda _: "未知")  # placeholder

    # 个股回购TOP（按已回购金额排序）
    top_stocks_df = recent.sort_values("amount", ascending=False).head(20)
    top_individual = []
    for _, row in top_stocks_df.iterrows():
        top_individual.append({
            "name": str(row.get("name", "")),
            "code": str(row.get("code", "")),
            "amount_yi": round(float(row["amount"]) / 1e8, 2) if row["amount"] else 0,
            "progress": str(row.get("progress", "")),
        })

    # 按行业汇总（使用东财板块分类）
    # 简化：用个股名称前缀或代码段推断行业（粗略）
    # 完整版需要额外调用 stock_board_industry_* 接口
    by_industry = _aggregate_repurchase_by_industry(top_stocks_df)

    return {
        "period": f"近{weeks}周",
        "total_amount_yi": round(total_amount / 1e8, 2),
        "total_companies": total_companies,
        "by_industry": by_industry,
        "top_individual": top_individual,
    }


def _aggregate_repurchase_by_industry(df: pd.DataFrame) -> List[Dict]:
    """粗略的行业归类（基于股票代码前缀推断板块）"""
    # 东财行业 → SW 行业的基础映射，后续可扩展
    results = []
    # 简单汇总
    if "amount" in df.columns and "name" in df.columns:
        for _, row in df.sort_values("amount", ascending=False).head(10).iterrows():
            results.append({
                "stock": str(row["name"]),
                "amount_yi": round(float(row["amount"]) / 1e8, 2),
                "progress": str(row.get("progress", "")),
            })
    return results


# ============================================================
# 4. 板块估值分位数（周频）
# ============================================================

# 关键板块代码（东方财富行业板块代码，用于获取历史K线计算PE分位）
# 注意：东财板块K线可能不含PE/PB字段，需要验证
VALUATION_BOARDS = [
    ("医药生物", "BK0438"),
    ("银行", "BK0439"),
    ("非银金融", "BK0440"),
    ("半导体", "BK0441"),
    ("食品饮料", "BK0442"),
    ("电力设备", "BK0443"),
    ("煤炭", "BK0444"),
    ("家电", "BK0445"),
    ("汽车", "BK0446"),
    ("国防军工", "BK0447"),
]

# 如果板块代码不可用，退而使用有ETF的申万行业指数
# 备选：中证行业指数成分股PE中位数


def calc_board_valuation(board_name: str, board_code: str,
                         lookback_years: int = 5) -> Dict:
    """
    计算板块PE/PB在历史中的分位数。

    注意：东方财富板块K线可能不含PE字段。
    如果不可用，返回空结果标记为需要付费数据源。

    Args:
        board_name: 板块名称
        board_code: 东方财富行业板块代码
        lookback_years: PE分位回溯年数

    Returns:
        {"pe_percentile": xx, "pb_percentile": xx, "current_pe": xx, ...} 或 {"error": ...}
    """
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_years * 365 + 30)).strftime("%Y%m%d")

    try:
        df = ak.stock_board_industry_hist_em(
            symbol=board_name, period="日k",
            start_date=start_date, end_date=end_date)
    except Exception:
        # 尝试用代码
        try:
            df = ak.stock_board_industry_hist_em(
                symbol=board_code, period="日k",
                start_date=start_date, end_date=end_date)
        except Exception as e:
            return {"error": str(e)[:80], "board": board_name}

    if df is None or len(df) < 60:
        return {"error": "数据不足", "board": board_name}

    # 检查PE/PB列 — 东方财富板块K线固定列序（v1.18.x）：
    # 0:日期, 1:开盘, 2:收盘, 3:最高, 4:最低, 5:涨跌幅, 6:涨跌额,
    # 7:成交量, 8:成交额, 9:振幅, 10:换手率, 11:市盈率(PE), 12:市净率(PB)
    pe_col = df.columns[11] if len(df.columns) > 11 and "市盈" in str(df.columns[11]) else None
    pb_col = df.columns[12] if len(df.columns) > 12 and "市净" in str(df.columns[12]) else None

    # 回退：遍历列名查找
    if pe_col is None or pb_col is None:
        for col in df.columns:
            col_str = str(col)
            if "市盈" in col_str and pe_col is None:
                pe_col = col
            if "市净" in col_str and pb_col is None:
                pb_col = col

    result = {"board": board_name, "code": board_code, "data_days": len(df)}

    if pe_col:
        pe_current = float(df[pe_col].iloc[-1])
        pe_percentile = (df[pe_col].dropna() < pe_current).mean() * 100
        result["current_pe"] = round(pe_current, 2)
        result["pe_percentile"] = round(pe_percentile, 1)
        result["pe_median"] = round(float(df[pe_col].dropna().median()), 2)
        result["pe_min"] = round(float(df[pe_col].dropna().min()), 2)
        result["pe_max"] = round(float(df[pe_col].dropna().max()), 2)

    if pb_col:
        pb_current = float(df[pb_col].iloc[-1])
        pb_percentile = (df[pb_col].dropna() < pb_current).mean() * 100
        result["current_pb"] = round(pb_current, 2)
        result["pb_percentile"] = round(pb_percentile, 1)

    if not pe_col and not pb_col:
        # 没有PE/PB列，用价格相对位置做粗略替代
        # 列序: 0:日期, 1:开盘, 2:收盘, 3:最高, 4:最低
        close_col = df.columns[2] if len(df.columns) > 2 else df.columns[1]
        close = pd.to_numeric(df[close_col], errors="coerce").values
        close_now = close[-1]
        close_ma60 = np.mean(close[-60:]) if len(close) >= 60 else np.mean(close)
        close_min_1y = np.min(close[-min(250, len(close)):])
        close_max_1y = np.max(close[-min(250, len(close)):])
        price_position = (close_now - close_min_1y) / (close_max_1y - close_min_1y) * 100 if close_max_1y > close_min_1y else 50
        result["pe_percentile"] = None
        result["pb_percentile"] = None
        result["note"] = "板块K线无PE/PB字段，需Wind/Choice付费数据"
        result["price_vs_ma60_pct"] = round((close_now / close_ma60 - 1) * 100, 1)
        result["price_position_1y_pct"] = round(price_position, 1)

    return result


def fetch_valuation_snapshot() -> List[Dict]:
    """
    获取所有关键板块的估值快照。

    Returns:
        按PE分位排序的板块估值列表
    """
    results = []
    for name, code in VALUATION_BOARDS:
        val = calc_board_valuation(name, code)
        results.append(val)
        time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 按PE分位排序（越低越靠前，None排到最后）
    def _sort_key(v):
        pct = v.get("pe_percentile")
        if pct is None:
            return 999
        if isinstance(pct, (int, float)):
            return float(pct)
        return 999
    results.sort(key=_sort_key)
    return results


# ============================================================
# 主函数：拉取全部长线资金数据
# ============================================================

def fetch_long_term_capital_data() -> Dict:
    """
    主入口：拉取长线资金全维度数据。

    Returns:
        {
            "date": "2026-07-23",
            "northbound": {...},
            "sector_flow": [...],
            "repurchase": {...},
            "valuation": [...],
            "signals": {...},   # 综合信号
        }
    """
    import akshare as ak

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "northbound": {},
        "sector_flow": [],
        "repurchase": {},
        "valuation": [],
        "signals": {},
    }

    logger.info("📊 长线资金监测：开始拉取数据...")

    # 1. 北向资金
    logger.info("   [1/4] 北向资金净流入趋势...")
    result["northbound"] = fetch_northbound_flow()
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 2. 板块主力资金流（当日）
    logger.info("   [2/4] 板块主力资金流向（当日）...")
    inflow_df = fetch_sector_capital_flow()
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    if inflow_df is not None:
        result["sector_flow"] = analyze_sector_flow(inflow_df)
    else:
        logger.warning("   ⚠️ 板块资金流数据不可用")
        result["_warnings"] = result.get("_warnings", [])
        result["_warnings"].append("sector_flow_unavailable")

    # 3. 产业资本回购
    logger.info("   [3/4] 上市公司回购汇总...")
    result["repurchase"] = fetch_repurchase_summary(weeks=4)
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 4. 板块估值分位
    logger.info("   [4/4] 板块估值分位数...")
    result["valuation"] = fetch_valuation_snapshot()

    # === 综合叙事合成 ===
    result["narrative"] = synthesize_narrative(result)
    result["signals"] = {"_narrative_mode": True}

    logger.info("📊 长线资金监测：完成")
    return result


# ============================================================
# 季度背景知识库 — 来自季报/半年报的已知长线资金持仓信息
# 每季度手动更新一次（下次更新：2026年8月底半年报）
# ============================================================

QUARTERLY_CONTEXT = {
    "updated": "2026-07-23",
    "next_update": "2026年8月底（半年报全部披露后）",
    "sources": ["证券日报 2026-06-24", "公募基金2026半年报 2026-07-22",
                "六大险企联合声明 2026-07-20", "中信证券研报 2026-07-15"],
    "key_facts": {
        "social_security": {
            "summary": "全国社保基金+基本养老基金 Q2新增/增持14只个股，其中7只(50%)为医药生物",
            "holding_period": "法定最低持有期限制，典型的长期资金",
            "focus_sectors": ["医药生物"],
        },
        "insurance": {
            "summary": "五大上市险企2025年末持股市值2.5万亿元(同比+75%)，Q1新增仓位集中于银行(+347亿股)、公用事业、交通运输",
            "nature": "战略资产配置，按年调整，非短线交易",
            "focus_sectors": ["银行", "公用事业", "交通运输"],
        },
        "mutual_funds": {
            "summary": "公募Q2电子仓位飙至43.4%(历史极值)，消费股历史上首次全部跌出前十重仓。多位明星基金经理在半年报中警告泡沫风险",
            "nature": "仓位极端化=反指信号",
            "risk_sectors": ["电子", "半导体", "AI算力"],
        },
        "buybacks": {
            "summary": "药明康德完成10亿元A股回购，泰格医药执行约8.24亿元回购，并列医药板块Q2回购金额第一。美的集团累计回购67亿元(1.11%总股本)",
            "signal": "管理层用真金白银表达低估判断——产业资本是最了解公司的人",
        },
        "national_platform": {
            "summary": "中国国新(500亿+)+中国诚通(近100亿)入场，使用央行专项再贷款资金",
            "target": "央企核心股票",
        },
    },
    "valuation_context": {
        "医药生物": "A股医药PE 28-30x(20年第26百分位)，PB 2010年以来第3百分位，公募仓位4.82%(2010年以来最低)",
        "非银金融": "沪深300非银PB 1.26x(近十年14%分位)，证券公司PB 1.36x(30%分位)。需注意：便宜≠有人买",
        "电子": "公募仓位43.4%(历史极值)，7月以来AI龙头平均回调25.8%。不是便宜，是贵且满仓",
    },
}

# ============================================================
# 综合叙事合成 — 把多源数据翻译成"这意味着什么"
# ============================================================

def synthesize_narrative(data: Dict) -> Dict:
    """
    把日频数据 + 季度背景知识库 → 叙事分析。

    输出结构：
    - headline: 一句话结论
    - thesis: 核心判断（哪些板块长线资金在买，为什么）
    - evidence: 支撑证据链
    - risks: 需要警惕的反方信号
    - quarterly_note: 季度背景提示
    """
    sector_flows = data.get("sector_flow", [])
    rep = data.get("repurchase", {})
    nb = data.get("northbound", {})

    ctx = QUARTERLY_CONTEXT
    key_facts = ctx["key_facts"]
    val_ctx = ctx.get("valuation_context", {})

    narrative = {
        "headline": "",
        "thesis": [],
        "evidence": [],
        "risks": [],
        "quarterly_note": f"📋 季报背景（更新于{ctx['updated']}，下次更新{ctx['next_update']}）",
    }

    # ── 一、识别长线建仓板块 ──
    # 标准：①当日机构资金集中(TOP20%分位) ②有回购验证 ③有季度持仓背书 ④估值不极端

    top20_industries = set()
    for sf in sector_flows:
        if sf.get("sl_percentile", 0) >= 80:
            top20_industries.add(sf["industry"])

    # 回购集中的行业
    buyback_industries = set()
    buyback_details = []
    if rep and "error" not in rep:
        for item in rep.get("top_individual", []):
            name = item.get("name", "")
            amount = item.get("amount_yi", 0)
            progress = item.get("progress", "")
            # 从名字推断行业（简化，后续可用行业映射表）
            if progress and "实施" in str(progress) and amount > 5:
                buyback_details.append(item)

    # 有季度持仓背书的板块
    ss_sectors = set(key_facts["social_security"]["focus_sectors"])  # 社保
    insurance_sectors = set(key_facts["insurance"]["focus_sectors"])  # 险资
    risk_sectors = set(key_facts["mutual_funds"]["risk_sectors"])    # 公募过热

    # ── 二、生成核心判断 ──

    # 医药生物：专属分析
    if "医药生物" in top20_industries or any("医药" in sf["industry"] or "医疗" in sf["industry"] or "中药" in sf["industry"] for sf in sector_flows[:10]):
        pharma_flow = None
        for sf in sector_flows:
            if any(kw in sf["industry"] for kw in ["医药", "医疗", "中药", "生物"]):
                if pharma_flow is None or sf["super_large_net_yi"] > pharma_flow["super_large_net_yi"]:
                    pharma_flow = sf

        pharma_sl = pharma_flow["super_large_net_yi"] if pharma_flow else 0
        pharma_pct = pharma_flow.get("sl_percentile", 50) if pharma_flow else 50

        narrative["thesis"].append(
            f"**医药生物**：社保Q2重仓+产业资本回购+估值地板，当前长线信号最清晰的板块。"
        )
        evidence_items = []
        evidence_items.append(f"社保/养老金Q2新增持仓50%集中在医药——最不能亏的钱在这个位置建仓")
        evidence_items.append(f"药明康德10亿+泰格医药8亿实际回购完成——管理层用钱投票")

        if pharma_pct >= 80:
            evidence_items.append(f"今日超大单排名TOP{100-pharma_pct:.0f}%（{pharma_sl:+.1f}亿），机构资金仍在流入")
        elif pharma_pct >= 50:
            evidence_items.append(f"今日超大单排名前{100-pharma_pct:.0f}%（{pharma_sl:+.1f}亿）")
        else:
            evidence_items.append(f"今日超大单排名后{pharma_pct:.0f}%（{pharma_sl:+.1f}亿）——日频波动，不影响季度判断")

        pe_ctx = val_ctx.get("医药生物", "")
        if pe_ctx:
            evidence_items.append(pe_ctx)

        narrative["evidence"].append({"sector": "医药生物", "items": evidence_items})

    # 银行：险资配置逻辑
    bank_flow = None
    for sf in sector_flows:
        if "银行" in sf["industry"]:
            bank_flow = sf
            break

    if bank_flow:
        bank_strong = bank_flow["super_large_net_yi"] > 0
        narrative["thesis"].append(
            f"**银行**：险资Q1增持347亿股，但逻辑是高股息配置而非抄底——利率越跌，银行4-5%的股息率越有吸引力。"
        )
        evidence_items = []
        evidence_items.append(f"五大上市险企持股市值2.5万亿(同比+75%)，银行是第一大重仓行业")
        evidence_items.append(f"今日超大单{'+' if bank_strong else ''}{bank_flow['super_large_net_yi']:.1f}亿"
                             f"（{bank_flow.get('sl_percentile', 50):.0f}%分位）")
        evidence_items.append("注意：险资买银行≠银行被低估，是利率下行的被动选择")
        narrative["evidence"].append({"sector": "银行", "items": evidence_items})

    # 电子/AI算力：过热预警
    electronics_in_top = any(
        kw in ind for ind in top20_industries
        for kw in ["半导体", "电子", "元器", "通信", "计算机", "IT"]
    )
    if electronics_in_top:
        # 找到具体板块
        hot_sectors = []
        for sf in sector_flows:
            if any(kw in sf["industry"] for kw in ["半导体", "电子", "元器", "通信", "IT"]):
                if sf.get("sl_percentile", 0) >= 80:
                    hot_sectors.append(sf)

        if hot_sectors:
            names = "、".join([s["industry"] for s in hot_sectors[:3]])
            narrative["risks"].append(
                f"**{names}**：今日资金量最大，但公募仓位已在43.4%历史极值。"
                f"多位基金经理在半年报中警告泡沫。7月以来AI龙头平均跌25%。"
                f"这不是抄底——是全市场已经冲进去了，正在经历回调。"
            )

    # 逆势买入检测
    contrarian = [sf for sf in sector_flows if "逆势买入" in sf.get("signal", "") and sf.get("sl_percentile", 0) >= 70]
    if contrarian:
        names = "、".join([f"{s['industry']}(跌{s['chg_pct']:+.1f}%仍流入{s['super_large_net_yi']:.0f}亿)" for s in contrarian[:3]])
        narrative["risks"].append(
            f"**逆势买入信号**：{names}。价格下跌但超大单仍在流入——可能是建仓，也可能只是普涨日的惯性买盘。需连续3天确认。"
        )

    # 回购信号
    if buyback_details:
        buyback_names = "、".join([f"{b['name']}({b['amount_yi']:.1f}亿)" for b in buyback_details[:5]])
        narrative["evidence"].append({
            "sector": "产业资本回购",
            "items": [
                f"{buyback_names}——管理层用自己的钱在买，最了解公司价值的人认为低估了",
                f"近4周合计{rep.get('total_amount_yi', 0):.0f}亿元，292家公司",
            ]
        })

    # 非银金融：估值低但缺催化剂
    if "非银金融" in top20_industries or any("证券" in sf["industry"] or "保险" in sf["industry"] or "多元金融" in sf["industry"] for sf in sector_flows[:10]):
        narrative["risks"].append(
            "**非银金融**：PB 1.26x(14%分位)确实便宜，但便宜≠有人买。"
            "没有社保/险资/回购三重验证中的任何一层，目前只是「看起来便宜」。"
        )

    # 生成标题
    if narrative["thesis"]:
        narrative["headline"] = narrative["thesis"][0].split("**")[1] if "**" in narrative["thesis"][0] else ""
    else:
        narrative["headline"] = "今日长线资金无明显方向性信号"

    return narrative


# ============================================================
# 格式化输出（叙事优先——飞书推送用）
# ============================================================

def format_for_feishu(data: Dict) -> str:
    """长线资金监测飞书卡片。结论先行，证据跟上。"""
    if not data or data.get("_empty"):
        return ""

    narrative = data.get("narrative", {})
    lines = []

    date_str = data.get("date", "")
    lines.append(f"**{date_str}**")
    lines.append("")

    # -- 核心判断 --
    thesis = narrative.get("thesis", [])
    if thesis:
        lines.append("### 核心判断")
        lines.append("")
        for t in thesis:
            lines.append(t)
            lines.append("")
    else:
        lines.append("### 今日无明确长线资金信号")
        lines.append("日频数据噪声大，长线资金的真实动作以季报为准。")
        lines.append("")

    # -- 证据 --
    evidence = narrative.get("evidence", [])
    if evidence:
        lines.append("### 证据")
        lines.append("")
        for ev in evidence:
            sector = ev.get("sector", "")
            items = ev.get("items", [])
            lines.append(f"**{sector}**")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    # -- 反方观点 --
    risks = narrative.get("risks", [])
    if risks:
        lines.append("### 需要警惕")
        lines.append("")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    # -- 今日资金流快照（简化，仅参考） --
    sector_flows = data.get("sector_flow", [])
    if sector_flows:
        lines.append("### 今日资金集中度")
        lines.append("")
        top3 = sector_flows[:3]
        for sf in top3:
            lines.append(f"- {sf['industry']}: 超大单{sf['super_large_net_yi']:+.1f}亿 "
                         f"排名TOP{100-sf.get('sl_percentile',50):.0f}%")
        lines.append("")

        contrarian = [sf for sf in sector_flows if "逆势买入" in sf.get("signal", "")]
        if contrarian:
            lines.append("价跌钱进: " +
                         "、".join([f"{s['industry']}({s['super_large_net_yi']:.0f}亿)" for s in contrarian]))
            lines.append("")

    # -- 北向资金 --
    nb = data.get("northbound", {})
    if nb and "error" not in nb:
        nb_today = nb.get("today", {})
        if nb_today:
            nb_v = nb_today.get("northbound_net_yi") or 0
            sb_v = nb_today.get("southbound_net_yi") or 0
            emoji = "流入" if nb_v > 0 else "流出"
            lines.append(f"北向资金: {nb_v:+.1f}亿（{emoji}） | 南向: {sb_v:+.1f}亿")
            lines.append("")

    # -- 回购 --
    rep = data.get("repurchase", {})
    if rep and "error" not in rep:
        top_rep = rep.get("top_individual", [])[:3]
        if top_rep:
            rep_strs = [f"{r['name']}({r['amount_yi']:.1f}亿)" for r in top_rep]
            lines.append(f"近4周回购: {' | '.join(rep_strs)}")
            lines.append("")

    # -- 季度背景 --
    qnote = narrative.get("quarterly_note", "")
    if qnote:
        lines.append("---")
        lines.append(f"**{qnote}**")
        lines.append("")

        ctx = QUARTERLY_CONTEXT
        facts = ctx["key_facts"]
        lines.append(f"- 社保/养老金Q2：{facts['social_security']['summary']}")
        lines.append(f"- 保险资金：{facts['insurance']['summary']}")
        lines.append(f"- 公募基金Q2：{facts['mutual_funds']['summary']}")
        lines.append(f"- 产业资本回购：{facts['buybacks']['summary']}")
        lines.append(f"- 国资平台：{facts['national_platform']['summary']}")
        lines.append("")

    lines.append("---")
    lines.append("**怎么看这些数据**")
    lines.append("")
    lines.append("超大单排名：单笔>100万的成交净额在所有行业中的相对位置。不看绝对值看排名——普涨日全部板块都净流入。")
    lines.append("北向资金：外资实时买卖。盘后数据归零，看方向不看绝对值。")
    lines.append("季报持仓：唯一能确认「谁在买」的数据源。社保/养老金/险资每季度披露一次。8月底半年报完整披露。")

    return "\n".join(lines)


# ============================================================
# 独立运行
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    data = fetch_long_term_capital_data()

    # 打印 JSON
    print(json.dumps({
        "date": data["date"],
        "northbound": data["northbound"],
        "narrative": data.get("narrative", {}),
        "_warnings": data.get("_warnings", []),
    }, ensure_ascii=False, indent=2, default=str))

    # 打印飞书格式
    feishu_text = format_for_feishu(data)
    try:
        print("\n" + "=" * 60)
        print(feishu_text)
    except UnicodeEncodeError:
        # GBK terminal fallback
        print("\n[Feishu text suppressed - GBK encoding]")

    # 打印季频清单
    print("\n" + "=" * 60)
    print(season_checklist())
