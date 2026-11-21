# -*- coding: utf-8 -*-
"""状态机与白话渲染的最小测试。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import data  # noqa: E402
import push  # noqa: E402
import render  # noqa: E402
from config import MARKETS, Market  # noqa: E402
from render import render_brief, render_text  # noqa: E402
from state import (  # noqa: E402
    Analysis,
    Mag7State,
    MarketState,
    _reclaim,
    action_for,
    build_frame,
    classify_stage,
)

QQQ = Market("qqq", "纳指/科技", "QQQ", "🚀", "us")
SEMIS = Market("soxq", "半导体", "SOXQ", "💻", "semis")
CHINA = Market("kweb", "中概互联网", "KWEB", "🇨🇳", "china")


def make_df(closes, ma20, ma50, ma200, dd):
    n = len(closes)
    return pd.DataFrame({
        "close": closes,
        "ma20": [ma20] * n if not isinstance(ma20, list) else ma20,
        "ma50": [ma50] * n if not isinstance(ma50, list) else ma50,
        "ma200": [ma200] * n if not isinstance(ma200, list) else ma200,
        "dd": [dd] * n,
    })


def test_healthy_is_stage6():
    df = make_df([100] * 5, 95, 90, 80, -0.01)
    assert classify_stage(df) == 6


def test_deep_drawdown_is_stage2():
    df = make_df([70] * 5, 80, 85, 90, -0.30)
    assert classify_stage(df) == 2


def test_reclaim_20_after_5_days_below_is_stage3():
    closes = [70] * 9 + [75] * 5
    ma20 = [80] * 9 + [72] * 5
    df = make_df(closes, ma20, 85, 90, -0.25)
    assert classify_stage(df) == 3


def test_reclaim_50_is_stage4():
    closes = [70] * 24 + [86]
    ma50 = [85] * 24 + [85]
    df = make_df(closes, 80, ma50, 90, -0.20)
    assert classify_stage(df) == 4


def test_reclaim_200_is_stage5():
    closes = [70] * 24 + [96]
    ma200 = [90] * 24 + [90]
    df = make_df(closes, 80, 95, ma200, -0.10)
    assert classify_stage(df) == 5


def test_reclaim_requires_enough_days_below():
    closes = [88, 89, 90, 91]
    ma20 = [85, 85, 85, 85]
    assert not _reclaim(pd.Series(closes), pd.Series(ma20), 5)


def test_reclaim_persists_after_crossing():
    closes = pd.Series([70] * 10 + [80] * 10)
    ma = pd.Series([75] * 20)
    assert _reclaim(closes, ma, 5)


def test_reclaim_false_after_falling_back():
    closes = pd.Series([70] * 10 + [80] * 5 + [74] * 2)
    ma = pd.Series([75] * 17)
    assert not _reclaim(closes, ma, 5)


def test_stage3_persists_after_confirmation():
    closes = [70] * 9 + [75] * 5 + [76]
    ma20 = [80] * 9 + [72] * 6
    df = make_df(closes, ma20, 85, 90, -0.25)
    assert classify_stage(df) == 3


def test_stage4_pullback_is_observe_not_add():
    df = make_df([94] * 5, 90, 95, 80, -0.04)
    assert classify_stage(df) == 4
    s = MarketState(QQQ, 94, 90, 95, 80, -0.04, 4, 80)
    assert s.branch == "pullback"
    assert "不新加" in s.action


def test_death_cross_rebound_is_not_stage5():
    df = make_df([96] * 5, 80, 90, 95, -0.10)
    assert classify_stage(df) == 4


def test_action_is_specific():
    assert "1/3" in action_for(QQQ, 3)
    assert "不抄底" in action_for(QQQ, 2)
    assert "10–15%" in action_for(CHINA, 2)
    assert "减半" in action_for(SEMIS, 3)
    assert "数据不足" in action_for(QQQ, 0)


def test_expected_us_date():
    assert data._expected_us_date(date(2026, 11, 21)) == pd.Timestamp("2026-11-20")  # 周六
    assert data._expected_us_date(date(2026, 11, 23)) == pd.Timestamp("2026-11-20")  # 周一
    assert data._expected_us_date(date(2026, 11, 24)) == pd.Timestamp("2026-11-23")  # 周二


def test_split_adjustment_removes_fake_gap():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    raw = pd.DataFrame({
        "date": dates,
        "open": [100, 101, 102, 10.3, 10.4],
        "high": [101, 102, 103, 10.5, 10.6],
        "low": [99, 100, 101, 10.2, 10.3],
        "close": [100, 101, 102, 10.4, 10.5],
    })
    adj = data._adjust_splits(raw)
    assert adj["close"].iloc[3] / adj["close"].iloc[2] > 0.9
    assert abs(adj["close"].iloc[0] - 10.0) < 0.01


def test_actions_stage0_no_nan():
    states = [
        MarketState(m, float("nan"), float("nan"), float("nan"), float("nan"),
                    float("nan"), 0, float("nan"))
        for m in MARKETS
    ]
    a = Analysis(states, [], [], 0, float("nan"), pd.Timestamp("2026-11-20"))
    text = render_brief(a)
    assert "nan" not in text.lower()
    assert "数据不足" in text


def test_push_markers_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "CACHE", tmp_path)
    push.mark_pushed("2026-11-20", kind="daily")
    push.mark_pushed("2026-W47", kind="weekly")
    assert push.already_pushed("2026-11-20", kind="daily")
    assert push.already_pushed("2026-W47", kind="weekly")
    assert not push.already_pushed("2026-W47", kind="daily")


def test_card_has_title_template_and_markdown():
    s = MarketState(QQQ, 100, 95, 90, 80, -0.10, 4, 85)
    m = Mag7State("AAPL", "苹果", 100, 90, 5)
    a = Analysis([s], [m], [], 1, -0.03, pd.Timestamp("2026-11-20"))
    c = render.card(a, weekly=False)
    assert c["title"].startswith("美股状态日报")
    assert "🇺🇸" not in c["title"] and "US" not in c["title"]
    assert c["template"] in ("red", "orange", "green", "grey")
    assert "**一句话：**" in c["content"]
    assert "**🧭 今天怎么做**" in c["content"]


def test_build_frame_has_indicators():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    close = pd.Series([100 + i * 0.1 for i in range(320)], index=idx)
    df = build_frame(close)
    assert pd.notna(df["ma200"].iloc[-1])
    assert df["dd"].iloc[-1] <= 0


def test_render_contains_plain_language_sections():
    s = MarketState(QQQ, 100, 95, 90, 80, -0.10, 4, 85)
    m = Mag7State("AAPL", "苹果", 100, 90, 5)
    a = Analysis(
        states=[s], mag7=[m], risk_flags=[("趋势", "大盘在 200 日线下方")],
        mag7_breadth=1, rsp_spy_20d=-0.03,
        data_date=pd.Timestamp("2026-11-20"),
    )
    text = render_text(a)
    for must in ("这意味着", "现在怎么做", "小白词典", "什么会改变这个判断", "规则输出"):
        assert must in text
    assert "200 日线" in text
    assert "top_adj" not in text


def test_render_brief_contains_actions_and_levels():
    states = [
        MarketState(MARKETS[0], 689, 678, 710, 733, -0.115, 1, 640),
        MarketState(MARKETS[1], 608, 678, 711, 683, -0.199, 1, 608),
        MarketState(MARKETS[2], 66, 80, 90, 86, -0.425, 2, 66),
        MarketState(MARKETS[3], 20, 23, 24, 27, -0.455, 2, 20),
        MarketState(MARKETS[4], 31, 33, 34, 35, -0.234, 2, 31),
    ]
    mag7 = [
        Mag7State("AAPL", "苹果", 342, 300, 6),
        Mag7State("NVDA", "英伟达", 125, 200, 2),
    ]
    a = Analysis(states, mag7, [("趋势", "大盘在 200 日线下方")], 1, -0.03,
                 pd.Timestamp("2026-11-20"))
    text = render_brief(a)
    for must in ("这意味着", "今天怎么做", "关键价位", "什么会改变", "规则输出"):
        assert must in text
    assert "AAPL" in text and "NVDA" in text
