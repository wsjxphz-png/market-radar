#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分市场回测：月线 10 月均线规则 vs 买入持有。

用途：给周报里的“历史参考”提供数字，不是预测。
输出：results/backtest.json + 控制台表格。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import data
from config import MARKETS

BASE = Path(__file__).parent
RESULTS = BASE / "results"
RESULTS.mkdir(exist_ok=True)


def metrics(returns: pd.Series) -> dict:
    eq = (1 + returns).cumprod()
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else float("nan")
    max_dd = (eq / eq.cummax() - 1).min()
    return {"cagr": float(cagr), "max_drawdown": float(max_dd), "years": float(years)}


def run_one(symbol: str) -> dict:
    close = data.fetch_us(symbol)
    ret_all = close.pct_change()
    if (ret_all < -0.35).any():
        bad = ret_all.idxmin()
        raise ValueError(
            f"历史数据异常：{bad.date()} 单日 {ret_all.min():.1%}，疑似拆股/数据错误，跳过回测"
        )
    monthly = close.resample("ME").last().dropna()
    # 当前月份还没走完：resample 会把它标成月末（未来日期），必须剔除
    if len(monthly) and monthly.index[-1] > close.index[-1]:
        monthly = monthly.iloc[:-1]
    frame = pd.DataFrame({"close": monthly})
    frame["ma10"] = frame["close"].rolling(10).mean()
    frame["signal"] = (frame["close"] > frame["ma10"]).astype(int)
    frame["ret"] = frame["close"].pct_change()
    frame["strategy"] = frame["signal"].shift(1) * frame["ret"]
    frame = frame.dropna()

    bh = metrics(frame["ret"])
    st = metrics(frame["strategy"])
    switches = int((frame["signal"].diff().abs() > 0).sum())
    return {
        "symbol": symbol,
        "start": str(frame.index[0].date()),
        "end": str(frame.index[-1].date()),
        "buy_hold": bh,
        "trend_10m": {**st, "switches": switches, "in_market": float(frame["signal"].mean())},
    }


def main() -> None:
    rows = []
    for m in MARKETS:
        try:
            rows.append(run_one(m.symbol))
        except Exception as exc:
            print(f"{m.symbol} 回测失败: {type(exc).__name__} {exc}")

    out = {"generated_at": pd.Timestamp.now().isoformat(timespec="seconds"), "markets": rows}
    (RESULTS / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'symbol':8s} {'period':25s} {'bh CAGR':>8s} {'bh MDD':>8s} {'10M CAGR':>9s} {'10M MDD':>8s} {'sw':>4s} {'inMkt':>6s}")
    for r in rows:
        bh, st = r["buy_hold"], r["trend_10m"]
        period = f"{r['start']}~{r['end']}"
        print(f"{r['symbol']:8s} {period:25s} {bh['cagr']:8.1%} {bh['max_drawdown']:8.1%} "
              f"{st['cagr']:9.1%} {st['max_drawdown']:8.1%} {st['switches']:4d} {st['in_market']:6.0%}")


if __name__ == "__main__":
    main()
