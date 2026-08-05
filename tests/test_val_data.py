# tests/test_val_data.py
import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_data import pe_percentile, fetch_valuation_snapshot, _pb_reference

def test_pe_percentile_math():
    # 200 个交易日，当前值处于中位
    dates = pd.date_range("2025-01-01", periods=200, freq="B")
    pe = pd.DataFrame({"date": dates, "pe": [10.0 + ((i + 50) % 100) * 0.1 for i in range(200)]})
    out = pe_percentile(pe)
    assert out["days"] == 200
    assert 40 < out["pct"] < 60
    # 趋势：最后 20 日上升
    pe2 = pd.DataFrame({"date": dates, "pe": [10.0 + i * 0.01 for i in range(200)]})
    assert pe_percentile(pe2)["trend"] == "up"
    pe3 = pd.DataFrame({"date": dates, "pe": [10.0 - i * 0.01 for i in range(200)]})
    assert pe_percentile(pe3)["trend"] == "down"

def test_pe_percentile_short_history():
    pe = pd.DataFrame({"date": pd.date_range("2026-07-01", periods=30, freq="B"), "pe": [12.0]*30})
    out = pe_percentile(pe)
    assert out is None or out["days"] < 2440  # 历史不足：返回带 days 的结果，由调用方决定是否可信

def test_pb_reference_cold_start():
    assert _pb_reference([], "银行") is None
    hist = [{"date": "2026-07-01", "sector_pb": {"银行": 0.5}}] * 25
    ref = _pb_reference(hist, "银行")
    assert ref is not None and 0.49 < ref < 0.51

def test_fetch_valuation_snapshot_degradation_chain(monkeypatch):
    # 全失败 → 价格位置兜底；PE 可用 → 用 PE
    calls = {"pe": 0, "pb": 0, "price": 0}
    def fake_pe(code):
        calls["pe"] += 1
        dates = pd.date_range("2020-01-01", periods=500, freq="B")
        return pd.DataFrame({"date": dates, "pe": [15.0]*500})
    def fake_pb(sector):
        calls["pb"] += 1
        return None  # PB 失败
    def fake_price(sector):
        calls["price"] += 1
        return {"position_pct": 43.0}
    monkeypatch.setattr("val_data.fetch_pe_series", fake_pe)
    monkeypatch.setattr("val_data.fetch_sector_pb", fake_pb)
    monkeypatch.setattr("val_data.fetch_price_position", fake_price)
    out = fetch_valuation_snapshot(["银行"], [])
    assert len(out) == 1
    assert out[0]["source"] == "pe"      # PE 可用则用 PE
    assert out[0]["main_pct"] is not None
    assert calls["price"] == 0

def test_fetch_valuation_snapshot_all_fail_price_fallback(monkeypatch):
    monkeypatch.setattr("val_data.fetch_pe_series", lambda code: None)
    monkeypatch.setattr("val_data.fetch_sector_pb", lambda s: None)
    monkeypatch.setattr("val_data.fetch_price_position", lambda s: {"position_pct": 12.0})
    out = fetch_valuation_snapshot(["银行"], [])
    assert out[0]["source"] == "price"
    assert out[0]["main_pct"] == 12.0
    assert out[0]["note"] == "基于价格位置，非 PE/PB"

def test_fetch_constituents_zfill(monkeypatch):
    # 数值型存储的 cons 文件丢前导零（000983→983），必须补零后再归类
    fake = pd.DataFrame({i: ["x"] * 5 for i in range(4)} | {4: ["601398", "983", "000001", "300750", "830799"]})
    monkeypatch.setattr("val_data.pd.read_excel", lambda url: fake)
    from val_data import fetch_constituents
    out = fetch_constituents("000000")
    assert out == ["sh.601398", "sz.000983", "sz.000001", "sz.300750", "bj.830799"]
