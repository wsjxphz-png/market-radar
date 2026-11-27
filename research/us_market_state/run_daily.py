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

import data
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
STATUS_FILE = CACHE / "last_run_status.json"
MAX_DATA_AGE_DAYS = 4


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"run_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _record_status(mode: str, *, pushed: bool, day_key: str | None = None,
                   reason: str = "") -> None:
    """记录本次运行的真实结果，供 workflow 的失败通知判断“到底发没发”。"""
    try:
        STATUS_FILE.write_text(json.dumps({
            "kind": "daily",
            "date": date.today().isoformat(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "day_key": day_key,
            "pushed": pushed,
            "reason": reason,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"状态文件写入失败: {type(exc).__name__} {exc}")


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

    mode = "shadow" if args.shadow else ("dry-run" if (args.dry_run or args.no_push) else "live")
    log("开始生成美股状态日报")

    # 美股休市：不发整卡，只发一句提示（按休市日期去重，重试班次不重复发）
    session_date = data._last_session_date()
    holiday = data._us_holiday_name(session_date)
    if holiday and not args.force:
        notice = (f"今天美股不开盘（{session_date.month}月{session_date.day}日，"
                  f"{holiday}），这次没有新行情；下一交易日见。")
        notice_key = f"closed-{session_date.isoformat()}"
        if (not args.shadow and not (args.dry_run or args.no_push)
                and push.already_pushed(notice_key, kind="notice")):
            log(f"{notice_key} 休市提示已发过，跳过")
            _record_status(mode, pushed=True, reason="休市提示已发出，跳过重复")
            return 0
        print(notice)
        if args.dry_run or args.no_push:
            log("dry-run/no-push：休市提示不推送")
            _record_status(mode, pushed=False,
                           reason=f"美股休市（{holiday}），dry-run 未推送")
            return 0
        try:
            creds = push.load_credentials()
        except Exception as exc:
            log(f"凭据读取失败: {type(exc).__name__} {exc}")
            _record_status(mode, pushed=False, reason="飞书凭据缺失")
            return 1
        text = f"【干跑】{notice}" if args.shadow else notice
        if push.send_text(text, creds):
            if not args.shadow:
                push.mark_pushed(notice_key, kind="notice")
            _record_status(mode, pushed=True,
                           reason=f"美股休市（{holiday}），已发休市提示")
            log(f"休市提示已发送（{holiday}）")
            return 0
        log("休市提示发送失败，不写标记，下次会重试")
        _record_status(mode, pushed=False, reason="休市提示发送失败")
        return 1

    try:
        a = state.analyze(force=args.force)
    except Exception as exc:
        log(f"分析失败: {type(exc).__name__} {exc}")
        _record_status(mode, pushed=False, reason=f"分析失败: {type(exc).__name__}")
        return 1

    data_date = pd.Timestamp(a.data_date).date()
    day_key = str(data_date)
    expected = data._expected_us_date().date()
    age = (expected - data_date).days

    # 当天已推送（包括重试班次）就直接收工，不再花 AI/搜索的钱重新生成
    if (not args.shadow and not (args.dry_run or args.no_push) and not args.force
            and push.already_pushed(day_key, kind="daily")):
        log(f"{day_key} 已推送过，跳过")
        _record_status(mode, pushed=True, day_key=day_key, reason="当天已推送，跳过")
        return 0

    changes = _state_changes(a)
    digest = sellside_radar.fetch_digest(weekly=False)
    log(f"市场声音: {digest.status} {digest.reason}".rstrip())
    card = render.card(a, digest=digest, weekly=False)
    text = card["content"]
    if changes:
        text = "**🔄 状态变化：**" + "；".join(changes) + "\n\n" + text

    if age > 0:
        text = (f"⚠️ **数据只更新到 {data_date}（应到 {expected}），可能不是最新。**\n\n"
                + text)

    print(text)

    if age > MAX_DATA_AGE_DAYS and not args.force:
        log(f"数据停在 {data_date}，比应到交易日 {expected} 早 {age} 天，超过 "
            f"{MAX_DATA_AGE_DAYS} 天，先不推送")
        _record_status(mode, pushed=False, day_key=day_key,
                       reason=f"数据停在 {data_date}，比应到交易日 {expected} 早 {age} 天")
        return 0

    if args.dry_run or args.no_push:
        log("dry-run/no-push：不推送")
        _record_status(mode, pushed=False, day_key=day_key, reason="dry-run/no-push")
        return 0

    try:
        creds = push.load_credentials()
    except Exception as exc:
        log(f"凭据读取失败: {type(exc).__name__} {exc}")
        _record_status(mode, pushed=False, day_key=day_key, reason="飞书凭据缺失")
        return 1

    title = card["title"]
    if args.shadow:
        title = "【干跑】" + title
        text = "【干跑】" + text
    log(f"卡片标题：{title}")
    if push.send_card(title, text, card["template"], creds):
        if not args.shadow:
            push.mark_pushed(day_key, kind="daily")
            _record_status(mode, pushed=True, day_key=day_key, reason="推送成功")
            try:
                _write_stage_map(a)
            except Exception as exc:
                log(f"状态基线写入失败（不影响已发出的卡片）: {type(exc).__name__} {exc}")
        else:
            _record_status(mode, pushed=True, day_key=day_key, reason="shadow 干跑卡片已发出")
        log(f"推送完成，day_key={day_key}{'（干跑）' if args.shadow else ''}")
        return 0
    log("推送失败，不写标记，下次会重试")
    _record_status(mode, pushed=False, day_key=day_key, reason="飞书推送失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
