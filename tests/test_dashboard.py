# -*- coding: utf-8 -*-
"""A股全景仪表盘纯函数测试 — F7 审计修复补测。
原则：注入假数据，不触网。覆盖 compute_signals / analyze_fund_flow /
format_dashboard 关键区块（含"数据暂不可用"分支）/ build_ai_prompt 兜底 /
detect_signal_flips 与 trade_log 格式匹配。
"""
import sys, os, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_dashboard as md
from market_dashboard import (compute_signals, format_dashboard, build_ai_prompt,
                              detect_signal_flips, append_signal_snapshot,
                              SENTIMENT_CYCLE, Signal)
from sector_monitor import analyze_fund_flow


# ═══════════════════════════════════════════════════════════
# 假数据构造
# ═══════════════════════════════════════════════════════════

def _idx_df(n=300, uptrend=0.001, volume=None, flat_tail=0):
    """构造指数日线假数据（无网络）。flat_tail: 尾部 N 日收盘持平（用于放量滞涨）。"""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = [100 * (1 + uptrend) ** i for i in range(n)]
    if flat_tail > 0:
        close[-flat_tail:] = [close[-flat_tail - 1]] * flat_tail
    open_ = [c * 0.99 for c in close]
    high = [c * 1.02 for c in close]
    low = [c * 0.98 for c in close]
    if volume is None:
        volume = [1e9] * n
    return pd.DataFrame({"date": dates, "open": open_, "close": close,
                         "high": high, "low": low, "volume": volume})


def _cycle():
    return dict(SENTIMENT_CYCLE["startup"])  # 含 emoji/name/position/action/watch/quote


# ═══════════════════════════════════════════════════════════
# compute_signals — 信号与阈值断言
# ═══════════════════════════════════════════════════════════

def test_compute_signals_full_set_and_uptrend():
    """强上升趋势 → 10 个信号齐全，年线/三周期健康，M1 缺数据标记 caution"""
    signals = compute_signals(_idx_df(), None)
    names = {s.name for s in signals}
    assert names == {"年线位置", "520战法", "MACD动能", "RSI", "乖离率",
                     "三周期趋势", "量价结构", "波动率", "支撑压力", "M1宏观锚"}
    by_name = {s.name: s for s in signals}
    assert by_name["年线位置"].status == "healthy"      # 收盘距年线 +14%
    assert "共振向上" in by_name["三周期趋势"].value
    assert by_name["M1宏观锚"].status == "caution"       # m1 缺数据 → 数据不可用
    assert "数据不可用" in by_name["M1宏观锚"].value


def test_compute_signals_downtrend():
    """持续下跌 → 年线下方 danger + 三周期共振向下"""
    signals = compute_signals(_idx_df(uptrend=-0.002), None)
    by_name = {s.name: s for s in signals}
    assert by_name["年线位置"].status == "danger"
    assert by_name["三周期趋势"].status == "danger"
    assert "共振向下" in by_name["三周期趋势"].value


def test_compute_signals_volume_stagnant():
    """放量滞涨（量 3 倍 + 收盘持平）→ 量价结构 danger，触发阈值 VOL_STAGNANT=2.0"""
    df = _idx_df(n=120, uptrend=0.001)
    df.loc[df.index[-1], "volume"] = 3e9
    df.loc[df.index[-1], "close"] = df.loc[df.index[-2], "close"]  # 当日涨幅 ~0
    signals = compute_signals(df, None)
    vp = {s.name: s for s in signals}["量价结构"]
    assert vp.status == "danger"
    assert "放量滞涨" in vp.value


# ═══════════════════════════════════════════════════════════
# build_ai_prompt — F1: 市场宽度不可用时不喂 0 兜底数据
# ═══════════════════════════════════════════════════════════

def test_ai_prompt_breadth_unavailable_no_zero_feed():
    """宽度不可用 → prompt 出现"暂不可用"，绝无"涨0家"兜底数字"""
    cycle = _cycle()
    temp = {"up_count": 0, "down_count": 0, "limit_up": 0, "limit_down": 0,
            "total": 0, "breadth_ok": False}
    prompt = build_ai_prompt(cycle, [], [], temp)
    assert "市场宽度数据暂不可用" in prompt
    assert "涨0家" not in prompt and "跌0家" not in prompt
    assert "不得据此项" in prompt


def test_ai_prompt_breadth_available_shows_numbers():
    """宽度可用 → prompt 展示真实数字"""
    temp = {"up_count": 3200, "down_count": 1900, "limit_up": 45, "limit_down": 3,
            "total": 5100, "breadth_ok": True}
    prompt = build_ai_prompt(_cycle(), [], [], temp)
    assert "涨3200家" in prompt and "涨停45" in prompt


# ═══════════════════════════════════════════════════════════
# format_dashboard — 关键区块（含不可用分支）
# ═══════════════════════════════════════════════════════════

def test_format_dashboard_breadth_unavailable():
    """宽度不可用 → 推送正文显示"数据暂不可用"，不出现 0 值表格"""
    idx = _idx_df()
    signals = compute_signals(idx, None)
    temp = {"up_count": 0, "down_count": 0, "limit_up": 0, "limit_down": 0,
            "total": 0, "volume": {}, "breadth_ok": False, "volume_ok": False}
    msg = format_dashboard(_cycle(), signals, [], None, idx, temp_data=temp)
    assert "市场温度" in msg
    assert "数据暂不可用" in msg
    assert "涨0家" not in msg


def test_format_dashboard_sector_unavailable_block():
    """F2: 板块整体异常 → 推送出现"板块数据暂不可用"，且无空标题"""
    idx = _idx_df()
    signals = compute_signals(idx, None)
    msg = format_dashboard(_cycle(), signals, [], None, idx,
                           sector_unavailable=True)
    assert "⚠️ **板块数据暂不可用**" in msg
    assert "## 🔍 板块操作信号" not in msg  # 空板块不再输出空标题


def test_format_dashboard_fund_flow_hist_unavailable():
    """F5: 板块资金流 5d/10d 失败 → 标注"资金流历史数据暂不可用"，当日资金流仍显示"""
    idx = _idx_df()
    signals = compute_signals(idx, None)
    fake_sector = {"name": "半导体", "category": "科技", "rating": "🟡 观察中",
                   "phase": "震荡", "tags": "指标正常"}
    msg = format_dashboard(_cycle(), signals, [fake_sector], None, idx,
                           fund_flow_hist_ok=False,
                           flow_data={"sectors": ["半导体: 流入43.5亿"]})
    assert "资金流历史数据暂不可用" in msg
    assert "半导体: 流入43.5亿" in msg          # 当日资金流仍显示


def test_format_dashboard_flow_blocks():
    """F3: 资金异动 + 指数监测（板块模块）并入推送正文"""
    idx = _idx_df()
    signals = compute_signals(idx, None)
    msg = format_dashboard(_cycle(), signals, [], None, idx,
                           sector_overview={"上证指数": {"close": 3450.0, "above_ma5": True, "bias_pct": 1.2}},
                           flow_anomalies=[{"sector": "半导体", "anomaly": "资金面反转：此前持续流出，近期转为流入"}])
    assert "## 📊 指数监测（板块模块）" in msg
    assert "站上MA5" in msg
    assert "## ⚠️ 资金异动" in msg
    assert "半导体" in msg


def test_format_dashboard_manual_reference_marker():
    """F8: 交易手册区块带"参考材料 · 非今日数据"标注"""
    idx = _idx_df()
    signals = compute_signals(idx, None)
    msg = format_dashboard(_cycle(), signals, [], None, idx)
    assert "参考材料 · 非今日数据" in msg


# ═══════════════════════════════════════════════════════════
# analyze_fund_flow — 资金流信号（注入假 df，不触网）
# ═══════════════════════════════════════════════════════════

def _f5df(name, v):
    return pd.DataFrame([{"board_name": name, "main_inflow_5d": v,
                          "main_inflow_pct_5d": 2.0, "top_inflow_stock_5d": "X"}])


def _f10df(name, v):
    return pd.DataFrame([{"board_name": name, "main_inflow_10d": v,
                          "main_inflow_pct_10d": 1.0}])


def test_analyze_fund_flow_signals():
    """5d/10d 同号/反号 → accumulation / turning_bullish / turning_bearish / distribution"""
    assert analyze_fund_flow("A", _f5df("A", 10), _f10df("A", 5), None)["signal"] == "accumulation"
    assert analyze_fund_flow("A", _f5df("A", 10), _f10df("A", -5), None)["signal"] == "turning_bullish"
    assert analyze_fund_flow("A", _f5df("A", -10), _f10df("A", 5), None)["signal"] == "turning_bearish"
    assert analyze_fund_flow("A", _f5df("A", -10), _f10df("A", -5), None)["signal"] == "distribution"


def test_analyze_fund_flow_missing_inputs_neutral_zero():
    """数据源为 None（获取失败）→ 静默归零 + neutral，由调用方标注不可用"""
    r = analyze_fund_flow("A", None, None, None)
    assert r["main_inflow_5d"] == 0 and r["main_inflow_10d"] == 0
    assert r["signal"] == "neutral"


def test_analyze_fund_flow_anomaly():
    """历史资金流前10日流出 + 近10日流入 → flow_anomaly 资金面反转"""
    hist = pd.DataFrame({"date": pd.date_range("2026-06-01", periods=20),
                         "main_inflow": [-1.0] * 10 + [1.0] * 10})
    r = analyze_fund_flow("A", _f5df("A", 10), _f10df("A", 5), hist)
    assert "资金面反转" in r.get("flow_anomaly", "")
    assert "此前持续流出" in r["flow_anomaly"]


# ═══════════════════════════════════════════════════════════
# detect_signal_flips ↔ trade_log 格式匹配 (F4)
# ═══════════════════════════════════════════════════════════

def test_append_snapshot_roundtrip_format(tmp_path):
    """append 的条目格式与 detect_signal_flips 读取格式一致（type/date/signals）"""
    log = tmp_path / "trade_log.jsonl"
    signals = [Signal("三周期趋势", "v", "healthy"), Signal("RSI", "v", "caution")]
    append_signal_snapshot("2026-08-05", signals, str(log))
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["type"] == "index"
    assert entry["date"] == "2026-08-05"
    assert entry["signals"] == {"三周期趋势": "healthy", "RSI": "caution"}


def test_detect_signal_flips_matches_yesterday(tmp_path):
    """昨日条目日期=昨日 → 检出翻转；非 index 类型/坏行被跳过"""
    log = tmp_path / "trade_log.jsonl"
    yesterday = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).strftime("%Y-%m-%d")
    log.write_text(
        "not json line\n"
        + json.dumps({"type": "stock", "date": yesterday, "signals": {"RSI": "danger"}}) + "\n"
        + json.dumps({"type": "index", "date": yesterday,
                      "signals": {"三周期趋势": "caution", "RSI": "healthy"}}) + "\n",
        encoding="utf-8")
    today = [Signal("三周期趋势", "v", "healthy"), Signal("RSI", "v", "caution")]
    flips = detect_signal_flips(today, str(log))
    flip_names = " | ".join(flips)
    assert "三周期趋势" in flip_names          # caution→healthy ↑
    assert "RSI" in flip_names                 # healthy→caution ↓
    assert "stock" not in flip_names           # 非 index 类型不参与


def test_detect_signal_flips_no_yesterday_entry(tmp_path):
    """无昨日条目 → 返回空列表"""
    log = tmp_path / "trade_log.jsonl"
    log.write_text(json.dumps({"type": "index", "date": "2020-01-01",
                               "signals": {"三周期趋势": "danger"}}) + "\n", encoding="utf-8")
    assert detect_signal_flips([Signal("三周期趋势", "v", "healthy")], str(log)) == []


# ═══════════════════════════════════════════════════════════
# F10 推送去重 — last_pushed_date 状态（复用 ltc_store 模式）
# ═══════════════════════════════════════════════════════════

def test_is_already_pushed_logic():
    """F10: 去重判断 — 空状态/新数据放行，同日重复/数据落后跳过"""
    assert md.is_already_pushed("2026-08-05", {}) is False                          # 首次运行
    assert md.is_already_pushed("2026-08-05", {"last_pushed_date": "2026-08-04"}) is False  # 新数据
    assert md.is_already_pushed("2026-08-05", {"last_pushed_date": "2026-08-05"}) is True   # 同日重复
    assert md.is_already_pushed("2026-08-05", {"last_pushed_date": "2026-08-06"}) is True   # 数据落后


def test_push_state_roundtrip(tmp_path):
    """F10: state 读写往返 — 自动建目录 / 缺文件 / 坏文件均不炸"""
    path = str(tmp_path / "sub" / "state.json")
    md._save_push_state(path, "2026-08-05")
    assert md._load_push_state(path) == {"last_pushed_date": "2026-08-05"}
    assert md._load_push_state(str(tmp_path / "missing.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert md._load_push_state(str(bad)) == {}


class _FakeStockData:
    """main() 市场温度段落的假实现（零网络）"""
    def get_market_breadth(self):
        return {"up_count": 1000, "down_count": 800, "limit_up": 30, "limit_down": 5,
                "total": 1800, "available": True}
    def get_market_volume(self, idx_volume):
        return {"total_amount": 9e11, "ratio": 1.1, "available": True}
    def get_sector_fund_flow(self):
        return pd.DataFrame()


def _fake_sector_data():
    """sector_monitor 假返回 — 含一个降级板块条目（technical/fund 为空）"""
    return {
        "date": "2026-08-05",
        "sectors": [{"name": "半导体", "category": "科技", "meeting_status": "watch",
                     "meeting_rule": "x", "meeting_note": "y",
                     "today": {}, "technical": {}, "fund_flow": {}, "signal": {}}],
        "summary": {"entry": [], "hold": [], "watch": ["半导体"], "avoid": [],
                    "entry_count": 0, "hold_count": 0, "watch_count": 1, "avoid_count": 0},
        "market_overview": {}, "flow_anomalies": [], "fund_flow_hist_ok": True,
    }


def _mock_main_deps(monkeypatch, idx, tmp_path, calls):
    """把 main() 的抓取/推送/板块依赖全部替换为假实现，send 次数记入 calls"""
    import sys
    import sector_monitor
    monkeypatch.setattr(md, "fetch_index", lambda name, code, days=300: idx)
    monkeypatch.setattr(md, "fetch_m1", lambda: None)
    monkeypatch.setattr(md, "ai_audit", lambda *a, **k: None)
    monkeypatch.setattr(md, "append_signal_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(md, "StockData", _FakeStockData)
    monkeypatch.setattr(md, "PUSH_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(md, "send_feishu",
                        lambda content: (calls.__setitem__("send", calls["send"] + 1), True)[1])
    monkeypatch.setattr(sector_monitor, "fetch_sector_monitor_data", lambda: _fake_sector_data())
    monkeypatch.setattr(sys, "argv", ["market_dashboard.py", "--force"])


def test_main_push_dedup_two_runs(tmp_path, monkeypatch, capsys):
    """F10: 两次运行 — 第一次推送成功并写 state，第二次同数据日期 → 跳过推送（send 不再被调用）"""
    idx = _idx_df()
    data_date = idx["date"].iloc[-1].strftime("%Y-%m-%d")
    calls = {"send": 0}
    _mock_main_deps(monkeypatch, idx, tmp_path, calls)

    md.main()
    out1 = capsys.readouterr().out
    assert "飞书: OK" in out1
    assert calls["send"] == 1                                     # 第一次真实推送
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_pushed_date"] == data_date                 # 状态已记录

    md.main()
    out2 = capsys.readouterr().out
    assert "已推送过" in out2                                     # 第二次打印跳过原因
    assert "跳过本次推送" in out2
    assert calls["send"] == 1                                     # 未重复推送
