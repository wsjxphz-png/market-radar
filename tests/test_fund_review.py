# tests/test_fund_review.py（v3 版：板块决策留痕回看）
import sys, os
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fund_review import load_history, future_returns, summarize, render_report

def _kline(closes, start="2026-07-01"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(closes), freq="D"),
                         "close": closes})

def _history():
    return [
        {"date": "2026-07-05", "board": "半导体", "action": "多买", "cheap": True, "trend": True},
        {"date": "2026-07-05", "board": "银行", "action": "按计划", "cheap": False, "trend": True},
        {"date": "2026-08-01", "board": "半导体", "action": "观察", "cheap": True, "trend": False},
        {"date": "2026-07-05", "board": "煤炭", "action": "无法判断", "cheap": False, "trend": False},
    ]

def test_load_history_filters_bad_rows():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write('{"date": "2026-07-05", "board": "A"}\n')
        f.write("not-json\n")
        f.write('{"date": "2026-07-06", "board": "B"}\n')
        path = f.name
    rows = load_history(path)
    os.unlink(path)
    assert len(rows) == 2

def _fetcher(board):
    if board == "半导体":
        return _kline([92 + i * 2 for i in range(30)])  # 7/5=idx4(100), 7/10(110,+10%), 7/25(140,+40%)
    if board == "银行":
        return _kline([100.0] * 30)
    return None

def test_future_returns():
    rows = future_returns(_history(), _fetcher)
    by_board = {r["board"]: r for r in rows}
    assert by_board["半导体"]["ret_5"] == 10.0
    assert by_board["半导体"]["ret_20"] == 40.0
    assert by_board["银行"]["ret_5"] == 0.0
    # 8/1 距今不足 5 交易日 → ret 为 None；煤炭 K线缺失 → 跳过
    assert len(rows) == 2

def test_summarize_groups_by_action():
    rows = future_returns(_history(), _fetcher)
    summary = summarize(rows)
    by = {g["action"]: g for g in summary}
    assert by["多买"]["n"] == 1
    assert by["多买"]["ret_5_avg"] == 10.0
    assert by["多买"]["ret_5_win"] == 100.0
    assert summary[0]["action"] == "多买"  # 动作优先级排序

def test_render_report():
    rows = future_returns(_history(), _fetcher)
    text = render_report(summarize(rows))
    assert "板块建议→结果反馈" in text
    assert "多买" in text and "10.0%" in text
    assert "【便宜+趋势】" in text
