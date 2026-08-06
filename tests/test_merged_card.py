# -*- coding: utf-8 -*-
"""合并卡片纯函数测试 — build_merged_card 全量保留原仪表盘区块 + 估值/资金新增区块 + 无北向
+ compute_fund_state 资金维确认。
原则：注入假数据，不触网。内容丢失修复（feat/board-overview）：原六区块简化版已废弃，
合并卡 = format_dashboard 全量（原 14+ 区块一个不丢）+ 估值判断（顶部）+ 资金观察（尾部）。
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from market_dashboard import (build_merged_card, compute_fund_state,
                              _with_metric_disclosure, compute_signals,
                              SENTIMENT_CYCLE)


# ═══════════════════════════════════════════════════════════
# 假数据构造（与 test_dashboard 同构，不触网）
# ═══════════════════════════════════════════════════════════

def _idx_df(n=300, uptrend=0.001):
    """构造指数日线假数据（无网络）：强上升趋势 → 三周期/年线 healthy"""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = [100 * (1 + uptrend) ** i for i in range(n)]
    return pd.DataFrame({"date": dates, "open": [c * 0.99 for c in close],
                         "close": close, "high": [c * 1.02 for c in close],
                         "low": [c * 0.98 for c in close], "volume": [1e9] * n})


def _full_card_data():
    """构造 build_merged_card 全量入参（覆盖原仪表盘全部区块 + 新增区块）"""
    from ltc_format import build_fund_section
    idx = _idx_df()
    signals = compute_signals(idx, None)
    sectors = [
        {"name": "半导体", "category": "科技", "rating": "🟡 观察中", "phase": "震荡",
         "tags": "指标正常", "_entry_check": "❌ 年线下方——手册第2节：选股需五条件全满足"},
    ]
    focus, southbound, refs, repurchase = _fund_fixture()
    return {
        # ── format_dashboard 全量参数（原仪表盘全部区块） ──
        "cycle": dict(SENTIMENT_CYCLE["startup"]),
        "signals": signals,
        "sectors": sectors,
        "ai_text": "AI 解读文本（事实清单模式）",
        "idx": idx,
        "indices": {"上证": idx, "深证": idx, "创业板": idx, "科创50": idx},
        "temp_data": {"up_count": 3200, "down_count": 1900, "limit_up": 45, "limit_down": 3,
                      "total": 5100, "volume": {"total_amount": 1.2e12, "ratio": 1.1},
                      "breadth_ok": True, "volume_ok": True},
        "flow_data": {"sectors": ["半导体: 流入43.5亿", "银行: 流出12.3亿"]},
        "sector_unavailable": False,
        "sector_overview": {"上证指数": {"close": 3450.0, "above_ma5": True, "bias_pct": 1.2}},
        "flow_anomalies": [{"sector": "半导体",
                            "anomaly": "资金面反转：此前持续流出，近期转为流入"}],
        "fund_flow_hist_ok": True,
        "board_ok": True,
        # ── 新增区块 ──
        "valuation_judgements": [
            {"board": "银行", "verdict": "便宜", "dominant": "估值+资金", "confidence": "高",
             "action": "full", "note": "推断"}],
        "valuation_snapshots": [
            {"board": "银行", "source": "pb", "main_pct": None, "pb_pct": 42.0,
             "pe_pct": None, "trend": "flat", "note": "PB=0.42（成分中位数）"}],
        "fund_observation": build_fund_section(focus, southbound, repurchase, refs),
        "honest": "诚实声明",
    }


# ═══════════════════════════════════════════════════════════
# 内容丢失修复：原仪表盘全部区块必须在合并卡中出现，一个不能丢
# ═══════════════════════════════════════════════════════════

def test_merged_card_keeps_all_original_dashboard_blocks(monkeypatch):
    """合并卡保留原 format_dashboard 大盘/交易区块（温度/手册/指数监测/信号翻转/
    今日信号/入场出场/冲突裁决/综合策略/每日一得等）；板块视图由聚合卡承担
    （2026-08-07 重构：估值判断/板块总览/操作信号/板块全貌/资金流向 合并入聚合卡）"""
    monkeypatch.setattr("market_dashboard.detect_signal_flips",
                        lambda signals, log_path=None: ["量价结构: healthy→danger ↓"])
    data = _full_card_data()
    data["board_facts"] = _board_facts_fixture()
    card = build_merged_card(data)
    for marker in [
        "## 🔥 市场温度",                       # 市场温度
        "**怎么看**",                          # 市场温度"怎么看"说明
        "## 📖 交易手册",                      # 交易手册
        "## ⚠️ 信号翻转 — 与昨日对比",         # 信号翻转
        "## 📊 今日信号",                      # 今日信号
        "## 🎯 入场/出场信号与仓位",           # 入场/出场信号
        "## ⚔️ 信号冲突裁决",                  # 冲突裁决
        "## 🎯 综合策略",                      # 综合策略
        "## 📖 每日一得",                      # 每日一得
        "━━━ 🔷 板块判断",                    # 板块聚合卡（替代原板块多区块）
    ]:
        assert marker in card, f"原仪表盘区块丢失: {marker}"
    # 板块视图唯一化：被聚合卡替代的区块不得再单独出现（去重契约）
    for gone in ["## 💰 板块资金流向", "## 🔍 板块操作信号", "## 📋 板块全貌",
                 "## 👀 板块观察池", "━━━ 💰 估值判断", "━━━ 🧭 板块总览"]:
        assert gone not in card, f"板块区块应并入聚合卡: {gone}"


def test_merged_card_order_market_first_sector_grouped_trade_last():
    """区块顺序(2026-08-07 v3)：大盘(日期/温度/手册/释义)最前 → 板块聚合卡
    → 资金观察 → 交易组 → 诚实声明。释义紧跟手册(风险点1)，聚合卡替代旧板块区块。"""
    data = _full_card_data()
    data["board_facts"] = _board_facts_fixture()
    card = build_merged_card(data)
    assert card.startswith("**")                  # 日期行是卡首字符(审查 P3-2)
    i_temp = card.find("## 🔥 市场温度")           # 大盘组
    i_manual = card.find("## 📖 交易手册")         # 手册
    i_glossary = card.find("名词释义")             # 释义必须紧跟手册(风险点1)
    i_agg = card.find("━━━ 🔷 板块判断")          # 板块聚合卡
    i_fund_obs = card.find("资金观察（南向/回购/承接）")
    i_trade = card.find("## 📊 今日信号")          # 交易组
    i_insight = card.find("## 📖 每日一得")
    i_honest = card.find("诚实声明")
    # 大盘组在前：温度→手册→释义，释义不得后置到板块区
    assert 0 <= i_temp < i_manual < i_glossary < i_agg
    # 聚合卡 → 资金观察 → 交易组 → 诚实声明
    assert i_agg < i_fund_obs < i_trade < i_insight < i_honest
    # 旧板块区块全部被聚合卡替代
    for gone in ["## 💰 板块资金流向", "## 🔍 板块操作信号", "## 📋 板块全貌",
                 "━━━ 💰 估值判断", "━━━ 🧭 板块总览"]:
        assert gone not in card, f"板块区块应并入聚合卡: {gone}"


def test_merged_card_fund_observation_enhanced_block():
    """资金观察增强区块（南向/回购/承接）存在（非板块级维度，独立保留）"""
    card = build_merged_card(_full_card_data())
    assert "━━━ 📈 资金观察（南向/回购/承接） ━━━" in card
    assert "南向" in card
    assert "回购" in card
    assert "承接" in card
    # 2026-08-07 重构：板块资金流向并入聚合卡（资金列），不再单独输出
    assert "## 💰 板块资金流向" not in card


def test_merged_card_sector_unavailable_block():
    """板块模块整体异常 → 聚合卡输出「板块数据暂不可用」（F2 不静默缺块）"""
    data = _full_card_data()
    data["sector_unavailable"] = True
    data["sectors"] = []
    data["board_facts"] = []
    card = build_merged_card(data)
    assert "━━━ 🔷 板块判断" in card
    assert "板块数据暂不可用" in card
    assert "## 🔍 板块操作信号" not in card  # 空板块不输出空标题


def test_merged_card_has_no_northbound():
    """北向硬性不出现：卡片任何位置不得出现"北向" """
    card = build_merged_card(_full_card_data())
    assert "北向" not in card


def test_merged_card_no_empty_new_blocks():
    """新增区块数据缺失时不得输出空标题（F2：不静默缺块，也不得编造）"""
    data = _full_card_data()
    data["valuation_judgements"] = []
    data["valuation_snapshots"] = []
    data["fund_observation"] = ""
    data["honest"] = ""
    card = build_merged_card(data)
    assert "估值判断" not in card
    assert "资金观察" not in card
    assert "诚实声明" not in card
    assert "## 🔥 市场温度" in card          # 原区块仍然在


def test_fund_observation_header_emoji_no_collision():
    """资金观察区块 emoji(📈)与板块聚合卡(🔷)不冲突(2026-08-07:估值/资金流向区块已并入聚合卡)"""
    data = _full_card_data()
    data["board_facts"] = _board_facts_fixture()
    card = build_merged_card(data)
    assert "━━━ 📈 资金观察" in card
    assert "━━━ 🔷 板块判断" in card


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
    data = _full_card_data()
    data["fund_observation"] = build_fund_section(focus, southbound, repurchase, refs)
    merged = build_merged_card(data)
    for key in ["净额（板块资金流入-流出",
                "承接：偏长期布局特征（推断）｜ 价格处于 1 年低位区",
                "南向（内地资金买入港股市场",
                "对你意味着什么"]:
        assert key in ltc and key in merged
    # 旧的双轨口径（合并卡裸写"（超大单 X亿，股价"）不得再现（净额口径断言见 test_ltc_narrative）
    assert "（超大单 173.9亿，股价" not in merged


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


# ── Task 2: 资金维补记 12 板块 — focus 之外的板块也必须留痕，否则 compute_fund_state 永停 cold_start ──

def _flow_fixture():
    """完整 sector_flow 假数据（同花顺口径行业名，super_large_net_yi = 净额）：
    含 12 个估值板块的全部 THS 成分行业 + 无关行业；国防军工成分故意缺失（测 None 路径）"""
    rows = [
        # ── 与东财同名直映 ──
        ("半导体", 12.5), ("银行", -33.02), ("通信设备", 8.1),
        # ── 多对一聚合：THS 细分 → 一个东财板块 ──
        ("化学制药", 3.1), ("中药", -1.2), ("医疗服务", 0.5), ("医疗器械", 2.0),   # → 医药生物
        ("白酒", 5.5), ("食品加工制造", -2.0),                                     # → 食品饮料
        ("工业金属", 4.0),                                                          # → 有色金属
        ("IT服务", 1.1), ("软件开发", 0.9),                                         # → 计算机
        ("电池", 6.0), ("光伏设备", -3.0), ("电网设备", 1.0),                      # → 电力设备
        ("煤炭开采加工", 7.7),                                                      # → 煤炭
        ("证券", 9.0), ("保险", -4.0),                                              # → 非银金融
        ("汽车整车", 1.2), ("汽车零部件", 3.4),                                     # → 汽车
        # ── 无关行业（59/90 行业里的大多数；Task 6 补全的子行业在 _flow_fixture_extended）──
        ("教育", 0.1), ("游戏", -5.0),
    ]
    return pd.DataFrame(rows, columns=["industry", "super_large_net_yi"])


def _flow_fixture_extended():
    """Task 6 审查 Important：THS_TO_EM 补全的 10 个同花顺子行业（2026-08-06 实测名单）
    ——原本静默丢弃的行业必须进 12 板块聚合（有色金属/电力设备/食品饮料/半导体/汽车）"""
    rows = [
        # ── 与东财同名直映 ──
        ("半导体", 12.5), ("银行", -33.02), ("通信设备", 8.1),
        # ── 多对一聚合：THS 细分 → 一个东财板块 ──
        ("化学制药", 3.1), ("中药", -1.2), ("医疗服务", 0.5), ("医疗器械", 2.0),   # → 医药生物
        ("白酒", 5.5), ("食品加工制造", -2.0),                                     # → 食品饮料
        ("工业金属", 4.0),                                                          # → 有色金属
        ("IT服务", 1.1), ("软件开发", 0.9),                                         # → 计算机
        ("电池", 6.0), ("光伏设备", -3.0), ("电网设备", 1.0),                      # → 电力设备
        ("煤炭开采加工", 7.7),                                                      # → 煤炭
        ("证券", 9.0), ("保险", -4.0),                                              # → 非银金融
        ("汽车整车", 1.2), ("汽车零部件", 3.4),                                     # → 汽车
        # ── Task 6 补全子行业 ──
        ("贵金属", 69.88), ("小金属", 2.2), ("能源金属", -1.1), ("金属新材料", 3.3),  # → 有色金属
        ("风电设备", 0.7), ("电机", 1.3), ("其他电源设备", -0.4),                    # → 电力设备
        ("饮料制造", 2.4),                                                          # → 食品饮料
        ("电子化学品", 0.9),                                                         # → 半导体（材料）
        ("汽车服务及其他", 0.6),                                                     # → 汽车
        # ── 同级错配防护：面板/PCB 类（"电子"二级，非半导体子集）不并入半导体 ──
        ("光学光电子", 4.4), ("元件", -1.6),
        # ── 无关行业 ──
        ("教育", 0.1), ("游戏", -5.0),
    ]
    return pd.DataFrame(rows, columns=["industry", "super_large_net_yi"])


def test_record_merge_success_includes_all_12_boards(tmp_path):
    """留痕 fund_by_board 覆盖全部 12 个 INDEX_MAP 板块（含不在 focus 的）：
    focus 仅 2 板块 → tags 只有 2 键，fund_by_board 必须 12 键全齐；
    多 THS 细分聚合（医药生物=4行求和、汽车=整车+零部件）；无匹配板块记 None"""
    from market_dashboard import _record_merge_success
    from val_config import INDEX_MAP
    hist = tmp_path / "history.jsonl"
    focus = [{"industry": "半导体", "tag": "逆势吸筹嫌疑", "sl_net": 12.5,
              "accum": {"period": None, "reasons": []}}]   # focus 只有 1 个
    snapshots = [{"board": "半导体", "pb": 0.5}]
    assert _record_merge_success("2026-08-05", 25.7, focus, snapshots,
                                 str(hist), flow=_flow_fixture())
    e = json.loads(open(hist, encoding="utf-8").readline())
    fb = e["fund_by_board"]
    assert set(fb.keys()) == set(INDEX_MAP.keys())          # 12 板块全齐
    assert len(e["tags"]) == 1                              # tags 仍是 focus 子集（不破坏旧结构）
    # 不在 focus 的板块必须有值（这正是 cold_start 修复点）
    assert fb["银行"] == -33.02
    assert fb["煤炭"] == 7.7
    assert fb["非银金融"] == 5.0                             # 证券 9.0 + 保险 -4.0 = 5.0
    # 多对一聚合求和
    assert fb["医药生物"] == 4.4                             # 3.1 + (-1.2) + 0.5 + 2.0
    assert fb["汽车"] == 4.6                                 # 1.2 + 3.4
    assert fb["电力设备"] == 4.0                             # 6.0 + (-3.0) + 1.0
    # 直映板块
    assert fb["半导体"] == 12.5 and fb["通信设备"] == 8.1
    # 无匹配行业 → None（不当作 0）
    assert fb["国防军工"] is None


def test_record_merge_success_fund_by_board_flow_none(tmp_path):
    """sector_flow 抓取失败（None）→ fund_by_board 12 键仍写入但全为 None（诚实留痕）"""
    from market_dashboard import _record_merge_success
    hist = tmp_path / "history.jsonl"
    assert _record_merge_success("2026-08-05", None, [], [], str(hist), flow=None)
    e = json.loads(open(hist, encoding="utf-8").readline())
    assert len(set(e["fund_by_board"].keys())) == 12
    assert all(v is None for v in e["fund_by_board"].values())


def test_ths_em_extension_subindustries_aggregate(tmp_path):
    """Task 6 审查 Important 锁定：THS_TO_EM 补全的 10 个子行业进 12 板块聚合
    （贵金属/小金属/能源金属/金属新材料→有色金属、风电/电机/其他电源设备→电力设备、
    饮料制造→食品饮料、电子化学品→半导体、汽车服务及其他→汽车）；
    光学光电子/元件（面板/PCB，同级错配）不并入半导体"""
    from market_dashboard import _record_merge_success
    hist = tmp_path / "history.jsonl"
    assert _record_merge_success("2026-08-05", None, [], [], str(hist),
                                 flow=_flow_fixture_extended())
    e = json.loads(open(hist, encoding="utf-8").readline())
    fb = e["fund_by_board"]
    # 补全板块：原映射值 + 新增子行业求和
    assert fb["有色金属"] == 78.28      # 4.0 + 69.88 + 2.2 - 1.1 + 3.3
    assert fb["电力设备"] == 5.6        # 6.0 - 3.0 + 1.0 + 0.7 + 1.3 - 0.4
    assert fb["食品饮料"] == 5.9        # 5.5 - 2.0 + 2.4
    assert fb["半导体"] == 13.4         # 12.5 + 0.9（仅电子化学品；光学光电子/元件不并入）
    assert fb["汽车"] == 5.2            # 1.2 + 3.4 + 0.6
    # 未受影响板块原样（不回归）
    assert fb["医药生物"] == 4.4
    assert fb["非银金融"] == 5.0
    assert fb["煤炭"] == 7.7
    assert fb["计算机"] == 2.0
    assert fb["国防军工"] is None


def test_compute_fund_state_uses_fund_by_board():
    """compute_fund_state 优先读 fund_by_board：银行不在 focus（tags 无此板块）
    但 fund_by_board 有连续 3 日净流入 → inflow_confirm（冷启动解除）"""
    history = [
        {"tags": {"半导体": {"sl_net": 1.0}}, "fund_by_board": {"银行": 2.1}},
        {"tags": {"半导体": {"sl_net": 1.0}}, "fund_by_board": {"银行": 1.5}},
        {"tags": {"半导体": {"sl_net": 1.0}}, "fund_by_board": {"银行": 0.8}},
    ]
    assert compute_fund_state(history, "银行") == "inflow_confirm"


def test_compute_fund_state_fund_by_board_precedence():
    """fund_by_board 与 tags 同时存在时以 fund_by_board 为准（同一源，口径统一）"""
    history = [
        {"tags": {"银行": {"sl_net": 2.0}}, "fund_by_board": {"银行": -3.0}},
        {"tags": {"银行": {"sl_net": 2.0}}, "fund_by_board": {"银行": -1.0}},
        {"tags": {"银行": {"sl_net": 2.0}}, "fund_by_board": {"银行": -0.5}},
    ]
    assert compute_fund_state(history, "银行") == "outflow_confirm"


def test_compute_fund_state_fund_by_board_none_skipped():
    """fund_by_board 中 None/缺失（行业无匹配）不计数、不当作 0"""
    history = [
        {"fund_by_board": {"银行": None}},
        {"fund_by_board": {"银行": 1.2}},
    ]
    assert compute_fund_state(history, "银行") == "single_day"


def test_compute_fund_state_fallback_to_tags():
    """历史条目无 fund_by_board（旧格式留痕）→ 回退 tags，不丢历史"""
    history = [
        {"tags": {"银行": {"sl_net": 2.1}}},
        {"fund_by_board": {"银行": 1.5}},
        {"tags": {"银行": {"sl_net": 0.8}}},
    ]
    assert compute_fund_state(history, "银行") == "inflow_confirm"


# ── Task 2: 成交额单位 — 成交量被当作成交额的换算错误（"592亿" → 应为数千亿量级） ──

def _volume_kline_fixture():
    """上证指数日线假数据：20 个交易日，volume 单位股（最后一日 592 亿股 = 旧 bug 输入值）"""
    vols = [5.5e10] * 19 + [5.9216e10]
    return pd.DataFrame({"date": pd.date_range("2026-07-01", periods=20, freq="B"),
                         "volume": vols})


def _spot_fixture():
    """sina 全量行情假数据：沪市（sh*）成交额合计 1.2082e12 元 = 12082亿（08-05 实测量级，
    上交所官方 12097亿）；深市/北交所股票必须被排除（上证口径）"""
    return pd.DataFrame({
        "代码": ["sh600000", "sh688000", "sz000001", "sz300750", "bj920000"],
        "成交额": [6.041e11, 6.041e11, 1.0e12, 5.0e11, 1.0e8],
    })


def test_market_volume_unit(monkeypatch):
    """get_market_volume 成交额量级：旧代码把上证成交量 592 亿股当成交额显示"592亿"，
    真实上证成交额是数千亿（2026-08-05 上交所官方 12097亿）——修复后必须显示真实量级"""
    import akshare as ak
    from stock_data import StockData
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: _volume_kline_fixture())
    monkeypatch.setattr(ak, "stock_zh_a_spot", lambda: _spot_fixture())
    vol = StockData().get_market_volume(idx_volume=59215512400.0)  # 旧口径传入的成交量
    assert vol["available"] is True
    # 成交额 = 沪市成交额求和（深市/北交所排除），单位元
    assert vol["total_amount"] == 1.2082e12
    # 渲染路径量级（format_dashboard 的 vol_str 公式）：12082亿（数千亿），绝非 592亿
    assert f"{vol['total_amount']/1e8:.0f}亿" == "12082亿"
    assert "592亿" != f"{vol['total_amount']/1e8:.0f}亿"
    # 20 日均比值来自成交量序列（单位无关）
    vols = _volume_kline_fixture()["volume"]
    assert abs(vol["ratio"] - vols.iloc[-1] / vols.mean()) < 1e-9
    assert vol["avg_amount_20d"] > 0


def test_market_volume_unavailable_when_source_fails(monkeypatch):
    """数据源失败时不得退回成交量冒充成交额（旧 bug 路径）：诚实返回 unavailable"""
    import akshare as ak
    from stock_data import StockData
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: _volume_kline_fixture())
    monkeypatch.setattr(ak, "stock_zh_a_spot",
                        lambda: (_ for _ in ()).throw(OSError("sina 全量行情被墙")))
    vol = StockData().get_market_volume(idx_volume=59215512400.0)
    assert vol["available"] is False
    assert vol["total_amount"] == 0


# ═══════════════════════════════════════════════════════════
# Task 5: 板块总览三层结构（事实→说明→判断）+ TREND_STATE 映射（渲染层）
# synthesize 输出契约（Task 4 实测）：board/facts/explanation/short_term_judge/
# dca_judge/conflict_note 六 key——渲染层逐 key 核对，错位会静默降级（Issue 3 交接）
# ═══════════════════════════════════════════════════════════

def _board_facts_fixture():
    """synthesize 输入契约全 key 事实（上升/下降/全缺三态）"""
    return [
        {"board": "银行", "trend_state": "above20_rising", "valuation": "合理",
         "fund_state": "inflow_confirm", "sl_net": 12.5, "main_pct": 50.0,
         "metric": "PE", "years": 10.0, "terms": ["PE", "分位", "20日线"]},
        {"board": "煤炭", "trend_state": "below20_falling", "valuation": "便宜",
         "fund_state": "outflow_confirm", "sl_net": -3.2, "main_pct": 15.0,
         "metric": "PB", "years": 8.0,
         "terms": ["PB", "分位", "20日线", "永不下跌补仓", "微笑曲线"]},
        {"board": "通信设备", "trend_state": "unknown", "valuation": "观察",
         "fund_state": "unknown", "sl_net": None, "main_pct": None,
         "metric": "PE", "years": None, "terms": []},
    ]


def test_board_overview_three_layers():
    """每板块包含 事实/说明/判断 三个标记；判断含短线+定投两场景；名词首次出现有解释"""
    from val_format import build_board_overview
    out = build_board_overview(_board_facts_fixture())
    assert "🧭 板块总览" in out
    assert "3 板块" in out
    assert out.count("◆ 事实") == 3
    assert out.count("◆ 说明") == 3
    assert out.count("◆ 判断") == 3
    assert "短线（推断）" in out
    assert "定投（推断）" in out
    # 名词首次出现有解释（PE/分位 大白话）
    assert "PE：市盈率" in out
    assert "分位：百分位" in out


def test_board_overview_judgment_can_be_absent():
    """数据不足 → "无法判断"照常渲染（判断可缺席是设计，不强行给结论）"""
    from val_format import build_board_overview
    out = build_board_overview(_board_facts_fixture())
    assert "无法判断：趋势状态不明" in out       # 短线缺席
    assert "无法判断：估值证据不足" in out       # 定投缺席
    assert "无法判断：趋势与估值证据均不足" in out  # 冲突分流整体缺席
    assert "◆ 事实" in out and "◆ 说明" in out and "◆ 判断" in out


def test_board_overview_trend_framework_loaded():
    """判断措辞含趋势交易论框架：不破20日线 / 永不下跌补仓 / 微笑曲线"""
    from val_format import build_board_overview
    out = build_board_overview(_board_facts_fixture())
    assert "不破 20 日线" in out          # 银行：上升顺势持有
    assert "永不下跌补仓" in out          # 煤炭：下降不抄底
    assert "微笑曲线" in out              # 煤炭：便宜可加码（定投场景）


def test_board_overview_renders_all_synthesize_keys():
    """渲染层逐 key 核对 synthesize 输出契约（facts/explanation/short_term_judge/
    dca_judge/conflict_note 全部渲染，错位会静默降级——Task 4 Issue 3 交接）"""
    from val_format import build_board_overview
    out = build_board_overview(_board_facts_fixture())
    # facts 层：趋势/估值/资金三行原样在场
    assert "板块：银行" in out
    assert "趋势：" in out and "估值：" in out and "资金：" in out
    # explanation 层：翻译文本透出（分位/趋势/资金三翻译）
    assert "只有 50% 的时间比现在便宜" in out
    assert "20 日线上方上升期" in out
    assert "有持续性的买入" in out
    # judgment 层：短线 + 定投 + 冲突分流（煤炭=下降+便宜 冲突存在）
    assert "场景分流（推断）" in out


def test_board_overview_per_board_exception_degraded(monkeypatch):
    """Task 6 审查 Important：单板块 synthesize 抛错 → 该板块降级"无法判断：数据异常（推断）"，
    其余板块三层照常渲染，不击穿整卡（val_format.py 裸下标 KeyError 击穿修复）"""
    from val_format import build_board_overview
    import val_explain
    real = val_explain.synthesize
    def flaky(facts):
        if facts.get("board") == "煤炭":
            raise ValueError("模拟 synthesize 异常")
        return real(facts)
    monkeypatch.setattr(val_explain, "synthesize", flaky)
    out = build_board_overview(_board_facts_fixture())
    assert out.count("◆ 事实") == 3                 # 3 板块都渲染（煤炭降级，不击穿整卡）
    assert "无法判断：数据异常（推断）" in out        # 煤炭降级文案
    assert "数据异常，事实层缺失" in out             # 事实层降级标注
    assert "只有 50% 的时间比现在便宜" in out         # 银行（正常板块）说明层照常
    assert "不破 20 日线" in out                     # 银行判断层照常（永不下跌补仓在降级板块上）


def test_board_overview_syn_missing_key_fallback(monkeypatch):
    """Task 6 审查 Important：synthesize 输出缺 key（契约违反）→ .get 兜底降级而非 KeyError"""
    from val_format import build_board_overview
    import val_explain
    real = val_explain.synthesize
    def partial(facts):
        syn = real(facts)
        if facts.get("board") == "煤炭":
            del syn["dca_judge"]                    # 模拟契约违反：判断 key 缺失
        return syn
    monkeypatch.setattr(val_explain, "synthesize", partial)
    out = build_board_overview(_board_facts_fixture())
    assert "无法判断：数据异常（推断）" in out        # 缺 key 板块降级
    assert out.count("◆ 判断") == 3                  # 三板块判断层仍在


def test_trend_state_map():
    """sector_monitor trend_phase → val_explain TREND_STATE 词表（渲染层映射）：
    topping（筑顶）在 20 日线上方应归"震荡"而非"上升"（val_explain docstring 标注）"""
    from market_dashboard import trend_state_map
    assert trend_state_map("rally") == "above20_rising"
    assert trend_state_map("downtrend") == "below20_falling"
    for phase in ("oscillation", "topping", "bottoming", "mixed"):
        assert trend_state_map(phase) == "around20_oscillation"
    assert trend_state_map("unknown") == "unknown"
    assert trend_state_map(None) == "unknown"
    assert trend_state_map("不存在的阶段") == "unknown"


def test_build_board_facts_full_contract():
    """synthesize 输入契约端到端组装（Task 4 Issue 3 交接：逐 key 核对，缺 key 静默降级）：
    snapshots+judgements+history+sector 条目 → 每板块事实全 key；
    多子行业 trend_phase 全一致取该值、不一致归 mixed（→震荡）、无 THS 映射 → unknown"""
    from market_dashboard import build_board_facts
    snapshots = [
        {"board": "银行", "source": "pb", "main_pct": 12.0, "pb_pct": 12.0,
         "pe_pct": None, "trend": "flat", "years": 1.2, "note": "PB=0.42"},
        {"board": "医药生物", "source": "pe", "main_pct": 55.0, "pe_pct": 55.0,
         "pb_pct": None, "trend": "flat", "years": 4.0, "note": ""},
        {"board": "计算机", "source": "pe", "main_pct": 40.0, "pe_pct": 40.0,
         "pb_pct": None, "trend": "flat", "years": 2.5, "note": ""},
        {"board": "半导体", "source": "pe", "main_pct": None, "pe_pct": None,
         "pb_pct": None, "trend": "flat", "years": None, "note": ""},
    ]
    judgements = [
        {"board": "银行", "verdict": "便宜", "confidence": "高", "action": "full", "note": ""},
        {"board": "医药生物", "verdict": "合理", "confidence": "中", "action": "half", "note": ""},
        {"board": "计算机", "verdict": "观察", "confidence": "低", "action": "skip", "note": ""},
        {"board": "半导体", "verdict": "观察", "confidence": "低", "action": "skip", "note": ""},
    ]
    history = [
        {"tags": {"银行": {"sl_net": 2.1}}, "fund_by_board": {"银行": 3.3}},
        {"tags": {"银行": {"sl_net": 1.5}}, "fund_by_board": {"银行": 2.2}},
        {"tags": {"银行": {"sl_net": 0.9}}, "fund_by_board": {"银行": 1.1}},
    ]
    sectors = [
        {"name": "银行", "technical": {"trend_phase": "topping"}},          # 筑顶 → 震荡
        {"name": "化学制药", "technical": {"trend_phase": "rally"}},        # → 医药生物
        {"name": "中药", "technical": {"trend_phase": "rally"}},            # 全一致 → rally
        {"name": "IT服务", "technical": {"trend_phase": "rally"}},          # → 计算机
        {"name": "软件开发", "technical": {"trend_phase": "oscillation"}},  # 不一致 → mixed
    ]
    facts = build_board_facts(snapshots, judgements, history, sectors)
    assert len(facts) == 4
    by_board = {f["board"]: f for f in facts}
    # 单子行业 topping → 震荡（非上升）
    b = by_board["银行"]
    assert b["trend_state"] == "around20_oscillation"
    assert b["valuation"] == "便宜"
    assert b["fund_state"] == "inflow_confirm"      # fund_by_board 优先（Task 2 口径）
    assert b["sl_net"] == 1.1                        # 最新留痕优先（列表末条为最新）
    assert b["main_pct"] == 12.0 and b["metric"] == "PB" and b["years"] == 1.2
    assert "PB" in b["terms"] and "20日线" in b["terms"] and "微笑曲线" in b["terms"]
    # 多子行业全一致 → 取该阶段
    assert by_board["医药生物"]["trend_state"] == "above20_rising"
    assert by_board["医药生物"]["valuation"] == "合理"
    # 多子行业不一致 → mixed → 震荡（诚实，不取单边）
    assert by_board["计算机"]["trend_state"] == "around20_oscillation"
    # 无 THS 映射 → unknown；数据缺 → main_pct None 透传
    assert by_board["半导体"]["trend_state"] == "unknown"
    assert by_board["半导体"]["main_pct"] is None
    assert "20日线" not in by_board["半导体"]["terms"]


def test_merged_card_board_overview_between_valuation_and_dashboard():
    """板块聚合卡(2026-08-07)：位于大盘组(温度/手册/释义)之后、资金观察之前；
    聚合卡含每板块 事实+短线/定投 行；板块总览/估值判断旧区块不再单独出现"""
    data = _full_card_data()
    data["board_facts"] = _board_facts_fixture()
    card = build_merged_card(data)
    i_glossary = card.find("名词释义")
    i_agg = card.find("━━━ 🔷 板块判断")
    i_fund = card.find("资金观察")
    i_honest = card.find("诚实声明")
    assert -1 not in (i_glossary, i_agg, i_fund, i_honest)
    assert 0 <= i_glossary < i_agg < i_fund < i_honest
    assert "◆ 板块：" in card  # 聚合卡事实行首行
    assert "趋势：" in card     # 事实行含趋势
    assert "◆ 短线：" in card  # 聚合卡判断行


def test_merged_card_board_overview_empty_no_header():
    """board_facts 空 → 不输出板块总览空标题（F2：不静默缺块，也不得输出空标题）"""
    data = _full_card_data()
    data["board_facts"] = []
    card = build_merged_card(data)
    assert "板块总览" not in card
    assert "## 🔥 市场温度" in card          # 原区块不受影响
