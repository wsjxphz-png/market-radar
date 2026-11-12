"""日报送达闸门：GitHub 定时任务延迟到凌晨时仍应出最近一个交易日的数据。"""
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


def test_title_uses_report_day_not_wall_clock():
    stats = {"filtered": 1, "ok_sources": 1, "total_sources": 1, "calibration": {}}
    card = main.format_feishu({}, stats, date(2026, 11, 16))
    title = card["card"]["header"]["title"]["content"]
    assert "2026.11.16" in title and "周一" in title
