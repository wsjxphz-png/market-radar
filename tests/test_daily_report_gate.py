"""日报送达闸门：GitHub 定时任务延迟到凌晨时仍应出最近一个交易日的数据。"""
import sys
import types
from datetime import date, datetime
from zoneinfo import ZoneInfo

import main


def test_delayed_overnight_run_targets_previous_trading_day():
    # 周二 02:00 才跑到 → 推周一；周六 02:00 才跑到 → 推周五（收盘 11 小时，未过期）
    assert main.resolve_report_day(datetime(2026, 11, 17, 2, 0)) == date(2026, 11, 16)
    assert main.resolve_report_day(datetime(2026, 11, 14, 2, 0)) == date(2026, 11, 13)


def test_stale_weekend_and_holiday_runs_skip():
    # 周六 09:00（收盘已 18 小时）太旧；周一凌晨只可能拿到上周五（已过期）；
    # 国庆假期凌晨也不能推节前的旧日报。
    assert main.resolve_report_day(datetime(2026, 11, 14, 9, 0)) is None
    assert main.resolve_report_day(datetime(2026, 11, 16, 2, 0)) is None
    assert main.resolve_report_day(datetime(2026, 10, 2, 2, 0)) is None


def test_aware_beijing_datetime_from_workflow():
    # 云端传入的是带时区的北京时间（datetime.now(ZoneInfo(...))），不能和 naive 时间相减
    tz = ZoneInfo("Asia/Shanghai")
    assert main.resolve_report_day(datetime(2026, 11, 17, 2, 0, tzinfo=tz)) == date(2026, 11, 16)
    assert main.resolve_report_day(datetime(2026, 11, 14, 9, 0, tzinfo=tz)) is None


def test_normal_evening_run_targets_today():
    assert main.resolve_report_day(datetime(2026, 11, 17, 19, 0)) == date(2026, 11, 17)
    assert main.resolve_report_day(datetime(2026, 11, 17, 14, 0)) is None


def test_report_state_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "REPORT_STATE_FILE", str(tmp_path / "last_daily_report.json"))
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    assert not main.report_already_pushed(date(2026, 11, 16))
    main.mark_report_pushed(date(2026, 11, 16))
    assert main.report_already_pushed(date(2026, 11, 16))
    assert not main.report_already_pushed(date(2026, 11, 17))


def test_calendar_covers_checks_max_date(monkeypatch):
    monkeypatch.setattr(main, "_trade_days_from_file", lambda: {date(2026, 12, 31)})
    assert main.calendar_covers(date(2026, 12, 31))
    assert not main.calendar_covers(date(2027, 1, 4))


def test_market_data_confirms_only_when_data_reaches_report_day():
    # 日历没覆盖今天时，只有行情数据里真有这一天，才确认开市
    assert not main.market_data_confirms(date(2027, 1, 4), None)
    assert not main.market_data_confirms(date(2027, 1, 4), "2026-12-31")
    assert main.market_data_confirms(date(2027, 1, 4), "2027-01-04")
    assert main.market_data_confirms(date(2027, 1, 4), "2027-01-05")


class _FakeResult:
    def __init__(self, rows):
        self.error_code = "0"
        self._it = iter(rows)
        self._row = None

    def next(self):
        try:
            self._row = next(self._it)
            return True
        except StopIteration:
            return False

    def get_row_data(self):
        return self._row


class _FakeBSIndex:
    def login(self):
        return types.SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        pass

    def query_history_k_data_plus(self, code, fields, start_date, end_date, frequency="d"):
        # 上证指数只在开市日有日K：10-1 国庆休市没有，9-30 有
        return _FakeResult([["2026-09-30"]] if start_date == "2026-09-30" else [])


class _FakeBSLoginFail:
    def login(self):
        return types.SimpleNamespace(error_code="1", error_msg="认证失败")


def test_latest_market_trade_day_walks_back_from_holiday(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, 9, 0)  # 国庆假期

    monkeypatch.setitem(sys.modules, "baostock", _FakeBSIndex())
    monkeypatch.setattr(main, "datetime", FakeDT)
    assert main.latest_market_trade_day() == "2026-09-30"


def test_latest_market_trade_day_login_failure_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "baostock", _FakeBSLoginFail())
    assert main.latest_market_trade_day() is None


def test_title_uses_report_day_not_wall_clock():
    stats = {"filtered": 1, "ok_sources": 1, "total_sources": 1, "calibration": {}}
    card = main.format_feishu({}, stats, date(2026, 11, 16))
    title = card["card"]["header"]["title"]["content"]
    assert "2026.11.16" in title and "周一" in title
