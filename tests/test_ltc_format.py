# tests/test_ltc_format.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ltc_config
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
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "2026-08-04" in card

def test_card_no_northbound():
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "北向" not in card

def test_card_sections_separated():
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "今日数据" in card and "长期数据" in card
    assert "今日数据" in card.split("长期数据")[0]

def test_card_glossary_and_phase():
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "净额" in card and "流入-流出" in card              # 术语解释（净额口径，非超大单拆分）
    assert "董事会预案" in card                                  # 回购阶段

def test_card_valuation_partial_failure_note():
    """FR-1.4 复盘修复：部分板块估值源失败不得静默消失，须明确标注"""
    focus, southbound, _, repurchase, refs = _args()
    valuation = [{"board": "银行", "ok": True, "position_pct": 23.5, "price_vs_ma60_pct": 1.2},
                 {"board": "医药生物", "ok": False}, {"board": "家电", "ok": False}]
    card = format_card("2026-08-04", "解读文本", focus, southbound, valuation, repurchase, refs)
    assert "银行" in card
    assert "另有 2 个板块当日估值源不可用" in card
    card_all_fail = format_card("2026-08-04", "解读文本", *_args())
    assert "暂无数据" not in card_all_fail  # 全成功时不出现失败提示

def test_card_expired_warning(monkeypatch):
    """复审 Minor C：过期警告只由 build_quarterly_block 单通道输出，与 format_card 参数无关"""
    monkeypatch.setattr(ltc_config, "is_expired", lambda _: True)
    qb, expired = build_quarterly_block()
    assert expired is True and "过期" in qb
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "过期" in card  # 季度背景过期时警告出现在卡里

def test_card_no_expired_warning_when_fresh(monkeypatch):
    monkeypatch.setattr(ltc_config, "is_expired", lambda _: False)
    card = format_card("2026-08-04", "解读文本", *_args())
    assert "过期" not in card

def test_card_no_operation_words():
    card = format_card("2026-08-04", "解读文本", *_args())
    for w in ["建议买入", "你要", "可以买入"]:
        assert w not in card
