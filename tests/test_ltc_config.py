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
