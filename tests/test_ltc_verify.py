import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_verify import forward_returns, differentiation, update_signal_config

def test_forward_returns_window():
    hist = [{"data_date": "2026-07-01", "tags": {"半导体": {"tag": "资金关注", "sl_net": 100.0, "accum": ""}}}]
    # 2026-07-01 在 td 中下标 4；+10TD → td[14]="2026-07-15"；+20TD → td[24]="2026-07-29"
    td = ["2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02",
          "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
          "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20",
          "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
          "2026-07-29", "2026-07-30"]
    price = {"2026-07-01": {"半导体": 100.0}, "2026-07-15": {"半导体": 110.0}, "2026-07-29": {"半导体": 105.0}}
    out = forward_returns(hist, price, td)
    assert out[0]["ret_10td"] == 10.0
    assert out[0]["ret_20td"] == 5.0

def test_differentiation_verdict():
    results = [
        {"tag": "资金关注", "ret_20td": 8.0}, {"tag": "资金关注", "ret_20td": 6.0},
        {"tag": "", "ret_20td": 1.0}, {"tag": "", "ret_20td": 0.0},
    ]
    out = differentiation(results)
    assert out["verdict"] == "有效"
    assert abs(out["diff_20"] - 3.25) < 1e-9  # (8+6)/2 - (8+6+1+0)/4 = 7 - 3.75

def test_update_signal_config_insufficient_no_strike(tmp_path, monkeypatch):
    # 复审 Issue 1：窗口未满的"数据不足"不计入降级 strike，否则闭环在给出真实判断前就误杀信号
    monkeypatch.chdir(tmp_path)
    update_signal_config("数据不足", 0.0)
    cfg = json.load(open("data/ltc/signals_config.json", encoding="utf-8"))
    assert cfg["consecutive_invalid"] == 0
    assert cfg["active"] == ["资金关注", "逆势吸筹嫌疑", "派发嫌疑", "资金撤离"]

def test_update_signal_config_two_invalid_removes_signal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    update_signal_config("无效", 0.0)
    update_signal_config("无效", 0.0)
    cfg = json.load(open("data/ltc/signals_config.json", encoding="utf-8"))
    assert cfg["consecutive_invalid"] == 2
    assert "资金撤离" not in cfg["active"]
    assert cfg["removed"] == ["资金撤离"]

def test_update_signal_config_removed_no_duplicate(tmp_path, monkeypatch):
    # 复审 Issue 2：连续第 3 个无效期不得重复追加 removed
    monkeypatch.chdir(tmp_path)
    for _ in range(3):
        update_signal_config("无效", 0.0)
    cfg = json.load(open("data/ltc/signals_config.json", encoding="utf-8"))
    assert cfg["removed"] == ["资金撤离"]

def test_update_signal_config_valid_resets_counter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    update_signal_config("无效", 0.0)
    update_signal_config("无效", 0.0)
    update_signal_config("有效", 5.0)
    cfg = json.load(open("data/ltc/signals_config.json", encoding="utf-8"))
    assert cfg["consecutive_invalid"] == 0
