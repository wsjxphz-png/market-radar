# 板块总览重构实现计划（事实→说明→判断三层结构）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已上线的合并系统卡片从"六区块拼盘"重构为"板块总览为核心"——每板块 事实→说明→判断 三层结构，融合趋势交易论（短线判断）与微笑曲线（定投判断），场景分流不冲突。

**Architecture:** 在现有 merge_main 基础上：新增 val_explain.py（说明层规则化生成：分位含义翻译/名词解释/场景裁决器）；板块口径对齐层（THS↔东财 12 板块映射）；留痕补记 12 板块资金维；卡片渲染从六区块改为板块总览为主。

**Tech Stack:** Python 3.12、pandas、pytest（现有 197 测试全绿基线）

## Global Constraints

- 三层结构：事实层（三维事实完整呈现，机器可核验）/ 说明层（数据含义翻译+名词解释，加载趋势交易论参考框架）/ 判断层（基于事实推论的"推断"，**可缺席**——矛盾时输出"无法判断：…，等待证据"）
- 场景分流（判断不冲突）：趋势定短线（趋势第一性/永不下跌补仓）、估值定定投（微笑曲线跌时多买）、资金做确认；两场景各自标注用途，冲突时分流不互相覆盖
- 独立想法：与趋势交易论 skill 存疑时可标注分歧补充
- 板块口径：仪表盘板块（同花顺名）为主行，估值/资金（东财名）映射挂上（12 个核心板块映射）；估值↔资金同为东财口径直接交叉
- 事实永远在场；判断可缺席；名词首次出现大白话解释（GLOSSARY 扩展）
- 趋势是骨架，估值/资金不覆盖趋势结论
- 测试真实断言；全量 pytest 全绿（197 基线）

---

### Task 1: 口径对齐层 — val_config 扩展（THS↔东财映射）

**Files:**
- Modify: `market-radar/val_config.py`
- Test: `market-radar/tests/test_val_config.py`

**Interfaces:**
- Produces: `THS_TO_EM: dict[str, str]`（同花顺板块名→东财板块名，12 个核心板块）、`em_name_for(ths_name: str) -> Optional[str]`、`ths_name_for(em_name: str) -> Optional[str]`

- [ ] **Step 1: 写失败测试**

```python
def test_ths_em_mapping_covers_12_boards():
    # 估值表 12 个东财板块，每个都有同花顺对应名（仪表盘 SECTOR_RULES 口径）
    from val_config import INDEX_MAP, THS_TO_EM
    for em in INDEX_MAP:
        assert em in THS_TO_EM.values(), f"{em} 缺同花顺映射"

def test_ths_em_roundtrip():
    from val_config import em_name_for, ths_name_for
    # 半导体：东财"半导体" ↔ 同花顺"半导体"（同名）；银行同名
    assert em_name_for("半导体") == "半导体"
    assert ths_name_for("半导体") == "半导体"
    # 已知差异名：东财"汽车" ↔ 同花顺"汽车整车"；东财"煤炭" ↔ 同花顺"煤炭开采加工"等
    assert em_name_for("汽车整车") == "汽车"
    assert em_name_for("煤炭开采加工") == "煤炭"

def test_em_name_for_unknown():
    from val_config import em_name_for
    assert em_name_for("不存在的板块") is None
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# val_config.py 追加（实现时用仪表盘 SECTOR_RULES 实际板块名核对——先 grep sector_monitor.py 的板块名单）
# 同花顺板块名 → 东财板块名（仅覆盖 INDEX_MAP 的 12 个核心板块；其余板块无估值映射不强制）
THS_TO_EM = {
    "半导体": "半导体",
    "银行": "银行",
    "医药商业": "医药生物",      # 待核对 SECTOR_RULES 实际名
    # ... 实现时逐条核对 sector_monitor.py SECTOR_RULES 的板块名
}
```

（注意：实现时**必须**先 grep `sector_monitor.py` 的 SECTOR_RULES 拿真实同花顺板块名，逐条对应 INDEX_MAP 的 12 个东财板块；对不上的板块在测试中显式记录"无映射"并允许缺失——12 个核心板块必须全部有映射或明确记录缺口）

- [ ] **Step 3: 通过 + 提交**（message: `feat: val_config 口径对齐 — THS↔东财 12 板块映射`）

---

### Task 2: 资金维补记 12 板块 + 成交额单位修复

**Files:**
- Modify: `market-radar/market_dashboard.py`（merge_main 留痕）、`market-radar/ltc_data.py` 或 stock_data.py（成交额）
- Test: `market-radar/tests/test_merged_card.py`

**Interfaces:**
- Produces: `_record_merge_success` 扩展——留痕 tags 之外补记 `fund_by_board: {东财板块名: sl_net}`（12 板块，从当天完整 sector_flow 取，focus 之外的板块也算）；`compute_fund_state` 改为从 `fund_by_board` 读（12 板块全覆盖）

- [ ] **Step 1: 写失败测试**

```python
def test_record_merge_success_includes_all_12_boards(monkeypatch, tmp_path):
    # 完整 sector_flow 有 86 行，12 个估值板块即使不在 focus 前6 也要有 sl_net
    # merge_main 推送成功 → history 条目的 fund_by_board 含全部 12 个 INDEX_MAP 板块

def test_compute_fund_state_uses_fund_by_board():
    # 银行不在 focus 但 fund_by_board 有值 → inflow_confirm 可判定

def test_market_volume_unit():
    # 成交额 592亿 量级错误：get_market_volume 单位换算（亿/万）
    # 修复后 592亿 应显示为 5920亿 量级（或按实际数据源口径修正）
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# market_dashboard.py merge_main 留痕部分：
# 从当天 sector_flow（完整 86 行）构建 fund_by_board = {INDEX_MAP 板块名: 该板块 sl_net}
# （东财口径名直接匹配 industry 列；匹配不到的板块记 None）
# compute_fund_state 读 history 条目的 fund_by_board（优先），回退 tags

# 成交额：检查 get_market_volume 的单位（亿 vs 万），修复换算
```

- [ ] **Step 3: 通过 + 提交**（message: `fix: 资金维补记 12 板块留痕 + 成交额单位修复`）

---

### Task 3: 分位年数标注

**Files:**
- Modify: `market-radar/val_data.py`、`market-radar/val_format.py`
- Test: `market-radar/tests/test_val_format.py`

**Interfaces:**
- Produces: `pe_percentile` 输出加 `"years": float`（实际数据年数=days/244）；val_format 渲染"（指数历史 N 年）"

- [ ] **Step 1: 写失败测试**

```python
def test_pe_percentile_reports_years():
    # 500 天数据 → years≈2.0
    # 渲染含"历史 2 年"

def test_valuation_block_notes_history_years():
    # 估值区块每行显示指数历史年数（诚实标注数据窗口）
```

- [ ] **Step 2: 实现 → Step 3: 通过 + 提交**（message: `feat: 估值分位标注指数历史年数（数据窗口诚实性）`）

---

### Task 4: 说明层规则化生成 — val_explain.py（核心）

**Files:**
- Create: `market-radar/val_explain.py`
- Test: `market-radar/tests/test_val_explain.py`

**Interfaces:**
- Consumes: 事实（趋势状态/估值结论/资金状态）、`val_config`、GLOSSARY
- Produces:
  - `explain_percentile(pct: float, metric: str) -> str` — "PE 分位 15% = 过去十年只有 15% 的时间比现在便宜"
  - `explain_fund_state(fund_state: str, sl_net: float) -> str` — "连续 3 日净流入 = 有持续性的买入，不是一天的热闹"
  - `explain_trend(trend_state: str) -> str` — "20 日线上方上升期 = 短线处于顺势"
  - `glossary_for(terms: list[str]) -> str` — 首次出现的名词大白话解释（GLOSSARY 扩展：PE/PB/分位/净流入/20日线/微笑曲线/520战法/趋势第一性/永不下跌补仓）
  - `judge_short_term(trend_state: str, valuation: str) -> str` — **短线判断（趋势交易论）**：趋势上升→顺势持有不破20日线不动；趋势下降→不抄底（永不下跌补仓）等反转；震荡→等待明确
  - `judge_dca(valuation: str) -> str` — **定投判断（微笑曲线）**：便宜→可加码；合理→正常；贵→减量
  - `synthesize(板块事实) -> dict` — 三层结构组装：facts/explanation/short_term_judge/dca_judge/conflict_note（矛盾时"无法判断：…等待证据"）
- 生成说明时加载「趋势交易论」skill 参考框架（skill 内容在 .claude/skills/趋势交易论/，实现者读 cheatsheet.md + ch20 摘要）

- [ ] **Step 1: 写失败测试**（覆盖：分位翻译、资金状态翻译、趋势翻译、短线判断三态、定投判断三态、冲突分流——趋势下降+估值便宜 → 短线"不抄底"定投"可加码"且说明层解释；无法裁决场景输出"无法判断"）

- [ ] **Step 2: 实现**（规则化模板，无 AI；所有输出标"推断"）

- [ ] **Step 3: 通过 + 提交**（message: `feat: val_explain 说明层 — 事实翻译/名词解释/场景裁决器（趋势论+微笑曲线）`）

---

### Task 5: 板块总览重构 — 卡片渲染

**Files:**
- Modify: `market-radar/market_dashboard.py`（merge_main 渲染）、`market-radar/val_format.py`
- Test: `market-radar/tests/test_merged_card.py`

**Interfaces:**
- Produces: `build_board_overview(板块事实列表, 裁决结果) -> str` — 板块总览区块（每板块 事实→说明→判断 三层）；merge_main 卡片 = 板块总览（核心）+ 大盘概况 + AI 解读 + 诚实声明（次要区块）

- [ ] **Step 1: 写失败测试**

```python
def test_board_overview_three_layers():
    # 每板块包含 事实/说明/判断 三个标记
    # 判断含"短线操作"和"定投加码"两个场景
    # 名词首次出现有解释（PE/分位 等）

def test_board_overview_judgment_can_be_absent():
    # 矛盾场景 → "无法判断" 出现，不强行给结论

def test_board_overview_trend_framework_loaded():
    # 判断措辞含趋势交易论框架（"不破20日线"、"永不下跌补仓"、"微笑曲线"）
```

- [ ] **Step 2: 实现**（渲染层调用 val_explain；六区块 → 板块总览为核心）
- [ ] **Step 3: 通过 + dry-run**（真实网络，通读三层结构）→ **Step 4: 提交**（message: `feat: 板块总览重构 — 事实→说明→判断三层结构`）

---

### Task 6: 收尾 — 全量验证 + 生产

**Files:**
- Modify: `docs/prd/2026-08-05-merged-observation-system.md`（验收更新）

- [ ] **Step 1: 全量测试**（`python -m pytest tests/ -q` 全绿）
- [ ] **Step 2: 完整 dry-run** + 卡片逐段通读（三层结构/场景分流/名词解释/判断可缺席/无北向）
- [ ] **Step 3: PRD 验收更新 + 提交**（message: `refactor: 板块总览收尾`）
- [ ] **Step 4: 合并 main + push + 手动触发生产验证**（清 state 后触发，验证三层结构卡片推送）
