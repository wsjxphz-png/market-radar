#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每天美股收盘后推送一张短卡（取代原来的美股 AI 日报）。

- 短卡：四个市场状态 + 风险积累趋势 + 七姐妹强弱 + 今天怎么做。
- 状态变化：如果某个市场从 Stage 1→2、2→3 等，会在开头标出来。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import push
import render
import sellside_radar
import state
from config import STAGE_LABELS

BASE = Path(__file__).parent
CACHE = BASE / "cache"
LOGS = BASE / "logs"
CACHE.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

STATE_FILE = CACHE / "last_state.json"
MAX_DATA_AGE_DAYS = 4


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_stage_map() -> dict[str, int]:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {k: int(v["stage"]) for k, v in raw.items()}
    except Exception:
        return {}


def _state_changes(a: state.Analysis) -> list[str]:
    old = _load_stage_map()
    out: list[str] = []
    for s in a.states:
        prev = old.get(s.market.key)
        if prev is not None and prev != s.stage:
            out.append(f"{s.market.name}：{STAGE_LABELS.get(prev, '?')} → {s.label}")
    return out


def _write_stage_map(a: state.Analysis) -> None:
    data = {s.market.key: {"stage": s.stage, "label": s.label} for s in a.states}
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="美股状态日报（短卡）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--shadow", action="store_true", help="推送带【干跑】前缀，不写状态/去重")
    ap.add_argument("--no-push", action="store_true", help="生成但不推送")
    ap.add_argument("--force", action="store_true", help="忽略数据过旧/当天已推")
    args = ap.parse_args()

    log("开始生成美股状态日报")
    try:
        a = state.analyze(force=args.force)
    except Exception as exc:
        log(f"分析失败: {type(exc).__name__} {exc}")
        return 1

    changes = _state_changes(a)
    digest = sellside_radar.fetch_digest(weekly=False)
    log(f"市场声音: {digest.status} {digest.reason}".rstrip())
    card = render.card(a, digest=digest, weekly=False)
    text = card["content"]
    if changes:
        text = "**🔄 状态变化：**" + "；".join(changes) + "\n\n" + text

    data_date = pd.Timestamp(a.data_date).date()
    age = (date.today() - data_date).days
    if age > 2:
        text = f"⚠️ **数据只更新到 {data_date}，可能不是最新。**\n\n" + text

    print(text)

    if age > MAX_DATA_AGE_DAYS and not args.force:
        log(f"数据日期 {data_date} 距今 {age} 天，超过 {MAX_DATA_AGE_DAYS} 天，先不推送")
        return 0

    if args.dry_run or args.no_push:
        log("dry-run/no-push：不推送")
        return 0

    day_key = str(data_date)
    if not args.shadow and push.already_pushed(day_key, kind="daily") and not args.force:
        log(f"{day_key} 已推送过，跳过")
        return 0

    try:
        creds = push.load_credentials()
    except Exception as exc:
        log(f"凭据读取失败: {type(exc).__name__} {exc}")
        return 1

    title = card["title"]
    log(f"卡片标题：{title}{'（干跑）' if args.shadow else ''}")
    if push.send_card(title, text, card["template"], creds):
        if not args.shadow:
            push.mark_pushed(day_key, kind="daily")
            _write_stage_map(a)
        log(f"推送完成，day_key={day_key}{'（干跑）' if args.shadow else ''}")
        return 0
    log("推送失败，不写标记，下次会重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
