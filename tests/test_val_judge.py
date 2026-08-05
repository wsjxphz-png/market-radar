# tests/test_val_judge.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_judge import judge_valuation

def test_growth_pe_low_inflow_high_confidence():
    # 成长股（主指标PE）：PE 分位 15%（便宜）+ 资金流入确认 → 高置信度，全额
    out = judge_valuation("半导体", 15.0, "down", 20.0, "inflow_confirm", None)
    assert out["verdict"] == "便宜"
    assert out["confidence"] == "高"
    assert out["action"] == "full"
    assert out["dominant"] == "估值+资金"
    assert "推断" in out["note"]

def test_cyclical_pb_low_inflow_high_confidence():
    # 周期股（主指标PB）：用 PB 分位判断
    out = judge_valuation("银行", 15.0, "flat", 12.0, "inflow_confirm", 0.6)
    assert out["verdict"] == "便宜"
    assert out["confidence"] == "高"

def test_pb_veto_degrades_not_upgrades():
    # PB 否决：主指标便宜（PE 15%）但 PB 分位高（60%>40%）→ 降级到观察
    out = judge_valuation("半导体", 15.0, "down", 60.0, "inflow_confirm", None)
    assert out["confidence"] == "中"
    assert out["verdict"] in ("便宜", "观察")
    # PB 便宜不捧场：主指标合理（PE 50%）+ PB 便宜（10%）+ 资金确认 → 不能升到高
    out2 = judge_valuation("半导体", 50.0, "flat", 10.0, "inflow_confirm", None)
    assert out2["confidence"] != "高"

def test_earnings_trend_modifier_only_for_growth():
    # 修正1 仅主指标=PE（成长股）启用：PE 便宜 + 趋势降 → 倾向错杀
    out = judge_valuation("半导体", 15.0, "down", None, "cold_start", None)
    assert "错杀" in out["note"]
    # 周期股（主指标PB）不套用修正1
    out2 = judge_valuation("银行", 15.0, "down", 10.0, "cold_start", None)
    assert "错杀" not in out2["note"]

def test_single_dim_medium_confidence():
    # 单维（估值便宜但资金冷启动）→ 中置信度
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "cold_start", None)
    assert out["confidence"] == "中"

def test_contradiction_low_confidence():
    # 矛盾：估值便宜 + 资金流出确认 → 低置信度，观察
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "outflow_confirm", None)
    assert out["confidence"] == "低"
    assert out["action"] == "skip"

def test_expensive_no_add():
    # 贵 → 不追加
    out = judge_valuation("半导体", 85.0, "up", 90.0, "inflow_confirm", None)
    assert out["verdict"] in ("贵", "需警惕")
    assert out["action"] == "none"

def test_main_pct_none_watch():
    # 数据全缺 → 观察
    out = judge_valuation("半导体", None, "flat", None, "unknown", None)
    assert out["verdict"] == "观察"

def test_growth_cheap_trend_up_trap_note():
    # 修正1 趋势升 → 倾向陷阱（推断标记）
    out = judge_valuation("半导体", 15.0, "up", 20.0, "cold_start", None)
    assert out["verdict"] == "便宜"
    assert "陷阱" in out["note"]
    assert "推断" in out["note"]

def test_single_day_medium_half():
    # 资金单日（0.5 维）：便宜 + 单日 → 中置信度 → 半额
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "single_day", None)
    assert out["verdict"] == "便宜"
    assert out["confidence"] == "中"
    assert out["action"] == "half"

def test_fund_unknown_cheap_medium_half():
    # fund_state=unknown → 资金维 0：便宜 + 未知 → 中置信度 → 半额
    out = judge_valuation("半导体", 15.0, "flat", 20.0, "unknown", None)
    assert out["confidence"] == "中"
    assert out["action"] == "half"
    assert "资金维数据不可用" in out["note"]

def test_pb_veto_note_overrides_modifier():
    # PB 否决生效后，修正1 的"错杀"推断不再输出（note 与 verdict 不矛盾）
    out = judge_valuation("半导体", 15.0, "down", 60.0, "inflow_confirm", None)
    assert out["verdict"] == "观察"
    assert out["action"] == "skip"
    assert "否决" in out["note"]
    assert "错杀" not in out["note"]

def test_reasonable_medium_skip():
    # 合理 + 资金确认 → 中置信度 → skip（仅高置信度才 half）
    out = judge_valuation("半导体", 50.0, "flat", 10.0, "inflow_confirm", None)
    assert out["verdict"] == "合理"
    assert out["confidence"] == "中"
    assert out["action"] == "skip"
