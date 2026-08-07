# tests/test_val_explain.py
"""说明层规则化生成测试：事实翻译 / 名词解释 / 场景裁决器（趋势交易论 + 微笑曲线）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_explain import (explain_percentile, explain_fund_state, explain_trend,
                         glossary_for, judge_short_term, judge_dca, synthesize,
                         GLOSSARY)


# ══════════ 分位翻译 ══════════

def test_explain_percentile_with_window():
    # "PE 分位 15% = 过去 10 年只有 15% 的时间比现在便宜"
    out = explain_percentile(15.0, "PE", years=10)
    assert "PE 分位 15%" in out
    assert "过去 10 年" in out
    assert "只有 15% 的时间比现在便宜" in out

def test_explain_percentile_rounds_display():
    # 分位 15.4 显示为 15%，0.5 年窗口如实保留一位
    out = explain_percentile(15.4, "PE", years=0.5)
    assert "PE 分位 15%" in out
    assert "过去 0.5 年" in out

def test_explain_percentile_no_window_no_fabrication():
    # 无窗口参数时不得虚构"过去十年"（数据窗口诚实性）——只说"历史区间"
    out = explain_percentile(15.0, "PE")
    assert "十年" not in out
    assert "历史区间" in out

def test_explain_percentile_none_returns_data_missing():
    assert explain_percentile(None, "PE") == "估值分位数据不可用"

def test_explain_percentile_pb_metric():
    # 周期板块主指标 PB：同样句式
    out = explain_percentile(30.0, "PB", years=8.0)
    assert "PB 分位 30%" in out
    assert "过去 8 年" in out


# ══════════ 资金状态翻译 ══════════

def test_explain_fund_state_inflow_confirm():
    # "连续 3 日净流入 = 有持续性的买入，不是一天的热闹"
    out = explain_fund_state("inflow_confirm", 12.5)
    assert "连续 3 日" in out
    assert "净流入" in out
    assert "有持续性的买入" in out
    assert "12.5" in out  # 当日净额带数字，可核验

def test_explain_fund_state_outflow_confirm():
    out = explain_fund_state("outflow_confirm", -8.0)
    assert "连续 3 日" in out
    assert "净流出" in out
    assert "8.0" in out

def test_explain_fund_state_single_day():
    # 单日 = 方向未确认，不夸大为持续行为
    out = explain_fund_state("single_day", 3.0)
    assert "单日" in out
    assert "未确认" in out or "热" in out

def test_explain_fund_state_cold_start():
    # 冷启动 = 留痕不足，诚实标注积累中
    out = explain_fund_state("cold_start", None)
    assert "不足" in out or "积累" in out

def test_explain_fund_state_unknown_no_sl_net():
    # unknown / sl_net 缺失 → 不虚构数字
    out = explain_fund_state("unknown", None)
    assert "不可用" in out
    assert "12.5" not in out


# ══════════ 趋势翻译 ══════════

def test_explain_trend_up():
    out = explain_trend("above20_rising")
    assert "20 日线上方上升期" in out
    assert "短期趋势向上" in out

def test_explain_trend_down():
    out = explain_trend("below20_falling")
    assert "20 日线下方下降期" in out
    assert "短期趋势向下" in out

def test_explain_trend_oscillation():
    out = explain_trend("around20_oscillation")
    assert "震荡" in out and "方向不明" in out

def test_explain_trend_unknown():
    out = explain_trend("unknown")
    assert "不足" in out or "不可用" in out


# ══════════ 定投决策表（2026-08-07 重构：便宜度×趋势→买入条件）══════════

def test_judge_dca_cheap_launched_buy():
    # 便宜+趋势启动 → 多买（符合【便宜+趋势】买入条件，中长期持有）
    out = judge_dca("便宜", "above20_rising", "above60")
    assert "多买" in out
    assert "便宜+趋势启动" in out
    assert "中长期持有" in out

def test_judge_dca_cheap_emerging_wait():
    # 便宜+趋势初现 → 观察（等 60 日线确认）
    out = judge_dca("便宜", "above20_rising", "below60")
    assert "观察" in out
    assert "60日线" in out

def test_judge_dca_cheap_not_started_wait():
    # 便宜+趋势未启动 → 观察（便宜但没人买，等趋势）
    out = judge_dca("便宜", "below20_falling", "below60")
    assert "观察" in out
    assert "趋势未启动" in out

def test_judge_dca_fair_normal():
    out = judge_dca("合理", "around20_oscillation", None)
    assert "按计划" in out

def test_judge_dca_expensive_launched_reduce():
    # 贵+趋势启动 → 减量不追
    out = judge_dca("贵", "above20_rising", "above60")
    assert "减量" in out
    assert "不追" in out

def test_judge_dca_expensive_pause():
    out = judge_dca("贵", "below20_falling", "below60")
    assert "暂停/减量" in out

def test_judge_dca_watch_cannot_judge():
    # 观察（估值证据不足）→ 无法判断，等待证据
    out = judge_dca("观察")
    assert "无法判断" in out and "等待证据" in out


# ══════════ synthesize 结构（2026-08-07 重构：去 short_term_judge/conflict_note）══════════

def test_synthesize_three_layers_structure():
    facts = {"board": "银行", "trend_state": "above20_rising", "trend_mid": "above60",
             "valuation": "合理", "fund_state": "inflow_confirm",
             "sl_net": 12.5, "main_pct": 50.0, "metric": "PE",
             "years": 10, "terms": ["PE", "分位", "净流入"]}
    out = synthesize(facts)
    assert set(out) == {"board", "facts", "explanation", "dca_judge", "trend_ok", "cheap"}
    assert "银行" in out["facts"]
    assert "PE 分位 50%" in out["facts"]
    assert "12.5" in out["facts"]
    assert "市盈率" in out["explanation"]            # PE 大白话
    assert "百分位" in out["explanation"]            # 分位大白话
    assert "持续性的买入" in out["explanation"]       # 净流入翻译
    assert "正常定投" in out["dca_judge"]            # 合理+趋势启动
    assert out["trend_ok"] is True and out["cheap"] is False

def test_synthesize_cheap_launched_buy_condition():
    # 便宜+趋势启动 → 【便宜+趋势】买入条件成立
    out = synthesize({"board": "医药生物", "trend_state": "above20_rising",
                      "trend_mid": "above60", "valuation": "便宜",
                      "fund_state": "inflow_confirm", "sl_net": 10.0,
                      "main_pct": 15.0, "metric": "PE", "years": 10, "terms": []})
    assert out["cheap"] is True and out["trend_ok"] is True
    assert "多买" in out["dca_judge"]

def test_synthesize_cheap_not_started_wait():
    # 便宜+趋势未启动 → 买入条件不成立（便宜≠马上买，等趋势）
    out = synthesize({"board": "煤炭", "trend_state": "below20_falling",
                      "trend_mid": "below60", "valuation": "便宜",
                      "fund_state": "cold_start", "sl_net": None,
                      "main_pct": 20.0, "metric": "PE", "years": 10, "terms": []})
    assert out["cheap"] is True and out["trend_ok"] is False
    assert "观察" in out["dca_judge"]
    assert "板块：煤炭" in out["facts"]
    assert "20%" in out["facts"]

def test_synthesize_unknown_valuation_cannot_judge():
    # 趋势+估值均无证据 → 无法判断
    out = synthesize({"board": "汽车", "trend_state": "unknown", "trend_mid": None,
                      "valuation": "观察", "fund_state": "unknown",
                      "sl_net": None, "main_pct": None, "metric": "PE",
                      "years": None, "terms": []})
    assert "无法判断" in out["dca_judge"]
    assert out["cheap"] is False and out["trend_ok"] is False

def test_synthesize_missing_key_logs_warning(caplog):
    # 契约违反（key 缺失）→ logger.warning 告警 + 降级不崩溃（Task 4 Issue 3 加固：
    # 渲染层组装错位会在卡片上静默降级而非报错，日志必须显式暴露缺 key）
    # synthesize 输出契约是三层结构（board/facts/explanation/两判断/conflict_note），
    # 输入缺 key 的降级体现在 facts 文本与判断层里
    import logging
    with caplog.at_level(logging.WARNING, logger="val_explain"):
        out = synthesize({"board": "银行", "trend_state": "above20_rising"})
    assert "数据不可用" in out["facts"]                            # fund_state 降级 unknown
    assert "无法判断：估值证据不足" in out["dca_judge"]             # valuation 降级 观察
    assert "PE" in out["facts"]                                     # metric 降级 PE（收紧：只断 PE）
    msgs = [r.message for r in caplog.records]
    assert any("fund_state" in m for m in msgs)
    assert any("valuation" in m for m in msgs)
    assert any("main_pct" in m for m in msgs)
    # 合法缺省（sl_net=None 属数据不可用，非契约违反）不告警
    assert not any("sl_net" in m for m in msgs)


# ══════════ 名词解释（GLOSSARY 扩展）══════════

def test_glossary_covers_brief_terms():
    # brief 要求全部名词都有大白话解释
    for term in ["PE", "PB", "分位", "净流入", "20日线", "微笑曲线",
                 "520战法", "趋势第一性", "永不下跌补仓"]:
        assert term in GLOSSARY, f"GLOSSARY 缺 {term}"

def test_glossary_for_plain_language_not_tautology():
    # 解释必须是大白话，不能只是换个写法再说一遍（PE → 市盈率 + 含义）
    out = glossary_for(["PE", "PB", "分位", "微笑曲线"])
    assert "市盈率" in out
    assert "市净率" in out
    assert "百分位" in out
    assert "跌" in out and "买" in out   # 微笑曲线：跌时多买

def test_glossary_for_framework_terms():
    # 趋势交易论框架名词
    out = glossary_for(["趋势第一性", "永不下跌补仓", "520战法", "20日线"])
    assert "顺势" in out
    assert "补仓" in out
    assert "金叉" in out and "死叉" in out
    assert "均线" in out

def test_glossary_for_dedup_and_unknown_skipped():
    # 重复词去重；未知词跳过不虚构、不崩溃
    out = glossary_for(["PE", "PE", "不存在的词", "PB"])
    assert out.count("市盈率") == 1
    assert "不存在的词" not in out
    assert "市净率" in out

def test_glossary_for_empty():
    assert glossary_for([]) == ""


# ═══════════════════════════════════════════════════════════
# 维度分歧标准化（2026-08-07 用户风险点2）
# ═══════════════════════════════════════════════════════════

def test_dimension_signals_mapping():
    from val_explain import dimension_signals
    assert dimension_signals("above20_rising", "贵", "inflow_confirm") == \
        {"short": "多", "dca": "空", "fund": "多"}
    assert dimension_signals("below20_falling", "便宜", "outflow_confirm") == \
        {"short": "空", "dca": "多", "fund": "空"}
    assert dimension_signals("around20_oscillation", "合理", "single_day") == \
        {"short": "中性", "dca": "中性", "fund": "中性"}
    assert dimension_signals("unknown", "观察", "unknown") == \
        {"short": "中性", "dca": "中性", "fund": "中性"}


def test_dimension_conflict_emits_standard_template():
    from val_explain import format_dimension_conflict, fund_state_short
    sig = {"short": "空", "dca": "多", "fund": "中性"}
    out = format_dimension_conflict("煤炭", "短线（推断）：不抄底", "定投（推断）：可加码",
                                    fund_state_short("outflow_confirm", -3.2), sig)
    assert "维度分歧（煤炭）" in out
    assert "【短线维度】" in out and "【定投维度】" in out
    assert "【资金维度】" not in out          # 中性维度不列出（L2）
    assert "视角不同" in out and "不是系统冲突" in out  # 固定话术
    # 资金维度文本为精简格式（L1: 不与事实行全文重复）
    # 2026-08-07 用户通读：方向词按符号 + 数值标注"当日"
    assert fund_state_short("outflow_confirm", -3.2) == "连续净流出（当日 -3.2亿）"
    assert fund_state_short("single_day", -26.1) == "单日净流出（当日 -26.1亿）"
    assert fund_state_short("single_day", 5.3) == "单日净流入（当日 +5.3亿）"
    assert fund_state_short("cold_start", None) == "数据不足"


def test_dimension_conflict_silent_when_aligned_or_neutral():
    from val_explain import format_dimension_conflict
    # 三维同向 → 不输出
    assert format_dimension_conflict("银行", "顺势持有", "按计划",
                                     "", {"short": "多", "dca": "中性", "fund": "多"}) == ""
    # 只有一个方向明确（其余中性）→ 不输出（len(clear)<2）
    assert format_dimension_conflict("通信", "无法判断", "无法判断",
                                     "", {"short": "中性", "dca": "中性", "fund": "多"}) == ""
