import sys, os, pytest, requests, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ltc_data
from ltc_data import parse_sector_flow, parse_repurchase, _pick_kline_cols


@pytest.fixture(autouse=True)
def _reset_breakers():
    """每个测试前复位熔断标志，保证用例相互独立"""
    ltc_data._EM_AVAILABLE = True
    ltc_data._THS_AVAILABLE = True
    yield
    ltc_data._EM_AVAILABLE = True
    ltc_data._THS_AVAILABLE = True


def _kline_df(n=130):
    """同花顺列序的板块K线假数据（>=60 行才被视为有效响应）"""
    dates = pd.date_range("2022-01-01", periods=n)
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘价": 10.0, "最高价": 11.0, "最低价": 9.0, "收盘价": 10.5,
        "成交量": 1000, "成交额": 1e8,
    })


def _no_sleep(monkeypatch):
    monkeypatch.setattr(ltc_data.time, "sleep", lambda _: None)


def test_em_network_failure_trips_breaker_ths_fallback(monkeypatch):
    """EM 网络异常 → 第一块板熔断 EM，第二块板直接跳过 EM（EM 全程只被调用 1 次），THS 兜底两块"""
    calls = {"em": 0, "ths": 0}

    def fake_em(*a, **k):
        calls["em"] += 1
        raise requests.exceptions.ConnectionError("IP blocked")

    def fake_ths(*a, **k):
        calls["ths"] += 1
        return _kline_df()

    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_hist_em", fake_em)
    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_index_ths", fake_ths)
    _no_sleep(monkeypatch)

    r1 = ltc_data.fetch_board_kline("银行", days=1300)
    assert r1 is not None and len(r1) >= 60
    assert ltc_data._EM_AVAILABLE is False and ltc_data._THS_AVAILABLE is True

    r2 = ltc_data.fetch_board_kline("券商", days=1300)
    assert r2 is not None
    assert calls["em"] == 1   # 第二块板不再探测已熔断的 EM
    assert calls["ths"] == 2   # THS 两块板各兜底一次


def test_both_sources_network_down_fast_none_no_api_calls(monkeypatch):
    """EM+THS 均网络失败 → 双双熔断，后续板块零 API 调用直接返回 None（快速失败路径）"""
    calls = {"em": 0, "ths": 0}

    def fake_em(*a, **k):
        calls["em"] += 1
        raise requests.exceptions.ConnectionError("blocked")

    def fake_ths(*a, **k):
        calls["ths"] += 1
        raise TimeoutError("timeout")

    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_hist_em", fake_em)
    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_index_ths", fake_ths)
    _no_sleep(monkeypatch)

    assert ltc_data.fetch_board_kline("银行", days=1300) is None
    assert ltc_data.fetch_board_kline("券商", days=1300) is None
    assert ltc_data.fetch_board_kline("地产", days=1300) is None
    assert calls["em"] == 1 and calls["ths"] == 1  # 第 2、3 块板零调用


def test_short_response_data_issue_does_not_trip_breaker(monkeypatch):
    """EM 返回 <60 行响应（数据质量问题，非网络故障）→ 不熔断，下一块板仍探测 EM"""
    calls = {"em": 0, "ths": 0}

    def fake_em(*a, **k):
        calls["em"] += 1
        return _kline_df(n=30)

    def fake_ths(*a, **k):
        calls["ths"] += 1
        return _kline_df()

    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_hist_em", fake_em)
    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_index_ths", fake_ths)

    assert ltc_data.fetch_board_kline("银行", days=1300) is not None  # THS 兜底成功
    assert ltc_data._EM_AVAILABLE is True and ltc_data._THS_AVAILABLE is True
    ltc_data.fetch_board_kline("券商", days=1300)
    assert calls["em"] == 2  # 数据问题不熔断 → EM 继续被探测


def test_non_network_exception_retries_no_trip(monkeypatch):
    """KeyError 等数据类异常 → 按 retries=1 重试（共 2 次），但不熔断"""
    calls = {"em": 0}

    def fake_em(*a, **k):
        calls["em"] += 1
        raise KeyError("数据列缺失")

    def fake_ths(*a, **k):
        return _kline_df()

    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_hist_em", fake_em)
    monkeypatch.setattr(ltc_data.ak, "stock_board_industry_index_ths", fake_ths)
    _no_sleep(monkeypatch)

    assert ltc_data.fetch_board_kline("银行", days=1300) is not None
    assert calls["em"] == 2  # retries=1 → 首次失败后重试 1 次
    assert ltc_data._EM_AVAILABLE is True  # 数据类异常不触发熔断

def test_parse_sector_flow_ths_simplified_schema():
    """THS 简化变体（akshare stock_fund_flow_industry 现行，data.10jqka.com.cn/funds/hyzjl）：
    按列名识别 流入资金/流出资金/净额 —— 无超大单拆分，super_large_net_yi=净额（资金方向代理）。
    旧按位置映射会把 流出资金 当作超大单（半导体流出 1617.54 显示为 超大单+1617.5亿，方向被反转）。"""
    df = pd.DataFrame([
        [1, "半导体", 15699.8, 5.63, 1716.35, 1617.54, 98.81, 183, "有研硅", 17.62, 32.97],
        [2, "银行", 4000.0, -1.15, 112.2, 145.22, -33.02, 42, "招商银行", 1.2, 10.0],
    ], columns=["序号", "行业", "行业指数", "行业-涨跌幅", "流入资金", "流出资金", "净额",
                "公司家数", "领涨股", "领涨股-涨跌幅", "当前价"])
    out = parse_sector_flow(df)
    assert list(out.columns) == ["industry", "chg_pct", "main_net_yi", "super_large_net_yi", "large_net_yi"]
    assert out.iloc[0]["industry"] == "半导体"
    assert abs(out.iloc[0]["chg_pct"] - 5.63) < 1e-6
    # 核心：超大单列取净额（98.81），而不是流出资金（1617.54）
    assert abs(out.iloc[0]["super_large_net_yi"] - 98.81) < 1e-6
    assert abs(out.iloc[0]["large_net_yi"] - 1617.54) < 1e-6  # 流出资金入 large，不再冒充超大单
    assert abs(out.iloc[1]["super_large_net_yi"] - (-33.02)) < 1e-6  # 银行净流出为负，方向正确

def test_parse_sector_flow_full_variant_by_name():
    """THS 全量变体（含大单拆分）：按列名直接取 主力/超大单/大单 净额列"""
    df = pd.DataFrame([
        ["半导体", 5.63, 1437.42, 1218.06, 219.35],
    ], columns=["行业", "涨跌幅", "主力净流入-净额", "超大单净流入-净额", "大单净流入-净额"])
    out = parse_sector_flow(df)
    assert abs(out.iloc[0]["super_large_net_yi"] - 1218.06) < 1e-6
    assert abs(out.iloc[0]["large_net_yi"] - 219.35) < 1e-6
    assert abs(out.iloc[0]["main_net_yi"] - 1437.42) < 1e-6

def test_parse_sector_flow_missing_industry_none():
    df = pd.DataFrame([["A", 1.0, 2.0, 3.0, 4.0]], columns=["x", "y", "z", "w", "v"])
    assert parse_sector_flow(df) is None

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
