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
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import push
import render
import state

BASE = Path(__file__).parent
LOGS = BASE / "logs"
LOGS.mkdir(exist_ok=True)
MAX_DATA_AGE_DAYS = 5


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="美股多市场状态周报")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--shadow", action="store_true", help="推送带【干跑】前缀，不写状态/去重")
    ap.add_argument("--no-push", action="store_true", help="生成但不推送")
    ap.add_argument("--force", action="store_true", help="忽略本周已推/数据过旧")
    args = ap.parse_args()

    log("开始生成美股状态周报")
    try:
        a = state.analyze(force=args.force)
    except Exception as exc:
        log(f"分析失败: {type(exc).__name__} {exc}")
        return 1

    card = render.card(a, weekly=True)
    text = card["content"]
    if args.shadow:
        text = "**【干跑】只观察，不执行实盘动作**\n\n" + text
    print(text)

    data_date = pd.Timestamp(a.data_date).date()
    age = (date.today() - data_date).days
    if age > MAX_DATA_AGE_DAYS and not args.force:
        log(f"数据日期 {data_date} 距今 {age} 天，超过 {MAX_DATA_AGE_DAYS} 天，先不推送")
        return 0

    if args.dry_run or args.no_push:
        log("dry-run/no-push：不推送")
        return 0

    iso = data_date.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    if not args.shadow and push.already_pushed(week_key, kind="weekly") and not args.force:
        log(f"{week_key} 已推送过，跳过")
        return 0

    try:
        creds = push.load_credentials()
    except Exception as exc:
        log(f"凭据读取失败: {type(exc).__name__} {exc}")
        return 1

    title = ("【干跑】" if args.shadow else "") + card["title"]
    if push.send_card(title, text, card["template"], creds):
        if not args.shadow:
            push.mark_pushed(week_key, kind="weekly")
        log(f"推送完成，week_key={week_key}{'（干跑）' if args.shadow else ''}")
        return 0
    log("推送失败，不写标记，下次会重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
