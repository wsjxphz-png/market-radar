# -*- coding: utf-8 -*-
"""合并卡片纯函数测试 — build_merged_card 六区块顺序/无北向 + compute_fund_state 资金维确认。
原则：注入假数据，不触网。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from market_dashboard import build_merged_card, compute_fund_state, _with_metric_disclosure


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
    # 注：brief 原测试用 "market_overview"/"fund_section"/"interpretation" 作 find 目标，
    # 但实现按 brief 逐字渲染中文标题（大盘概况/今日资金）与数据值（今日解读文本），
    # 字面 key 永不出现 → 按 Task 3/4 先例改为真实渲染标记，断言意图不变（区块顺序）
    order = [card.find(s) for s in ["估值判断", "大盘概况", "今日资金", "今日解读文本", "诚实声明"]]
    assert all(o >= 0 for o in order)
    assert order == sorted(order)  # 区块顺序：估值→概况→资金→解读→声明


def test_merged_card_has_no_northbound():
    data = {"valuation_judgements": [], "valuation_snapshots": [],
            "market_overview": "x", "sector_ops": "y", "fund_section": "z",
            "interpretation": "w", "honest": "h"}
    card = build_merged_card(data)
    assert "北向" not in card


def test_merged_card_skips_empty_blocks():
    """缺块不得输出占位标题（F2：数据不可用不得静默缺块，也不得编造）"""
    card = build_merged_card({})
    assert card == ""


def test_compute_fund_state_inflow_confirm():
    """连续 3 日主力净流入 → inflow_confirm"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": 2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": 1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "inflow_confirm"


def test_compute_fund_state_outflow_confirm():
    """连续 3 日主力净流出 → outflow_confirm"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": -2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": -1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": -0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "outflow_confirm"


def test_compute_fund_state_mixed_single_day():
    """方向混合 → single_day（确认需连续同向）"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": 2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": -1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_empty_history_unknown():
    assert compute_fund_state([], "银行") == "unknown"


def test_compute_fund_state_no_sector_cold_start():
    """有留痕但该板块从未出现 → cold_start"""
    history = [
        {"tags": {"半导体": {"tag": "", "sl_net": 1.0}}},
        {"tags": {"其他": {"tag": "", "sl_net": 2.0}}},
    ]
    assert compute_fund_state(history, "银行") == "cold_start"


def test_compute_fund_state_sl_net_none_skipped():
    """sl_net 为 None/缺失的条目不计数（不当作 0）"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": None}}},
        {"tags": {"银行": {"tag": "", "sl_net": 1.2}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_metric_disclosure_pe_degradation():
    """周期板块主指标降级（PB 分位积累中）须并入判定 note，否则渲染层静默丢失（audit 输出内容）"""
    judgements = [
        {"board": "煤炭", "verdict": "贵", "dominant": "估值", "confidence": "中",
         "action": "none", "note": "资金维数据积累中（冷启动）；推断"},
    ]
    snapshots = [
        {"board": "煤炭", "source": "pe", "main_pct": 88.0, "pe_pct": 88.0, "pb_pct": None,
         "trend": "flat", "note": "主指标=PE 分位（PB 分位积累中，冷启动降级）"},
    ]
    out = _with_metric_disclosure(judgements, snapshots)
    assert "主指标=PE 分位" in out[0]["note"]
    assert "冷启动降级" in out[0]["note"]
    # 非降级快照不追加
    snaps2 = [{"board": "煤炭", "source": "pe", "note": ""}]
    out2 = _with_metric_disclosure(judgements, snaps2)
    assert out2[0]["note"] == judgements[0]["note"]
