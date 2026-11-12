#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频面板的冒烟与规则测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import panel_data as p


def test_state_key():
    assert p.state_key("确认（趋势修复）") == "确认"
    assert p.state_key("疑似（边际改善）") == "疑似"
    # 强名单变化本身是信号，不能被压成笼统的“观察”
    assert p.state_key("观察（未确认；强：创业板指）") == "观察（强：创业板指）"
    assert p.state_key("观察（未确认）") == "观察"
    assert p.state_key("未出现（仍在恶化）") == "未出现"


def test_classify():
    assert p.classify(110, 100, 0.6, 0.0)[0] == "顶部风险区"
    assert p.classify(90, 100, 0.0, 0.8)[0] == "底部观察区（强）"
    assert p.classify(90, 100, 0.0, 0.6)[0] == "底部观察区（弱）"
    assert p.classify(110, 100, 0.2, 0.2)[0] == "趋势正常"
    assert p.classify(90, 100, 0.0, 0.2)[0] == "下跌趋势"


def test_score_requires_three_dims():
    assert np.isnan(p.score({"a": True, "b": False}))
    assert p.score({"a": True, "b": False, "c": True}) == pytest.approx(2 / 3)
    assert p.score({"a": False, "b": False, "c": False, "d": False}) == 0.0


def test_credit_flag_respects_point_in_time():
    row = pd.Series({
        "pe_pct": 0.5, "erp_pct": 0.5, "gap_chg3": -1.0, "cn_10y_chg60": 0.1,
        "sz_yoy": -0.1, "ret_60": 0.0, "breadth_ma200_chg20": 0.0,
        "breadth_ma200": 0.5, "drawdown_252": 0.0, "margin_20d_chg": 0.0,
        "turnover_z": 0.0, "margin_pct": 0.5, "qvix_pct": 0.5,
        "qvix_20d_chg": 0.0, "extension": 0.0, "ret_120": 0.0,
        "close": 100.0, "ma200": 100.0,
    })
    row["credit_ok"] = True
    top, _ = p.recompute_flags(row, set())
    assert "信用" in top and top["信用"] is True
    row["credit_ok"] = False
    top2, _ = p.recompute_flags(row, set())
    assert "信用" not in top2


def test_last_episode_skips_short_blip():
    n = 40
    s = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "close": np.arange(100.0, 100.0 + n),
        "top_adj": np.zeros(n),
    })
    s.loc[5:9, "top_adj"] = 0.6
    s.loc[20, "top_adj"] = 0.6
    ep = p.last_episode(s, "top_adj", 0.6, min_len=5)
    assert ep is not None
    assert ep["start"] == s["date"].iloc[5].date()
    assert ep["end"] == s["date"].iloc[9].date()
    assert ep["days"] == 5


def test_zone_stats_signs():
    # 区间起点 5、终点 12：需要 n-1 >= 12+120，125 行不足以算完整窗口
    n = 160
    close = np.r_[np.linspace(100, 110, 30), np.linspace(108, 90, 130)]
    s = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "close": close,
        "top_adj": np.where((np.arange(n) >= 5) & (np.arange(n) <= 12), 0.6, 0.0),
    })
    z = p.zone_stats(s, "top_adj", 0.6)
    assert z["n"] == 1
    assert z["n_full"] == 1
    assert z["fwd_dd_med"] < 0
    assert z["post_dd_med"] < 0


def test_zone_stats_post_prob_excludes_incomplete_window():
    n = 160
    close = np.full(n, 100.0)
    close[5:10] = 110.0
    close[10:] = 90.0
    close[140:145] = 110.0
    s = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "close": close,
        "top_adj": np.where(((np.arange(n) >= 5) & (np.arange(n) <= 9))
                            | ((np.arange(n) >= 140) & (np.arange(n) <= 144)), 0.6, 0.0),
    })
    z = p.zone_stats(s, "top_adj", 0.6)
    assert z["n"] == 2
    assert z["n_full"] == 1
    # 第二个区间观察窗不足 120 日（post 为 NaN），不能计入分母
    assert z["post_dd_10"] == pytest.approx(1.0)


def test_episodes_merges_runs_within_gap():
    n = 80
    s = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "close": np.full(n, 100.0),
        "top_adj": np.zeros(n),
    })
    # 两段相隔 16 个交易日（<=20）应合并；第三段相隔 31 日应独立
    s.loc[5:9, "top_adj"] = 0.6
    s.loc[25:29, "top_adj"] = 0.6
    s.loc[60:64, "top_adj"] = 0.6
    ep = p.episodes(s, "top_adj", 0.6, gap=20)
    assert ep == [(5, 29), (60, 64)]


def test_lpr_cuts_include_5y_only():
    cuts = p.lpr_cuts(p._load_lpr())
    row = cuts[cuts["date"] == pd.Timestamp("2024-02-20")].iloc[0]
    assert row["chg1"] == 0 and row["chg5"] < 0
    assert p.lpr_tenor(row) == "5Y"


def test_attach_credit_ok_current_and_history():
    sig = p.load_signal("hs300")
    out = p.attach_credit_ok(sig).set_index("date")
    social = pd.read_csv(p.CACHE / "social_financing.csv", parse_dates=["month"])
    after = sig[sig["date"] > social["month"].max()].reset_index(drop=True)
    assert len(after) > 70, "社融停更后交易日不足，测试前置条件失效"
    near = after.iloc[10]["date"]   # 社融月后约 11 个交易日，仍可用
    far = after.iloc[60]["date"]    # 约 61 个交易日，已过期
    assert bool(out.loc[near, "credit_ok"]) is True
    assert bool(out.loc[far, "credit_ok"]) is False
    assert bool(out.iloc[-1]["credit_ok"]) is False


def test_market_bottom_is_confirmed_once_trend_repairs():
    # 趋势修复就是底成立的证据；后来涨出顶部风险，不能反过来把底“撤回”
    st, tone = p.market_bottom_status(True, 0.6, [], [])
    assert st.startswith("确认")
    assert "顶部风险" in st
    assert p.state_key(st) == "确认"
    assert tone == "watch"
    assert p.market_bottom_status(True, 0.2, [], []) == ("确认（趋势修复）", "ok")
    assert p.market_bottom_status(False, 0.2, ["创业板指"], [])[0].startswith("观察")


def test_phase_text_uses_all_three_statuses():
    assert "均已确认" in p.phase_text("确认", "确认（趋势修复）", "确认（改善）")
    assert "盈利底未现" in p.phase_text("确认", "确认（趋势修复）", "未出现（仍在恶化）")
    assert "边际改善" in p.phase_text("确认", "确认（趋势修复）", "疑似（边际改善）")
    assert "磨底" in p.phase_text("疑似", "观察（未确认；强：创业板指）", "未出现（仍在恶化）")


def test_fmt_handles_nan():
    assert p.fmt(float("nan"), "+.1%") == "—"
    assert p.fmt(1.234, ".0%") == "123%"


def test_pct_of_last_constant_window_is_nan():
    s = pd.Series(np.zeros(40))
    assert np.isnan(p.pct_of_last(s))
    assert np.isnan(p.pct_of_last(pd.Series(np.arange(10.0))))


def test_panel_smoke():
    pan = p.build_panel()
    assert isinstance(pan.date, pd.Timestamp)
    assert len(pan.indices) == 4
    assert len(pan.dims) == 5
    for i in pan.indices:
        assert 0.0 <= i.top_adj <= 1.0
        assert 0.0 <= i.bottom_adj <= 1.0
        assert i.top_adj == pytest.approx(p.score(i.top_flags))
        assert i.bottom_adj == pytest.approx(p.score(i.bottom_flags))
    assert pan.etf.date <= pan.date
    assert len(pan.etf.baskets) >= 4
    assert [h.name for h in pan.health][:3] == ["指数行情", "成交额(沪深300)", "PE估值"]
    assert [b.name for b in (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings)] == \
        ["政策底", "市场底", "盈利底"]


def test_push_message_is_plain_and_not_stale():
    import push_feishu
    pan = p.build_panel()
    msg = push_feishu.build_message(pan)
    for key in ("一句话", "顶部风险", "三个底", "五维体检", "数据日期", "名词小抄", "不是交易指令"):
        assert key in msg
    # 上涨阶段要有对称的顶部检查，不能只找底
    assert "风险条件亮了" in msg
    assert "已经进入顶部风险区" not in msg or any(i.state.startswith("顶部") for i in pan.indices)
    assert "nan" not in msg.lower()
    assert "None" not in msg
    # 冒号后面不能出现只有内行才懂的缩写/记号
    for jargon in ("top<0.4", "MA20", "pp", "T-", "净申赎", "分位", "底部分数"):
        assert jargon not in msg, f"推送里还有黑话: {jargon}"
    # 每个指数都要有一句“这意味着什么”，不能只有数字
    for i in pan.indices:
        assert i.name in msg and push_feishu._index_meaning(i)


def test_push_data_dates_come_from_health_not_hardcoded():
    import push_feishu
    pan = p.build_panel()
    msg = push_feishu.build_message(pan)
    line = next(l for l in msg.splitlines() if l.startswith("数据日期"))
    for name in ("指数行情", "PE估值", "ETF份额", "两融", "社融"):
        h = next(x for x in pan.health if x.name == name)
        assert push_feishu._note_plain(h) in line, f"推送缺少 {name} 的实时状态说明"
    # 旧版把“两融 T-5”“社融停更”写死；数据源恢复后会变成错误信息
    assert "T-5" not in line and "停更" not in line


def test_note_plain_translates_internal_marks():
    import push_feishu

    class H:
        def __init__(self, note):
            self.note = note

    assert push_feishu._note_plain(H("当日")) == "和行情同一天"
    assert push_feishu._note_plain(H("T-5")) == "比行情晚 5 个交易日"
    assert "5 个交易日" in push_feishu._note_plain(H("滞后 5 个交易日"))
    assert "没更新" in push_feishu._note_plain(H("停更/过期 130 个交易日，不参与判断"))
    assert "没更新" in push_feishu._note_plain(H("停更/过期 20 个交易日，不参与判断"))


def test_push_alert_hides_cli_commands():
    import push_feishu
    out = push_feishu._plain_alert("数据可能未更新；请运行 panel.py --refresh。")
    assert "请运行" not in out and "panel.py" not in out
    assert "自动任务" in out


def test_non_trading_day_skips_without_pushing(monkeypatch):
    import run_daily
    from datetime import datetime as real_datetime

    class FakeDT:
        @staticmethod
        def now():
            return real_datetime(2026, 11, 14, 9, 0)  # 周六早上开机

    pushed = []
    monkeypatch.setattr(run_daily, "datetime", FakeDT)
    monkeypatch.setattr(run_daily, "refresh_calendar", lambda: True)
    monkeypatch.setattr(run_daily, "is_trading_day", lambda d: False)
    monkeypatch.setattr(run_daily, "log", lambda msg: None)
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])
    assert run_daily.main() == 0  # 休市日直接跳过，不补发周五的面板
    assert pushed == []


def test_unknown_calendar_uses_index_data_before_pushing(monkeypatch):
    import run_daily
    from datetime import datetime as real_datetime

    class FakeDT:
        @staticmethod
        def now():
            return real_datetime(2026, 11, 14, 19, 0)  # 日历坏了，无法判断

    class FakePanel:
        date = pd.Timestamp("2026-11-13")
        headline = "test"

        @staticmethod
        def run(**kwargs):
            class P:
                date = pd.Timestamp("2026-11-13")
                headline = "test"
            return P()

    pushed = []
    monkeypatch.setattr(run_daily, "datetime", FakeDT)
    monkeypatch.setattr(run_daily, "refresh_calendar", lambda: True)
    monkeypatch.setattr(run_daily, "is_trading_day", lambda d: None)
    monkeypatch.setattr(run_daily, "latest_completed_trading_day", lambda now: None)
    monkeypatch.setattr(run_daily, "log", lambda msg: None)
    monkeypatch.setitem(sys.modules, "panel", FakePanel)
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])
    # 指数数据停在昨天：无法证明今天开市，按休市处理，不推旧面板
    assert run_daily.main() == 0
    assert pushed == []


def test_latest_completed_trading_day_uses_previous_day_before_close():
    import run_daily
    from datetime import date, datetime as real_datetime
    # 2026-11-16 周一：开盘前/盘中应算上周五，收盘后才算当天
    assert run_daily.latest_completed_trading_day(real_datetime(2026, 11, 16, 9, 0)) == date(2026, 11, 13)
    assert run_daily.latest_completed_trading_day(real_datetime(2026, 11, 16, 19, 0)) == date(2026, 11, 16)
    assert run_daily.latest_completed_trading_day(real_datetime(2026, 11, 14, 9, 0)) == date(2026, 11, 13)


def test_index_meaning_handles_missing_ma200():
    import push_feishu

    class I:
        state = "底部观察区（强）"
        top_adj = 0.0
        bottom_adj = 1.0
        ma200 = float("nan")
        close = 100.0
        drawdown_252 = float("nan")

    text = push_feishu._index_meaning(I)
    assert "数据不足" in text
    assert "已站上" not in text


def test_etf_read_follows_numbers_not_hardcoded():
    import push_feishu

    class E:
        total_d20 = 500.0
        total_d20_pct = 0.98
        total_d5 = -20.0

    assert "极高" in push_feishu._etf_read(E)
    assert "暂停" in push_feishu._etf_read(E)

    class F:
        total_d20 = 500.0
        total_d20_pct = 0.98
        total_d5 = 30.0

    read = push_feishu._etf_read(F)
    assert "在继续" in read and "暂停" not in read


def test_push_marker_is_per_panel_date(tmp_path, monkeypatch):
    import push_feishu
    monkeypatch.setattr(push_feishu, "MARK", tmp_path / "last_push.json")
    from datetime import date
    d = pd.Timestamp("2026-11-12")
    run_day = date(2026, 11, 12)
    assert not push_feishu.already_pushed(d, run_date=run_day)
    push_feishu.mark_pushed(d, run_date=run_day)
    assert push_feishu.already_pushed(d, run_date=run_day)
    assert not push_feishu.already_pushed(pd.Timestamp("2026-11-13"), run_date=run_day)
    # 数据源没更新时，面板日期仍是昨天；换了一个运行日必须重新推送，
    # 不能因为“昨天推过”就静默跳过今天的提醒。
    assert not push_feishu.already_pushed(d, run_date=date(2026, 11, 13))


def test_trading_day_gate():
    from datetime import date
    import run_daily
    assert run_daily.is_trading_day(date(2026, 11, 12)) is True   # 周四
    assert run_daily.is_trading_day(date(2026, 11, 14)) is False  # 周六


def test_stale_mark_allows_one_fresh_correction(tmp_path, monkeypatch):
    import push_feishu
    monkeypatch.setattr(push_feishu, "MARK", tmp_path / "last_push.json")
    d = pd.Timestamp("2026-11-12")
    run_day = __import__("datetime").date(2026, 11, 12)
    push_feishu.mark_pushed(d, run_date=run_day, fresh=False)
    # 旧数据推过一次：同一天的新数据要允许补发；仍然没新数据则不重复
    assert not push_feishu.already_pushed(d, run_date=run_day, fresh=True)
    assert push_feishu.already_pushed(d, run_date=run_day, fresh=False)
    push_feishu.mark_pushed(d, run_date=run_day, fresh=True)
    assert push_feishu.already_pushed(d, run_date=run_day, fresh=True)


def test_feishu_uuid_is_content_keyed():
    import push_feishu
    a = push_feishu._uuid("内容 A")
    assert a == push_feishu._uuid("内容 A")
    assert a != push_feishu._uuid("内容 B")
    assert len(a) <= 50


def test_push_message_adapts_to_confirmed_market_bottom():
    import push_feishu
    pan = p.build_panel()
    pan.bottoms.market.status = "确认（趋势修复）"
    pan.bottoms.market.plain = (
        "市场底已经确认：价格连续5个交易日站在200日线上方，同时顶部风险很低。")
    msg = push_feishu.build_message(pan)
    assert "市场底：已确认，下跌趋势修复" in msg
    assert "市场底已经确认" in msg
    assert "现在只是观察区，不是买点" not in msg
    # 市场已修复后，政策底不再说“还没出现”，否则像还在等底
    assert "政策底：当前不适用" in msg
    assert "政策底：还没出现" not in msg


def test_headline_warns_when_top_risk_builds():
    import push_feishu
    pan = p.build_panel()
    for i in pan.indices:
        i.state = "趋势正常"
        i.top_adj = 0.4
    out = push_feishu._plain_headline(pan)
    assert "顶部风险开始累积" in out
    assert "不加仓" in out


def test_push_failure_leaves_no_marker(tmp_path, monkeypatch):
    import push_feishu

    class P:
        date = pd.Timestamp("2026-11-12")

    monkeypatch.setattr(push_feishu, "MARK", tmp_path / "last_push.json")
    monkeypatch.setattr(push_feishu, "build_message", lambda pan: "test")
    monkeypatch.setattr(push_feishu, "load_credentials", lambda: {})
    monkeypatch.setattr(push_feishu, "send_text", lambda text, creds: False)
    assert push_feishu.push_panel(P) == "failed"
    assert not push_feishu.already_pushed(P.date)

    # 同一天重试成功后写标记；再次调用不再重复发送
    monkeypatch.setattr(push_feishu, "send_text", lambda text, creds: True)
    assert push_feishu.push_panel(P) == "sent"
    assert push_feishu.already_pushed(P.date)
    assert push_feishu.push_panel(P) == "skipped"
