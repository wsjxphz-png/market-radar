#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 A 股趋势周期日频面板。

    python panel.py                 # 用现有缓存生成（几秒）
    python panel.py --refresh       # 每日收盘后跑：抓行情/ETF份额→重建信号→出面板
    python panel.py --refresh --no-state   # 试跑，不写状态基线

输出 results/panel_YYYY-MM-DD.html 与同名 .txt；状态基线在 cache/panel_state.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "results"


def refresh() -> None:
    import build_signals
    import download_data
    import fetch_etf_shares

    download_data.update()
    fetch_etf_shares.update()
    build_signals.main()


def run(refresh_first: bool = False, no_state: bool = False,
        extra_alerts: list[str] | None = None, state_note: str | None = None,
        save_state: bool = True):
    """生成面板、写 HTML/TXT，按需写状态基线，返回 Panel。
    供 CLI 与 run_daily.py 共用，避免两套流程漂移。"""
    if refresh_first:
        refresh()

    import panel_data
    import panel_render

    pan = panel_data.build_panel()
    if extra_alerts:
        pan.alerts.extend(extra_alerts)
    if no_state:
        pan.alerts.append(state_note or "--no-state：本次未写入状态基线，未生成状态变化提醒。")
    OUT.mkdir(exist_ok=True)
    hp = OUT / f"panel_{pan.date.date()}.html"
    tp = OUT / f"panel_{pan.date.date()}.txt"
    hp.write_text(panel_render.render_html(pan), encoding="utf-8")
    tp.write_text(panel_render.render_text(pan), encoding="utf-8")
    if save_state and not no_state:
        panel_data.save_state(pan)
    print(f"HTML: {hp}")
    print(f"TXT : {tp}")
    print(pan.headline)
    return pan


def main() -> None:
    ap = argparse.ArgumentParser(description="A 股趋势周期日频面板")
    ap.add_argument("--refresh", action="store_true", help="先抓取行情/ETF份额/重建信号")
    ap.add_argument("--no-state", action="store_true", help="不写入状态基线")
    args = ap.parse_args()
    run(refresh_first=args.refresh, no_state=args.no_state)


if __name__ == "__main__":
    main()
