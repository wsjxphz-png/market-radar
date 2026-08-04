import sys, os, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_data import parse_sector_flow, parse_repurchase, _pick_kline_cols

def test_parse_sector_flow_position_mapping():
    df = pd.DataFrame([
        [1, "半导体", 5000, 6.01, 1437.42, 1218.06, 219.35, 200, "中芯国际", 8.0, 1],
        [2, "银行", 4000, -2.69, 103.79, 173.92, -70.13, 40, "招商银行", 1.2, 2],
    ])
    out = parse_sector_flow(df)
    assert list(out.columns) == ["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]
    assert out.iloc[0]["industry"] == "半导体"
    assert abs(out.iloc[0]["super_large_net_yi"] - 1218.06) < 1e-6
    assert abs(out.iloc[1]["chg_pct"] - (-2.69)) < 1e-6

def test_parse_repurchase_phase_and_filter():
    # 18 列对齐东财 stock_repurchase_em: 0序号 1代码 2简称 3最新价 4价上 5价下 6数上 7数下
    # 8比上 9比下 10金额上 11金额下 12起始 13进度 14价高 15价低 16已回购数量 17已回购金额 18公告日期
    def row(code, name, plan_max, plan_min, progress, done, ann):
        r = [0] * 18
        r[1], r[2], r[9], r[10] = code, name, plan_max, plan_min
        r[12], r[16], r[17] = progress, done, ann
        return r
    df = pd.DataFrame([
        row("300750", "宁德时代", 400e8, 400e8, "董事会预案", 0, "2026-08-01"),
        row("002352", "顺丰控股", 60e8, 40e8, "完成实施", 50e8, "2026-06-20"),  # 6月公告=超出4周窗口
    ])
    out = parse_repurchase(df, weeks=4)
    assert len(out["items"]) == 1
    assert out["items"][0]["name"] == "宁德时代"
    assert out["items"][0]["phase"] == "董事会预案"
    assert out["items"][0]["amount_yi"] == 400.0  # 已回购为空→用计划金额，阶段标注为预案

def test_kline_col_pick_em_and_ths_layout():
    # 东财 stock_board_industry_hist_em（akshare 1.18.64 重排后）：收盘在第3列
    em = pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额", "成交量", "成交额"])
    assert _pick_kline_cols(em) == ("日期", "收盘", "成交量", "成交额")
    # 同花顺 stock_board_industry_index_ths：收盘价在第5列
    ths = pd.DataFrame(columns=["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"])
    assert _pick_kline_cols(ths) == ("日期", "收盘价", "成交量", "成交额")
    # 无收盘列 → None
    assert _pick_kline_cols(pd.DataFrame(columns=["日期", "开盘"])) is None
