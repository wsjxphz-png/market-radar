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
