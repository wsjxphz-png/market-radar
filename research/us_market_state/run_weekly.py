#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每周生成美股状态周报并推飞书。

用法：
  python run_weekly.py --dry-run     # 只打印，不推送
  python run_weekly.py --no-push     # 生成但不推送
  python run_weekly.py               # 生成并推送（同一周只推一次）
  python run_weekly.py --force       # 忽略“本周已推”和“数据太旧”
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import data
import push
import render
import sellside_radar
import state

BASE = Path(__file__).parent
CACHE = BASE / "cache"
LOGS = BASE / "logs"
CACHE.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)
STATUS_FILE = CACHE / "last_run_status.json"
MAX_DATA_AGE_DAYS = 5


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _record_status(mode: str, *, pushed: bool, week_key: str | None = None,
                   reason: str = "") -> None:
    """记录本次运行的真实结果，供 workflow 的失败通知判断“到底发没发”。"""
    try:
        STATUS_FILE.write_text(json.dumps({
            "kind": "weekly",
            "date": date.today().isoformat(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "day_key": week_key,
            "pushed": pushed,
            "reason": reason,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"状态文件写入失败: {type(exc).__name__} {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="美股多市场状态周报")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--shadow", action="store_true", help="推送带【干跑】前缀，不写状态/去重")
    ap.add_argument("--no-push", action="store_true", help="生成但不推送")
    ap.add_argument("--force", action="store_true", help="忽略本周已推/数据过旧")
    args = ap.parse_args()

    mode = "shadow" if args.shadow else ("dry-run" if (args.dry_run or args.no_push) else "live")
    log("开始生成美股状态周报")
    try:
        a = state.analyze(force=args.force)
    except Exception as exc:
        log(f"分析失败: {type(exc).__name__} {exc}")
        _record_status(mode, pushed=False, reason=f"分析失败: {type(exc).__name__}")
        return 1

    data_date = pd.Timestamp(a.data_date).date()
    iso = data_date.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    expected = data._expected_us_date().date()
    age = (expected - data_date).days

    # 本周已推送（包括重试班次）就直接收工，不再花 AI/搜索的钱重新生成
    if (not args.shadow and not (args.dry_run or args.no_push) and not args.force
            and push.already_pushed(week_key, kind="weekly")):
        log(f"{week_key} 已推送过，跳过")
        _record_status(mode, pushed=True, week_key=week_key, reason="本周已推送，跳过")
        return 0

    digest = sellside_radar.fetch_digest(weekly=True)
    log(f"市场声音: {digest.status} {digest.reason}".rstrip())
    card = render.card(a, digest=digest, weekly=True)
    text = card["content"]
    print(text)

    if age > MAX_DATA_AGE_DAYS and not args.force:
        log(f"数据停在 {data_date}，比应到交易日 {expected} 早 {age} 天，超过 "
            f"{MAX_DATA_AGE_DAYS} 天，先不推送")
        _record_status(mode, pushed=False, week_key=week_key,
                       reason=f"数据停在 {data_date}，比应到交易日 {expected} 早 {age} 天")
        return 0

    if args.dry_run or args.no_push:
        log("dry-run/no-push：不推送")
        _record_status(mode, pushed=False, week_key=week_key, reason="dry-run/no-push")
        return 0

    try:
        creds = push.load_credentials()
    except Exception as exc:
        log(f"凭据读取失败: {type(exc).__name__} {exc}")
        _record_status(mode, pushed=False, week_key=week_key, reason="飞书凭据缺失")
        return 1

    title = card["title"]
    log(f"卡片标题：{title}{'（干跑）' if args.shadow else ''}")
    if push.send_card(title, text, card["template"], creds):
        if not args.shadow:
            push.mark_pushed(week_key, kind="weekly")
            _record_status(mode, pushed=True, week_key=week_key, reason="推送成功")
        else:
            _record_status(mode, pushed=True, week_key=week_key, reason="shadow 干跑卡片已发出")
        log(f"推送完成，week_key={week_key}{'（干跑）' if args.shadow else ''}")
        return 0
    log("推送失败，不写标记，下次会重试")
    _record_status(mode, pushed=False, week_key=week_key, reason="飞书推送失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
