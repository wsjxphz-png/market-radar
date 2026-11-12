#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个交易日收盘后自动：刷新数据 → 生成面板 → 推送飞书。

云端由 GitHub Actions 定时调用（电脑关机也照跑），本地 Windows 计划任务只作手动/调试入口。
休市日（周末、法定节假日）不推当天内容；定时任务延迟到次日凌晨时，送达的是最近一个
已收盘交易日的面板（收盘超过 16 小时则不补发），不会在假期推旧数据。
也可以手动：
    python run_daily.py                 # 正常跑（只推最近一个已收盘交易日，超过 16 小时不补发）
    python run_daily.py --force         # 强制跑，忽略交易日、时间、已推送限制
    python run_daily.py --no-push       # 只生成面板，不推飞书
    python run_daily.py --skip-refresh  # 不联网，直接用缓存生成（调试用）

容错原则：
- 刷新失败不中断：用缓存数据出面板，并在推送里明确写“数据未更新”，同时不写状态基线，
  避免旧数据被当成今天的数据、误报“状态变化”。
- 数据没更新到最近交易日时照常推送带警告的面板，但不推进状态基线；任务按失败退出，
  GitHub Actions 失败会留下红色记录，本地重跑可补。
- 交易日历不可用时按“指数数据里有没有今天”判断开市；判断不了就当天不推，绝不强推。
- 推送失败不写“已推送”标记；同一条内容用固定 uuid 交给飞书去重，重试不会刷屏。
- 全程写日志到 logs/run_YYYYMM.log，出问题可回查。
"""
from __future__ import annotations

import argparse
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
CACHE = BASE / "cache"
LOGS = BASE / "logs"
CAL = CACHE / "trade_dates.csv"

# GitHub 定时任务实测常晚 5~10 小时（19:00 的班次经常北京时间凌晨才真正跑到），
# 所以不能按“现在几点”判断要不要推，只能按“要推的那份面板离收盘多久”判断。
# 收盘后 16 小时内算正常送达；超过就视为过期，不补发（周末白天不会收到周五的旧面板）。
PUSH_WINDOW_HOURS = 16


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trading_days() -> set[date]:
    df = pd.read_csv(CAL, encoding="utf-8-sig")
    return set(pd.to_datetime(df["trade_date"]).dt.date)


def refresh_calendar() -> bool:
    """交易日历快到期时更新；失败就用旧日历（旧日历覆盖到年底，够用）。"""
    try:
        days = trading_days()
    except Exception:
        days = set()
    if days and max(days) >= date.today() + timedelta(days=30):
        return True
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df.to_csv(CAL, index=False, encoding="utf-8-sig")
        log(f"交易日历已更新至 {df['trade_date'].max().date()}")
        return True
    except Exception as exc:
        log(f"交易日历刷新失败，沿用旧日历: {type(exc).__name__} {exc}")
        return False


def is_trading_day(day: date) -> bool | None:
    """True/False＝日历明确；None＝日历不可用或没覆盖今天，无法判断。
    休市日绝不强推：判断不了时由调用方在刷新后看指数数据是否更新到今天。"""
    try:
        days = trading_days()
    except Exception as exc:
        log(f"读取交易日历失败，无法判断是否交易日: {type(exc).__name__} {exc}")
        return None
    if not days or day < min(days) or day > max(days):
        return None
    return day in days


def latest_trading_day(day: date) -> date | None:
    """最近一个交易日（含当天）。"""
    try:
        days = trading_days()
    except Exception:
        return None
    past = [d for d in days if d <= day]
    return max(past) if past else None


def latest_completed_trading_day(now: datetime) -> date | None:
    """此刻数据源理应更新到的最近一个“已收盘”交易日：
    交易日 16:00 前算上一个交易日，交易日 16:00 后和非交易日算最近交易日。"""
    day = now.date()
    if is_trading_day(day) and now.hour < 16:
        day -= timedelta(days=1)
    return latest_trading_day(day)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日自动跑面板并推飞书")
    ap.add_argument("--force", action="store_true", help="忽略交易日/16:00/已推送限制")
    ap.add_argument("--no-push", action="store_true", help="只生成面板，不推飞书")
    ap.add_argument("--skip-refresh", action="store_true", help="不联网，直接用缓存")
    args = ap.parse_args()

    now = datetime.now()
    refresh_calendar()  # 先更新日历；失败会沿用旧日历
    today = now.date()
    target = latest_completed_trading_day(now)
    cal_unknown = False
    if not args.force:
        if target is None:
            # 日历没覆盖/取不到：先照常刷新，稍后用指数数据判断今天有没有开市
            cal_unknown = True
            log("交易日历不可用，无法提前判断是否交易日；刷新后按指数数据判断，判断不了就不推")
        else:
            # 不再问“今天是不是交易日、现在过没过 16:00”——GitHub 实际送达时间常在
            # 次日凌晨，那样问会把每一班都当成“未收盘”静默吞掉。只问一件事：
            # 要推的这份面板（最近一个已收盘交易日）离收盘多久。16 小时内算正常送达。
            close_at = pd.Timestamp(target) + pd.Timedelta(hours=15)
            age_hours = (pd.Timestamp(now) - close_at).total_seconds() / 3600
            if age_hours > PUSH_WINDOW_HOURS:
                log(f"最近交易日 {target} 收盘已 {age_hours:.1f} 小时，"
                    f"超过 {PUSH_WINDOW_HOURS} 小时送达窗口，不补发")
                return 0
            # 19:00 推过之后，凌晨/重试班次不必再联网刷新（数据滞后时此检查会放行）。
            # 去重键用 target 而不是 today：23:55 推过、00:05 重试不能重复发一遍。
            if not args.no_push:
                try:
                    import push_feishu
                    if push_feishu.already_pushed(pd.Timestamp(target), run_date=target, fresh=True):
                        log(f"{target} 的面板已推送过，跳过重复刷新")
                        return 0
                except Exception as exc:
                    log(f"检查推送标记失败，继续执行: {type(exc).__name__} {exc}")

    alerts: list[str] = []
    refresh_ok = True
    if not args.skip_refresh:
        try:
            import panel
            panel.refresh()
            log("数据刷新完成")
        except Exception as exc:
            refresh_ok = False
            alerts.append(
                f"自动刷新失败（{type(exc).__name__}），本次用缓存数据生成；"
                "数据可能不是今天的，请看下方“数据日期”。")
            log("数据刷新失败:\n" + traceback.format_exc())
    else:
        log("--skip-refresh：跳过数据刷新")

    import panel
    import panel_data
    try:
        # 状态基线等推送成功后再写：推送失败时下次重跑仍能带着“今天的变化”再推一遍，
        # 不会因为基线先推进去而把变化吞掉。
        pan = panel.run(
            no_state=not refresh_ok,
            extra_alerts=alerts,
            state_note="本次未更新状态基线（避免用旧数据误报“状态变化”）；数据恢复后会自动补上。",
            save_state=False,
        )
    except Exception:
        log("面板生成失败:\n" + traceback.format_exc())
        return 1
    log(f"面板已生成: {pan.date.date()} | {pan.headline}")

    # 日历不可用时，用指数数据反推今天有没有开市：数据没更新到今天，就无法证明今天开市。
    # 用户明确要求休市日不强推，这种情况按休市处理（宁可少推一天，也不在假期推旧面板）。
    if not args.force and cal_unknown and pan.date.date() < today:
        log(f"交易日历不可用，且指数数据停在 {pan.date.date()}，无法确认今天开市；按休市处理，不推送")
        return 0

    # 数据是否更新到“最近一个应收盘的交易日”；没有就不能推进状态基线，也不能标成新鲜
    expected = latest_completed_trading_day(now)
    data_fresh = refresh_ok and (expected is None or pan.date.date() >= expected)

    if args.no_push:
        if data_fresh:
            panel_data.save_state(pan)
        log("--no-push：未推送飞书")
        return 0

    import push_feishu
    try:
        status = push_feishu.push_panel(pan, force=args.force, fresh=data_fresh, run_date=target)
    except Exception:
        log("飞书推送异常:\n" + traceback.format_exc())
        return 1
    if status == "failed":
        log("飞书推送失败，未写“已推送”标记；下一次定时任务会自动重试")
        return 1
    if data_fresh:
        panel_data.save_state(pan)
    log("飞书推送完成" if status == "sent" else "今天已推送过，跳过重复发送")
    if not data_fresh:
        log(f"数据未更新到最近交易日 {expected}，已推送带警告的旧面板；"
            "本次按失败处理，下一次定时任务会自动重试")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # 兜底：连 main 内部都没包住的异常也要进日志，不能只丢在控制台
        log("未捕获异常:\n" + traceback.format_exc())
        raise SystemExit(1)
