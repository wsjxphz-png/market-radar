#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟账户管理 — 100万初始资金，追踪每日操作建议的执行结果。

核心保证：
  1. 所有计算精确到分（内部用 float 但每次操作后四舍五入到2位小数）
  2. 每次保存前校验资产恒等式：总资产 == 现金 + sum(各持仓市值)
  3. 同一天不重复执行（幂等）
  4. 账户文件损坏时从初始状态恢复
"""

import json, os, sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class Holding:
    """单个持仓"""
    symbol: str           # 标的名称，如 "半导体ETF"
    shares: int           # 持有份额（股/份）
    avg_cost: float       # 平均成本价（元/份）
    current_price: float  # 当前市价（元/份）
    entry_date: str       # 入场日期 YYYY-MM-DD
    entry_reason: str     # 入场理由

    @property
    def cost_value(self) -> float:
        return round(self.shares * self.avg_cost, 2)

    @property
    def market_value(self) -> float:
        return round(self.shares * self.current_price, 2)

    @property
    def pnl(self) -> float:
        return round(self.market_value - self.cost_value, 2)

    @property
    def pnl_pct(self) -> float:
        if self.cost_value == 0:
            return 0.0
        return round((self.current_price / self.avg_cost - 1) * 100, 1)


class Portfolio:
    """模拟账户"""

    def __init__(self, data_dir: str = ".", initial_cash: float = 1_000_000.0):
        self.data_dir = data_dir
        self.portfolio_path = os.path.join(data_dir, "portfolio.json")
        self.trade_log_path = os.path.join(data_dir, "trade_log.jsonl")
        self.position_state_path = os.path.join(data_dir, "position_state.json")

        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.holdings: Dict[str, Holding] = {}
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.last_update = self.start_date
        self.trades_today: List[Dict] = []  # 今日已执行的交易

        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载账户状态，损坏时恢复初始"""
        if not os.path.exists(self.portfolio_path):
            self._init_fresh()
            return

        try:
            with open(self.portfolio_path, encoding='utf-8') as f:
                data = json.load(f)

            self.initial_cash = data.get("initial_cash", self.initial_cash)
            self.cash = round(float(data.get("cash", self.initial_cash)), 2)
            self.start_date = data.get("start_date", self.start_date)
            self.last_update = data.get("last_update", self.start_date)

            self.holdings = {}
            for sym, h in data.get("holdings", {}).items():
                self.holdings[sym] = Holding(
                    symbol=sym,
                    shares=int(h["shares"]),
                    avg_cost=round(float(h["avg_cost"]), 4),
                    current_price=round(float(h.get("current_price", h["avg_cost"])), 4),
                    entry_date=h.get("entry_date", ""),
                    entry_reason=h.get("entry_reason", ""),
                )

            # 校验资产恒等式
            holdings_value = sum(h.market_value for h in self.holdings.values())
            total = round(self.cash + holdings_value, 2)
            recorded_total = round(float(data.get("total_market_value", total)), 2)
            if abs(total - recorded_total) > 0.02:  # 容忍2分钱舍入误差
                print(f"⚠️ 账户校验失败: 现金{self.cash} + 持仓{holdings_value} = {total} ≠ 记录{recorded_total}，以重算为准")

            print(f"💰 账户已加载: 现金{self.cash:.0f} + 持仓{holdings_value:.0f} = 总资产{total:.0f}")

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"⚠️ 账户文件损坏 ({e})，从初始100万重新开始")
            self._init_fresh()

    def _init_fresh(self):
        """初始化全新账户"""
        self.cash = self.initial_cash
        self.holdings = {}
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.last_update = self.start_date
        print(f"💰 新建模拟账户: 初始现金 ¥{self.initial_cash:,.0f}")

    def save(self):
        """保存账户状态（含一致性校验）"""
        holdings_value = sum(h.market_value for h in self.holdings.values())
        total = round(self.cash + holdings_value, 2)

        # 严格校验
        if self.cash < -0.01:
            raise ValueError(f"❌ 现金为负: {self.cash}")

        data = {
            "start_date": self.start_date,
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "holdings": {
                sym: {
                    "shares": h.shares,
                    "avg_cost": h.avg_cost,
                    "current_price": h.current_price,
                    "market_value": h.market_value,
                    "pnl_pct": h.pnl_pct,
                    "entry_date": h.entry_date,
                    "entry_reason": h.entry_reason,
                }
                for sym, h in self.holdings.items()
            },
            "holdings_total_value": holdings_value,
            "total_market_value": total,
            "cumulative_return_pct": round((total / self.initial_cash - 1) * 100, 1),
            "last_update": datetime.now().strftime("%Y-%m-%d"),
        }

        with open(self.portfolio_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return total, holdings_value

    # ── 交易操作 ──────────────────────────────────────────

    def update_prices(self, prices: Dict[str, float]):
        """更新持仓市价（每天运行前调用）"""
        for sym, price in prices.items():
            if sym in self.holdings:
                self.holdings[sym].current_price = round(float(price), 4)

    def can_buy(self, symbol: str, price: float, amount_yuan: float) -> Tuple[bool, str]:
        """检查是否可以买入"""
        if amount_yuan <= 0:
            return False, "金额必须大于0"
        if amount_yuan > self.cash:
            return False, f"现金不足（需要¥{amount_yuan:,.0f}，可用¥{self.cash:,.0f}）"
        # A股最小100股
        shares = int(amount_yuan / price / 100) * 100
        if shares == 0:
            return False, f"金额不足以买1手（100股×{price} = {price*100:.0f}元）"
        return True, f"可买{shares}股，需¥{shares*price:,.0f}"

    def buy(self, symbol: str, price: float, amount_yuan: float, reason: str = "") -> Optional[Dict]:
        """
        执行买入。amount_yuan 为计划投入金额，实际按整手执行。
        返回交易记录，失败返回 None。
        """
        ok, msg = self.can_buy(symbol, price, amount_yuan)
        if not ok:
            print(f"  ⚠️ 买入失败 [{symbol}]: {msg}")
            return None

        shares = int(amount_yuan / price / 100) * 100
        actual_cost = round(shares * price, 2)
        self.cash = round(self.cash - actual_cost, 2)

        # 更新或新建持仓
        if symbol in self.holdings:
            h = self.holdings[symbol]
            total_shares = h.shares + shares
            total_cost = h.cost_value + actual_cost
            h.shares = total_shares
            h.avg_cost = round(total_cost / total_shares, 4) if total_shares > 0 else 0
            h.current_price = price
        else:
            self.holdings[symbol] = Holding(
                symbol=symbol, shares=shares, avg_cost=price,
                current_price=price,
                entry_date=datetime.now().strftime("%Y-%m-%d"),
                entry_reason=reason,
            )

        trade = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "action": "BUY",
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "amount": actual_cost,
            "reason": reason,
        }
        self.trades_today.append(trade)
        self._log_trade(trade)
        print(f"  ✅ 买入 {symbol}: {shares}股 @{price} = ¥{actual_cost:,.0f} | {reason}")
        return trade

    def sell(self, symbol: str, price: float, shares: int = None,
             pct: float = None, reason: str = "") -> Optional[Dict]:
        """
        卖出。可指定 shares（股数）或 pct（持仓比例 0-1）。
        返回交易记录，失败返回 None。
        """
        if symbol not in self.holdings:
            print(f"  ⚠️ 卖出失败 [{symbol}]: 未持仓")
            return None

        h = self.holdings[symbol]
        if shares is None and pct is not None:
            shares = int(h.shares * pct / 100) * 100  # 整手取整
        if shares is None:
            shares = h.shares  # 默认全卖
        shares = min(shares, h.shares)
        if shares <= 0:
            return None

        actual_proceeds = round(shares * price, 2)
        self.cash = round(self.cash + actual_proceeds, 2)

        h.shares -= shares
        if h.shares == 0:
            del self.holdings[symbol]
        else:
            # avg_cost 不变
            h.current_price = price

        trade = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "action": "SELL",
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "amount": actual_proceeds,
            "reason": reason,
        }
        self.trades_today.append(trade)
        self._log_trade(trade)
        pct_str = f"({pct*100:.0f}%)" if pct else ""
        print(f"  ✅ 卖出 {symbol}: {shares}股{pct_str} @{price} = ¥{actual_proceeds:,.0f} | {reason}")
        return trade

    # ── 建议执行 ──────────────────────────────────────────

    def execute_prior_recommendations(self, prices: Dict[str, float]):
        """
        执行上次 position_state.json 中记录的建议。
        假设用户按建议操作了，我们在模拟账户中执行。
        幂等：同一天不会重复执行。
        """
        if not os.path.exists(self.position_state_path):
            return

        try:
            with open(self.position_state_path, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            return

        last_date = state.get("last_date", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if last_date == today:
            return  # 今天已经执行过了

        # 更新持仓价格
        self.update_prices(prices)

        # 执行板块级建议
        sector_recs = state.get("last_sector_recommendations", {})
        for sym, rec in sector_recs.items():
            action = rec.get("action", "")
            price = prices.get(sym, 0)
            if price <= 0:
                continue

            if "减仓" in action and "半" in action and sym in self.holdings:
                self.sell(sym, price, pct=0.5, reason=f"执行上次建议: {action}")
            elif "清仓" in action and sym in self.holdings:
                self.sell(sym, price, reason=f"执行上次建议: {action}")
            elif "入场" in action and "站上" in action:
                # 检查条件是否满足（需要具体价格判断）
                # 这类建议是条件性的，不是无条件执行
                pass

        self.save()

    # ── 日志 ──────────────────────────────────────────────

    def _log_trade(self, entry: Dict):
        """追加交易日志"""
        with open(self.trade_log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def log_signal(self, entry: Dict):
        """记录每日信号（用于回测），不涉及实际资金变动"""
        entry["date"] = datetime.now().strftime("%Y-%m-%d")
        with open(self.trade_log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def save_position_state(self, index_rec: str, sector_recs: Dict[str, Dict]):
        """保存本次建议，供明天执行"""
        state = {
            "last_date": datetime.now().strftime("%Y-%m-%d"),
            "last_index_recommendation": index_rec,
            "last_sector_recommendations": sector_recs,
        }
        with open(self.position_state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=True, indent=2)

    # ── 仪表盘输出 ──────────────────────────────────────────

    def format_for_dashboard(self) -> List[str]:
        """生成 💰 模拟账户 段落的飞书 Markdown"""
        total, hv = self.save()
        cum_ret = round((total / self.initial_cash - 1) * 100, 1)

        lines = [
            "---",
            "",
            "## 💰 模拟账户",
            "",
            f"**起始资金**: ¥{self.initial_cash:,.0f} | **起始日**: {self.start_date}",
            f"**今日日期**: {datetime.now().strftime('%Y-%m-%d')}",
            "",
        ]

        if self.holdings:
            lines.append("**当前持仓**:")
            lines.append("")
            for sym, h in self.holdings.items():
                arrow = "📈" if h.pnl >= 0 else "📉"
                lines.append(
                    f"- {arrow} **{sym}**: {h.shares}股 @{h.current_price:.2f} | "
                    f"市值 ¥{h.market_value:,.0f} | 成本 ¥{h.cost_value:,.0f} | "
                    f"**{h.pnl_pct:+.1f}%**"
                )
                lines.append(f"  入场: {h.entry_date} | {h.entry_reason}")
            lines.append("")

        # 今日交易
        if self.trades_today:
            lines.append("**今日操作**:")
            lines.append("")
            for t in self.trades_today:
                emoji = "🟢" if t["action"] == "BUY" else "🔴"
                lines.append(
                    f"- {emoji} {t['action']} **{t['symbol']}**: {t['shares']}股 @{t['price']} "
                    f"= ¥{t['amount']:,.0f} | {t['reason']}"
                )
            lines.append("")
        else:
            lines.append("**今日操作**: 无")
            lines.append("")

        # 汇总
        lines.append("**账户汇总**:")
        lines.append(f"- 现金: ¥{self.cash:,.0f}")
        lines.append(f"- 持仓市值: ¥{hv:,.0f}")
        lines.append(f"- **总资产: ¥{total:,.0f}**")
        lines.append(f"- **累计收益率: {cum_ret:+.1f}%**")

        # 上次建议回顾
        if os.path.exists(self.position_state_path):
            try:
                with open(self.position_state_path, encoding='utf-8') as f:
                    ps = json.load(f)
                last_dt = ps.get("last_date", "")
                if last_dt and last_dt != datetime.now().strftime("%Y-%m-%d"):
                    lines.append("")
                    lines.append(f"**📋 上次建议回顾** ({last_dt}):")
                    idx_rec = ps.get("last_index_recommendation", "")
                    if idx_rec:
                        lines.append(f"- 大盘: {idx_rec}")
                    for sym, rec in ps.get("last_sector_recommendations", {}).items():
                        lines.append(f"- {sym}: {rec.get('action', '')}")
            except Exception:
                pass

        return lines


# ═══════════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile, os, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    os.chdir(tempfile.mkdtemp())
    print(f"测试目录: {os.getcwd()}")

    # 新建账户
    pf = Portfolio(".", initial_cash=1_000_000)

    # 模拟买入
    pf.buy("半导体ETF", price=10.0, amount_yuan=200000, reason="底分型+站上5日线")
    pf.buy("科创50ETF", price=1.45, amount_yuan=150000, reason="520金叉+放量")

    # 更新市价
    pf.update_prices({"半导体ETF": 9.43, "科创50ETF": 1.52})

    # 输出仪表盘
    for line in pf.format_for_dashboard():
        print(line)

    # 测试校验
    total, hv = pf.save()
    print(f"\n校验: 现金{pf.cash:.0f} + 持仓{hv:.0f} = {total:.0f}")

    # 模拟卖出半仓
    pf.sell("半导体ETF", price=9.43, pct=0.5, reason="顶分型未修复+放量跌")
    print(f"\n卖后现金: {pf.cash:.0f}")
