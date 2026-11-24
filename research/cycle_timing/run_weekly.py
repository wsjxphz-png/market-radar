#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动推送 A股周期周报：完整解释 + 本周涨跌（定时已停用，不在日卡外额外推送）。

    python run_weekly.py              # 手动跑（刷新数据 → 生成周报 → 推飞书）
    python run_weekly.py --dry-run    # 只打印，不发飞书
    python run_weekly.py --force      # 忽略“本周已推送”标记
    python run_weekly.py --skip-refresh  # 不联网，直接用缓存生成

与日卡的分工：日卡只留决策项（短卡），完整版每天在 results/panel_*.html；
周报是同一个完整版加“本周涨跌”，只在需要时手动触发。
周报不写状态基线——基线由每日任务维护，避免把日卡的状态变化吞掉。
"""
from __future__ import annotations

import argparse
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LOGS = BASE / "logs"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="每周六推送 A股周期周报")
    ap.add_argument("--dry-run", action="store_true", help="只打印周报，不发送")
    ap.add_argument("--force", action="store_true", help="忽略“本周已推送”标记")
    ap.add_argument("--skip-refresh", action="store_true", help="不联网，直接用缓存")
    args = ap.parse_args()

    refresh_ok = True
    if not args.skip_refresh:
        try:
            import panel
            panel.refresh()
            log("数据刷新完成")
        except Exception:
            refresh_ok = False
            log("数据刷新失败，用缓存生成周报:\n" + traceback.format_exc())

    import panel
    import push_feishu
    try:
        # 不写状态基线：save_state=False；no_state=False 保留“和上次比”的变化提醒
        pan = panel.run(save_state=False)
    except Exception:
        log("周报生成失败:\n" + traceback.format_exc())
        return 1
    log(f"周报已生成: {pan.date.date()} | {pan.headline}")

    if args.dry_run:
        print(push_feishu.build_weekly_message(pan))
        return 0

    try:
        status = push_feishu.push_panel(
            pan, force=args.force, fresh=refresh_ok,
            run_date=pan.date.date(), kind="weekly")
    except Exception:
        log("飞书推送异常:\n" + traceback.format_exc())
        return 1
    if status == "failed":
        log("飞书推送失败，未写“已推送”标记；下次可重试")
        return 1
    log("周报推送完成" if status == "sent" else "本周周报已推送过，跳过")
    if not refresh_ok:
        log("本次数据未刷新，已推送带警告的周报；按失败处理，便于重试")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        log("未捕获异常:\n" + traceback.format_exc())
        raise SystemExit(1)
