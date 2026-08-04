import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_analysis import analyze_flows, compute_accumulation, pick_focus

def _flow_df():
    # industry, chg_pct, main, super_large, large
    # 超大单值: 1218.1 / 173.9 / 100.0 / -250.0 / 2.0 → 分位 100/80/60/20/40
    return pd.DataFrame([
        ["半导体", 6.01, 1437.4, 1218.1, 219.4],
        ["银行", -2.69, 103.8, 173.9, -70.1],
        ["通信设备", 5.4, 866.3, 100.0, 182.8],
        ["食品饮料", 1.0, -300.0, -250.0, -50.0],   # 涨但超大单流出 → 派发嫌疑
        ["煤炭", 0.5, 5.0, 2.0, 3.0],               # 中间 → 待观察
    ], columns=["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"])

def test_analyze_flows_tags():
    r = {x["industry"]: x for x in analyze_flows(_flow_df())}
    assert r["半导体"]["tag"] == "资金关注" or r["半导体"]["tag"] == "逆势吸筹嫌疑"  # 涨+流入 → 资金关注
    assert r["银行"]["tag"] == "逆势吸筹嫌疑"     # 跌+超大单流入
    assert r["食品饮料"]["tag"] == "派发嫌疑"     # 涨+超大单流出
    assert r["煤炭"]["tag"] == ""                  # 中间

def test_analyze_flows_percentile():
    r = analyze_flows(_flow_df())
    assert max(x["sl_percentile"] for x in r) == 100.0
    assert min(x["sl_percentile"] for x in r) == 20.0

def test_compute_accumulation_long():
    # 持续放量 + 低位 + 回购背书 → 偏长期
    kline = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=60),
                          "close": [10 + i * 0.01 for i in range(60)],
                          "volume": [1e6] * 40 + [2e6] * 20})
    out = compute_accumulation(kline, chg=-1.5, sl_net=100.0, backing=True, pos_1y=20.0)
    assert out["period"] == "偏长期布局特征"
    assert len(out["reasons"]) >= 2

def test_compute_accumulation_short():
    out = compute_accumulation(None, chg=5.0, sl_net=10.0, backing=False, pos_1y=None)
    assert out["period"] == "持续性数据积累中"  # 无K线 → 冷启动

def test_pick_focus_priority():
    a = [
        {"industry": "A", "tag": "资金关注", "sl_percentile": 90.0},
        {"industry": "B", "tag": "逆势吸筹嫌疑", "sl_percentile": 85.0},
        {"industry": "C", "tag": "", "sl_percentile": 50.0},
    ]
    out = pick_focus(a, top_n=2)
    assert [x["industry"] for x in out] == ["B", "A"]
