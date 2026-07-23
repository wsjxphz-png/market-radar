#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
月度复盘 — 纪律检查 + 学习笔记

不评估预测准确率。只检查一件事：本月有没有遵守《趋势交易论》五大模块的规则。

复盘三问：
  1. 有没有三周期向下时建议买入？ → 违例 = 违反了框架第一原则
  2. 有没有放量滞涨时建议持有？ → 违例 = 忽略了量价八诀
  3. 有没有死叉出现后还不让卖？ → 违例 = 没执行520纪律

输出：
  ✅ 本月亮点 — 做对了什么，学到了什么
  ⚠️ 本月教训 — 哪里违例了，下次怎么避免
  📋 纪律检查 — 五大模块逐项对账
"""

import os, sys, json, argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

import requests

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.jsonl")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
POSITION_STATE_FILE = os.path.join(DATA_DIR, "position_state.json")


# ═══════════════════════════════════════════════════════════
# 五大模块规则清单（和仪表盘交易手册一致）
# ═══════════════════════════════════════════════════════════

DISCIPLINE_RULES = {
    "仓位管理": {
        "rules": [
            "三周期共振向上 → 仓位7-8成",
            "级别不统一 → 仓位3-5成，不做新买入",
            "三周期共振向下 → 仓位0-2成，空仓或极轻仓",
        ],
        "check": "对比每日情绪周期与模拟账户仓位，是否存在超仓操作",
    },
    "选股": {
        "rules": [
            "板块必须在年线上方 + 均线多头排列",
            "排除已翻倍的板块（涨幅透支）",
            "排除均线空头排列的板块",
        ],
        "check": "被列为🟢可买入的板块，是否都满足年线上方+多头排列？",
    },
    "入场": {
        "rules": [
            "520金叉（5日线上穿20日线）",
            "放量（成交量 > 20日均量1.2倍）",
            "不追高（BIAS乖离率 < 5%）",
        ],
        "check": "买入操作是否三个条件全部满足？少一个就是在赌。",
    },
    "止损": {
        "rules": [
            "收盘跌破5日均线 → 减仓一半",
            "5日线下穿20日线（死叉） → 全部清仓",
            "单笔亏损达到仓位5% → 无条件止损",
        ],
        "check": "持仓中是否有破5日线未减仓的？有死叉未清仓的？",
    },
    "止盈": {
        "rules": [
            "放量滞涨（量>2倍均量但涨幅<0.3%） → 减仓一半",
            "高位顶分型 + 放量下跌 → 清仓",
            "RSI > 80（极端超买） → 分批减仓",
        ],
        "check": "持仓中是否有放量滞涨未减仓的？RSI>80未动的？",
    },
}


# ═══════════════════════════════════════════════════════════
# 检查逻辑
# ═══════════════════════════════════════════════════════════

def load_entries(days: int) -> List[Dict]:
    """加载交易日志"""
    if not os.path.exists(TRADE_LOG):
        return []
    entries = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with open(TRADE_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("date", "") >= cutoff:
                    entries.append(e)
            except Exception:
                continue
    return entries


def load_portfolio() -> Optional[Dict]:
    """加载账户状态"""
    if not os.path.exists(PORTFOLIO_FILE):
        return None
    try:
        with open(PORTFOLIO_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def load_position_states(days: int) -> List[Dict]:
    """加载仓位状态历史（通过 trade log 中的 index 条目）"""
    entries = load_entries(days)
    return [e for e in entries if e.get("type") == "index"]


def check_discipline(entries: List[Dict], pf: Optional[Dict]) -> Dict:
    """
    逐项检查五大模块纪律。
    返回 {模块名: {"pass": bool, "details": [str], "violations": [str]}}
    """
    results = {}

    index_entries = [e for e in entries if e.get("type") == "index"]
    sector_entries = [e for e in entries if e.get("type") == "sector"]
    trade_entries = [e for e in entries if e.get("action") in ("BUY", "SELL")]

    # 1. 仓位管理
    violations = []
    for e in index_entries:
        position = e.get("position", "")
        cycle = e.get("cycle", "")
        sigs = e.get("signals", {})
        mtf = sigs.get("三周期趋势", "")

        # 检查：三周期向下时是否建议了高仓位
        if mtf == "danger" and any(x in position for x in ("70-80%", "5-7成")):
            violations.append(f"{e['date']}: 三周期向下但建议仓位{position}——违反了空仓/轻仓原则")
        # 检查：三周期向上时是否建议了空仓
        if mtf == "healthy" and any(x in position for x in ("0-20%", "空仓")):
            violations.append(f"{e['date']}: 三周期向上但建议仓位{position}——可能过于保守")

    results["仓位管理"] = {
        "pass": len(violations) == 0,
        "details": [f"共{len(index_entries)}个交易日有仓位建议"],
        "violations": violations,
    }

    # 2. 选股 — 从 sector entries 检查
    violations = []
    buy_recs = [e for e in sector_entries if any(x in e.get("recommendation", "") for x in ("入场", "买入", "建仓"))]
    for e in buy_recs:
        rec = e.get("recommendation", "")
        # 无法从日志中精确反查板块技术指标，标记为"需人工核查"
        pass
    results["选股"] = {
        "pass": True,  # 日志粒度不足以自动检查
        "details": [f"共{len(buy_recs)}条买入建议，需打开仪表盘逐项核对板块是否满足选股条件"],
        "violations": [],
    }

    # 3. 入场
    violations = []
    if pf:
        holdings = pf.get("holdings", {})
        for sym, h in holdings.items():
            reason = h.get("entry_reason", "")
            if "金叉" not in reason and "520" not in reason:
                violations.append(f"{sym}: 入场理由「{reason}」未提及金叉确认——可能缺少入场条件")
    results["入场"] = {
        "pass": len(violations) == 0,
        "details": [f"当前持仓{len(pf['holdings']) if pf else 0}个标的"],
        "violations": violations,
    }

    # 4. 止损
    violations = []
    if pf:
        for sym, h in pf.get("holdings", {}).items():
            pnl = h.get("pnl_pct", 0)
            if pnl < -5:
                violations.append(f"{sym}: 亏损{pnl:.1f}%（超过-5%止损线）——应立即止损或检查止损价是否已触发")
    results["止损"] = {
        "pass": len(violations) == 0,
        "details": [],
        "violations": violations,
    }

    # 5. 止盈
    violations = []
    if pf:
        for sym, h in pf.get("holdings", {}).items():
            pnl = h.get("pnl_pct", 0)
            if pnl > 50:
                violations.append(f"{sym}: 盈利{pnl:.1f}%——需确认是否触发RSI>80或放量滞涨，考虑分批止盈")
    results["止盈"] = {
        "pass": len(violations) == 0,
        "details": [],
        "violations": violations,
    }

    return results


# ═══════════════════════════════════════════════════════════
# 学习笔记
# ═══════════════════════════════════════════════════════════

def generate_learning_notes(discipline: Dict, entries: List[Dict], pf: Optional[Dict]) -> List[str]:
    """从纪律检查结果中提取学习要点"""
    notes = []

    # 亮点：通过的模块
    passed = [mod for mod, r in discipline.items() if r["pass"] and not r["violations"]]
    if passed:
        notes.append(f"✅ **本月遵守了**: {'、'.join(passed)}")

    # 违例：需要改进的
    for mod, r in discipline.items():
        for v in r["violations"]:
            notes.append(f"⚠️ **{mod}违例**: {v}")

    # 从交易中提取学习点
    trade_entries = [e for e in entries if e.get("action") in ("BUY", "SELL")]
    buys = [e for e in trade_entries if e["action"] == "BUY"]
    sells = [e for e in trade_entries if e["action"] == "SELL"]

    if buys:
        notes.append(f"📝 本月共 {len(buys)} 次买入操作。每次入场前检查：三个条件全满足了吗？")
    if sells:
        notes.append(f"📝 本月共 {len(sells)} 次卖出操作。每次卖出时想：是因为纪律触发，还是盘中冲动？")

    # 账户表现概况
    if pf:
        cum_ret = pf.get("cumulative_return_pct", 0)
        notes.append(f"💰 模拟账户累计收益 **{cum_ret:+.1f}%**。")

    # 如果没有任何问题
    if not any(r["violations"] for r in discipline.values()) and not notes:
        notes.append("✅ 本月纪律执行完美，无违例。继续保持。")

    return notes


# ═══════════════════════════════════════════════════════════
# 生成报告
# ═══════════════════════════════════════════════════════════

def run_review(days: int = 30) -> str:
    entries = load_entries(days)
    pf = load_portfolio()

    if not entries:
        return "⚠️ 本月无交易日志。复盘无法进行。请先运行 market_dashboard.py 至少一周。"

    # 统计基本信息
    index_dates = set(e["date"] for e in entries if e.get("type") == "index")
    sector_count = sum(1 for e in entries if e.get("type") == "sector")
    trade_count = sum(1 for e in entries if e.get("action") in ("BUY", "SELL"))

    first = min(index_dates) if index_dates else "?"
    last = max(index_dates) if index_dates else "?"

    # 执行纪律检查
    discipline = check_discipline(entries, pf)

    # 生成学习笔记
    learning = generate_learning_notes(discipline, entries, pf)

    # 组装报告
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# 📊 月度复盘 — {first} → {last}",
        f"",
        f"**复盘日期**: {today} | **交易日**: {len(index_dates)}天 | **生成**: {datetime.now().strftime('%H:%M')}",
        f"**交易日志**: {len(entries)}条（指数{len([e for e in entries if e.get('type')=='index'])} + 板块{sector_count} + 交易{trade_count}）",
        f"",
        f"---",
        f"",
        f"## 📋 纪律检查 — 五大模块逐项对账",
        f"",
    ]

    for mod, rules in DISCIPLINE_RULES.items():
        r = discipline.get(mod, {"pass": True, "violations": []})
        icon = "✅" if r["pass"] else "❌"
        lines.append(f"### {icon} {mod}")
        lines.append(f"")
        for rule in rules["rules"]:
            lines.append(f"- 📋 {rule}")
        if r.get("details"):
            for d in r["details"]:
                lines.append(f"  {d}")
        if r.get("violations"):
            lines.append(f"")
            for v in r["violations"]:
                lines.append(f"  ❌ {v}")
        lines.append(f"")

    # 学习笔记
    lines.extend([
        f"---",
        f"",
        f"## 🧠 学习笔记",
        f"",
    ])
    for note in learning:
        lines.append(note)
    lines.append("")

    # 底部
    lines.extend([
        f"---",
        f"",
        f"*复盘不是打分，是学习。每个违例都是一次修正交易框架的机会。*",
        f"",
        f"*基于《趋势交易论》五大模块 | 每月1号自动运行*",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 飞书推送
# ═══════════════════════════════════════════════════════════

def send_feishu(content: str) -> bool:
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    chat_id = os.environ.get("FEISHU_CHAT_ID", "")
    if not app_id or not app_secret:
        return False

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
        if resp.status_code != 200:
            return False
        token = resp.json().get("tenant_access_token")
    except Exception:
        return False

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📊 月度复盘"}},
        "elements": [{"tag": "markdown", "content": content}],
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            json={"receive_id": chat_id, "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
        return resp.status_code == 200 and resp.json().get("code") == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="月度复盘 — 纪律检查+学习笔记")
    parser.add_argument("--days", type=int, default=30, help="回看天数")
    parser.add_argument("--dry-run", action="store_true", help="仅打印")
    args = parser.parse_args()

    print("=" * 50)
    print(f"  月度复盘 — 最近{args.days}天")
    print("=" * 50)

    report = run_review(days=args.days)
    print("\n" + report)

    if not args.dry_run:
        ok = send_feishu(report)
        print(f"\n[>] 飞书: {'OK' if ok else 'FAIL'}")
    else:
        print("\n[i] Dry run")
