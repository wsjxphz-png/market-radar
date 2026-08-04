import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_store import (load_state, save_state, load_history, append_history,
                       should_push, compute_reference, reference_label)

def test_state_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"last_pushed_date": "2026-08-04"})
    assert load_state(p) == {"last_pushed_date": "2026-08-04"}
    assert load_state(str(tmp_path / "missing.json")) == {}

def test_bare_filename_no_crash(tmp_path, monkeypatch):
    # 偏差锁定：裸文件名（无目录）时逐字版 save/append 会 makedirs('') 崩溃
    monkeypatch.chdir(tmp_path)
    save_state("state.json", {"last_pushed_date": "2026-08-04"})
    assert load_state("state.json") == {"last_pushed_date": "2026-08-04"}
    append_history("h.jsonl", {"date": "2026-08-04", "southbound_net_yi": 25.7})
    assert len(load_history("h.jsonl")) == 1

def test_history_append_and_load(tmp_path):
    p = str(tmp_path / "h.jsonl")
    append_history(p, {"date": "2026-08-04", "southbound_net_yi": 25.7})
    append_history(p, {"date": "2026-08-01", "southbound_net_yi": 50.0})
    assert len(load_history(p)) == 2

def test_should_push_rules():
    assert should_push("2026-08-04", {}) == (True, "首次运行")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-03"}) == (True, "新数据")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-04"}) == (False, "重复")
    assert should_push("2026-08-04", {"last_pushed_date": "2026-08-05"}) == (False, "数据落后")

def test_reference_and_label():
    hist = [{"date": f"2026-07-{d:02d}", "southbound_net_yi": 40.0} for d in range(1, 21)]
    ref = compute_reference(hist, "southbound_net_yi")
    assert abs(ref - 40.0) < 1e-9
    assert reference_label(50.0, 40.0) == "比平时多"      # 50/40=1.25 >= 1.2
    assert reference_label(40.0, 40.0) == "正常"
    assert reference_label(25.0, 40.0) == "比平时少"      # 0.625 <= 0.8
    assert reference_label(25.7, None) == "参照积累中"
