# -*- coding: utf-8 -*-
"""合并卡片纯函数测试 — build_merged_card 六区块顺序/无北向 + compute_fund_state 资金维确认。
原则：注入假数据，不触网。"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from market_dashboard import build_merged_card, compute_fund_state, _with_metric_disclosure


def test_merged_card_sections_order():
    data = {
        "valuation_judgements": [
            {"board": "银行", "verdict": "便宜", "dominant": "估值+资金", "confidence": "高",
             "action": "full", "note": "推断"}],
        "valuation_snapshots": [
            {"board": "银行", "source": "pb", "main_pct": None, "pb_pct": 0.42,
             "pe_pct": None, "trend": "flat", "note": "PB=0.42（成分中位数）"}],
        "market_overview": "上证 +0.33%",
        "sector_ops": "半导体 hold",
        "fund_section": "今日资金：银行 逆势吸筹嫌疑",
        "interpretation": "今日解读文本",
        "honest": "诚实声明",
    }
    card = build_merged_card(data)
    # 注：brief 原测试用 "market_overview"/"fund_section"/"interpretation" 作 find 目标，
    # 但实现按 brief 逐字渲染中文标题（大盘概况/今日资金）与数据值（今日解读文本），
    # 字面 key 永不出现 → 按 Task 3/4 先例改为真实渲染标记，断言意图不变（区块顺序）
    order = [card.find(s) for s in ["估值判断", "大盘概况", "今日资金", "今日解读文本", "诚实声明"]]
    assert all(o >= 0 for o in order)
    assert order == sorted(order)  # 区块顺序：估值→概况→资金→解读→声明


def test_merged_card_has_no_northbound():
    data = {"valuation_judgements": [], "valuation_snapshots": [],
            "market_overview": "x", "sector_ops": "y", "fund_section": "z",
            "interpretation": "w", "honest": "h"}
    card = build_merged_card(data)
    assert "北向" not in card


def test_merged_card_skips_empty_blocks():
    """缺块不得输出占位标题（F2：数据不可用不得静默缺块，也不得编造）"""
    card = build_merged_card({})
    assert card == ""


def test_compute_fund_state_inflow_confirm():
    """连续 3 日主力净流入 → inflow_confirm"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": 2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": 1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "inflow_confirm"


def test_compute_fund_state_outflow_confirm():
    """连续 3 日主力净流出 → outflow_confirm"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": -2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": -1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": -0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "outflow_confirm"


def test_compute_fund_state_mixed_single_day():
    """方向混合 → single_day（确认需连续同向）"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": 2.1}}},
        {"tags": {"银行": {"tag": "", "sl_net": -1.5}}},
        {"tags": {"银行": {"tag": "", "sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_empty_history_unknown():
    assert compute_fund_state([], "银行") == "unknown"


def test_compute_fund_state_no_sector_cold_start():
    """有留痕但该板块从未出现 → cold_start"""
    history = [
        {"tags": {"半导体": {"tag": "", "sl_net": 1.0}}},
        {"tags": {"其他": {"tag": "", "sl_net": 2.0}}},
    ]
    assert compute_fund_state(history, "银行") == "cold_start"


def test_compute_fund_state_sl_net_none_skipped():
    """sl_net 为 None/缺失的条目不计数（不当作 0）"""
    history = [
        {"tags": {"银行": {"tag": "", "sl_net": None}}},
        {"tags": {"银行": {"tag": "", "sl_net": 1.2}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


# ── Important 1：fund 区块双轨漂移 — 抽公共 build_fund_section 到 ltc_format，两卡共用 ──

def _fund_fixture():
    focus = [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": 173.9, "chg_pct": -2.69,
              "sl_percentile": 94.0, "signal": "TOP20% 🔵",
              "accum": {"period": "偏长期布局特征",
                        "reasons": ["价格处于 1 年低位区", "有回购/季报实名背书"]}}]
    southbound = {"date": "2026-08-04", "southbound_net_yi": 25.7}
    refs = {"southbound_label": "参照积累中"}
    repurchase = {"period": "近4周",
                  "items": [{"name": "宁德时代", "amount_yi": 400.0, "phase": "董事会预案"}]}
    return focus, southbound, refs, repurchase


def test_build_fund_section_shared_render():
    """ltc_format.build_fund_section 为两卡共用渲染入口：术语表展开 + 承接 reasons + 南向 + 回购 + 桥接行"""
    from ltc_format import build_fund_section
    focus, southbound, refs, repurchase = _fund_fixture()
    section = build_fund_section(focus, southbound, repurchase, refs)
    assert "净额（板块资金流入-流出" in section                      # 术语表展开（净额口径）
    assert "承接：偏长期布局特征（推断）｜ 价格处于 1 年低位区" in section  # 承接 reasons（合并卡原先缺失）
    assert "南向（内地资金买入港股市场" in section                    # 南向术语表展开（合并卡原先缺失）
    assert "• 近4周回购：宁德时代 400.0亿" in section
    assert "对你意味着什么" in section                                # 桥接行（合并卡原先缺失）
    # 无回购参数（ltc 卡：回购在长期数据区块）→ 不渲染回购行
    no_rep = build_fund_section(focus, southbound, None, refs)
    assert "近4周回购" not in no_rep
    assert "净额（板块资金流入-流出" in no_rep


def test_merged_and_ltc_card_fund_section_same_caliber():
    """合并卡与 ltc 卡同一输入输出同口径关键行（术语表/承接/南向/桥接行逐条一致）"""
    from ltc_format import build_fund_section, format_card as ltc_format_card
    from market_dashboard import build_merged_card
    focus, southbound, refs, repurchase = _fund_fixture()
    ltc = ltc_format_card("2026-08-04", "解读", focus, southbound, [], repurchase, refs)
    merged = build_merged_card({
        "valuation_judgements": [], "valuation_snapshots": [],
        "market_overview": "x", "sector_ops": "y",
        "fund_section": build_fund_section(focus, southbound, repurchase, refs),
        "interpretation": "w", "honest": "h"})
    for key in ["净额（板块资金流入-流出",
                "承接：偏长期布局特征（推断）｜ 价格处于 1 年低位区",
                "南向（内地资金买入港股市场",
                "对你意味着什么"]:
        assert key in ltc and key in merged
    # 旧的双轨口径（合并卡裸写"（超大单 X亿，股价"）不得再现（净额口径断言见 test_ltc_narrative）
    assert "（超大单 173.9亿，股价" not in merged


def test_fund_section_emoji_dedup():
    """Minor：资金区块头与估值区块头 emoji 去重（💰 估值判断 / 📊 今日资金，飞书保守集合）"""
    card = build_merged_card({
        "valuation_judgements": [
            {"board": "银行", "verdict": "便宜", "dominant": "估值", "confidence": "高",
             "action": "full", "note": "推断"}],
        "valuation_snapshots": [
            {"board": "银行", "source": "pb", "main_pct": None, "pb_pct": 0.42,
             "pe_pct": None, "trend": "flat", "note": "PB=0.42"}],
        "fund_section": "x"})
    assert "💰 估值判断" in card
    assert "📊 今日资金" in card
    assert card.count("💰") == 1


def test_empty_fund_annotation_distinguishes_empty_vs_failure():
    """Minor：空 focus 时"数据为空"（源正常）≠"数据源故障"（源失败）；南向可用时保留并追加"""
    from market_dashboard import _annotate_empty_fund_section
    bridge = "👉 对你意味着什么：单日资金信号不等于趋势，观察是否连续多日出现。"
    with_sb = ("• 南向（内地资金买入港股市场的净额（通过港股通））：+25.7亿（参照积累中）\n"
               + bridge)
    out = _annotate_empty_fund_section(bridge, flow_ok=True)   # 源正常但空 DataFrame
    assert out == "板块资金数据为空（今日无资金归因与 AI 解读）"
    assert "故障" not in out
    out2 = _annotate_empty_fund_section(bridge, flow_ok=False)  # 源故障
    assert out2.startswith("板块资金流数据源故障")
    out3 = _annotate_empty_fund_section(with_sb, flow_ok=True)  # 南向可用 → 保留 + 追加
    assert "南向" in out3 and "板块资金数据为空" in out3
    assert "今日无资金归因与 AI 解读" in out3


# ── Important 2：compute_fund_state 嵌套异常防护（tags 非 dict / sl_net 非数值不得崩溃） ──

def test_compute_fund_state_tags_not_dict_no_crash():
    """history 嵌套异常：tags 为 list/str/None 不得崩溃，跳过该条不计入"""
    history = [
        {"tags": [{"银行": {"sl_net": 1.0}}]},
        {"tags": "corrupted"},
        {"tags": None},
        {"tags": {"银行": {"sl_net": 2.0}}},
        "not-a-dict-row",  # 非 dict 行（防御层再兜底，合并入口已过滤）
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_sl_net_not_numeric_no_crash():
    """sl_net 非数值（字符串/列表）不得崩溃：跳过该条，不当作 0"""
    history = [
        {"tags": {"银行": {"sl_net": "abc"}}},
        {"tags": {"银行": {"sl_net": [1, 2]}}},
        {"tags": {"银行": {"sl_net": 1.2}}},
        {"tags": {"银行": {"sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_entry_not_dict_no_crash():
    """板块条目非 dict（str/None）不得崩溃"""
    history = [
        {"tags": {"银行": "not-a-dict"}},
        {"tags": {"银行": None}},
        {"tags": {"银行": {"sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_all_malformed_cold_start():
    """全部条目损坏 → cold_start（有留痕但无有效数据），不崩溃"""
    history = [{"tags": "corrupted"}, {"tags": None}, {"tags": {"银行": "x"}}]
    assert compute_fund_state(history, "银行") == "cold_start"


def test_judge_boards_malformed_history_no_crash():
    """估值判定循环防护：history 嵌套异常（sector_pb 非 dict 等）不得击穿 merge_main，
    该板块降级为"数据不足"占位，不静默消失（F2）"""
    from market_dashboard import _judge_boards
    snapshots = [{"board": "银行", "source": "pb", "main_pct": 42.0, "pe_pct": None,
                  "pb_pct": 42.0, "trend": "flat", "note": "PB=0.42"}]
    history = [
        {"tags": "corrupted", "sector_pb": [1, 2, 3]},   # sector_pb 非 dict → _pb_reference 抛异常
        {"tags": None, "sector_pb": {"银行": "abc"}},     # sl_net 类非数值 → 跳过
    ]
    out = _judge_boards(snapshots, history)
    assert len(out) == 1                        # 板块不静默消失
    assert out[0]["board"] == "银行"
    assert out[0]["verdict"] == "观察" and "异常" in out[0]["note"]


def test_judge_boards_normal_judgement():
    """正常留痕路径：连续 3 日净流入 + PB 分位低 → 便宜/高置信"""
    from market_dashboard import _judge_boards
    snapshots = [{"board": "银行", "source": "pb", "main_pct": 12.0, "pe_pct": None,
                  "pb_pct": 12.0, "trend": "flat", "note": "PB=0.42"}]
    history = [
        {"tags": {"银行": {"sl_net": 2.1}}, "sector_pb": {"银行": 0.40}},
        {"tags": {"银行": {"sl_net": 1.5}}, "sector_pb": {"银行": 0.41}},
        {"tags": {"银行": {"sl_net": 0.8}}, "sector_pb": {"银行": 0.42}},
    ]
    out = _judge_boards(snapshots, history)
    assert len(out) == 1
    assert out[0]["verdict"] == "便宜" and out[0]["confidence"] == "高"


def test_metric_disclosure_pe_degradation():
    """周期板块主指标降级（PB 分位积累中）须并入判定 note，否则渲染层静默丢失（audit 输出内容）"""
    judgements = [
        {"board": "煤炭", "verdict": "贵", "dominant": "估值", "confidence": "中",
         "action": "none", "note": "资金维数据积累中（冷启动）；推断"},
    ]
    snapshots = [
        {"board": "煤炭", "source": "pe", "main_pct": 88.0, "pe_pct": 88.0, "pb_pct": None,
         "trend": "flat", "note": "主指标=PE 分位（PB 分位积累中，冷启动降级）"},
    ]
    out = _with_metric_disclosure(judgements, snapshots)
    assert "主指标=PE 分位" in out[0]["note"]
    assert "冷启动降级" in out[0]["note"]
    # 非降级快照不追加
    snaps2 = [{"board": "煤炭", "source": "pe", "note": ""}]
    out2 = _with_metric_disclosure(judgements, snaps2)
    assert out2[0]["note"] == judgements[0]["note"]


# ── C1 修复：留痕写入环 — 推送成功必须 append 当日留痕（生产唯一每日写者） ──

def test_record_merge_success_appends_history(tmp_path):
    """推送成功路径 append 留痕：结构与 ltc_main 同构 + sector_pb（原始 PB 值）"""
    from market_dashboard import _record_merge_success
    hist = tmp_path / "history.jsonl"
    focus = [{"industry": "银行", "tag": "逆势吸筹嫌疑", "sl_net": -33.02,
              "accum": {"period": "偏长期布局特征", "reasons": []}}]
    snapshots = [{"board": "银行", "pb": 0.42}, {"board": "煤炭", "pb": None}]
    assert _record_merge_success("2026-08-05", 25.7, focus, snapshots, str(hist))
    rows = [json.loads(line) for line in open(hist, encoding="utf-8")]
    assert len(rows) == 1
    e = rows[0]
    assert e["data_date"] == "2026-08-05"
    assert e["southbound_net_yi"] == 25.7
    assert e["tags"]["银行"] == {"tag": "逆势吸筹嫌疑", "accum": "偏长期布局特征", "sl_net": -33.02}
    assert e["sector_pb"] == {"银行": 0.42}  # 原始 PB 比值，非分位（_pb_percentile 的 v < cur_pb 契约）
    assert len(e["date"]) == 19  # "%Y-%m-%d %H:%M:%S"
    # 二次推送：append 不覆盖（与 ltc_main 同构，JSONL 追加）
    _record_merge_success("2026-08-06", -5.0, [], snapshots, str(hist))
    assert len([json.loads(l) for l in open(hist, encoding="utf-8")]) == 2


def test_record_merge_success_write_failure_does_not_raise(monkeypatch, tmp_path):
    """留痕写失败不得阻塞推送：吞异常返回 False，不击穿 merge_main"""
    import ltc_store
    from market_dashboard import _record_merge_success
    monkeypatch.setattr(ltc_store, "append_history",
                        lambda path, entry: (_ for _ in ()).throw(OSError("磁盘只读")))
    ok = _record_merge_success("2026-08-05", None, [], [], str(tmp_path / "x.jsonl"))
    assert ok is False
