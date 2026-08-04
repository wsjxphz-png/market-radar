# 长线资金监测 → 小白版每日资金观察日报 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 long_term_capital.py 重构为面向零基础用户的小白版每日资金观察产品（数据诚实化 + 观察对象日期规则 + 资金归因与承接周期 + AI 解读层 + 验证闭环 + 新闻降级）。

**Architecture:** 拆分为扁平模块（沿用仓库单文件风格，`ltc_` 前缀）：config / data / store / analysis / narrative / format / news / main / verify。抓取函数返回可注入的 DataFrame/Dict（纯函数化便于 TDD，网络层只做 smoke test）；编排入口 `ltc_main.py` 负责日期判定 → 抓取 → 推送决策 → 构建卡片 → 发送 → 留痕。GitHub Actions 两个 workflow：日报（含留痕 commit 回仓库）+ 月验证报告。

**Tech Stack:** Python 3.12、akshare 1.18.64、pandas、requests、feedparser（新闻降级复用 sources.json 的 RSSHub 源）、DeepSeek API（openai 兼容）、pytest（新增 dev 依赖）。

## Global Constraints

- 所有"今天"用北京时间：`datetime.now(ZoneInfo("Asia/Shanghai"))`（GitHub runner 是 UTC，早 8 点前会错一天）
- 卡片顶部日期 = 数据日期（观察对象当天），永不显示运行日期
- 推送判据：`data_date > last_pushed_date`，否则跳过（首次运行无记录 → 允许推）
- 禁止出现操作指令措辞："建议买入/建议加仓/可以买入/你要/你应该/推荐买入/立即买入/逢低买入/重仓买入/减仓/卖出"
- 正常模式不接新闻源；仅降级模式（Level 2）用新闻做定性兜底，不产生精确数字
- 代理指标归因措辞必须带"通常被认为代表"限定；承接周期判断必须标"推断"
- 飞书 emoji 只用保守集合：⚠️ 📊 📈 📉 💰 ✅ ❌ 🔴 🟢（有静默回退前车之鉴，✈️ 类花哨 emoji 禁用）
- 回购阶段标注：含"预案"→董事会预案；含"完成"→已完成实施；含"实施中/进行中"→实施中；其余→阶段未知
- 参照阈值（config 常量）：今日值 / 近20日均值 >= 1.2 → 比平时多；<= 0.8 → 比平时少；否则正常
- 删除北向数字；南向保留并配解释"内地资金买入港股市场的净额"
- 估值温度 = 价格位置分位（板块 K 线无 PE 列，已实测），标注"基于价格，非 PE"
- 季度背景块标注更新时间；更新时间 +14 天未更新 → 带过期警告

---

### Task 1: ltc_config.py — 配置中心

**Files:**
- Create: `market-radar/ltc_config.py`
- Test: `market-radar/tests/test_ltc_config.py`

**Interfaces:**
- Produces: `GLOSSARY: dict[str, str]`、`BANNED_PHRASES: list[str]`、`BOARDS: list[tuple[str, str]]`（板块名, 代码）、`QUARTERLY_CONTEXT: dict`（含 updated/next_update/key_facts/valuation_note）、`REF_RATIO_HIGH/REF_RATIO_LOW: float`、`CARD_EMOJIS: dict[str,str]`、`PHASE_LABEL(text) -> str`（回购阶段标注函数）、`is_expired(updated: str, days=14) -> bool`（季度背景过期判断）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_config.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_config import GLOSSARY, BANNED_PHRASES, PHASE_LABEL, is_expired, QUARTERLY_CONTEXT

def test_glossary_covers_key_terms():
    for term in ["超大单", "大单", "主力", "回购", "南向", "估值分位"]:
        assert term in GLOSSARY

def test_banned_phrases_no_operation_words():
    assert "抄底" not in BANNED_PHRASES  # 分类标签可用
    assert "建议买入" in BANNED_PHRASES

def test_phase_label():
    assert PHASE_LABEL("董事会预案") == "董事会预案"
    assert PHASE_LABEL("完成实施") == "已完成实施"
    assert PHASE_LABEL("实施中") == "实施中"
    assert PHASE_LABEL("") == "阶段未知"

def test_expiry():
    assert not is_expired("2026-08-04")          # 今天不算过期
    assert is_expired("2026-07-01")              # 34 天后过期
    assert not is_expired("2026-08-15")          # 未来日期不过期

def test_quarterly_has_required_keys():
    for k in ["updated", "next_update", "key_facts"]:
        assert k in QUARTERLY_CONTEXT
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_config.py -v`
Expected: FAIL（ModuleNotFoundError: No module named 'ltc_config'）

- [ ] **Step 3: 实现**

```python
# ltc_config.py
"""长线资金观察 — 配置中心：术语表/禁用词/板块/季度背景/阈值"""
from datetime import datetime
from zoneinfo import ZoneInfo

# 北京时间
def bj_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))

GLOSSARY = {
    "超大单": "单笔 100 万以上的大资金交易，通常被认为代表机构行为",
    "大单": "单笔 20-100 万的中大资金交易，通常被认为代表大户或游资",
    "主力": "超大单+大单的合计",
    "回购": "上市公司用自己的钱从市场买回自家股票，通常被认为代表管理层认为股价被低估",
    "南向": "内地资金买入港股市场的净额（通过港股通）",
    "估值分位": "当前价格在历史区间里的位置，数值越低越便宜",
    "承接": "资金买入行为的持续性，短期/中期/长期",
}

BANNED_PHRASES = [
    "建议买入", "建议加仓", "可以买入", "你要", "你应该",
    "推荐买入", "立即买入", "逢低买入", "重仓买入", "减仓", "赶紧买", "快买",
]

# 估值板块列表（东财行业板块，代码备用）
BOARDS = [
    ("医药生物", "BK0438"), ("银行", "BK0439"), ("非银金融", "BK0440"),
    ("半导体", "BK0441"), ("食品饮料", "BK0442"), ("电力设备", "BK0443"),
    ("煤炭", "BK0444"), ("家电", "BK0445"), ("汽车", "BK0446"), ("国防军工", "BK0447"),
]

REF_RATIO_HIGH = 1.2   # 今日/近20日均值 >= 1.2 → 比平时多
REF_RATIO_LOW = 0.8    # <= 0.8 → 比平时少

# 季报实名背书的行业关键词（季度背景未过期时，作为承接周期判断的背书信号）
BACKING_SECTORS = ["医药", "医疗", "生物", "银行", "公用", "交通"]

CARD_EMOJIS = {"header": "📊", "daily": "📅", "long": "🗓", "warn": "⚠️",
               "bridge": "👉", "honest": "🔎", "up": "📈", "down": "📉", "money": "💰"}

def PHASE_LABEL(text: str) -> str:
    if not text or str(text) in ("nan", "None"):
        return "阶段未知"
    t = str(text)
    if "预案" in t: return "董事会预案"
    if "完成" in t: return "已完成实施"
    if "实施中" in t or "进行中" in t: return "实施中"
    return "阶段未知"

def is_expired(updated: str, days: int = 14) -> bool:
    """季度背景是否过期：updated + days 之后判定过期"""
    try:
        d = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return (bj_now() - d).days > days
    except ValueError:
        return True  # 无法解析视为过期

# 季度背景知识库 — 每季度手动更新；过期后卡片带警告（FR-6.1）
QUARTERLY_CONTEXT = {
    "updated": "2026-07-23",
    "next_update": "2026年8月底（半年报全部披露后）",
    "key_facts": {
        "social_security": "社保/养老金 Q2 新增/增持 14 只个股，其中 7 只（50%）为医药生物",
        "insurance": "五大上市险企 Q1 新增仓位集中于银行（+347 亿股）、公用事业、交通运输",
        "mutual_funds": "公募 Q2 电子仓位 43.4%（历史极值），多位明星基金经理警告泡沫风险",
        "buybacks": "药明康德完成 10 亿元回购，美的集团累计回购 67 亿元",
        "national_platform": "中国国新（500亿+）+ 中国诚通（近100亿）入场，使用央行专项再贷款资金",
    },
    "valuation_note": {
        "医药生物": "医药 PE 28-30x（5年第26分位），PB 处 2010 年以来第 3 分位",
        "非银金融": "沪深300 非银 PB 1.26x（近十年 14% 分位）",
        "电子": "公募仓位 43.4%（历史极值），不是便宜，是贵且满仓",
    },
}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_config.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add ltc_config.py tests/test_ltc_config.py
git commit -m "feat: ltc_config 配置中心 — 术语表/禁用词/板块/季度背景"
```

---

### Task 2: ltc_data.py — 数据抓取层

**Files:**
- Create: `market-radar/ltc_data.py`
- Test: `market-radar/tests/test_ltc_data.py`

**Interfaces:**
- Consumes: `ltc_config.BOARDS`
- Produces:
  - `get_trading_date() -> Optional[str]` — 新浪 sh000001 末行日期（"YYYY-MM-DD"），失败 None
  - `fetch_southbound() -> Optional[dict]` — `{"date": str, "southbound_net_yi": float}`，失败 None
  - `fetch_sector_flow() -> Optional[pd.DataFrame]` — 列: `industry, chg_pct, main_net_yi, super_large_net_yi, large_net_yi`
  - `fetch_repurchase(weeks=4) -> dict` — `{"period", "items": [{"name","code","amount_yi","phase"}]}`
  - `fetch_board_kline(board_name: str, days=1300) -> Optional[pd.DataFrame]` — 东财→同花顺兜底；列: `date, close, volume, amount`；均按日期升序
  - `fetch_valuation() -> list[dict]` — 每板块 `{"board", "position_pct", "price_vs_ma60_pct", "ok": bool}`
  - `parse_sector_flow(df_raw) -> Optional[pd.DataFrame]`（位置列映射，可注入测试）
  - `parse_repurchase(df_raw, weeks) -> dict`（列映射+阶段+近N周过滤，可注入测试）

- [ ] **Step 1: 写失败测试（解析逻辑纯函数，不触网）**

```python
# tests/test_ltc_data.py
import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_data import parse_sector_flow, parse_repurchase

def test_parse_sector_flow_position_mapping():
    df = pd.DataFrame([
        [1, "半导体", 5000, 6.01, 1437.42, 1218.06, 219.35, 200, "中芯国际", 8.0, 1],
        [2, "银行", 4000, -2.69, 103.79, 173.92, -70.13, 40, "招商银行", 1.2, 2],
    ])
    out = parse_sector_flow(df)
    assert list(out.columns) == ["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]
    assert out.iloc[0]["industry"] == "半导体"
    assert abs(out.iloc[0]["super_large_net_yi"] - 1218.06) < 1e-6
    assert abs(out.iloc[1]["chg_pct"] - (-2.69)) < 1e-6

def test_parse_repurchase_phase_and_filter():
    # 18 列对齐东财 stock_repurchase_em: 0序号 1代码 2简称 3最新价 4价上 5价下 6数上 7数下
    # 8比上 9比下 10金额上 11金额下 12起始 13进度 14价高 15价低 16已回购数量 17已回购金额 18公告日期
    def row(code, name, plan_max, plan_min, progress, done, ann):
        r = [0] * 18
        r[1], r[2], r[9], r[10] = code, name, plan_max, plan_min
        r[12], r[16], r[17] = progress, done, ann
        return r
    df = pd.DataFrame([
        row("300750", "宁德时代", 400e8, 400e8, "董事会预案", 0, "2026-08-01"),
        row("002352", "顺丰控股", 60e8, 40e8, "完成实施", 50e8, "2026-06-20"),  # 6月公告=超出4周窗口
    ])
    out = parse_repurchase(df, weeks=4)
    assert len(out["items"]) == 1
    assert out["items"][0]["name"] == "宁德时代"
    assert out["items"][0]["phase"] == "董事会预案"
    assert out["items"][0]["amount_yi"] == 400.0  # 已回购为空→用计划金额，阶段标注为预案
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_data.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_data.py
"""数据抓取层：新浪交易日/南向/板块资金流/回购/板块K线/估值位置"""
import json, logging, random, time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
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
    df["amount"] = pd.to_numeric(df["done_amount"], errors="coerce").fillna(
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
    cols = df.columns.tolist()
    out = pd.DataFrame({
        "date": pd.to_datetime(df[cols[0]]),
        "close": pd.to_numeric(df[cols[4]], errors="coerce"),  # 收盘价
        "volume": pd.to_numeric(df[cols[5]], errors="coerce") if len(cols) > 5 else 0,
        "amount": pd.to_numeric(df[cols[6]], errors="coerce") if len(cols) > 6 else 0,
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
```

- [ ] **Step 4: 运行确认通过 + 真实网络 smoke test**

Run: `python -m pytest tests/test_ltc_data.py -v`
Expected: 2 passed

Run: `python -c "import sys; sys.path.insert(0,'.'); import ltc_data; print(ltc_data.get_trading_date()); print(ltc_data.fetch_southbound()); print(len(ltc_data.fetch_sector_flow()))"`
Expected: 打印最近交易日日期 + 南向 dict（非 None）+ 板块数（>50）。若南向/资金流返回 None，检查接口是否被封（东财限流时本任务先通过，端到端任务再处理）

- [ ] **Step 5: 提交**

```bash
git add ltc_data.py tests/test_ltc_data.py
git commit -m "feat: ltc_data 数据抓取层 — 新浪交易日/南向/板块资金流/回购/K线/估值位置"
```

---

### Task 3: ltc_store.py — 留痕/状态/参照/推送决策

**Files:**
- Create: `market-radar/ltc_store.py`
- Test: `market-radar/tests/test_ltc_store.py`

**Interfaces:**
- Produces:
  - `load_state(path) -> dict` / `save_state(path, state)`
  - `load_history(path) -> list[dict]` / `append_history(path, entry)`
  - `should_push(data_date: str, state: dict) -> tuple[bool, str]` — `(是否推送, 原因)`；首次运行允许；`data_date > last_pushed_date` 允许
  - `compute_reference(history: list[dict], key: str, days=20) -> Optional[float]` — 近 N 天均值（按 date 降序取最近 days 条非空值）
  - `reference_label(today: float, ref: Optional[float]) -> str` — "比平时多/少/正常" 或 "参照积累中"

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_store.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_store import (load_state, save_state, load_history, append_history,
                       should_push, compute_reference, reference_label)

def test_state_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"last_pushed_date": "2026-08-04"})
    assert load_state(p) == {"last_pushed_date": "2026-08-04"}
    assert load_state(str(tmp_path / "missing.json")) == {}

def test_history_append_and_load(tmp_path):
    p = str(tmp_path / "h.jsonl")
    append_history(p, {"date": "2026-08-04", "southbound_net_yi": 25.7})
    append_history(p, {"date": "2026-08-01", "southbound_net_yi": 50.0})
    assert len(load_history(p)) == 2

def test_should_push_rules():
    assert should_push("2026-08-04", {}) == (True, "首次运行")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-03"}) == (True, "新数据")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-04"}) == (False, "重复")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-05"}) == (False, "数据落后")

def test_reference_and_label():
    hist = [{"date": f"2026-07-{d:02d}", "southbound_net_yi": 40.0} for d in range(1, 21)]
    ref = compute_reference(hist, "southbound_net_yi")
    assert abs(ref - 40.0) < 1e-9
    assert reference_label(50.0, 40.0) == "比平时多"      # 50/40=1.25 >= 1.2
    assert reference_label(40.0, 40.0) == "正常"
    assert reference_label(25.0, 40.0) == "比平时少"      # 0.625 <= 0.8
    assert reference_label(25.7, None) == "参照积累中"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_store.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_store.py
"""留痕/状态/参照计算/推送决策（JSONL + state.json）"""
import json, os
from typing import Dict, List, Optional
from ltc_config import REF_RATIO_HIGH, REF_RATIO_LOW

def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_history(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows

def append_history(path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def should_push(data_date: str, state: dict) -> tuple:
    last = state.get("last_pushed_date", "")
    if not last:
        return True, "首次运行"
    if data_date > last:
        return True, "新数据"
    if data_date == last:
        return False, "重复"
    return False, "数据落后"

def compute_reference(history: List[dict], key: str, days: int = 20) -> Optional[float]:
    """取最近 days 条含非空 key 的记录求均值"""
    vals = [float(h[key]) for h in reversed(history) if h.get(key) is not None]
    if not vals:
        return None
    vals = vals[:days]
    return sum(vals) / len(vals) if vals else None

def reference_label(today: float, ref: Optional[float]) -> str:
    if ref is None or ref == 0:
        return "参照积累中"
    ratio = today / ref
    if ratio >= REF_RATIO_HIGH:
        return "比平时多"
    if ratio <= REF_RATIO_LOW:
        return "比平时少"
    return "正常"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_store.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add ltc_store.py tests/test_ltc_store.py
git commit -m "feat: ltc_store 留痕/状态/参照/推送决策"
```

---

### Task 4: ltc_analysis.py — 分类/归因/承接周期

**Files:**
- Create: `market-radar/ltc_analysis.py`
- Test: `market-radar/tests/test_ltc_analysis.py`

**Interfaces:**
- Consumes: `ltc_data.parse_sector_flow` 输出、`ltc_data.fetch_board_kline` 输出
- Produces:
  - `analyze_flows(df) -> list[dict]` — 每板块 `{"industry","signal","sl_percentile","chg_pct","sl_net","large_net","tag"}`；tag ∈ {逆势吸筹嫌疑, 派发嫌疑, 资金关注, 资金撤离, ""}；signal 为 TOP20% 等分位标签
  - `compute_accumulation(kline: Optional[pd.DataFrame], chg: float, sl_net: float, backing: bool, pos_1y: Optional[float]) -> dict` — `{"period": "偏长期布局特征"|"有中期承接迹象"|"短期行为特征"|"持续性数据积累中", "reasons": [...]}`
  - `pick_focus(analyses: list[dict], top_n=6) -> list[dict]` — 优先级：逆势吸筹 > 派发嫌疑 > 资金关注 > 资金撤离 > 待观察，去重取前 N

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_analysis.py
import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_analysis import analyze_flows, compute_accumulation, pick_focus

def _flow_df():
    # industry, chg_pct, main, super_large, large
    # 超大单值: 1218.1 / 173.9 / 100.0 / -250.0 / 2.0 → 分位 100/80/60/20/40
    return pd.DataFrame([
        ["半导体", 6.01, 1437.4, 1218.1, 219.4],
        ["银行", -2.69, 103.8, 173.9, -70.1],
        ["通信设备", 5.4, 866.3, 100.0, 182.8],
        ["食品饮料", 1.0, -300.0, -250.0, -50.0],   # 涨但超大单流出 → 派发嫌疑
        ["煤炭", 0.5, 5.0, 2.0, 3.0],               # 中间 → 待观察
    ], columns=["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"])

def test_analyze_flows_tags():
    r = {x["industry"]: x for x in analyze_flows(_flow_df())}
    assert r["半导体"]["tag"] == "资金关注" or r["半导体"]["tag"] == "逆势吸筹嫌疑"  # 涨+流入 → 资金关注
    assert r["银行"]["tag"] == "逆势吸筹嫌疑"     # 跌+超大单流入
    assert r["食品饮料"]["tag"] == "派发嫌疑"     # 涨+超大单流出
    assert r["煤炭"]["tag"] == ""                  # 中间

def test_analyze_flows_percentile():
    r = analyze_flows(_flow_df())
    assert max(x["sl_percentile"] for x in r) == 100.0
    assert min(x["sl_percentile"] for x in r) == 0.0

def test_compute_accumulation_long():
    # 持续放量 + 低位 + 回购背书 → 偏长期
    kline = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=60),
                          "close": [10 + i * 0.01 for i in range(60)],
                          "volume": [1e6] * 40 + [2e6] * 20})
    out = compute_accumulation(kline, chg=-1.5, sl_net=100.0, backing=True, pos_1y=20.0)
    assert out["period"] == "偏长期布局特征"
    assert len(out["reasons"]) >= 2

def test_compute_accumulation_short():
    out = compute_accumulation(None, chg=5.0, sl_net=10.0, backing=False, pos_1y=None)
    assert out["period"] == "持续性数据积累中"  # 无K线 → 冷启动

def test_pick_focus_priority():
    a = [
        {"industry": "A", "tag": "资金关注", "sl_percentile": 90.0},
        {"industry": "B", "tag": "逆势吸筹嫌疑", "sl_percentile": 85.0},
        {"industry": "C", "tag": "", "sl_percentile": 50.0},
    ]
    out = pick_focus(a, top_n=2)
    assert [x["industry"] for x in out] == ["B", "A"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_analysis.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_analysis.py
"""数据驱动分类 + 资金归因 + 承接周期判断（全部推断，不带实名表述）"""
from typing import Dict, List, Optional
import pandas as pd

TAG_PRIORITY = {"逆势吸筹嫌疑": 0, "派发嫌疑": 1, "资金关注": 2, "资金撤离": 3}

def analyze_flows(df: pd.DataFrame) -> List[dict]:
    if df is None or len(df) == 0:
        return []
    sl = pd.to_numeric(df["super_large_net_yi"], errors="coerce")
    sl_rank = sl.rank(pct=True) * 100
    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        chg = float(row.get("chg_pct", 0) or 0)
        sl_net = float(row.get("super_large_net_yi", 0) or 0)
        sl_pct = float(sl_rank.iloc[i])
        tag = ""
        if chg <= -1 and sl_pct >= 80 and sl_net > 0:
            tag = "逆势吸筹嫌疑"
        elif chg >= 1 and sl_pct <= 20 and sl_net < 0:
            tag = "派发嫌疑"
        elif sl_pct >= 80:
            tag = "资金关注"
        elif sl_pct <= 20:
            tag = "资金撤离"
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
        results.append({
            "industry": str(row.get("industry", "")),
            "signal": signal, "sl_percentile": round(sl_pct, 0),
            "chg_pct": round(chg, 2), "sl_net": round(sl_net, 2),
            "large_net": round(float(row.get("large_net_yi", 0) or 0), 2),
            "tag": tag,
        })
    results.sort(key=lambda x: x["sl_net"], reverse=True)
    return results

def compute_accumulation(kline: Optional[pd.DataFrame], chg: float, sl_net: float,
                         backing: bool, pos_1y: Optional[float]) -> dict:
    """承接周期：K线(量价历史)+当日资金+背书+位置 → 短期/中期/长期（推断）"""
    if kline is None or len(kline) < 20:
        return {"period": "持续性数据积累中", "reasons": ["量价历史数据积累中"]}
    close = kline["close"].values
    amount = kline["amount"].values if "amount" in kline.columns else kline["volume"].values
    vol_ratio = float(amount[-1]) / float(amount[-20:].mean()) if float(amount[-20:].mean()) > 0 else 1.0
    pct5 = float(close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0.0
    pos = pos_1y if pos_1y is not None else (
        float((close[-1] - close[-250:].min()) / (close[-250:].max() - close[-250:].min()) * 100)
        if close[-250:].max() > close[-250:].min() else 50.0)
    reasons = []
    if vol_ratio >= 1.2:
        reasons.append(f"近20日成交额放大 {vol_ratio:.1f} 倍")
    if pct5 >= 2:
        reasons.append(f"近5日累计涨 {pct5:+.1f}%")
    if backing:
        reasons.append("有回购/季报实名背书")
    if pos < 40:
        reasons.append(f"价格处于 1 年低位区（{pos:.0f}% 分位）")
    elif pos > 75:
        reasons.append(f"价格处于 1 年高位区（{pos:.0f}% 分位）")
    if backing and pos < 60 and (pct5 > 0 or vol_ratio >= 1.2):
        return {"period": "偏长期布局特征", "reasons": reasons}
    if vol_ratio >= 1.2 and -3 <= pct5 <= 3:
        return {"period": "有中期承接迹象", "reasons": reasons}
    if backing and pos < 60:
        return {"period": "有中期承接迹象", "reasons": reasons}
    return {"period": "短期行为特征", "reasons": reasons or ["单日资金行为，持续性不足"]}

def pick_focus(analyses: List[dict], top_n: int = 6) -> List[dict]:
    """优先级：逆势吸筹 > 派发嫌疑 > 资金关注 > 资金撤离 > 待观察"""
    tagged = [a for a in analyses if a.get("tag")]
    tagged.sort(key=lambda x: TAG_PRIORITY.get(x["tag"], 9))
    others = [a for a in analyses if not a.get("tag")]
    others.sort(key=lambda x: x.get("sl_percentile", 50), reverse=True)
    return (tagged + others)[:top_n]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_analysis.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add ltc_analysis.py tests/test_ltc_analysis.py
git commit -m "feat: ltc_analysis 数据驱动分类+归因+承接周期判断"
```

---

### Task 5: ltc_narrative.py — 事实清单/AI 解读/校验/模板回退

**Files:**
- Create: `market-radar/ltc_narrative.py`
- Test: `market-radar/tests/test_ltc_narrative.py`

**Interfaces:**
- Consumes: `ltc_config.BANNED_PHRASES/GLOSSARY`、`ltc_analysis` 输出
- Produces:
  - `build_facts(data: dict, focus: list[dict], refs: dict) -> dict` — 已核验事实清单（只有数字，无解读）
  - `template_interpretation(facts: dict) -> str` — 模板回退（≤150字）
  - `validate_output(text: str) -> bool` — 禁用词扫描
  - `interpretation(facts: dict, api_key: str, model: str) -> str` — AI → 校验 → 失败回退模板

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_narrative.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_narrative import build_facts, template_interpretation, validate_output

def test_build_facts_only_verified_numbers():
    data = {"data_date": "2026-08-04", "southbound": {"southbound_net_yi": 25.7},
            "repurchase": {"items": [{"name": "宁德时代", "amount_yi": 400.0, "phase": "董事会预案"}]}}
    focus = [{"industry": "半导体", "tag": "资金关注", "sl_net": 1218.1, "chg_pct": 6.01, "sl_percentile": 100.0,
              "accum": {"period": "短期行为特征", "reasons": []}}]
    f = build_facts(data, focus, {"southbound_ref": (None, "参照积累中")})
    assert f["southbound"]["value"] == 25.7
    assert f["southbound"]["ref_label"] == "参照积累中"
    assert f["focus"][0]["industry"] == "半导体"
    assert f["focus"][0]["tag"] == "资金关注"

def test_template_interpretation_contains_facts():
    facts = {"data_date": "2026-08-04",
             "focus": [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": 173.9, "chg_pct": -2.69,
                        "accum": {"period": "偏长期布局特征", "reasons": ["有回购/季报实名背书"]}}],
             "southbound": {"value": 25.7, "ref_label": "参照积累中"}}
    text = template_interpretation(facts)
    assert "银行" in text
    assert len(text) <= 200

def test_validate_output_blocks_banned():
    assert validate_output("今天资金流入半导体") is True
    assert validate_output("你可以考虑买入半导体") is False
    assert validate_output("建议加仓银行") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_narrative.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_narrative.py
"""规则保真 + AI 润色：事实清单 → DeepSeek 解读 → 校验 → 模板回退"""
import json, logging
from typing import Dict, Optional
import requests
from ltc_config import BANNED_PHRASES

logger = logging.getLogger(__name__)

def build_facts(data: dict, focus: list, refs: dict) -> dict:
    """已核验事实清单：只含实测数字与标签，AI 只能引用这里的内容"""
    facts = {
        "data_date": data.get("data_date", ""),
        "focus": [{
            "industry": f.get("industry"), "tag": f.get("tag"),
            "sl_net": f.get("sl_net"), "chg_pct": f.get("chg_pct"),
            "sl_percentile": f.get("sl_percentile"),
            "accum_period": f.get("accum", {}).get("period"),
            "accum_reasons": f.get("accum", {}).get("reasons", []),
        } for f in focus],
        "southbound": {
            "value": (data.get("southbound") or {}).get("southbound_net_yi"),
            "ref_label": refs.get("southbound_label", "参照积累中"),
        },
        "repurchase_top": [{"name": i["name"], "amount_yi": i["amount_yi"], "phase": i["phase"]}
                           for i in (data.get("repurchase") or {}).get("items", [])[:3]],
        "valuation_note": data.get("valuation_note", ""),
    }
    return facts

def template_interpretation(facts: dict) -> str:
    """模板回退：只填核验过的数字"""
    parts = []
    for f in facts.get("focus", [])[:3]:
        action = {"逆势吸筹嫌疑": "价格在跌但大资金在买",
                  "派发嫌疑": "价格在涨但大资金在卖",
                  "资金关注": "大资金集中流入",
                  "资金撤离": "大资金在撤离"}.get(f["tag"], "资金动作明显")
        parts.append(f"{f['industry']}（{action}，超大单{f['sl_net']:+.1f}亿，{f['accum_period']}）")
    sb = facts.get("southbound", {})
    sb_txt = f"南向资金{sb['value']:+.1f}亿（{sb.get('ref_label','参照积累中')}）" if sb.get("value") is not None else "南向数据暂不可用"
    if not parts:
        return f"今日各板块资金动作分散，无明显集中方向。{sb_txt}。单日资金行为不代表趋势，建议结合多日观察。（数据日期 {facts['data_date']}）"
    return f"今日资金焦点：{'；'.join(parts)}。{sb_txt}。以上均为推断，资金流不代表实名持仓。（数据日期 {facts['data_date']}）"

def validate_output(text: str) -> bool:
    for w in BANNED_PHRASES:
        if w in text:
            return False
    return True

def call_deepseek(facts: dict, api_key: str, model: str = "deepseek-chat") -> Optional[str]:
    if not api_key:
        return None
    system = (
        "你是《每日资金观察》的解读员。读者是零基础股票小白，不提供任何买卖建议。"
        "只允许引用给定事实中的数字和标签，禁止编造任何数字、新闻、传闻或人物观点。"
        "禁止出现买入/卖出/加仓/减仓等操作指令。视角放在中长期：单日信号要说明持续性未知。"
        "用大白话，100-150 字，分两段：今天发生了什么；对你意味着什么。"
    )
    user = json.dumps(facts, ensure_ascii=False)
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}],
                "temperature": 0.4, "max_tokens": 300},
            timeout=60)
        if resp.status_code != 200:
            logger.warning("DeepSeek HTTP %s: %s", resp.status_code, resp.text[:120])
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if not validate_output(content):
            logger.warning("DeepSeek output rejected by validation")
            return None
        return content[:300]
    except Exception as e:
        logger.warning("DeepSeek call failed: %s", str(e)[:120])
        return None

def interpretation(facts: dict, api_key: str = "", model: str = "deepseek-chat") -> str:
    """AI 解读 → 校验失败/调用失败 → 模板回退"""
    ai = call_deepseek(facts, api_key, model)
    if ai:
        return ai
    return template_interpretation(facts)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_narrative.py -v`
Expected: 3 passed

Run（真实 API smoke test，若有 key）: `python -c "import sys,os; sys.path.insert(0,'.'); from ltc_narrative import interpretation; f={'data_date':'2026-08-04','focus':[{'industry':'银行','tag':'逆势吸筹嫌疑','sl_net':173.9,'chg_pct':-2.69,'accum_period':'偏长期布局特征','accum_reasons':['有回购/季报实名背书']}],'southbound':{'value':25.7,'ref_label':'参照积累中'}}; print(interpretation(f, os.environ.get('DEEPSEEK_API_KEY','')))"`
Expected: 输出 ≤150 字解读；无 key 时输出模板（不回退崩溃）

- [ ] **Step 5: 提交**

```bash
git add ltc_narrative.py tests/test_ltc_narrative.py
git commit -m "feat: ltc_narrative 事实清单+AI解读+校验+模板回退"
```

---

### Task 6: ltc_format.py — 飞书卡片（日更/长更分离）

**Files:**
- Create: `market-radar/ltc_format.py`
- Test: `market-radar/tests/test_ltc_format.py`

**Interfaces:**
- Consumes: `ltc_config.GLOSSARY/CARD_EMOJIS/QUARTERLY_CONTEXT`、analysis/narrative 输出
- Produces:
  - `format_card(data_date, interpretation_text, focus, southbound, valuation, repurchase, quarterly_block, quarterly_expired, references) -> str`
  - `build_quarterly_block() -> tuple[str, bool]` — (文本, 是否过期)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_format.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_format import format_card, build_quarterly_block

def _args():
    focus = [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": 173.9, "chg_pct": -2.69,
              "sl_percentile": 94.0, "signal": "TOP20% 🔵",
              "accum": {"period": "偏长期布局特征", "reasons": ["价格处于 1 年低位区"]}}]
    southbound = {"date": "2026-08-04", "southbound_net_yi": 25.7}
    valuation = [{"board": "银行", "ok": True, "position_pct": 23.5, "price_vs_ma60_pct": 1.2}]
    repurchase = {"period": "近4周", "items": [{"name": "宁德时代", "amount_yi": 400.0, "phase": "董事会预案"}]}
    refs = {"southbound_label": "参照积累中"}
    return focus, southbound, valuation, repurchase, refs

def test_card_contains_data_date_not_run_date():
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=False)
    assert "2026-08-04" in card

def test_card_no_northbound():
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=False)
    assert "北向" not in card

def test_card_sections_separated():
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=False)
    assert "今日数据" in card and "长期数据" in card
    assert "今日数据" in card.split("长期数据")[0]

def test_card_glossary_and_phase():
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=False)
    assert "超大单" in card and "单笔 100 万以上" in card       # 术语解释
    assert "董事会预案" in card                                  # 回购阶段

def test_card_expired_warning():
    qb, expired = build_quarterly_block()
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=True)
    assert "过期" in card

def test_card_no_operation_words():
    card = format_card("2026-08-04", "解读文本", *_args(), quarterly_expired=False)
    for w in ["建议买入", "你要", "可以买入"]:
        assert w not in card
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_format.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_format.py
"""飞书卡片：日更/长更分离 + 术语解释 + 搭桥 + 诚实声明"""
from typing import Dict, List
from ltc_config import GLOSSARY, CARD_EMOJIS, QUARTERLY_CONTEXT

E = CARD_EMOJIS

def _term(term: str) -> str:
    return f"{term}（{GLOSSARY[term]}）" if term in GLOSSARY else term

def build_quarterly_block() -> tuple:
    """季度背景块 + 过期标记（非今日数据，仅作背景）"""
    ctx = QUARTERLY_CONTEXT
    lines = [f"{E['long']} 季度背景（更新于 {ctx['updated']}，非今日数据，下次更新 {ctx['next_update']}）"]
    for v in ctx["key_facts"].values():
        lines.append(f"- {v}")
    expired = False
    from ltc_config import is_expired
    if is_expired(ctx.get("updated", "")):
        expired = True
        lines.append(f"{E['warn']} 季度背景已过期，请尽快更新")
    return "\n".join(lines), expired

def format_card(data_date: str, interpretation_text: str, focus: List[dict],
                southbound: dict, valuation: List[dict], repurchase: dict,
                references: dict, quarterly_expired: bool = False) -> str:
    lines = []
    lines.append(f"{E['header']} 每日资金观察 · {data_date}")
    lines.append("")
    lines.append(interpretation_text.strip())
    lines.append("")
    # ── 今日数据 ──
    lines.append(f"━━━ {E['daily']} 今日数据（当日更新） ━━━")
    for f in focus[:6]:
        acc = f.get("accum", {})
        lines.append(f"• {f['industry']}：{f['tag'] or '资金动作'}")
        lines.append(f"  {_term('超大单')} {f['sl_net']:+.1f}亿 ｜ 股价 {f['chg_pct']:+.1f}% ｜ 分位 {f['sl_percentile']:.0f}%")
        if acc.get("reasons"):
            lines.append(f"  承接：{acc['period']}（推断）｜ " + "；".join(acc["reasons"][:2]))
    if southbound:
        sb = southbound.get("southbound_net_yi")
        if sb is not None:
            label = references.get("southbound_label", "参照积累中")
            lines.append(f"• {_term('南向')}：{sb:+.1f}亿（{label}）")
    lines.append("")
    lines.append(f"{E['bridge']} 对你意味着什么：单日资金信号不等于趋势，观察是否连续多日出现。")
    lines.append("")
    # ── 长期数据 ──
    lines.append(f"━━━ {E['long']} 长期数据 ━━━")
    ok_vals = [v for v in valuation if v.get("ok")]
    lines.append("【估值温度】（基于价格位置，非 PE）")
    if ok_vals:
        for v in ok_vals[:6]:
            lines.append(f"- {v['board']}：价格处 5 年区间 {v['position_pct']:.0f}% 分位（vs 60日均线 {v['price_vs_ma60_pct']:+.1f}%）")
    else:
        lines.append("- 暂无数据")
    lines.append("")
    lines.append(f"【近4周回购】（{repurchase.get('period', '')}）")
    for i in repurchase.get("items", [])[:3]:
        lines.append(f"- {i['name']}：{i['amount_yi']:.1f}亿（{i['phase']}）")
    lines.append("")
    qb, _ = build_quarterly_block()
    lines.append(qb)
    if quarterly_expired:
        lines.append(f"{E['warn']} 季度背景已过期")
    lines.append("")
    lines.append(f"━━━ {E['honest']} 诚实声明 ━━━")
    lines.append("资金流按交易金额大小推断资金类型（超大单≈机构），不是实名数据；唯一实名披露是季报，每季度更新。承接周期判断为推断。本内容不构成任何买卖建议。")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_format.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add ltc_format.py tests/test_ltc_format.py
git commit -m "feat: ltc_format 卡片格式化 — 日更/长更分离+术语+搭桥+诚实声明"
```

---

### Task 7: ltc_news.py — 新闻降级卡（Level 2）

**Files:**
- Create: `market-radar/ltc_news.py`
- Test: `market-radar/tests/test_ltc_news.py`

**Interfaces:**
- Produces:
  - `fetch_news_titles(today_cn: str, limit: int = 15) -> list[dict]` — 财联社/金十快讯标题（RSSHub 多实例降级），只含当天标题，`{"title", "date"}`
  - `format_news_card(data_date: str, titles: list[dict]) -> str` — 降级卡（显著标注）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_news.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_news import format_news_card, filter_today

def test_format_news_card_marked_degraded():
    card = format_news_card("2026-08-04", [{"title": "A股收评：沪指涨0.33%", "date": "2026-08-04"}])
    assert "数据源暂时不可用" in card
    assert "新闻摘要" in card
    assert "A股收评" in card

def test_filter_today_only():
    titles = [{"title": "a", "date": "2026-08-04"}, {"title": "b", "date": "2026-08-01"}, {"title": "c", "date": ""}]
    out = filter_today(titles, "2026-08-04")
    assert [x["title"] for x in out] == ["a"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_news.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_news.py
"""新闻降级卡（Level 2）：主数据源失效时，用 RSSHub 快讯做定性兜底"""
import json, logging
from datetime import datetime
from typing import Dict, List, Optional
import feedparser, requests

logger = logging.getLogger(__name__)

RSSHUB_FEEDS = [
    ("财联社电报", "cls/telegraph"),
    ("金十数据", "jin10"),
    ("格隆汇快讯", "gelonghui/live"),
]

def _rsshub_instances() -> List[str]:
    try:
        with open("sources.json", "r", encoding="utf-8") as f:
            return json.load(f).get("sources", {}).get("rsshub_instances",
                ["https://rsshub.app", "https://rsshub.pseudoyu.com"])
    except Exception:
        return ["https://rsshub.app", "https://rsshub.pseudoyu.com"]

def filter_today(titles: List[dict], today_cn: str) -> List[dict]:
    return [t for t in titles if t.get("date", "")[:10] == today_cn]

def fetch_news_titles(today_cn: str, limit: int = 15) -> List[dict]:
    """多 RSSHub 实例 × 多源，取当天标题；任一源成功即返回"""
    seen, out = set(), []
    for inst in _rsshub_instances():
        if len(out) >= limit:
            break
        for name, path in RSSHUB_FEEDS:
            try:
                resp = requests.get(f"{inst}/{path}", timeout=10)
                if resp.status_code != 200:
                    continue
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:limit]:
                    d = entry.get("published") or entry.get("updated") or ""
                    date = ""
                    try:
                        date = datetime(*entry.get("published_parsed", (0, 0, 0, 0, 0, 0, 0, 0, 0))[:6]).strftime("%Y-%m-%d")
                    except Exception:
                        pass
                    title = entry.get("title", "").strip()
                    if title and title not in seen and date[:10] == today_cn:
                        seen.add(title)
                        out.append({"title": title, "date": date, "source": name})
            except Exception as e:
                logger.warning("rsshub %s/%s failed: %s", inst, path, str(e)[:80])
    return out[:limit]

def format_news_card(data_date: str, titles: List[dict]) -> str:
    lines = [f"{'⚠️'} 每日资金观察 · {data_date}（新闻摘要版）", "",
             "⚠️ 数据源暂时不可用，以下为当日新闻摘要，不含数据监测。", ""]
    if not titles:
        lines.append("今日未能获取新闻。")
        return "\n".join(lines)
    for t in titles[:12]:
        lines.append(f"- [{t.get('source','')}] {t['title']}")
    lines.append("")
    lines.append("新闻仅作定性参考；本内容不构成任何买卖建议。")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_news.py -v`
Expected: 2 passed

Run（真实 smoke test）: `python -c "import sys; sys.path.insert(0,'.'); from ltc_news import fetch_news_titles, format_news_card; t = fetch_news_titles('2026-08-04'); print(len(t), '条'); print(format_news_card('2026-08-04', t)[:400])"`
Expected: 输出当天标题列表（RSSHub 公开实例不稳定时允许 0 条——Level 3 行为）

- [ ] **Step 5: 提交**

```bash
git add ltc_news.py tests/test_ltc_news.py
git commit -m "feat: ltc_news 新闻降级卡（RSSHub 多实例兜底）"
```

---

### Task 8: ltc_main.py — 编排入口（日期→抓取→决策→卡片→发送→留痕）

**Files:**
- Create: `market-radar/ltc_main.py`
- Test: `market-radar/tests/test_ltc_main.py`

**Interfaces:**
- Consumes: 全部 ltc_* 模块
- Produces: `run_once(env: dict, data_dir: str) -> int`（0=推送/跳过正常，1=告警需要关注）；`send_feishu(msg, env) -> bool`；`main()`
- 告警规则：Level 2/3 失败时，若 state 中 `alert_streak` 达到 2 且上次告警日期 != 今天 → 发送运营告警卡

- [ ] **Step 1: 写失败测试（mock 全部抓取层，测决策链路）**

```python
# tests/test_ltc_main.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ltc_main

def test_run_skip_on_repeat(monkeypatch, tmp_path):
    env = {"FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "", "FEISHU_CHAT_ID": "", "DEEPSEEK_API_KEY": ""}
    ltc_store = __import__("ltc_store")
    ltc_store.save_state(str(tmp_path / "state.json"), {"last_pushed_date": "2026-08-04"})
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert sent == []  # 重复数据不推送

def test_run_news_fallback_on_flow_failure(monkeypatch, tmp_path):
    env = {"FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "", "FEISHU_CHAT_ID": "", "DEEPSEEK_API_KEY": ""}
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [{"title": "t1", "date": "2026-08-04"}])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert len(sent) == 1 and "新闻摘要" in sent[0]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_main.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_main.py
"""编排入口：日期判定 → 抓取 → 推送决策 → 卡片/降级 → 发送 → 留痕"""
import json, logging, os, sys
from typing import Dict, Optional
import ltc_analysis, ltc_data, ltc_format, ltc_narrative, ltc_news, ltc_store
from ltc_config import bj_now

logger = logging.getLogger(__name__)

DATA_DIR = "data/ltc"
STATE_FILE = "data/ltc/state.json"
HISTORY_FILE = "data/ltc/history.jsonl"

def send_feishu(msg: str, env: dict) -> bool:
    app_id, app_secret, chat_id = env.get("FEISHU_APP_ID", ""), env.get("FEISHU_APP_SECRET", ""), env.get("FEISHU_CHAT_ID", "")
    if not (app_id and app_secret and chat_id):
        logger.warning("飞书环境变量缺失，跳过发送")
        return False
    import requests
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
    token = r.json().get("tenant_access_token")
    if not token:
        logger.error("获取飞书 token 失败"); return False
    card = {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "每日资金观察"}},
            "elements": [{"tag": "markdown", "content": msg}]}
    r = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                      json={"receive_id": chat_id, "msg_type": "interactive",
                            "content": json.dumps(card, ensure_ascii=False)},
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
    return r.status_code == 200 and r.json().get("code") == 0

def run_once(env: dict, data_dir: str = DATA_DIR) -> int:
    state_path = os.path.join(data_dir, "state.json")
    hist_path = os.path.join(data_dir, "history.jsonl")
    state = ltc_store.load_state(state_path)

    trading_date = ltc_data.get_trading_date()
    southbound = ltc_data.fetch_southbound()
    data_date = (southbound or {}).get("date") or trading_date
    if not data_date:
        # Level 3 边界：交易日都无法判定 → 新闻兜底，新闻也失败则跳过+告警
        titles = ltc_news.fetch_news_titles(bj_now().strftime("%Y-%m-%d"))
        if titles:
            send_feishu(ltc_news.format_news_card(bj_now().strftime("%Y-%m-%d"), titles), env)
            return 0
        state["alert_streak"] = state.get("alert_streak", 0) + 1
        ltc_store.save_state(state_path, state)
        return 0 if state["alert_streak"] < 2 else 1

    push, reason = ltc_store.should_push(data_date, state)
    if not push:
        logger.info("跳过推送（%s）：data_date=%s", reason, data_date)
        return 0
    if reason == "首次运行":
        state["last_pushed_date"] = data_date

    flow = ltc_data.fetch_sector_flow()
    if flow is None:
        titles = ltc_news.fetch_news_titles(data_date)
        if titles:
            send_feishu(ltc_news.format_news_card(data_date, titles), env)
        state["alert_streak"] = state.get("alert_streak", 0) + 1
        ltc_store.save_state(state_path, state)
        return 0 if state["alert_streak"] < 2 else 1
    state["alert_streak"] = 0

    analyses = ltc_analysis.analyze_flows(flow)
    focus = ltc_analysis.pick_focus(analyses, top_n=6)
    repurchase = ltc_data.fetch_repurchase(weeks=4)
    valuation = ltc_data.fetch_valuation()

    # 承接周期（每板块 K 线 + 背书；背书来自季度实名数据，季度背景过期则不计）
    from ltc_config import BACKING_SECTORS, QUARTERLY_CONTEXT, is_expired
    backing_active = not is_expired(QUARTERLY_CONTEXT.get("updated", ""))
    for f in focus:
        kline = ltc_data.fetch_board_kline(f["industry"])
        backing = backing_active and any(b in f["industry"] for b in BACKING_SECTORS)
        acc = ltc_analysis.compute_accumulation(kline, f["chg_pct"], f["sl_net"], backing, None)
        f["accum"] = acc

    history = ltc_store.load_history(hist_path)
    sb_ref = ltc_store.compute_reference(history, "southbound_net_yi")
    sb_label = ltc_store.reference_label((southbound or {}).get("southbound_net_yi", 0), sb_ref)
    refs = {"southbound_label": sb_label}

    facts = ltc_narrative.build_facts(
        {"data_date": data_date, "southbound": southbound, "repurchase": repurchase}, focus, refs)
    interp = ltc_narrative.interpretation(facts, env.get("DEEPSEEK_API_KEY", ""))

    qb, expired = ltc_format.build_quarterly_block()
    card = ltc_format.format_card(data_date, interp, focus, southbound, valuation,
                                  repurchase, refs, quarterly_expired=expired)
    logger.info("卡片内容:\n%s", card[:2000])
    if send_feishu(card, env):
        state["last_pushed_date"] = data_date
        ltc_store.save_state(state_path, state)
        ltc_store.append_history(hist_path, {
            "date": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": data_date,
            "southbound_net_yi": (southbound or {}).get("southbound_net_yi"),
            "tags": {f["industry"]: {"tag": f["tag"], "accum": f.get("accum", {}).get("period"),
                                     "sl_net": f["sl_net"]} for f in focus},
        })
        return 0
    logger.error("飞书发送失败")
    return 1

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    env = dict(os.environ)
    code = run_once(env)
    sys.exit(code)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ltc_main.py -v`
Expected: 2 passed

Run（本地 dry-run，不发送）: `python -c "import sys,os; sys.path.insert(0,'.'); import ltc_main; env={'FEISHU_APP_ID':'','FEISHU_APP_SECRET':'','FEISHU_CHAT_ID':'','DEEPSEEK_API_KEY':os.environ.get('DEEPSEEK_API_KEY','')}; ltc_main.DATA_DIR='data/ltc_dry'; print('exit', ltc_main.run_once(env, 'data/ltc_dry'))"`
Expected: 输出卡片内容日志，exit 0；确认卡片含今日数据/长期数据/解读，无"北向"

- [ ] **Step 5: 提交**

```bash
git add ltc_main.py tests/test_ltc_main.py
git commit -m "feat: ltc_main 编排入口 — 观察对象日期规则+推送决策+降级+留痕"
```

---

### Task 9: workflow 重写 + 端到端真实运行

**Files:**
- Modify: `market-radar/.github/workflows/long-term-capital.yml`（整体重写）
- Create: `market-radar/.gitignore` 追加（如已存在则修改）

- [ ] **Step 1: 重写 workflow（删死参数、加留痕 commit、接 DEEPSEEK_API_KEY）**

```yaml
name: 每日资金观察

on:
  schedule:
    # 北京时间 16:30 (UTC 08:30), 周一至周五（节假日由数据日期规则自动跳过）
    - cron: '30 8 * * 1-5'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: 检出代码
        uses: actions/checkout@v4
      - name: 设置 Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: 安装依赖
        run: pip install -r requirements.txt
      - name: 每日资金观察
        env:
          FEISHU_APP_ID: ${{ secrets.FEISHU_APP_ID }}
          FEISHU_APP_SECRET: ${{ secrets.FEISHU_APP_SECRET }}
          FEISHU_CHAT_ID: ${{ secrets.FEISHU_CHAT_ID }}
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
        run: |
          python ltc_main.py || true
      - name: 提交留痕
        run: |
          mkdir -p data/ltc
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/ltc
          git diff --cached --quiet || git commit -m "data: 每日资金观察留痕 [skip ci]"
          git push
```

注：`|| true` 保证留痕提交始终执行（数据缺失告警时 exit 1 不阻塞 commit）。推送决策（重复/节假日）在 ltc_main 内静默跳过，卡片不会重复发送。

- [ ] **Step 2: 确保 requirements.txt 含 feedparser（已有）与 pytest（dev）**

Run: `grep -q "feedparser" requirements.txt && echo ok`
Expected: ok

Run: `python -m pytest tests/ -q`
Expected: 全部通过（当前 2 个文件的任务 1-8 的测试累计）

- [ ] **Step 3: 本地端到端（真实数据，dry-run 不发送）**

Run: `python -c "import sys,os; sys.path.insert(0,'.'); import ltc_main; ltc_main.DATA_DIR='data/ltc_dry'; os.makedirs('data/ltc_dry', exist_ok=True); env={'FEISHU_APP_ID':'','FEISHU_APP_SECRET':'','FEISHU_CHAT_ID':'','DEEPSEEK_API_KEY':os.environ.get('DEEPSEEK_API_KEY','')}; print(ltc_main.run_once(env,'data/ltc_dry'))"`
Expected: exit 0；日志中卡片含：解读（AI 或模板）、今日数据板块列表（带承接）、长期数据（估值温度/回购/季度背景）、诚实声明；无"北向"；state.json 已写入 last_pushed_date

- [ ] **Step 4: 检查推送决策（重复运行第二次应跳过）**

Run: 再次运行 Step 3 命令
Expected: 日志 "跳过推送（重复）"，不重新构建卡片

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/long-term-capital.yml .gitignore
git commit -m "ci: 每日资金观察 workflow 重写 — 观察对象日期规则+留痕提交+DEEPSEEK"
```

---

### Task 10: ltc_verify.py — 半月/月验证报告

**Files:**
- Create: `market-radar/ltc_verify.py`
- Create: `market-radar/.github/workflows/ltc-verify.yml`
- Test: `market-radar/tests/test_ltc_verify.py`

**Interfaces:**
- Consumes: `ltc_store.load_history`、`ltc_data.fetch_board_kline`
- Produces:
  - `forward_returns(history: list[dict], price_lookup) -> list[dict]` — 每条：`{"data_date", "industry", "tag", "ret_10td", "ret_20td"}`
  - `differentiation(results: list[dict]) -> dict` — 标签组 vs 全部均值差；`{"signal", "periods": {10: diff, 20: diff}, "verdict": "有效"|"无效"}`
  - `update_signal_config(results, config_path)` — 连续 2 期无区分度 → 从 ACTIVE_SIGNALS 移除
  - `main()` — 输出月报文本到 `data/ltc/verification.jsonl` + 终端

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ltc_verify.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_verify import forward_returns, differentiation

def test_forward_returns_window():
    hist = [{"data_date": "2026-07-01", "tags": {"半导体": {"tag": "资金关注", "sl_net": 100.0, "accum": ""}}}]
    # 2026-07-01 在 td 中下标 4；+10TD → td[14]="2026-07-15"；+20TD → td[24]="2026-07-29"
    td = ["2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02",
          "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
          "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20",
          "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
          "2026-07-29", "2026-07-30"]
    price = {"2026-07-01": {"半导体": 100.0}, "2026-07-15": {"半导体": 110.0}, "2026-07-29": {"半导体": 105.0}}
    out = forward_returns(hist, price, td)
    assert out[0]["ret_10td"] == 10.0
    assert out[0]["ret_20td"] == 5.0

def test_differentiation_verdict():
    results = [
        {"tag": "资金关注", "ret_20td": 8.0}, {"tag": "资金关注", "ret_20td": 6.0},
        {"tag": "", "ret_20td": 1.0}, {"tag": "", "ret_20td": 0.0},
    ]
    out = differentiation(results)
    assert out["verdict"] == "有效"
    assert abs(out["diff_20"] - 3.25) < 1e-9  # (8+6)/2 - (8+6+1+0)/4 = 7 - 3.75
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ltc_verify.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# ltc_verify.py
"""验证闭环：历史标签 vs 板块后续涨跌（10/20 交易日），相关性验证非因果证明"""
import json, logging, os
from typing import Dict, List, Optional
import ltc_data, ltc_store

logger = logging.getLogger(__name__)
ACTIVE_SIGNALS = ["资金关注", "逆势吸筹嫌疑", "派发嫌疑", "资金撤离"]
CONFIG_FILE = "data/ltc/signals_config.json"
VERIFY_FILE = "data/ltc/verification.jsonl"
STATE_FILE = "data/ltc/state.json"

def _trading_dates() -> List[str]:
    """用新浪上证指数日K取交易日序列"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000001")
    return [str(d)[:10] for d in df["date"].tolist()]

def forward_returns(hist: List[dict], price: Dict[str, Dict[str, float]], td: List[str]) -> List[dict]:
    """对每条留痕：取 data_date 在 td 中的位置，+10/+20 交易日的收盘价算收益"""
    idx = {d: i for i, d in enumerate(td)}
    out = []
    for h in hist:
        dd = h.get("data_date")
        if dd not in idx:
            continue
        i = idx[dd]
        for ind, meta in (h.get("tags") or {}).items():
            rets = {}
            for n in (10, 20):
                j = i + n
                if j < len(td) and dd in price and ind in price.get(dd, {}):
                    p0 = price[dd].get(ind)
                    p1 = price.get(td[j], {}).get(ind)
                    rets[f"ret_{n}td"] = round((p1 / p0 - 1) * 100, 2) if p0 and p1 else None
                else:
                    rets[f"ret_{n}td"] = None
            out.append({"data_date": dd, "industry": ind, "tag": meta.get("tag", ""),
                        "ret_10td": rets["ret_10td"], "ret_20td": rets["ret_20td"]})
    return out

def differentiation(results: List[dict]) -> Dict:
    """信号组（tag 非空）20日收益均值 vs 全部均值 → 区分度"""
    tagged = [r for r in results if r.get("tag") and r.get("ret_20td") is not None]
    allv = [r for r in results if r.get("ret_20td") is not None]
    if not tagged or not allv:
        return {"verdict": "数据不足"}
    avg_tagged = sum(r["ret_20td"] for r in tagged) / len(tagged)
    avg_all = sum(r["ret_20td"] for r in allv) / len(allv)
    diff = avg_tagged - avg_all
    return {"verdict": "有效" if diff > 2.0 else "无效", "diff_20": round(diff, 2),
            "n_tagged": len(tagged), "n_all": len(allv)}

def update_signal_config(verdict: str, diff: float) -> None:
    cfg_path = os.path.join(os.path.dirname(CONFIG_FILE), os.path.basename(CONFIG_FILE))
    cfg = ltc_store.load_state(cfg_path) or {"active": ACTIVE_SIGNALS, "consecutive_invalid": 0}
    if verdict == "有效":
        cfg["consecutive_invalid"] = 0
    else:
        cfg["consecutive_invalid"] = cfg.get("consecutive_invalid", 0) + 1
        if cfg["consecutive_invalid"] >= 2:
            cfg["active"] = ["资金关注", "逆势吸筹嫌疑", "派发嫌疑"]  # 资金撤离信号降级移除
            cfg["removed"] = cfg.get("removed", []) + ["资金撤离"]
    ltc_store.save_state(cfg_path, cfg)

def main() -> int:
    history = ltc_store.load_history("data/ltc/history.jsonl")
    if not history:
        print("无留痕数据，跳过验证")
        return 0
    td = _trading_dates()
    price: Dict[str, Dict[str, float]] = {}
    industries = {ind for h in history for ind in (h.get("tags") or {})}
    for ind in industries:
        k = ltc_data.fetch_board_kline(ind)
        if k is not None:
            for _, r in k.iterrows():
                price.setdefault(str(r["date"])[:10], {})[ind] = float(r["close"])
    results = forward_returns(history, price, td)
    results = [r for r in results if r.get("ret_20td") is not None]
    diff = differentiation(results)
    report = {"date": ltc_store.load_state(STATE_FILE).get("last_pushed_date", ""),
              "n_records": len(results), **diff}
    ltc_store.append_history("data/ltc/verification.jsonl", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    update_signal_config(diff.get("verdict", "数据不足"), diff.get("diff_20", 0.0))
    return 0

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过 + 本地验证 smoke test**

Run: `python -m pytest tests/test_ltc_verify.py -v`
Expected: 2 passed

Run: `python ltc_verify.py`（有留痕后）
Expected: 输出 report JSON；无留痕时输出"无留痕数据，跳过验证"

- [ ] **Step 5: 验证 workflow**

```yaml
# .github/workflows/ltc-verify.yml
name: 资金观察验证月报
on:
  schedule:
    # 每月1日 10:07 北京时间 (02:07 UTC)
    - cron: '7 2 1 * *'
  workflow_dispatch:
permissions:
  contents: write
jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: python ltc_verify.py || true
      - run: |
          mkdir -p data/ltc
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/ltc
          git diff --cached --quiet || git commit -m "data: 验证月报留痕 [skip ci]"
          git push
```

- [ ] **Step 6: 提交**

```bash
git add ltc_verify.py tests/test_ltc_verify.py .github/workflows/ltc-verify.yml
git commit -m "feat: ltc_verify 验证闭环 — 半月/月窗口区分度+信号降级"
```

---

### Task 11: 收尾 — 删旧文件、全量测试、清理临时文件

**Files:**
- Delete: `market-radar/long_term_capital.py`（git 历史保留；workflow 已不引用）
- Delete: `market-radar/_ltc_dump.json`、`market-radar/_ltc_push.txt`（本次会话临时文件）
- Delete: `market-radar/data/ltc_dry/`（本地 dry-run 残留，若有）

- [ ] **Step 1: 确认无引用后删除**

Run: `grep -rn "long_term_capital" --include="*.py" --include="*.yml" . | grep -v ".git/"`
Expected: 无输出（唯一引用已随 workflow 重写消失）

Run: `git rm long_term_capital.py _ltc_dump.json _ltc_push.txt && rm -rf data/ltc_dry`

- [ ] **Step 2: 全量测试**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（约 20+ tests）

- [ ] **Step 3: 最后端到端（真实数据 dry-run）**

Run: `python -c "import sys,os; sys.path.insert(0,'.'); import ltc_main; os.makedirs('data/ltc_dry', exist_ok=True); print(ltc_main.run_once({'FEISHU_APP_ID':'','FEISHU_APP_SECRET':'','FEISHU_CHAT_ID':'','DEEPSEEK_API_KEY':os.environ.get('DEEPSEEK_API_KEY','')}, 'data/ltc_dry'))"`
Expected: exit 0，卡片内容人工通读（对应 T0 原则：改完数据管道必须生成最终推送逐段通读，审计输出内容而非代码）

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor: 删除旧 long_term_capital 模块（已被 ltc_* 替代）"
```

- [ ] **Step 5: 交付检查（对照 PRD 验收标准 1-13 逐条勾选）**

完成后汇报：哪些验收标准已通过（可本地验证的）、哪些需要等真实运行验证（连续一周/节假日/跨夜）
