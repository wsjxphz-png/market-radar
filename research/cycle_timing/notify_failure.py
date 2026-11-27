#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 失败时发飞书：读推送标记，说清“已发出/未确认发出”。

push_feishu 只有推送成功才写 cache/last_push.json；拿它和本次应推的
交易日对比，就能给出比“可能已经发出，也可能没有”更具体的结论。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import push_feishu

BASE = Path(__file__).parent
MARK = BASE / "cache" / "last_push.json"


def build_message(mark: dict, expected, url: str) -> str:
    panel_date = mark.get("panel_date")
    if expected is not None and panel_date == str(expected):
        return (f"⚠️ A股周期面板已发出（面板日期 {panel_date}），"
                f"但收尾步骤失败，面板不用补看。\n{url}")
    if panel_date:
        target = str(expected) if expected is not None else "无法判断（交易日历不可用）"
        return (f"⚠️ A股周期面板未确认发出：最近一次成功推送的是 {panel_date} 的面板，"
                f"本次应推 {target}。若刚收到的面板日期是 {panel_date}，"
                f"说明已发出（可能是旧数据）；否则没有发出。\n{url}")
    return f"⚠️ A股周期面板未发出：没有找到任何成功推送记录。\n{url}"


def main() -> int:
    try:
        mark = json.loads(MARK.read_text(encoding="utf-8"))
    except Exception:
        mark = {}
    try:
        import run_daily
        expected = run_daily.latest_completed_trading_day(datetime.now())
    except Exception:
        expected = None

    url = (f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    msg = build_message(mark, expected, url)
    print(msg)
    try:
        creds = push_feishu.load_credentials()
        push_feishu.send_text(msg, creds)
    except Exception as exc:
        print("失败通知发送失败:", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
