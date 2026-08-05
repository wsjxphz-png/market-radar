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
    # "20 日线上方上升期 = 短线处于顺势"
    out = explain_trend("above20_rising")
    assert "20 日线上方上升期" in out
    assert "顺势" in out

def test_explain_trend_down():
    out = explain_trend("below20_falling")
    assert "20 日线下方下降期" in out
    assert "逆势" in out

def test_explain_trend_oscillation():
    out = explain_trend("around20_oscillation")
    assert "震荡" in out
    assert "方向不明" in out

def test_explain_trend_unknown():
    out = explain_trend("unknown")
    assert "不足" in out or "不可用" in out


# ══════════ 短线判断三态（趋势交易论）══════════

def test_judge_short_term_uptrend_hold():
    # 趋势上升 → 顺势持有不破 20 日线不动
    out = judge_short_term("above20_rising", "合理")
    assert "持有" in out
    assert "20 日线" in out
    assert "推断" in out

def test_judge_short_term_downtrend_no_bottom_fishing():
    # 趋势下降 → 不抄底（永不下跌补仓）等反转
    out = judge_short_term("below20_falling", "合理")
    assert "不抄底" in out
    assert "永不下跌补仓" in out
    assert "反转" in out
    assert "推断" in out

def test_judge_short_term_oscillation_wait():
    # 震荡 → 等待明确，不强行给方向
    out = judge_short_term("around20_oscillation", "合理")
    assert "等待" in out
    assert "推断" in out

def test_judge_short_term_unknown_cannot_judge():
    # 无法裁决 → "无法判断：…等待证据"，绝不强行凑结论
    out = judge_short_term("unknown", "便宜")
    assert "无法判断" in out
    assert "等待证据" in out
    assert "推断" in out

def test_judge_short_term_cheap_downtrend_no_contradiction():
    # 趋势下降 + 估值便宜：不抄底的立场不被估值动摇（估值便宜不等于见底）
    out = judge_short_term("below20_falling", "便宜")
    assert "不抄底" in out
    assert "便宜" in out          # 明确点名冲突中的估值，说明层解释
    assert "加码" not in out      # 短线场景绝不给出定投口径的结论


# ══════════ 定投判断三态（微笑曲线）══════════

def test_judge_dca_cheap_add():
    # 便宜 → 可加码（跌时多买）
    out = judge_dca("便宜")
    assert "加码" in out
    assert "微笑曲线" in out
    assert "推断" in out

def test_judge_dca_fair_normal():
    out = judge_dca("合理")
    assert "按计划" in out
    assert "推断" in out

def test_judge_dca_expensive_reduce():
    out = judge_dca("贵")
    assert "减量" in out
    assert "推断" in out

def test_judge_dca_watch_cannot_judge():
    # 观察（PB 否决/数据不足）→ 无法判断，等待证据
    out = judge_dca("观察")
    assert "无法判断" in out
    assert "等待证据" in out


# ══════════ 冲突分流：场景不互相覆盖 ══════════

def test_scene_split_trend_down_valuation_cheap():
    # 趋势下降 + 估值便宜：短线"不抄底"、定投"可加码"，两套结论并存
    short = judge_short_term("below20_falling", "便宜")
    dca = judge_dca("便宜")
    assert "不抄底" in short
    assert "加码" in dca
    assert "不抄底" not in dca    # 定投结论不带短线口径
    assert "加码" not in short    # 短线结论不带定投口径
    syn = synthesize({"board": "银行", "trend_state": "below20_falling",
                      "valuation": "便宜", "fund_state": "inflow_confirm",
                      "sl_net": 10.0, "main_pct": 15.0, "metric": "PE",
                      "years": 10, "terms": ["PE", "分位"]})
    assert "不抄底" in syn["short_term_judge"]
    assert "加码" in syn["dca_judge"]
    assert "场景分流" in syn["conflict_note"]
    assert "无法" in syn["conflict_note"]   # 无法统一裁决：两场景方向相反
    assert "等待" in syn["conflict_note"]

def test_scene_split_trend_up_valuation_expensive():
    # 趋势上升 + 估值贵：短线顺势持有 vs 定投减量，分流不覆盖
    short = judge_short_term("above20_rising", "贵")
    dca = judge_dca("贵")
    assert "持有" in short
    assert "减量" in dca
    syn = synthesize({"board": "半导体", "trend_state": "above20_rising",
                      "valuation": "贵", "fund_state": "outflow_confirm",
                      "sl_net": -5.0, "main_pct": 85.0, "metric": "PE",
                      "years": 10, "terms": []})
    assert "场景分流" in syn["conflict_note"]
    assert "不互相覆盖" in syn["conflict_note"]


# ══════════ synthesize 三层组装 ══════════

def test_synthesize_three_layers_structure():
    facts = {"board": "银行", "trend_state": "above20_rising",
             "valuation": "合理", "fund_state": "inflow_confirm",
             "sl_net": 12.5, "main_pct": 50.0, "metric": "PE",
             "years": 10, "terms": ["PE", "分位", "净流入"]}
    out = synthesize(facts)
    assert set(out) == {"board", "facts", "explanation",
                        "short_term_judge", "dca_judge", "conflict_note"}
    # 事实层：机器可核验数据
    assert "银行" in out["facts"]
    assert "PE 分位 50%" in out["facts"]
    assert "12.5" in out["facts"]
    # 说明层：翻译 + 名词解释
    assert "只有 50% 的时间比现在便宜" in out["explanation"]
    assert "市盈率" in out["explanation"]            # PE 大白话
    assert "百分位" in out["explanation"]            # 分位大白话
    assert "持续性的买入" in out["explanation"]       # 净流入翻译
    # 判断层：双场景 + 推断标记
    assert "推断" in out["short_term_judge"]
    assert "推断" in out["dca_judge"]
    assert "持有" in out["short_term_judge"]

def test_synthesize_judge_absent_when_trend_unknown():
    # 趋势数据不足 → 短线判断缺席（"无法判断"），其余层照常
    out = synthesize({"board": "煤炭", "trend_state": "unknown",
                      "valuation": "便宜", "fund_state": "cold_start",
                      "sl_net": None, "main_pct": 20.0, "metric": "PE",
                      "years": 10, "terms": []})
    assert "无法判断" in out["short_term_judge"]
    assert "等待证据" in out["short_term_judge"]
    assert "加码" in out["dca_judge"]                # 定投场景独立裁决，不受影响
    assert "板块：煤炭" in out["facts"]               # 事实层数据原样在场
    assert "20%" in out["facts"]

def test_synthesize_both_insufficient_conflict_note():
    # 趋势+估值均无证据 → conflict_note 无法判断
    out = synthesize({"board": "汽车", "trend_state": "unknown",
                      "valuation": "观察", "fund_state": "unknown",
                      "sl_net": None, "main_pct": None, "metric": "PE",
                      "years": None, "terms": []})
    assert "无法判断" in out["short_term_judge"]
    assert "无法判断" in out["dca_judge"]
    assert "无法判断" in out["conflict_note"]
    assert "等待证据" in out["conflict_note"]

def test_synthesize_no_conflict_when_directions_agree():
    # 趋势上升 + 估值合理：无冲突 → conflict_note 为空
    out = synthesize({"board": "半导体", "trend_state": "above20_rising",
                      "valuation": "合理", "fund_state": "inflow_confirm",
                      "sl_net": 8.0, "main_pct": 50.0, "metric": "PE",
                      "years": 10, "terms": []})
    assert out["conflict_note"] == ""

def test_synthesize_price_position_metric():
    # 兜底口径（价格位置）：说明层不误用"时间占比"句式；
    # 窗口诚实性：价格位置来自 K 线全程（fetch_board_kline days=1300 约 5 年）min/max，
    # 无"近一年"截断——输出不得虚构窗口（"近一年"），只能如实说"历史区间"
    out = synthesize({"board": "煤炭", "trend_state": "around20_oscillation",
                      "valuation": "合理", "fund_state": "unknown",
                      "sl_net": None, "main_pct": 30.0, "metric": "价格位置",
                      "years": None, "terms": []})
    assert "价格位置 30%" in out["facts"]
    assert "只有 30% 的时间" not in out["explanation"]  # 位置≠时间占比，不得虚构
    assert "近一年" not in out["explanation"]           # 不得虚构窗口（回归锁）
    assert "历史区间" in out["explanation"]
    assert "位置" in out["explanation"]


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
    assert "PE" in out["facts"] or "趋势" in out["facts"]           # metric 降级 PE
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
