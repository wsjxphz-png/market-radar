# tests/test_ltc_main.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import pytest
import ltc_main
ltc_store = __import__("ltc_store")

_EMPTY_ENV = {"FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "", "FEISHU_CHAT_ID": "", "DEEPSEEK_API_KEY": ""}


def test_run_skip_on_repeat(monkeypatch, tmp_path):
    env = dict(_EMPTY_ENV)
    ltc_store.save_state(str(tmp_path / "state.json"), {"last_pushed_date": "2026-08-04"})
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert sent == []  # 重复数据不推送


def test_run_news_fallback_on_flow_failure(monkeypatch, tmp_path):
    env = dict(_EMPTY_ENV)
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [{"title": "t1", "date": "2026-08-04"}])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert len(sent) == 1 and "新闻摘要" in sent[0]


def test_run_fetch_exception_degrades(monkeypatch, tmp_path):
    """FR-6.3：抓取抛异常不阻塞整体，视为该源失败（新闻也失败 → 仅累计告警）"""
    env = dict(_EMPTY_ENV)
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    def boom():
        raise RuntimeError("板块资金流源挂了")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", boom)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert sent == []
    state = ltc_store.load_state(str(tmp_path / "state.json"))
    assert state.get("alert_streak") == 1


def test_run_full_push_with_audit_trail(monkeypatch, tmp_path):
    """全链路：板块流正常 → 完整卡 → 发送成功 → 留痕（state + history）"""
    env = dict(_EMPTY_ENV)
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    flow = pd.DataFrame([{"industry": "银行", "chg_pct": -2.0, "main_net_yi": 5.0,
                          "super_large_net_yi": 3.0, "large_net_yi": 2.0}])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: flow)
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_repurchase", lambda weeks: {"period": "近4周", "items": []})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_valuation", lambda: [{"board": "银行", "ok": True, "position_pct": 23.5, "price_vs_ma60_pct": 1.2}])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_board_kline", lambda name: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert len(sent) == 1
    assert "今日数据" in sent[0] and "长期数据" in sent[0] and "诚实声明" in sent[0]
    state = ltc_store.load_state(str(tmp_path / "state.json"))
    assert state.get("last_pushed_date") == "2026-08-04"
    assert state.get("alert_streak") == 0
    hist = ltc_store.load_history(str(tmp_path / "history.jsonl"))
    assert len(hist) == 1 and hist[0]["data_date"] == "2026-08-04"


def test_run_dry_run_flag_skips_send_and_records(monkeypatch, tmp_path):
    """LTC_DRY_RUN=1：不调用发送、视为成功，state/history 留痕照常（复审 Issue 2）"""
    env = dict(_EMPTY_ENV)
    env["LTC_DRY_RUN"] = "1"
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    flow = pd.DataFrame([{"industry": "银行", "chg_pct": -2.0, "main_net_yi": 5.0,
                          "super_large_net_yi": 3.0, "large_net_yi": 2.0}])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: flow)
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_repurchase", lambda weeks: {"period": "近4周", "items": []})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_valuation", lambda: [])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_board_kline", lambda name: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert sent == []  # dry-run 不调用发送
    state = ltc_store.load_state(str(tmp_path / "state.json"))
    assert state.get("last_pushed_date") == "2026-08-04"
    hist = ltc_store.load_history(str(tmp_path / "history.jsonl"))
    assert len(hist) == 1


@pytest.mark.parametrize("val,called", [
    ("1", False),
    ("true", False),
    ("True", False),
    ("0", True),
    ("false", True),
    ("", True),
])
def test_run_dry_run_flag_truth_semantics(monkeypatch, tmp_path, val, called):
    """复审：LTC_DRY_RUN 真值判定 — 仅 "1"/"true"（不区分大小写）视为 dry-run 跳过发送；
    "0"/"false"/"" 一律走真实 send_feishu 路径"""
    env = dict(_EMPTY_ENV)
    env["LTC_DRY_RUN"] = val
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    flow = pd.DataFrame([{"industry": "银行", "chg_pct": -2.0, "main_net_yi": 5.0,
                          "super_large_net_yi": 3.0, "large_net_yi": 2.0}])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: flow)
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_repurchase", lambda weeks: {"period": "近4周", "items": []})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_valuation", lambda: [])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_board_kline", lambda name: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0
    assert (len(sent) == 1) == called, f"LTC_DRY_RUN={val!r} 应{'调用' if called else '跳过'} send_feishu"
    if called:
        state = ltc_store.load_state(str(tmp_path / "state.json"))
        assert state.get("last_pushed_date") == "2026-08-04"


def test_run_send_failure_no_state_write(monkeypatch, tmp_path):
    """无 dry-run 标记 + 发送失败 → return 1，不写 state（同日重试不丢数据，复审 Issue 2）"""
    env = dict(_EMPTY_ENV)  # 无 LTC_DRY_RUN
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    flow = pd.DataFrame([{"industry": "银行", "chg_pct": -2.0, "main_net_yi": 5.0,
                          "super_large_net_yi": 3.0, "large_net_yi": 2.0}])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", lambda: flow)
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_repurchase", lambda weeks: {"period": "近4周", "items": []})
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_valuation", lambda: [])
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_board_kline", lambda name: None)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or False)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 1
    assert len(sent) == 1  # 尝试了发送
    assert not os.path.exists(str(tmp_path / "state.json"))


def test_run_first_run_failure_keeps_retry_chain(monkeypatch, tmp_path):
    """复审 Issue 1：首次运行 + 数据全失败 → state 不得写 last_pushed_date（同日重跑仍可重试）"""
    env = dict(_EMPTY_ENV)
    monkeypatch.setattr(ltc_main.ltc_data, "get_trading_date", lambda: "2026-08-04")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_southbound", lambda: {"date": "2026-08-04", "southbound_net_yi": 25.7})
    def boom():
        raise RuntimeError("板块资金流源挂了")
    monkeypatch.setattr(ltc_main.ltc_data, "fetch_sector_flow", boom)
    monkeypatch.setattr(ltc_main.ltc_news, "fetch_news_titles", lambda today, limit: [])
    sent = []
    monkeypatch.setattr(ltc_main, "send_feishu", lambda msg, env: sent.append(msg) or True)
    code = ltc_main.run_once(env, str(tmp_path))
    assert code == 0  # 首次失败：alert_streak=1，不告警
    state = ltc_store.load_state(str(tmp_path / "state.json"))
    assert state.get("alert_streak") == 1
    assert "last_pushed_date" not in state  # 失败不得写 last_pushed_date
    # 同日重跑：仍按首次运行重试（不判"重复"），alert_streak 到 2 → 告警触发（exit 1）
    code2 = ltc_main.run_once(env, str(tmp_path))
    assert code2 == 1
    state2 = ltc_store.load_state(str(tmp_path / "state.json"))
    assert state2.get("alert_streak") == 2


def test_send_feishu_no_creds_returns_false():
    """生产语义：缺凭据 = 发送失败（False）；dry-run 语义由 LTC_DRY_RUN 标记承载"""
    assert ltc_main.send_feishu("x", dict(_EMPTY_ENV)) is False
