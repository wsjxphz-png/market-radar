#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 失败时发飞书：说清“到底发没发、哪一步失败”。

run_daily/run_weekly 每次退出都会写 cache/last_run_status.json；
这里读它 + workflow 传入的各步骤 outcome，生成明确结论，不再用
“可能已经发出，也可能没有”这种没法行动的模糊话术。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import push

BASE = Path(__file__).parent
STATUS_FILE = BASE / "cache" / "last_run_status.json"

KIND_LABELS = {"daily": "日报", "weekly": "周报"}

STEP_NAMES = {
    "daily": {
        "DEPS_OUTCOME": "安装依赖",
        "CALENDAR_OUTCOME": "休市检查",
        "SMOKE_OUTCOME": "数据源冒烟测试",
        "TESTS_OUTCOME": "状态机测试",
        "REPORT_OUTCOME": "生成并推送日报",
        "SAVE_OUTCOME": "保存状态",
    },
    "weekly": {
        "DEPS_OUTCOME": "安装依赖",
        "SMOKE_OUTCOME": "数据源冒烟测试",
        "TESTS_OUTCOME": "状态机测试",
        "REPORT_OUTCOME": "生成并推送周报",
        "SAVE_OUTCOME": "保存推送标记",
    },
}


def failed_steps(env: dict[str, str], kind: str) -> str:
    names = [name for key, name in STEP_NAMES[kind].items()
             if env.get(key) == "failure"]
    return "、".join(names) or "未知步骤"


def build_message(kind: str, status: dict, failed: str, url: str,
                  today: str | None = None) -> str:
    label = KIND_LABELS.get(kind, kind)
    today = today or date.today().isoformat()
    if status.get("kind") == kind and status.get("date") == today:
        if status.get("pushed"):
            day_key = status.get("day_key")
            when = (f"（{'数据日' if kind == 'daily' else '数据周'} {day_key}）"
                    if day_key else "（休市提示）")
            return (f"⚠️ 美股状态{label}已发出{when}，"
                    f"但「{failed}」失败，卡片不用补看。\n{url}")
        return (f"⚠️ 美股状态{label}未发出：{status.get('reason') or '未知原因'}"
                f"（失败步骤：{failed}）。\n{url}")
    return (f"⚠️ 美股状态{label}今天没有生成，也没有发出："
            f"「{failed}」失败，生成步骤未运行。\n{url}")


def main() -> int:
    ap = argparse.ArgumentParser(description="workflow 失败通知")
    ap.add_argument("--kind", choices=tuple(KIND_LABELS), required=True)
    args = ap.parse_args()

    status: dict = {}
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

    url = (f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    msg = build_message(args.kind, status, failed_steps(os.environ, args.kind), url)
    print(msg)
    try:
        creds = push.load_credentials()
        push.send_text(msg, creds)
    except Exception as exc:
        print("失败通知发送失败:", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
