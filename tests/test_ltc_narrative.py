# tests/test_ltc_narrative.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_narrative import build_facts, template_interpretation, validate_output

def test_build_facts_only_verified_numbers():
    data = {"data_date": "2026-08-04", "southbound": {"southbound_net_yi": 25.7},
            "repurchase": {"items": [{"name": "宁德时代", "amount_yi": 400.0, "phase": "董事会预案"}]}}
    focus = [{"industry": "半导体", "tag": "资金关注", "sl_net": 1218.1, "chg_pct": 6.01, "sl_percentile": 100.0,
              "accum": {"period": "短期行为特征", "reasons": []}}]
    f = build_facts(data, focus, {"southbound_label": "比平时多"})
    assert f["southbound"]["value"] == 25.7
    assert f["southbound"]["ref_label"] == "比平时多"
    assert f["focus"][0]["industry"] == "半导体"
    assert f["focus"][0]["tag"] == "资金关注"

def test_build_facts_includes_valuation_material():
    """复审 Minor A：估值温度素材（今日×长期搭桥核心）进 facts，AI 可引用价格分位"""
    data = {"data_date": "2026-08-04",
            "southbound": {"southbound_net_yi": 25.7},
            "repurchase": {"items": []},
            "valuation": [{"board": "医药生物", "position_pct": 26.0},
                          {"board": "银行", "position_pct": 23.5}]}
    f = build_facts(data, [], {})
    assert f["valuation"] == [{"board": "医药生物", "position_pct": 26.0},
                              {"board": "银行", "position_pct": 23.5}]
    assert f["valuation_note"] == ""  # 旧键保留兼容

def test_template_interpretation_contains_facts():
    # 结构与 build_facts 输出一致（flattened accum_period，与 brief smoke test 相同契约）
    facts = {"data_date": "2026-08-04",
             "focus": [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": 173.9, "chg_pct": -2.69,
                        "accum_period": "偏长期布局特征", "accum_reasons": ["有回购/季报实名背书"]}],
             "southbound": {"value": 25.7, "ref_label": "参照积累中"}}
    text = template_interpretation(facts)
    assert "银行" in text
    assert "净额+173.9亿" in text           # 净额口径（原"超大单+173.9亿"误标拆分口径，已修正）
    assert "超大单" not in text
    assert len(text) <= 200

def test_template_no_dazijin_attribution():
    """复审 I2：叙事不得归因"大资金"——数据只有板块资金净额，措辞只能是资金净流入/流出"""
    cases = [("逆势吸筹嫌疑", "价格在跌但资金净流入"),
             ("派发嫌疑", "价格在涨但资金净流出"),
             ("资金关注", "资金净流入集中"),
             ("资金撤离", "资金净流出")]
    for tag, expected in cases:
        facts = {"data_date": "2026-08-05",
                 "focus": [{"industry": "银行", "tag": tag, "sl_net": 1.0,
                            "chg_pct": 0.0, "accum_period": "短期行为特征"}],
                 "southbound": {"value": 25.7, "ref_label": "参照积累中"}}
        text = template_interpretation(facts)
        assert expected in text
        assert "大资金" not in text

def test_validate_output_blocks_banned():
    assert validate_output("今天资金流入半导体") is True
    assert validate_output("你可以考虑买入半导体") is False
    assert validate_output("建议加仓银行") is False
