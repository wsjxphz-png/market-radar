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
    assert "高置信度" in block and "定投" in block
    assert "推断" in block
    # 口径标注
    assert "PE分位 15%" in block
    assert "PB=0.42" in block

def test_block_empty():
    assert format_valuation_block([], []) == ""


def test_block_all_fail_no_crash():
    """估值源全败（main_pct=None, source=price）不得崩溃：F2 降级不崩溃，渲染 note 而非格式错误"""
    judgements = [
        {"board": "煤炭", "verdict": "观察", "dominant": "数据不足", "confidence": "低",
         "action": "skip", "note": "估值数据不可用（推断）"},
    ]
    snapshots = [
        {"board": "煤炭", "source": "price", "main_pct": None, "pe_pct": None,
         "pb_pct": None, "trend": "flat", "note": "估值数据源全部不可用"},
    ]
    block = format_valuation_block(judgements, snapshots)
    assert "估值数据源全部不可用" in block
    assert "0%" not in block  # 不把 None 渲染成价格位置 0%


def test_block_pe_pb_percentile_label():
    """pe 源下 pb_pct 是分位（0-100），渲染必须带 % 防误读为 PB 比值"""
    judgements = [
        {"board": "半导体", "verdict": "便宜", "dominant": "估值+资金", "confidence": "高",
         "action": "full", "note": "推断"},
    ]
    snapshots = [
        {"board": "半导体", "source": "pe", "main_pct": 15.0, "pb_pct": 20.0, "pe_pct": 15.0,
         "trend": "down", "note": ""},
    ]
    block = format_valuation_block(judgements, snapshots)
    assert "| PB 20%" in block
