# 合并系统（估值判断）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 执行方式（2026-08-05 判断）：子代理驱动——任务间接口耦合（val_config→val_data→val_judge→val_format→合并），每任务独立审查员。

**Goal:** 合并 A股全景仪表盘 × 每日资金观察为一个系统，新增估值判断（第三版框架：主指标+修正1/2+PB否决权+二维置信度+执行建议）。

**Architecture:** 新增 val_* 模块族（配置/数据/判定/渲染）挂在现有 market_dashboard 骨架前；数据层复用 ltc_data（资金流/南向/回购/K线/熔断）与 ltc_store（留痕/参照）。估值数据源全部官方静态（中证 oss 文件 + BaoStock），无爬虫。

**Tech Stack:** Python 3.12、akshare、baostock、requests、pandas、pytest

## Global Constraints

- 判定框架（用户确认第三版 + 三修正）：
  - 基础：主指标分位（周期股→PB / 成长股→PE，查静态分类表）+ 交叉指标
  - 修正1：**仅主指标=PE 时启用**（日频近似：主指标趋势降→倾向错杀/升→倾向陷阱，标"推断"）；主指标=PB 时停用（季度修正1' ROE 方向——本期不实现，留接口注释）
  - 修正2：资金维=主力资金估算（低可信度标注），方向性粗筛；连续 ≥3 日确认；冷启动标"积累中"
  - 置信度：**二维独立计数**（主指标估值 + 资金确认）；**PB 交叉只降级不升级**（主指标便宜但 PB 分位 >40% → 降级；PB 便宜不捧场）
  - 执行层：输出无量纲建议（全额/半额/跳过），不碰资金流决策
  - 输出：结论（便宜/合理/贵/需警惕/观察）+ 主导维度 + 置信度（高/中/低）+ 行动建议 + 标"推断"
- 数据链三级降级：PE 分位（中证 index-perf，peg=PE，10 年窗口）→ PB 分位（中证 cons xls 成分 → BaoStock peTTM/pbMRQ 中位数聚合 + 留痕积累历史）→ 价格位置分位兜底（标注"基于价格位置，非 PE/PB"）
- 板块口径：东财板块（资金/仪表盘既有）↔ 中证指数（估值）映射表静态配置，实现时逐一验证 cons 文件存在
- 卡片日期=数据日期，data_date > last_pushed 才推（沿用）；工作日 16:15 单卡单推
- 操作指令保留（仪表盘定位）；北向不出现；AI 解读用 ltc_narrative 护栏模式
- 飞书需求文档内容全部作废（≤15 行/申万口径/11 标的/盈利真实数据源均不实现）
- 测试真实断言；估值判定纯函数化；全量 pytest 全绿（当前 138）

---

### Task V1: val_config.py — 映射表/分类表/阈值

**Files:**
- Create: `market-radar/val_config.py`
- Test: `market-radar/tests/test_val_config.py`

**Interfaces:**
- Produces: `INDEX_MAP: dict[str, str]`（东财板块名→中证指数代码）、`CYCLICAL_SECTORS: set[str]`、`GROWTH_SECTORS: set[str]`、`PCT_LOW/PCT_HIGH: int`（25/75）、`PB_VETO_GAP: int`（40）、`is_cyclical(sector: str) -> bool`、`index_code_for(sector: str) -> Optional[str]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_val_config.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_config import INDEX_MAP, CYCLICAL_SECTORS, GROWTH_SECTORS, PCT_LOW, PCT_HIGH, PB_VETO_GAP, is_cyclical, index_code_for

def test_index_map_covers_dashboard_boards():
    # 仪表盘 SECTOR_RULES 的 29 板块中，估值表至少覆盖 12 个核心板块
    core = ["半导体", "银行", "医药生物", "食品饮料", "有色金属", "通信设备",
            "计算机", "电力设备", "煤炭", "非银金融", "汽车", "国防军工"]
    for b in core:
        assert b in INDEX_MAP, f"{b} 缺映射"

def test_index_codes_are_6_digit():
    for code in INDEX_MAP.values():
        assert code.isdigit() and len(code) == 6

def test_classification_exhaustive_and_disjoint():
    # 分类表覆盖 INDEX_MAP 全部板块，且周期/成长不重叠
    all_boards = set(INDEX_MAP.keys())
    assert all_boards <= (CYCLICAL_SECTORS | GROWTH_SECTORS)
    assert CYCLICAL_SECTORS.isdisjoint(GROWTH_SECTORS)

def test_is_cyclical():
    assert is_cyclical("银行") is True
    assert is_cyclical("半导体") is False

def test_index_code_for():
    assert index_code_for("银行") == "399986"
    assert index_code_for("不存在板块") is None

def test_thresholds():
    assert PCT_LOW == 25 and PCT_HIGH == 75 and PB_VETO_GAP == 40
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_val_config.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# val_config.py
"""估值判断配置：板块→中证指数映射、周期/成长分类、阈值"""
from typing import Optional

# 东财板块名 → 中证指数代码（实现时逐一验证 cons 文件存在性）
INDEX_MAP = {
    "半导体": "931865",    # 中证半导体产业
    "银行": "399986",      # 中证银行
    "医药生物": "000933",  # 中证医药卫生
    "食品饮料": "000932",  # 中证主要消费
    "有色金属": "930708",  # 中证有色金属
    "通信设备": "931160",  # 中证通信设备
    "计算机": "930651",    # 中证计算机
    "电力设备": "931151",  # 中证电力设备
    "煤炭": "930817",      # 中证煤炭
    "非银金融": "399975",  # 中证全指证券公司
    "汽车": "930997",      # 中证汽车
    "国防军工": "399967",  # 中证军工
}

# 周期/成长分类（第三版框架：主指标选择依据；静态，随季度校准可调）
CYCLICAL_SECTORS = {"银行", "有色金属", "煤炭", "非银金融", "钢铁", "房地产",
                    "建筑装饰", "交通运输", "石油石化", "基础化工"}
GROWTH_SECTORS = {"半导体", "医药生物", "食品饮料", "通信设备", "计算机",
                  "电力设备", "汽车", "国防军工", "电子", "机械设备"}

# 阈值（用户确认待校准：先跑一个月，用真实数据对照直觉调整）
PCT_LOW = 25    # 主指标分位 <25% → 便宜
PCT_HIGH = 75   # 主指标分位 >75% → 贵
PB_VETO_GAP = 40  # 主指标说"便宜"但 PB 分位 >40% → 降级（否决权）


def is_cyclical(sector: str) -> bool:
    return sector in CYCLICAL_SECTORS


def index_code_for(sector: str) -> Optional[str]:
    return INDEX_MAP.get(sector)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_val_config.py -v`
Expected: 6 passed

- [ ] **Step 5: 验证映射有效性（真实网络 smoke）**

Run: `python -c "import sys; sys.path.insert(0,'.'); from val_config import INDEX_MAP; import requests; [print(k, requests.head(f'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{v}cons.xls', timeout=10).status_code) for k,v in INDEX_MAP.items()]"`
Expected: 全部 200（有 404 的板块在报告中记录，任务 V2 换备用代码或标注不可用）

- [ ] **Step 6: 提交**

```bash
git add val_config.py tests/test_val_config.py
git commit -m "feat: val_config 估值配置 — 板块→中证指数映射/周期成长分类/阈值"
```

---

### Task V2: val_data.py — 估值数据源（PE 分位/PB 聚合/三级降级链）

**Files:**
- Create: `market-radar/val_data.py`
- Test: `market-radar/tests/test_val_data.py`

**Interfaces:**
- Consumes: `val_config.INDEX_MAP/is_cyclical/PCT_LOW/PCT_HIGH`
- Produces:
  - `fetch_pe_series(index_code: str) -> Optional[pd.DataFrame]` — 中证 index-perf 全历史 `{"date","peg"}`（peg=PE 已验证）
  - `pe_percentile(pe_series, lookback_days=2440) -> Optional[dict]` — `{"pe": float, "pct": float, "trend": "up"|"down"|"flat", "days": int}`（trend=近 20 交易日 PE 变化方向）
  - `fetch_constituents(index_code: str) -> Optional[list[str]]` — 中证 cons xls 成分代码（转 BaoStock 格式 sh./sz.）
  - `fetch_sector_pb(sector: str) -> Optional[dict]` — `{"pb": float, "method": "成分中位数", "n": int}`（BaoStock 成分股 pbMRQ 中位数）
  - `fetch_price_position(sector: str) -> Optional[dict]` — 复用 ltc_data.fetch_board_kline 算价格位置分位（兜底）
  - `fetch_valuation_snapshot(boards: list[str], history: list[dict]) -> list[dict]` — 全板块三级降级链，每板块 `{"board", "source": "pe"|"pb"|"price", "main_pct", "pb_pct", "pe_pct", "trend", "note"}`
  - `_pb_reference(history, sector) -> Optional[float]` — 留痕近 20 日 PB 均值（冷启动 None）

- [ ] **Step 1: 写失败测试（纯函数 + mock 网络）**

```python
# tests/test_val_data.py
import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_data import pe_percentile, fetch_valuation_snapshot, _pb_reference

def test_pe_percentile_math():
    # 200 个交易日，当前值处于中位
    dates = pd.date_range("2025-01-01", periods=200, freq="B")
    pe = pd.DataFrame({"date": dates, "peg": [10.0 + (i % 100) * 0.1 for i in range(200)]})
    out = pe_percentile(pe)
    assert out["days"] == 200
    assert 40 < out["pct"] < 60
    # 趋势：最后 20 日上升
    pe2 = pd.DataFrame({"date": dates, "peg": [10.0 + i * 0.01 for i in range(200)]})
    assert pe_percentile(pe2)["trend"] == "up"
    pe3 = pd.DataFrame({"date": dates, "peg": [10.0 - i * 0.01 for i in range(200)]})
    assert pe_percentile(pe3)["trend"] == "down"

def test_pe_percentile_short_history():
    pe = pd.DataFrame({"date": pd.date_range("2026-07-01", periods=30, freq="B"), "peg": [12.0]*30})
    out = pe_percentile(pe)
    assert out is None or out["days"] < 2440  # 历史不足：返回带 days 的结果，由调用方决定是否可信

def test_pb_reference_cold_start():
    assert _pb_reference([], "银行") is None
    hist = [{"date": "2026-07-01", "sector_pb": {"银行": 0.5}}] * 25
    ref = _pb_reference(hist, "银行")
    assert ref is not None and 0.49 < ref < 0.51

def test_fetch_valuation_snapshot_degradation_chain(monkeypatch):
    # 全失败 → 价格位置兜底；PE 可用 → 用 PE
    calls = {"pe": 0, "pb": 0, "price": 0}
    def fake_pe(code):
        calls["pe"] += 1
        dates = pd.date_range("2020-01-01", periods=500, freq="B")
        return pd.DataFrame({"date": dates, "peg": [15.0]*500})
    def fake_pb(sector):
        calls["pb"] += 1
        return None  # PB 失败
    def fake_price(sector):
        calls["price"] += 1
        return {"position_pct": 43.0}
    monkeypatch.setattr("val_data.fetch_pe_series", fake_pe)
    monkeypatch.setattr("val_data.fetch_sector_pb", fake_pb)
    monkeypatch.setattr("val_data.fetch_price_position", fake_price)
    out = fetch_valuation_snapshot(["银行"], [])
    assert len(out) == 1
    assert out[0]["source"] == "pe"      # PE 可用则用 PE
    assert out[0]["main_pct"] is not None
    assert calls["price"] == 0

def test_fetch_valuation_snapshot_all_fail_price_fallback(monkeypatch):
    monkeypatch.setattr("val_data.fetch_pe_series", lambda code: None)
    monkeypatch.setattr("val_data.fetch_sector_pb", lambda s: None)
    monkeypatch.setattr("val_data.fetch_price_position", lambda s: {"position_pct": 12.0})
    out = fetch_valuation_snapshot(["银行"], [])
    assert out[0]["source"] == "price"
    assert out[0]["main_pct"] == 12.0
    assert out[0]["note"] == "基于价格位置，非 PE/PB"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_val_data.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# val_data.py
"""估值数据源：中证 PE 全历史分位 / 中证成分×BaoStock PB 聚合 / 价格位置兜底"""
import logging
from typing import Dict, List, Optional
import pandas as pd
import requests
import baostock as bs
import ltc_data
from val_config import INDEX_MAP

logger = logging.getLogger(__name__)
H = {"User-Agent": "Mozilla/5.0"}
PE_LOOKBACK_DAYS = 2440  # 10 年交易日窗口

def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning("%s 失败: %s", getattr(fn, "__name__", fn), str(e)[:80])
        return None

def fetch_pe_series(index_code: str) -> Optional[pd.DataFrame]:
    """中证 index-perf 全历史：peg 字段=PE(TTM 总股本口径，已验证)"""
    r = requests.get("https://www.csindex.com.cn/csindex-home/perf/index-perf",
                     params={"indexCode": index_code, "startDate": "20100101",
                             "endDate": pd.Timestamp.now().strftime("%Y%m%d")},
                     timeout=30, headers=H)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        return None
    df = pd.DataFrame(data)
    df = df[["tradeDate", "peg"]].rename(columns={"tradeDate": "date", "peg": "pe"})
    df["date"] = pd.to_datetime(df["date"])
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    return df.dropna(subset=["pe"]).sort_values("date")

def pe_percentile(pe_series: Optional[pd.DataFrame], lookback_days: int = PE_LOOKBACK_DAYS) -> Optional[dict]:
    if pe_series is None or len(pe_series) < 60:
        return None
    recent = pe_series.tail(lookback_days)
    cur = float(recent["pe"].iloc[-1])
    pct = float((recent["pe"] < cur).mean() * 100)
    last20 = recent["pe"].tail(20)
    if len(last20) >= 10:
        chg = float(last20.iloc[-1] - last20.iloc[0])
        trend = "up" if chg > 0.05 else ("down" if chg < -0.05 else "flat")
    else:
        trend = "flat"
    return {"pe": round(cur, 2), "pct": round(pct, 1), "trend": trend, "days": len(recent)}

def fetch_constituents(index_code: str) -> Optional[List[str]]:
    """中证 cons xls 成分股 → BaoStock 代码格式（sh./sz.）"""
    url = (f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
           f"file/autofile/cons/{index_code}cons.xls")
    df = pd.read_excel(url)
    codes = []
    for raw in df.iloc[:, 4].dropna().astype(str):
        raw = raw.strip()
        if raw.startswith(("6", "9")):
            codes.append(f"sh.{raw}")
        elif raw.startswith(("0", "3")):
            codes.append(f"sz.{raw}")
        elif raw.startswith(("4", "8")):
            codes.append(f"bj.{raw}")
    return codes or None

def _bs_login():
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {lg.error_msg}")
    return lg

def fetch_sector_pb(sector: str) -> Optional[dict]:
    """板块 PB = 中证成分股 pbMRQ 中位数（BaoStock，官方源）"""
    code = INDEX_MAP.get(sector)
    if not code:
        return None
    cons = _safe(fetch_constituents, code)
    if not cons:
        return None
    lg = _safe(_bs_login)
    if lg is None:
        return None
    try:
        pb_vals = []
        for c in cons[:50]:  # 每板块最多 50 只代表成分，控制耗时
            rs = bs.query_history_k_data_plus(c, "date,pbMRQ",
                                              start_date=(pd.Timestamp.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                                              end_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
                                              frequency="d")
            row = None
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
            if row and row[1] and row[1] not in ("", "0"):
                pb_vals.append(float(row[1]))
        if not pb_vals:
            return None
        import statistics
        return {"pb": round(statistics.median(pb_vals), 2), "method": "成分中位数", "n": len(pb_vals)}
    finally:
        bs.logout()

def fetch_price_position(sector: str) -> Optional[dict]:
    """价格位置分位兜底（复用 ltc_data K 线）"""
    k = ltc_data.fetch_board_kline(sector)
    if k is None or len(k) < 60:
        return None
    close = k["close"].values
    pos = (close[-1] - close.min()) / (close.max() - close.min()) * 100 if close.max() > close.min() else 50
    return {"position_pct": round(float(pos), 1)}

def _pb_reference(history: List[dict], sector: str) -> Optional[float]:
    """留痕近 20 日板块 PB 均值（冷启动 None）"""
    vals = [h["sector_pb"][sector] for h in reversed(history)
            if h.get("sector_pb", {}).get(sector) is not None]
    if not vals:
        return None
    vals = vals[:20]
    return sum(vals) / len(vals)

def fetch_valuation_snapshot(boards: List[str], history: List[dict]) -> List[dict]:
    """全板块三级降级链：PE 分位 → PB 分位 → 价格位置（标注口径）"""
    out = []
    for board in boards:
        pe_series = _safe(fetch_pe_series, INDEX_MAP.get(board, "")) if board in INDEX_MAP else None
        pe_info = pe_percentile(pe_series) if pe_series is not None else None
        if pe_info is not None:
            pb = _safe(fetch_sector_pb, board)
            entry = {
                "board": board, "source": "pe",
                "main_pct": pe_info["pct"], "pe_pct": pe_info["pct"],
                "pb_pct": pb["pb"] if pb else None,
                "trend": pe_info["trend"], "note": "",
            }
            out.append(entry)
            continue
        pb = _safe(fetch_sector_pb, board)
        if pb is not None:
            ref = _pb_reference(history, board)
            entry = {
                "board": board, "source": "pb",
                "main_pct": None, "pe_pct": None, "pb_pct": pb["pb"],
                "trend": "flat",
                "note": f"PB={pb['pb']}（成分中位数）" + (f"，近20日均值 {ref:.2f}" if ref else "，PB 分位积累中"),
            }
            out.append(entry)
            continue
        price = _safe(fetch_price_position, board)
        entry = {
            "board": board, "source": "price",
            "main_pct": price["position_pct"] if price else None,
            "pe_pct": None, "pb_pct": None, "trend": "flat",
            "note": "基于价格位置，非 PE/PB" if price else "估值数据源全部不可用",
        }
        out.append(entry)
    return out
```

- [ ] **Step 4: 运行确认通过 + 真实网络 smoke**

Run: `python -m pytest tests/test_val_data.py -v`
Expected: 5 passed

Run（真实 smoke）: `python -c "import sys,logging; sys.path.insert(0,'.'); logging.basicConfig(level=logging.WARNING); from val_data import fetch_pe_series, pe_percentile, fetch_sector_pb; s=fetch_pe_series('399986'); print('银行PE分位:', pe_percentile(s)); print('银行PB:', fetch_sector_pb('银行'))"`
Expected: PE 分位（10 年窗口）+ PB 中位数（成分股）真实输出；记录到报告

- [ ] **Step 5: 提交**

```bash
git add val_data.py tests/test_val_data.py
git commit -m "feat: val_data 估值数据源 — 中证PE分位/成分PB聚合/三级降级链"
```

---

### Task V3: val_judge.py — 判定框架（第三版）

**Files:**
- Create: `market-radar/val_judge.py`
- Test: `market-radar/tests/test_val_judge.py`

**Interfaces:**
- Consumes: `val_config.is_cyclical/PCT_LOW/PCT_HIGH/PB_VETO_GAP`
- Produces:
  - `judge_valuation(board: str, main_pct: Optional[float], trend: str, pb_pct: Optional[float], fund_state: str, pb_ref: Optional[float]) -> dict` — 输出 `{"board", "verdict", "dominant", "confidence", "action", "note"}`
  - fund_state ∈ {"inflow_confirm", "outflow_confirm", "single_day", "cold_start", "unknown"}
  - action ∈ {"full", "half", "skip", "none"}（全额/半额/跳过/不追加）

- [ ] **Step 1: 写失败测试（覆盖第三版全部规则）**

```python
# tests/test_val_judge.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_judge import judge_valuation

def test_growth_pe_low_inflow_high_confidence():
    # 成长股（主指标PE）：PE 分位 15%（便宜）+ 资金流入确认 → 高置信度，全额
    out = judge_valuation("半导体", 15.0, "down", 20.0, "inflow_confirm", None)
    assert out["verdict"] == "便宜"
    assert out["confidence"] == "高"
    assert out["action"] == "full"
    assert out["dominant"] == "估值+资金"
    assert "推断" in out["note"]

def test_cyclical_pb_low_inflow_high_confidence():
    # 周期股（主指标PB）：用 PB 分位判断
    out = judge_valuation("银行", 15.0, "flat", 12.0, "inflow_confirm", 0.6)
    assert out["verdict"] == "便宜"
    assert out["confidence"] == "高"

def test_pb_veto_degrades_not_upgrades():
    # PB 否决：主指标便宜（PE 15%）但 PB 分位高（60%>40%）→ 降级到观察
    out = judge_valuation("半导体", 15.0, "down", 60.0, "inflow_confirm", None)
    assert out["confidence"] == "中"
    assert out["verdict"] in ("便宜", "观察")
    # PB 便宜不捧场：主指标合理（PE 50%）+ PB 便宜（10%）+ 资金确认 → 不能升到高
    out2 = judge_valuation("半导体", 50.0, "flat", 10.0, "inflow_confirm", None)
    assert out2["confidence"] != "高"

def test_earnings_trend_modifier_only_for_growth():
    # 修正1 仅主指标=PE（成长股）启用：PE 便宜 + 趋势降 → 倾向错杀
    out = judge_valuation("半导体", 15.0, "down", None, "cold_start", None)
    assert "错杀" in out["note"]
    # 周期股（主指标PB）不套用修正1
    out2 = judge_valuation("银行", 15.0, "down", 10.0, "cold_start", None)
    assert "错杀" not in out2["note"]

def test_single_dim_medium_confidence():
    # 单维（估值便宜但资金冷启动）→ 中置信度
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "cold_start", None)
    assert out["confidence"] == "中"

def test_contradiction_low_confidence():
    # 矛盾：估值便宜 + 资金流出确认 → 低置信度，观察
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "outflow_confirm", None)
    assert out["confidence"] == "低"
    assert out["action"] == "skip"

def test_expensive_no_add():
    # 贵 → 不追加
    out = judge_valuation("半导体", 85.0, "up", 90.0, "inflow_confirm", None)
    assert out["verdict"] in ("贵", "需警惕")
    assert out["action"] == "none"

def test_main_pct_none_watch():
    # 数据全缺 → 观察
    out = judge_valuation("半导体", None, "flat", None, "unknown", None)
    assert out["verdict"] == "观察"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_val_judge.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# val_judge.py
"""估值判定框架（第三版）：主指标 + 修正1(仅成长) + PB否决权 + 二维置信度 + 执行建议"""
from typing import Optional
from val_config import is_cyclical, PCT_LOW, PCT_HIGH, PB_VETO_GAP


def judge_valuation(board: str, main_pct: Optional[float], trend: str,
                    pb_pct: Optional[float], fund_state: str,
                    pb_ref: Optional[float]) -> dict:
    cyclical = is_cyclical(board)
    note_parts = []

    # ── 主指标估值判定 ──
    if main_pct is None:
        return {"board": board, "verdict": "观察", "dominant": "数据不足",
                "confidence": "低", "action": "skip", "note": "估值数据不可用（推断）"}
    if main_pct < PCT_LOW:
        verdict = "便宜"
    elif main_pct > PCT_HIGH:
        verdict = "贵"
    else:
        verdict = "合理"

    # ── 修正1：仅主指标=PE（非周期）时启用；趋势降→倾向错杀 / 升→倾向陷阱 ──
    if not cyclical and verdict == "便宜":
        if trend == "down":
            note_parts.append("主指标趋势下行，倾向错杀（盈利回升中，推断）")
        elif trend == "up":
            note_parts.append("主指标趋势上行，倾向陷阱（盈利仍在下滑，推断）")

    # ── PB 交叉：只降级不升级 ──
    if verdict == "便宜" and pb_pct is not None and pb_pct > PB_VETO_GAP:
        note_parts.append(f"PB 分位 {pb_pct:.0f}% > {PB_VETO_GAP}%（否决：PB 说真话）")
        verdict = "观察"

    # ── 资金维（估算，低可信度；方向性粗筛）──
    fund_dim = None
    if fund_state == "inflow_confirm":
        fund_dim = "up"
    elif fund_state == "outflow_confirm":
        fund_dim = "down"
    elif fund_state == "single_day":
        fund_dim = "single"
    # cold_start/unknown → fund_dim 保持 None

    # ── 置信度：二维独立（主指标估值 + 资金）──
    est_dim = 1 if (verdict in ("便宜", "贵")) else 0.5
    if fund_dim == "up" or fund_dim == "down":
        fund_dim_ok = 1
    elif fund_dim == "single":
        fund_dim_ok = 0.5
    else:
        fund_dim_ok = 0
    dims = est_dim + fund_dim_ok
    if fund_dim == "down" and verdict == "便宜":
        confidence = "低"  # 矛盾：资金流出确认
    elif dims >= 2:
        confidence = "高"
    elif dims >= 1:
        confidence = "中"
    else:
        confidence = "低"
    if fund_dim is None:
        note_parts.append("资金维数据积累中（冷启动）" if fund_state == "cold_start" else "资金维数据不可用")

    # ── 执行建议（无量纲）──
    if verdict == "贵":
        action, dominant = "none", "估值"
    elif verdict == "观察":
        action, dominant = "skip", "估值+PB否决" if "否决" in "".join(note_parts) else "数据不足"
    elif verdict == "便宜":
        if confidence == "高":
            action, dominant = "full", "估值+资金"
        elif confidence == "中":
            action, dominant = "half", "估值" if fund_dim is None else "估值+资金"
        else:
            action, dominant = "skip", "估值（矛盾）"
    else:  # 合理
        action, dominant = "half" if confidence == "高" else "skip", "估值"

    note_parts.append("推断")
    return {"board": board, "verdict": verdict, "dominant": dominant,
            "confidence": confidence, "action": action,
            "note": "；".join(note_parts)}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_val_judge.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add val_judge.py tests/test_val_judge.py
git commit -m "feat: val_judge 估值判定框架（第三版）— 主指标/修正1作用域/PB否决权/二维置信度/执行建议"
```

---

### Task V4: val_format.py — 估值表渲染

**Files:**
- Create: `market-radar/val_format.py`
- Test: `market-radar/tests/test_val_format.py`

**Interfaces:**
- Consumes: `val_judge.judge_valuation` 输出、`val_data.fetch_valuation_snapshot` 输出
- Produces: `format_valuation_block(judgements: list[dict], snapshots: list[dict]) -> str` — 估值表区块（卡片顶部）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_val_format.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_format import format_valuation_block

def test_block_structure():
    judgements = [
        {"board": "半导体", "verdict": "便宜", "dominant": "估值+资金", "confidence": "高",
         "action": "full", "note": "推断"},
        {"board": "银行", "verdict": "合理", "dominant": "估值", "confidence": "中",
         "action": "half", "note": "推断"},
    ]
    snapshots = [
        {"board": "半导体", "source": "pe", "main_pct": 15.0, "pb_pct": 20.0, "pe_pct": 15.0,
         "trend": "down", "note": ""},
        {"board": "银行", "source": "pb", "main_pct": None, "pb_pct": 0.42, "pe_pct": None,
         "trend": "flat", "note": "PB=0.42（成分中位数）"},
    ]
    block = format_valuation_block(judgements, snapshots)
    assert "估值判断" in block
    assert "半导体" in block and "便宜" in block
    assert "高置信度" in block and "全额" in block
    assert "推断" in block
    # 口径标注
    assert "PE分位 15%" in block
    assert "PB=0.42" in block

def test_block_empty():
    assert format_valuation_block([], []) == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_val_format.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# val_format.py
"""估值判断区块渲染（卡片顶部）"""
from typing import List

ACTION_TEXT = {"full": "按计划定投", "half": "半额定投/等资金确认", "skip": "只观察，不买", "none": "不追加"}


def format_valuation_block(judgements: List[dict], snapshots: List[dict]) -> str:
    if not judgements:
        return ""
    lines = ["━━━ 💰 估值判断（推断） ━━━"]
    for j in judgements:
        snap = next((s for s in snapshots if s["board"] == j["board"]), {})
        src = snap.get("source", "")
        if src == "pe":
            pos = f"PE分位 {snap.get('pe_pct', 0):.0f}%"
            if snap.get("pb_pct") is not None:
                pos += f" | PB {snap['pb_pct']:.2f}"
        elif src == "pb":
            pos = snap.get("note", "PB 数据")
        else:
            pos = f"价格位置 {snap.get('main_pct', 0):.0f}%"
        lines.append(f"- {j['board']}：{j['verdict']}（{pos}）")
        conf = {"高": "高置信度", "中": "中置信度", "低": "低置信度"}.get(j["confidence"], "观察")
        lines.append(f"  {conf}｜{ACTION_TEXT.get(j['action'], '观察')}｜{j['note']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_val_format.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add val_format.py tests/test_val_format.py
git commit -m "feat: val_format 估值判断区块渲染"
```

---

### Task V5: 合并编排 — market_dashboard 骨架 + 估值表 + 资金数据并入

**Files:**
- Modify: `market-radar/market_dashboard.py`（主骨架）、`market-radar/rally-health.yml`
- Create: `market-radar/tests/test_merged_card.py`

**Interfaces:**
- Consumes: `val_data.fetch_valuation_snapshot`、`val_judge.judge_valuation`、`val_format.format_valuation_block`、`ltc_data`（资金流/南向/回购）、`ltc_analysis`（归因/承接）、`ltc_store`（留痕/参照）
- Produces: `build_merged_card(data) -> str`（新卡片全文）；`merge_main()`（合并入口）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_merged_card.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from market_dashboard import build_merged_card

def test_merged_card_sections_order():
    data = {
        "valuation_judgements": [
            {"board": "银行", "verdict": "便宜", "dominant": "估值+资金", "confidence": "高",
             "action": "full", "note": "推断"}],
        "valuation_snapshots": [
            {"board": "银行", "source": "pb", "main_pct": None, "pb_pct": 0.42,
             "pe_pct": None, "trend": "flat", "note": "PB=0.42（成分中位数）"}],
        "market_overview": "上证 +0.33%",
        "sector_ops": "半导体 hold",
        "fund_section": "今日资金：银行 逆势吸筹嫌疑",
        "interpretation": "今日解读文本",
        "honest": "诚实声明",
    }
    card = build_merged_card(data)
    order = [card.find(s) for s in ["估值判断", "market_overview", "fund_section", "interpretation", "诚实声明"]]
    assert all(o >= 0 for o in order)
    assert order == sorted(order)  # 区块顺序：估值→概况→资金→解读→声明

def test_merged_card_has_no_northbound():
    data = {"valuation_judgements": [], "valuation_snapshots": [],
            "market_overview": "x", "sector_ops": "y", "fund_section": "z",
            "interpretation": "w", "honest": "h"}
    card = build_merged_card(data)
    assert "北向" not in card
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_merged_card.py -v`
Expected: FAIL（build_merged_card 不存在）

- [ ] **Step 3: 实现（合并编排）**

在 `market_dashboard.py` 增加：

```python
def build_merged_card(data: dict) -> str:
    """合并卡片：估值判断 → 大盘概况 → 板块异动+操作建议 → 今日资金 → AI 解读 → 诚实声明"""
    from val_format import format_valuation_block
    parts = []
    val = format_valuation_block(data.get("valuation_judgements", []),
                                 data.get("valuation_snapshots", []))
    if val:
        parts.append(val)
    if data.get("market_overview"):
        parts.append(f"━━━ 📈 大盘概况 ━━━\n{data['market_overview']}")
    if data.get("sector_ops"):
        parts.append(f"━━━ 🎯 板块异动与操作建议 ━━━\n{data['sector_ops']}")
    if data.get("fund_section"):
        parts.append(f"━━━ 💰 今日资金 ━━━\n{data['fund_section']}")
    if data.get("interpretation"):
        parts.append(data["interpretation"])
    if data.get("honest"):
        parts.append(f"━━━ 🔎 诚实声明 ━━━\n{data['honest']}")
    return "\n\n".join(parts)
```

新增 `merge_main()`（合并入口，逐步替换现有 main 的数据组装）：
- 复用现有 main 的抓取（大盘概况/板块异动/操作建议已存在）
- 新增：`snapshots = val_data.fetch_valuation_snapshot(BOARDS, history)`（BOARDS 从 val_config.INDEX_MAP 取）
- 新增：`judgements = [val_judge.judge_valuation(s["board"], s["main_pct"], s["trend"], s["pb_pct"], compute_fund_state(history, s["board"]), _pb_ref(history, s["board"])) for s in snapshots]`
- 新增：`fund_section` = ltc_analysis 归因 + 南向 + 回购（复用 ltc_main 的组装逻辑，抽成可复用函数）
- 新增：AI 解读用 ltc_narrative.interpretation（事实清单模式），替换 ai_audit
- 推送日期规则沿用（data_date > last_pushed）
- 北向相关全部不出现

新增资金确认函数（修正 2 核心，放 market_dashboard.py 或 ltc_analysis.py）：

```python
def compute_fund_state(history: list, sector: str, days: int = 3) -> str:
    """资金维状态：连续 ≥3 日主力净流入/流出确认；单日=观察；无留痕=冷启动
    返回 inflow_confirm / outflow_confirm / single_day / cold_start / unknown"""
    flows = []
    for h in reversed(history):
        tags = h.get("tags", {})
        entry = tags.get(sector)
        if entry and entry.get("sl_net") is not None:
            flows.append(float(entry["sl_net"]))
        if len(flows) >= days:
            break
    if not flows:
        return "cold_start" if history else "unknown"
    if len(flows) >= days and all(f > 0 for f in flows[:days]):
        return "inflow_confirm"
    if len(flows) >= days and all(f < 0 for f in flows[:days]):
        return "outflow_confirm"
    return "single_day"
```

测试（并入 tests/test_merged_card.py）：连续 3 日正 sl_net → inflow_confirm；混合 → single_day；空历史 → unknown；有历史无该板块 → cold_start。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_merged_card.py -v`
Expected: 2 passed

Run（真实网络 dry-run）: 参考现有 `python market_dashboard.py --dry-run` 结构，跑合并入口 dry-run，卡片内容通读（估值表/概况/异动/资金/解读/声明齐全；无北向；无操作指令违规——注意操作建议是保留功能，不在此列）
Expected: exit 0，卡片六区块齐全

- [ ] **Step 5: 提交**

```bash
git add market_dashboard.py tests/test_merged_card.py
git commit -m "feat: 合并编排 — 估值表+资金观察并入仪表盘骨架"
```

---

### Task V6: workflow 改造 — 单卡单推 16:15

**Files:**
- Modify: `market-radar/.github/workflows/rally-health.yml`（cron 16:15 + 合并入口）
- Modify: `market-radar/.github/workflows/long-term-capital.yml`（停 schedule，保留手动）

- [ ] **Step 1: 改造 rally-health.yml**

```yaml
on:
  schedule:
    # 北京时间 16:15 (UTC 08:15), 周一至周五
    - cron: '15 8 * * 1-5'
  workflow_dispatch:
```

- python 步骤改为调用合并入口（`python -c "from market_dashboard import merge_main; merge_main()"` 或脚本化），保留退出码捕获 + ::error:: + 留痕提交（含 data/dashboard/state.json 与 data/ltc/）

- [ ] **Step 2: 停用 long-term-capital.yml 的 schedule（保留 workflow_dispatch 手动）**

```yaml
on:
  # schedule 已停用（合并系统上线后单卡单推）
  workflow_dispatch:
```

- [ ] **Step 3: 本地端到端 dry-run（真实网络）**

Run: 合并入口 dry-run（--dry-run 模式）
Expected: exit 0；卡片六区块；估值表有真实 PE/PB 数据（或降级标注）；资金区块真实；无重复推送（state 去重生效）

- [ ] **Step 4: 全量测试 + 提交**

Run: `python -m pytest tests/ -q`
Expected: 全绿（138 + 新增）

```bash
git add .github/workflows/rally-health.yml .github/workflows/long-term-capital.yml
git commit -m "ci: 合并推送 — 16:15 单卡单推，停用资金观察独立推送"
```

---

### Task V7: 收尾 — 全量验证 + PRD 验收对照

**Files:**
- Delete: 临时文件（如有）
- Modify: `docs/prd/2026-08-05-merged-observation-system.md`（验收勾选）

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -v`
Expected: 全绿

- [ ] **Step 2: 最终 dry-run + 卡片逐段通读**

Run: 合并入口 dry-run（真实网络）
Expected: 逐段通读：估值表（含口径标注/置信度/行动建议/推断）→ 概况 → 异动+操作建议 → 资金（归因/承接/南向/回购）→ AI 解读 → 诚实声明；无北向

- [ ] **Step 3: PRD 验收对照（docs/prd/2026-08-05-merged-observation-system.md 的验收标准 1-8 逐条勾选）**

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor: 合并系统收尾 — 全量验证+验收对照"
```

- [ ] **Step 5: 交付检查 + 校准计划**

完成后汇报：本地可验证项 ✅；需真实运行验证（工作日 16:15 首次推送、留痕积累后 PB 分位、一个月后阈值校准）。将"留痕跑满一个月后贴实际信号对照直觉校准"写入待办。
