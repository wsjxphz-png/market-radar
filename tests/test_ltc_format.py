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
