# tests/test_ltc_main.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
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


def test_send_feishu_no_creds_is_dry_run_success():
    """缺少飞书凭据 = dry-run 成功（不发送但返回 True，留痕/重复跳过照常）"""
    assert ltc_main.send_feishu("x", dict(_EMPTY_ENV)) is True
