#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股数据层 — 多源融合，自动降级。

数据源优先级:
  1. adata (pip install adata) — 最全面，概念/行业/资金流/个股K线
  2. akshare (已有) — 兜底，stock_zh_a_hist 个股日线
  3. qstock (pip install qstock) — 选股引擎，RPS/MM趋势/财务过滤

用法:
  from stock_data import StockData
  sd = StockData()
  df = sd.get_stock_hist("000001", days=250)  # 平安银行日线
  top_stocks = sd.screen_stocks(sector="半导体", conditions=["金叉", "年线上方"])
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings("ignore")


class StockData:
    """A股个股数据获取与筛选"""

    def __init__(self):
        self._adata_ok = self._check_adata()
        self._qstock_ok = self._check_qstock()
        print(f"  📊 个股数据: adata={'✅' if self._adata_ok else '❌'}, qstock={'✅' if self._qstock_ok else '❌'}")

    def _check_adata(self) -> bool:
        try:
            import adata
            return True
        except ImportError:
            return False

    def _check_qstock(self) -> bool:
        try:
            import qstock as qs
            return True
        except ImportError:
            return False

    # ── 个股K线 ────────────────────────────────────────

    def get_stock_hist(self, code: str, days: int = 250) -> Optional[pd.DataFrame]:
        """
        获取个股日线K线。
        code: 纯数字如 "000001" 或 "600000"
        """
        # 尝试 adata
        if self._adata_ok:
            try:
                import adata
                # adata 格式: sz000001 或 sh600000
                full_code = self._to_adata_code(code)
                df = adata.stock.market.get_market(full_code, k_type=1, start_date=(datetime.now()-timedelta(days=days+30)).strftime("%Y-%m-%d"))
                if df is not None and len(df) >= 10:
                    return self._normalize_columns(df, days)
            except Exception:
                pass

        # 降级到 akshare
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=(datetime.now()-timedelta(days=days+30)).strftime("%Y%m%d"),
                                    end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
            if df is not None and len(df) >= 10:
                return self._normalize_columns(df, days)
        except Exception:
            pass

        return None

    def get_stocks_in_sector(self, sector_name: str) -> List[str]:
        """获取某板块的成分股代码列表"""
        if self._qstock_ok:
            try:
                import qstock as qs
                # 通过问财接口获取
                df = qs.wencai(f"{sector_name}板块")
                if df is not None and len(df) > 0:
                    codes = df["代码"].tolist() if "代码" in df.columns else []
                    return codes[:50]  # 每板块最多50只
            except Exception:
                pass

        # 降级：adata 行业成分股
        if self._adata_ok:
            try:
                import adata
                df = adata.stock.info.get_all_stock()
                if df is not None:
                    # 按行业筛选
                    if "industry" in df.columns:
                        matched = df[df["industry"].str.contains(sector_name, na=False)]
                        return matched["stock_code"].tolist()[:50]
            except Exception:
                pass

        return []

    # ── 选股筛选 ────────────────────────────────────────

    def screen_stocks(self, sector: str = None, conditions: List[str] = None) -> pd.DataFrame:
        """
        按条件筛选个股。
        conditions 示例: ["金叉", "年线上方", "放量"]
        """
        conditions = conditions or []
        candidates = []

        # 用 qstock 问财选股
        if self._qstock_ok and conditions:
            try:
                import qstock as qs
                query_parts = []
                if sector:
                    query_parts.append(f"{sector}板块")
                for c in conditions:
                    if c == "金叉":
                        query_parts.append("5日均线上穿20日均线")
                    elif c == "年线上方":
                        query_parts.append("股价大于250日均线")
                    elif c == "放量":
                        query_parts.append("成交量大于5日均量1.2倍")
                    elif c == "周线向上":
                        query_parts.append("周线多头排列")
                    elif c == "多头排列":
                        query_parts.append("均线多头排列")
                    elif c == "底分型":
                        query_parts.append("底分型")
                    elif c == "低估值":
                        query_parts.append("市盈率低于30")
                    elif c == "主力流入":
                        query_parts.append("主力资金净流入")
                    else:
                        query_parts.append(c)

                query = "，".join(query_parts)
                df = qs.wencai(query)
                if df is not None and len(df) > 0:
                    return df.head(30)
            except Exception as e:
                print(f"  ⚠️ qstock选股失败: {e}")

        # 降级：手动在成分股中过滤
        codes = self.get_stocks_in_sector(sector) if sector else []
        if not codes:
            return pd.DataFrame()

        for code in codes[:30]:
            hist = self.get_stock_hist(str(code), days=60)
            if hist is None or len(hist) < 20:
                continue
            ok = True
            for c in conditions:
                if not self._check_condition(hist, c):
                    ok = False
                    break
            if ok:
                candidates.append({"code": code, "name": str(code)})

        return pd.DataFrame(candidates)

    # ── 市场温度数据 ──────────────────────────────────────

    def get_market_breadth(self) -> Dict:
        """获取涨跌家数、涨停跌停数"""
        result = {"up_count": 0, "down_count": 0, "limit_up": 0, "limit_down": 0, "total": 0}
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                result["total"] = len(df)
                result["up_count"] = int((df["涨跌幅"] > 0).sum())
                result["down_count"] = int((df["涨跌幅"] < 0).sum())
                result["limit_up"] = int((df["涨跌幅"] >= 9.9).sum())
                result["limit_down"] = int((df["涨跌幅"] <= -9.9).sum())
        except Exception:
            pass
        return result

    def get_market_volume(self) -> Dict:
        """获取全市场成交额"""
        result = {"total_amount": 0, "avg_amount_20d": 0, "ratio": 1.0}
        try:
            import akshare as ak
            df = ak.stock_zh_index_daily(symbol="sh000001")
            if df is not None and len(df) >= 20:
                amt_col = [c for c in df.columns if "amount" in str(c).lower() or "成交额" in str(c)]
                if not amt_col:
                    amt_col = [c for c in df.columns if "volume" not in str(c).lower() and "收盘" not in str(c)][-1:]
                if amt_col:
                    recent = df[amt_col[0]].tail(20)
                    result["total_amount"] = float(recent.iloc[-1])
                    result["avg_amount_20d"] = float(recent.mean())
                    result["ratio"] = result["total_amount"] / result["avg_amount_20d"] if result["avg_amount_20d"] > 0 else 1.0
        except Exception:
            pass
        return result

    def get_north_flow(self) -> Dict:
        """获取北向资金流向"""
        result = {"net_flow": 0, "direction": "neutral"}
        try:
            import akshare as ak
            df = ak.stock_hsgt_north_net_flow_in_em()
            if df is not None and len(df) > 0:
                result["net_flow"] = float(df["value"].iloc[-1]) if "value" in df.columns else float(df.iloc[-1, -1])
                result["direction"] = "inflow" if result["net_flow"] > 0 else "outflow"
        except Exception:
            pass
        return result

    def get_sector_fund_flow(self) -> pd.DataFrame:
        """获取板块资金流向TOP"""
        try:
            import akshare as ak
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is not None and len(df) > 0:
                return df.head(10)
        except Exception:
            pass
        return pd.DataFrame()

    # ── 工具方法 ────────────────────────────────────────

    def _to_adata_code(self, code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("0", "3")):
            return f"sz{code}"
        return f"sh{code}"

    def _normalize_columns(self, df: pd.DataFrame, days: int) -> pd.DataFrame:
        """统一列名"""
        col_map = {
            "日期": "date", "date": "date",
            "开盘": "open", "open": "open",
            "收盘": "close", "close": "close",
            "最高": "high", "high": "high",
            "最低": "low", "low": "low",
            "成交量": "volume", "volume": "volume",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(days).reset_index(drop=True)
        for col in ["close", "open", "high", "low", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _check_condition(self, df: pd.DataFrame, condition: str) -> bool:
        """检查个股是否满足某个简单条件"""
        if len(df) < 20:
            return False
        try:
            close = df["close"].values
            if condition == "金叉":
                ma5 = pd.Series(close).rolling(5).mean().values
                ma20 = pd.Series(close).rolling(20).mean().values
                return ma5[-1] > ma20[-1] and ma5[-2] <= ma20[-2]
            elif condition == "年线上方":
                if len(close) < 250:
                    ma250 = pd.Series(close).rolling(len(close)).mean().values
                else:
                    ma250 = pd.Series(close).rolling(250).mean().values
                return close[-1] > ma250[-1] if not np.isnan(ma250[-1]) else False
            elif condition == "放量":
                vol = df["volume"].values
                avg_vol = pd.Series(vol).rolling(20).mean().values
                return vol[-1] > avg_vol[-1] * 1.2 if not np.isnan(avg_vol[-1]) else False
            elif condition == "多头排列":
                ma5 = pd.Series(close).rolling(5).mean().values
                ma20 = pd.Series(close).rolling(20).mean().values
                ma60 = pd.Series(close).rolling(60).mean().values
                return close[-1] > ma5[-1] > ma20[-1] > ma60[-1] if not any(np.isnan([ma5[-1],ma20[-1],ma60[-1]])) else False
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    sd = StockData()

    # 测试市场宽度
    breadth = sd.get_market_breadth()
    print(f"涨跌: {breadth['up_count']}↑ / {breadth['down_count']}↓ | 涨停{breadth['limit_up']} 跌停{breadth['limit_down']}")

    # 测试成交额
    vol = sd.get_market_volume()
    print(f"成交额: {vol['total_amount']/1e8:.0f}亿 | 20日均{vol['avg_amount_20d']/1e8:.0f}亿 | 比值{vol['ratio']:.1f}x")

    # 测试北向资金
    north = sd.get_north_flow()
    print(f"北向: {north['net_flow']:+.1f}亿 ({north['direction']})")
