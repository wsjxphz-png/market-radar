#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断这次该有的美股 session 是否休市，供 workflow 在休市日跳过数据源检查。

输出 GITHUB_OUTPUT: closed=true/false
"""
from __future__ import annotations

import os

import data


def main() -> int:
    d = data._last_session_date()
    holiday = data._us_holiday_name(d)
    print(f"{d.isoformat()} {'休市：' + holiday if holiday else '交易日'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"closed={'true' if holiday else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
