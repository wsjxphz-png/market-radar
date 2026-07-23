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

# 申万一级行业 → 东方财富板块名称映射（常见变体）
INDUSTRY_GROUPS = {
    "医药生物": ["医药", "医疗", "医药制造", "生物制品", "医疗器械", "医药生物"],
    "银行": ["银行"],
    "非银金融": ["券商", "保险", "多元金融", "证券"],
    "电子": ["半导体", "电子元件", "消费电子", "光学光电子", "电子"],
    "食品饮料": ["食品饮料", "酿酒", "食品", "饮料"],
    "电力设备": ["电气设备", "新能源", "光伏", "风电", "电池"],
    "计算机": ["互联网服务", "软件开发", "计算机"],
    "汽车": ["汽车", "汽车零部件", "汽车整车"],
    "机械设备": ["通用机械", "专用设备", "工业机械", "仪器仪表"],
    "基础化工": ["化学制品", "化工", "塑料", "橡胶"],
    "有色金属": ["有色金属", "贵金属", "稀有金属"],
    "公用事业": ["电力", "公用事业", "燃气", "水务"],
    "房地产": ["房地产", "房地产开发"],
    "建筑装饰": ["工程建设", "建筑", "装修装饰"],
    "交通运输": ["交通运输", "物流", "航运", "港口"],
    "家用电器": ["家电"],
    "农林牧渔": ["农牧饲渔", "农业", "渔业", "畜牧业"],
    "国防军工": ["航天航空", "船舶", "军工"],
    "煤炭": ["煤炭"],
    "钢铁": ["钢铁"],
    "石油石化": ["石油", "石化"],
    "通信": ["通信"],
    "传媒": ["文化传媒", "游戏", "影视"],
    "纺织服饰": ["纺织服装"],
    "轻工制造": ["造纸印刷", "包装材料"],
    "社会服务": ["旅游酒店", "教育"],
    "商贸零售": ["商业百货", "贸易"],
    "环保": ["环保"],
    "美容护理": ["美容护理", "化妆品"],
}


def _map_to_sw_industry(em_board_name: str) -> str:
    """东方财富板块名 → 申万一级行业名"""
    for sw_name, keywords in INDUSTRY_GROUPS.items():
        for kw in keywords:
            if kw in em_board_name:
                return sw_name
    return em_board_name


def fetch_sector_capital_flow(indicator: str = "今日") -> Optional[pd.DataFrame]:
    """
    获取板块资金流向排名（东方财富行业资金流）。

    Args:
        indicator: "今日" | "5日" | "10日" | "20日"

    Returns:
        DataFrame with columns: board_name, change_pct, main_inflow, super_large_inflow, ...
    """
    import akshare as ak
    df = _safe_call(ak.stock_sector_fund_flow_rank,
                    indicator=indicator, sector_type="行业资金流")
    if df is None or len(df) == 0:
        return None

    # 列名映射
    col_map = {
        "名称": "board_name",
        "今日涨跌幅": "chg_today",
        f"{indicator}涨跌幅": "chg",
        f"{indicator}主力净流入-净额": "main_inflow",
        f"{indicator}主力净流入-净占比": "main_inflow_pct",
        f"{indicator}超大单净流入-净额": "super_large_inflow",
        f"{indicator}超大单净流入-净占比": "super_large_inflow_pct",
        f"{indicator}大单净流入-净额": "large_inflow",
        f"{indicator}大单净流入-净占比": "large_inflow_pct",
        f"{indicator}中单净流入-净额": "mid_inflow",
        f"{indicator}中单净流入-净占比": "mid_inflow_pct",
        f"{indicator}小单净流入-净额": "small_inflow",
        f"{indicator}小单净流入-净占比": "small_inflow_pct",
        f"{indicator}主力净流入最大股": "top_stock",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # 映射到申万行业
    df["sw_industry"] = df["board_name"].apply(_map_to_sw_industry)

    return df


def analyze_sector_flow(inflow_5d_df, inflow_20d_df, top_n=15):
    """
    综合分析板块资金流向，识别长线资金信号。

    逻辑：
    - 超大单净流入 = 机构/长线资金代理指标（大单也可能是游资）
    - 5日流入 > 0 且 20日流入 > 0 → 持续流入（accumulation）
    - 5日流出但 20日流入 → 短期扰动（still accumulating）
    - 5日流入但 20日流出 → 刚转流入（early signal）
    - 5日流出且 20日流出 → 持续流出（distribution）

    Returns:
        List[Dict] — 按申万行业汇总的资金流分析
    """
    # 汇总到申万一级行业
    def aggregate(df):
        if df is None or len(df) == 0:
            return pd.DataFrame()
        agg_cols = {
            "main_inflow": "sum",
            "super_large_inflow": "sum",
            "large_inflow": "sum",
            "mid_inflow": "sum",
            "small_inflow": "sum",
        }
        available = [c for c in agg_cols if c in df.columns]
        if not available:
            return pd.DataFrame()
        grouped = df.groupby("sw_industry")[available].sum().reset_index()
        return grouped

    agg5 = aggregate(inflow_5d_df)
    agg20 = aggregate(inflow_20d_df)

    if agg5.empty and agg20.empty:
        return []

    # 合并5日和20日数据
    if not agg5.empty and not agg20.empty:
        merged = agg5.merge(agg20, on="sw_industry", how="outer",
                           suffixes=("_5d", "_20d")).fillna(0)
    elif not agg5.empty:
        merged = agg5.copy()
        for c in merged.columns:
            if c != "sw_industry":
                merged = merged.rename(columns={c: f"{c}_5d"})
                merged[f"{c}_20d"] = 0
    else:
        merged = agg20.copy()
        for c in merged.columns:
            if c != "sw_industry":
                merged = merged.rename(columns={c: f"{c}_20d"})
                merged[f"{c}_5d"] = 0

    # 分类信号
    results = []
    for _, row in merged.iterrows():
        name = row["sw_industry"]
        sl_5d = row.get("super_large_inflow_5d", 0)
        sl_20d = row.get("super_large_inflow_20d", 0)
        main_5d = row.get("main_inflow_5d", 0)

        if sl_5d > 0 and sl_20d > 0:
            signal = "持续流入 🔵"
        elif sl_5d < 0 and sl_20d > 0:
            signal = "短期扰动 🟡"
        elif sl_5d > 0 and sl_20d < 0:
            signal = "刚转流入 🟢"
        else:
            signal = "持续流出 🔴"

        results.append({
            "industry": name,
            "signal": signal,
            "super_large_5d_yi": round(sl_5d / 1e8, 2),
            "super_large_20d_yi": round(sl_20d / 1e8, 2),
            "main_inflow_5d_yi": round(main_5d / 1e8, 2),
        })

    # 按5日超大单流入排序
    results.sort(key=lambda x: x["super_large_5d_yi"], reverse=True)
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

    # 2. 板块主力资金流（今日 + 10日）
    logger.info("   [2/4] 板块主力资金流向（今日+10日）...")
    inflow_today = fetch_sector_capital_flow("今日")
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))
    inflow_10d = fetch_sector_capital_flow("10日")
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    if inflow_today is not None or inflow_10d is not None:
        result["sector_flow"] = analyze_sector_flow(inflow_today, inflow_10d)
    else:
        logger.warning("   ⚠️ 板块资金流数据不可用（push2.eastmoney.com 可能被代理拦截）")
        result["_warnings"] = result.get("_warnings", [])
        result["_warnings"].append("sector_flow_unavailable")

    # 3. 产业资本回购
    logger.info("   [3/4] 上市公司回购汇总...")
    result["repurchase"] = fetch_repurchase_summary(weeks=4)
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))

    # 4. 板块估值分位
    logger.info("   [4/4] 板块估值分位数...")
    result["valuation"] = fetch_valuation_snapshot()

    # === 综合信号合成 ===
    signals = _synthesize_signals(result)
    result["signals"] = signals

    logger.info("📊 长线资金监测：完成")
    return result


def _synthesize_signals(data: Dict) -> Dict:
    """
    综合多维度数据，生成长线资金抄底信号。

    信号强度逻辑：
    - 强信号：北向流入 + 超大单流入 + 估值低位 + 回购活跃
    - 中信号：满足2-3个条件
    - 弱信号：仅1个条件

    注意：这是一个启发式规则，不等同于投资建议。
    """
    signals = {"strong": [], "moderate": [], "weak": [], "avoid": []}

    # 北向资金方向
    nb = data.get("northbound", {})
    nb_today = nb.get("today", {})
    nb_net = nb_today.get("northbound_net_yi", 0) or 0
    nb_bullish = nb_net > 0

    # 板块资金流
    sector_flows = data.get("sector_flow", [])

    # 估值分位
    valuations = {v["board"]: v for v in data.get("valuation", []) if "error" not in v}

    # 回购
    repurchase = data.get("repurchase", {})
    repurchase_amount = repurchase.get("total_amount_yi", 0)

    # 综合判断每个板块
    for sf in sector_flows[:20]:  # TOP20
        industry = sf["industry"]
        sig = sf["signal"]
        sl_5d = sf["super_large_5d_yi"]

        val = valuations.get(industry, {})
        pe_pct = val.get("pe_percentile")

        score = 0
        reasons = []

        # 超大单流入
        if sl_5d > 5:
            score += 2
            reasons.append(f"超大单5日净流入{sl_5d:.1f}亿")
        elif sl_5d > 0:
            score += 1
            reasons.append(f"超大单小幅流入{sl_5d:.1f}亿")

        # 估值低位
        if pe_pct is not None and pe_pct < 20:
            score += 2
            reasons.append(f"PE分位仅{pe_pct:.0f}%（历史低位）")
        elif pe_pct is not None and pe_pct < 40:
            score += 1
            reasons.append(f"PE分位{pe_pct:.0f}%（偏低）")

        # 持续流入
        if "持续流入" in sig:
            score += 1
            reasons.append("5日+20日持续流入")
        elif "刚转流入" in sig:
            score += 1
            reasons.append("近期转流入")

        # 北向资金配合
        if nb_bullish and sl_5d > 0:
            score += 1
            reasons.append("北向同期净流入")

        entry = {"industry": industry, "score": score, "reasons": reasons,
                 "super_large_5d_yi": sl_5d, "pe_percentile": pe_pct}

        if score >= 4:
            signals["strong"].append(entry)
        elif score >= 2:
            signals["moderate"].append(entry)
        elif score >= 1:
            signals["weak"].append(entry)
        else:
            signals["avoid"].append(entry)

    # 排序
    for key in signals:
        signals[key].sort(key=lambda x: x["score"], reverse=True)

    # 添加回购活跃度标记
    if repurchase_amount > 50:
        signals["_repurchase_alert"] = f"近4周回购金额{repurchase_amount:.0f}亿元，产业资本积极"

    return signals


# ============================================================
# 格式化输出（飞书推送用）
# ============================================================

def format_for_feishu(data: Dict) -> str:
    """
    将长线资金数据格式化为飞书推送卡片文本。
    设计为追加到 rally_health_monitor 或独立卡片。
    """
    if not data or data.get("_empty"):
        return ""

    lines = []
    lines.append("---")
    lines.append("### 🏦 长线资金监测")

    # 一、北向资金方向
    nb = data.get("northbound", {})
    if nb and "error" not in nb:
        today_nb = nb.get("today", {})
        hist = nb.get("historical", {})

        if today_nb:
            nb_emoji = "🟢" if (today_nb.get("northbound_net_yi") or 0) > 0 else "🔴"
            lines.append(f"**北向资金**: {nb_emoji} 北向 {today_nb.get('northbound_net_yi', '?'):}亿 | "
                         f"南向 {today_nb.get('southbound_net_yi', '?'):}亿 | "
                         f"净流向 {today_nb.get('net_flow_yi', '?'):}亿")

        if hist:
            lines.append(f"  ↳ 最后有效净买入数据: {hist.get('last_valid_date', '?')} "
                         f"(近20日 {hist.get('last_20d_net_yi', '?'):}亿)")
        if nb.get("note"):
            lines.append(f"  ↳ {nb['note']}")
    else:
        lines.append("**北向资金**: 数据不可用")

    # 二、板块资金流 TOP
    sector_flows = data.get("sector_flow", [])
    if sector_flows:
        lines.append("")
        lines.append("**板块超大单净流入 TOP8**（5日）：")
        top8 = [sf for sf in sector_flows if sf["super_large_5d_yi"] > 0][:8]
        for sf in top8:
            lines.append(f"- {sf['industry']}: {sf['super_large_5d_yi']:+.1f}亿 "
                         f"（20日{sf['super_large_20d_yi']:+.1f}亿）{sf['signal']}")

        # 流出板块
        bottom5 = [sf for sf in sector_flows if sf["super_large_5d_yi"] < 0][:5]
        if bottom5:
            names = "、".join([f"{s['industry']}({s['super_large_5d_yi']:.0f}亿)" for s in bottom5])
            lines.append(f"持续流出: {names}")

    # 三、估值分位（仅列出 <20% 分位的板块）
    valuations = data.get("valuation", [])
    if valuations:
        cheap = [v for v in valuations if v.get("pe_percentile") is not None and v["pe_percentile"] < 20]
        if cheap:
            lines.append("")
            lines.append("**PE处于历史低位**（<20%分位）：")
            for v in cheap[:5]:
                extra = f" PB{v['pb_percentile']:.0f}%分位" if v.get("pb_percentile") else ""
                lines.append(f"- {v['board']}: PE {v.get('current_pe', '?')}（{v['pe_percentile']:.0f}%分位）{extra}")

    # 四、回购动态
    rep = data.get("repurchase", {})
    if rep and "error" not in rep:
        lines.append("")
        lines.append(f"**产业资本回购**（{rep.get('period', '近4周')}）："
                     f"{rep.get('total_companies', 0)}家公司，合计{rep.get('total_amount_yi', 0):.1f}亿元")
        top_rep = rep.get("top_individual", [])[:5]
        if top_rep:
            rep_strs = [f"{r['name']}({r['amount_yi']:.1f}亿)" for r in top_rep]
            lines.append(f"TOP5: {' | '.join(rep_strs)}")

    # 五、综合信号
    signals = data.get("signals", {})
    if signals:
        strong = signals.get("strong", [])
        moderate = signals.get("moderate", [])

        if strong or moderate:
            lines.append("")
            lines.append("**🎯 长线资金信号汇总：**")

            if strong:
                names = "、".join([s["industry"] for s in strong])
                lines.append(f"🟢 **强信号**: {names}")
                for s in strong:
                    lines.append(f"  → {s['industry']}: {' | '.join(s['reasons'])}")

            if moderate:
                names = "、".join([s["industry"] for s in moderate[:5]])
                lines.append(f"🟡 **中信号**: {names}")

        # 回购预警
        if signals.get("_repurchase_alert"):
            lines.append(f"⚠️ {signals['_repurchase_alert']}")

    # 数据可用性
    if data.get("_warnings"):
        lines.append(f"⚠️ 部分数据不可用: {', '.join(data['_warnings'])}")

    return "\n".join(lines)


# ============================================================
# 季频手动清单（用于8月底半年报披露后手动触发）
# ============================================================

SEASON_CHECKLIST = """
## 季频长线资金手动核查清单

### 社保基金持仓（每年3/4/8/10月底披露）
- [ ] 东方财富 → 数据中心 → 股东研究 → 社保基金持仓
- [ ] 按行业汇总新增/增持个股数量
- [ ] 关注：医药生物、电子、基础化工

### 保险资金行业配置（险企季报/年报）
- [ ] 五大上市险企：中国人保、中国人寿、中国平安、中国太保、新华保险
- [ ] 查看"权益投资"部分的行业分布
- [ ] 关注：银行、公用事业、交通运输

### 公募基金重仓行业（基金季报/半年报/年报）
- [ ] 天天基金 → 基金数据 → 重仓股分析
- [ ] 按行业汇总持仓变化
- [ ] 关注：电子仓位是否从43.4%极端位置回落

### 产业资本增持
- [ ] 巨潮资讯网 → 增持公告
- [ ] 按行业汇总增持金额
"""


def season_checklist() -> str:
    """返回季频手动核查清单"""
    return SEASON_CHECKLIST


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
        "signals": data["signals"],
        "_warnings": data.get("_warnings", []),
    }, ensure_ascii=False, indent=2, default=str))

    # 打印飞书格式
    print("\n" + "=" * 60)
    print(format_for_feishu(data))

    # 打印季频清单
    print("\n" + "=" * 60)
    print(season_checklist())
