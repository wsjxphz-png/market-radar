# tests/test_ltc_config.py
import sys, os
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_config import GLOSSARY, BANNED_PHRASES, PHASE_LABEL, is_expired, QUARTERLY_CONTEXT_FALLBACK, bj_now

def test_glossary_covers_key_terms():
    for term in ["超大单", "大单", "主力", "净额", "回购", "南向", "估值分位"]:
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
    # 动态日期（基于北京时间推算），固定日期会随时间变成时间炸弹
    today = bj_now().strftime("%Y-%m-%d")
    past = (bj_now() - timedelta(days=35)).strftime("%Y-%m-%d")   # 35 天前 → 过期
    future = (bj_now() + timedelta(days=10)).strftime("%Y-%m-%d") # 未来日期 → 不过期
    assert not is_expired(today)          # 今天不算过期
    assert is_expired(past)               # 35 天前过期
    assert not is_expired(future)         # 未来日期不过期
    assert is_expired(None)               # None 兜底为过期（不抛 TypeError）

def test_quarterly_has_required_keys():
    for k in ["updated", "next_update", "key_facts"]:
        assert k in QUARTERLY_CONTEXT_FALLBACK
