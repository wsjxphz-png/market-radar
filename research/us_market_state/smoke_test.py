#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源冒烟测试：只抓 SPY + VIX，确认能拿到最近交易日的数据。

放在 workflow 里，在生成报告前跑；失败就让 job 红，避免“数据源悄悄变了，
报告还在用旧数据”。
"""
from __future__ import annotations

import sys

import data


def main() -> int:
    expected = data._expected_us_date()
    spy = data.fetch_us("SPY", force=True)
    vix = data.fetch_fred("VIXCLS", force=True)
    print(f"应到交易日 {expected.date()}（已按 NYSE 休市日历扣除节假日）")
    print(f"SPY {spy.index[-1].date()} {spy.iloc[-1]:.2f}（{len(spy)} 行）")
    print(f"VIX {vix.index[-1].date()} {vix.iloc[-1]:.2f}")
    if len(spy) < 200:
        raise SystemExit("SPY 历史不足 200 行")
    if spy.index[-1] < expected:
        raise SystemExit(f"SPY 数据没有更新到应到交易日 {expected.date()}（最后 {spy.index[-1].date()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
