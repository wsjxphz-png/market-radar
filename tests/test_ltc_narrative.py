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

def test_template_interpretation_contains_facts():
    # 结构与 build_facts 输出一致（flattened accum_period，与 brief smoke test 相同契约）
    facts = {"data_date": "2026-08-04",
             "focus": [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": 173.9, "chg_pct": -2.69,
                        "accum_period": "偏长期布局特征", "accum_reasons": ["有回购/季报实名背书"]}],
             "southbound": {"value": 25.7, "ref_label": "参照积累中"}}
    text = template_interpretation(facts)
    assert "银行" in text
    assert len(text) <= 200

def test_validate_output_blocks_banned():
    assert validate_output("今天资金流入半导体") is True
    assert validate_output("你可以考虑买入半导体") is False
    assert validate_output("建议加仓银行") is False
