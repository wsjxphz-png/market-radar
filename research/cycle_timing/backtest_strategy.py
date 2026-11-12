#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略级回测：信号在收盘生成，次日开盘执行，单边成本 5bp。

关键约束：
- 所有信号只用到当日收盘为止的数据；两融已在 build_signals 里滞后 1 日。
- 目标仓位在收盘确定，次日开盘成交；持仓赚取 open[t+1] -> open[t+2] 的收益。
- 不优化参数，阈值 0.4/0.6/0.8 是事前设定；结果同时给出分期表现，避免只报全样本。
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
SIG = BASE / "signals"
OUT = BASE / "results"
OUT.mkdir(exist_ok=True)

COST = 0.0005
EPISODES = [
    ("2015 股灾", "2015-06-08", "2016-01-28"),
    ("2018 熊市", "2018-01-24", "2018-12-27"),
    ("2021-22 熊市", "2021-02-18", "2022-10-31"),
    ("2024 急跌", "2024-10-08", "2025-01-13"),
]


def load(name):
    s = pd.read_csv(SIG / f"signals_{name}.csv", parse_dates=["date"])
    s = s.sort_values("date").reset_index(drop=True)
    s["ma20_up"] = s["ma20"] > s["ma20"].shift(5)
    return s


def confirm(cond, k):
    if k <= 1:
        return cond.astype(bool)
    return cond.astype(int).rolling(k, min_periods=k).sum().eq(k)


def state_machine(exit_raw, entry_raw, k_exit=3, k_entry=3, start=1.0):
    ex = confirm(exit_raw, k_exit)
    en = confirm(entry_raw, k_entry)
    w = np.empty(len(ex))
    cur = start
    for i in range(len(ex)):
        if cur > 0 and bool(ex.iloc[i]):
            cur = 0.0
        elif cur == 0 and bool(en.iloc[i]):
            cur = 1.0
        w[i] = cur
    return pd.Series(w, index=ex.index)


def make_strategies(s):
    top = s["top_score"]
    bot = s["bottom_score"]
    close = s["close"]
    ma200 = s["ma200"]
    above = close > ma200
    above3 = confirm(above, 3)
    out = {
        "hold": pd.Series(1.0, index=s.index),
        "ma200": above.astype(float),
        "ma200_conf3": above3.astype(float),
        "top60_swing": state_machine(top >= 0.6, top < 0.4, 3, 3),
        "top80_swing": state_machine(top >= 0.8, top < 0.5, 3, 3),
        "ma200_top60": (above & (top < 0.6)).astype(float),
        "ma200_top60_conf3": (above3 & (top < 0.6)).astype(float),
        "ma200_top80": (above & (top < 0.8)).astype(float),
        "staged": pd.Series(
            np.where(~above, 0.0, np.where(top >= 0.6, 0.5, 1.0)), index=s.index),
        # 退出：顶部信号或跌破 200 日线；再入场：趋势修复且顶部风险已释放
        "top60_ma200_reentry": state_machine((top >= 0.6) | (~above), above, 3, 5),
        "top60_ma200_topnormal": state_machine(
            (top >= 0.6) | (~above3), above3 & (top < 0.4), 3, 5),
        # 再入场：极端底部 + 20 日线修复（不等 200 日线）
        "top60_bottom_reentry": state_machine(
            top >= 0.6, (bot >= 0.8) & (close > s["ma20"]) & s["ma20_up"], 3, 3),
    }
    return out


def metrics(s, w, cost=COST):
    r = s["open"].shift(-1) / s["open"] - 1
    e = w.shift(1)                       # e[t] = 在 open[t] 建好的仓位
    turn = (e - e.shift(1)).abs().fillna(0)
    daily = pd.DataFrame({"date": s["date"], "ret": e * r - cost * turn}).dropna()
    ret = daily.set_index("date")["ret"]
    nav = (1 + ret).cumprod()
    n = len(ret)
    cagr = nav.iloc[-1] ** (252 / n) - 1
    mdd = (nav / nav.cummax() - 1).min()
    sd = ret.std()
    sharpe = ret.mean() / sd * np.sqrt(252) if sd > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan

    # 退出质量：执行层面的清仓（1->0），看其后 120 日指数涨跌
    ex = (e.shift(1) > 0.01) & (e <= 0.01)
    ex_dates = s.index[ex.fillna(False)]
    fwd = []
    for i in ex_dates:
        j = min(i + 120, len(s) - 1)
        fwd.append(s["open"].iloc[j] / s["open"].iloc[i] - 1)
    fwd = pd.Series(fwd, dtype=float)
    return {
        "CAGR": cagr, "MaxDD": mdd, "Sharpe": sharpe, "Calmar": calmar,
        "Exposure": e.mean(), "Turns": int((turn > 0.01).sum()),
        "Turnover": turn.sum(), "Exits": int(ex.sum()),
        "FalseExit": float((fwd > 0.10).mean()) if len(fwd) else np.nan,
        "GoodExit": float((fwd < -0.10).mean()) if len(fwd) else np.nan,
    }, ret, nav


def window_ret(ret, start, end):
    m = (ret.index >= pd.Timestamp(start)) & (ret.index <= pd.Timestamp(end))
    seg = ret[m]
    if len(seg) == 0:
        return np.nan
    return float((1 + seg).prod() - 1)


def fmt_pct(x):
    return "  nan" if pd.isna(x) else f"{x:6.1%}"


def main():
    lines = []
    rows = []
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = load(name)
        start = s[["ma200", "top_score", "bottom_score"]].dropna().index.min()
        s = s.loc[start:].reset_index(drop=True)
        s["top_score"] = s["top_score"].fillna(0.0)
        s["bottom_score"] = s["bottom_score"].fillna(0.0)
        # 重新计算 ma20_up（切片后不影响，但保持一致）
        s["ma20_up"] = s["ma20"] > s["ma20"].shift(5)
        strats = make_strategies(s)
        lines.append(f"\n{'='*100}")
        lines.append(f"{name.upper()}  回测区间 {s.date.min().date()} -> {s.date.max().date()}  "
                     f"({len(s)} 个交易日，单边成本 {COST:.2%})")
        lines.append(f"指数同期: {fmt_pct(s.close.iloc[-1]/s.close.iloc[0]-1)}  "
                     f"最大回撤 {fmt_pct((s.close/s.close.cummax()-1).min())}")
        lines.append("")
        header = (f"{'策略':<24}{'CAGR':>8}{'MaxDD':>8}{'Sharpe':>8}{'Calmar':>8}"
                  f"{'Exposure':>9}{'Turns':>7}{'Exits':>7}{'FalseExit':>10}{'GoodExit':>9}")
        lines.append(header)
        lines.append("-" * len(header))
        for key, w in strats.items():
            m, ret, nav = metrics(s, w)
            rows.append({"index": name, "strategy": key, **m,
                         "start": s.date.min().date(), "end": s.date.max().date()})
            lines.append(
                f"{key:<24}{fmt_pct(m['CAGR']):>8}{fmt_pct(m['MaxDD']):>8}"
                f"{m['Sharpe']:>8.2f}{m['Calmar']:>8.2f}{m['Exposure']:>9.0%}"
                f"{m['Turns']:>7d}{m['Exits']:>7d}"
                f"{fmt_pct(m['FalseExit']):>10}{fmt_pct(m['GoodExit']):>9}")
            if key in ("hold", "ma200", "ma200_top60", "staged",
                       "top60_ma200_reentry", "top60_ma200_topnormal",
                       "top60_bottom_reentry"):
                ep = []
                for label, a, b in EPISODES:
                    v = window_ret(ret, a, b)
                    if not pd.isna(v):
                        ep.append(f"{label} {v:+.1%}")
                lines.append(f"{'':<24}" + "  |  ".join(ep))

        # 分期：每 4 年一段
        lines.append("\n[分期年化] 每段内的 CAGR / MaxDD")
        spans = [("2008-2012", "2008-01-01", "2012-12-31"),
                 ("2013-2017", "2013-01-01", "2017-12-31"),
                 ("2018-2022", "2018-01-01", "2022-12-31"),
                 ("2023-2026", "2023-01-01", "2026-12-31")]
        lines.append(f"{'策略':<24}" + "".join(f"{a:>18}" for a, _, _ in spans))
        for key, w in strats.items():
            m, ret, nav = metrics(s, w)
            cells = []
            for _, a, b in spans:
                seg = ret[(ret.index >= pd.Timestamp(a)) & (ret.index <= pd.Timestamp(b))]
                if len(seg) < 60:
                    cells.append(f"{'-':>18}")
                else:
                    c = (1 + seg).prod() ** (252 / len(seg)) - 1
                    navs = (1 + seg).cumprod()
                    d = (navs / navs.cummax() - 1).min()
                    cells.append(f"{c:>8.1%}/{d:>7.1%}")
            lines.append(f"{key:<24}" + "".join(cells))

        # 持仓结构：信号触发日的平均前瞻收益（仅诊断）
        lines.append("\n[诊断] 信号日 vs 非信号日的指数 120 日前瞻收益（重叠，仅看方向）")
        f120 = s["close"].shift(-120) / s["close"] - 1
        for label, mask in [
            ("top>=0.6", s.top_score >= 0.6), ("top<0.6", s.top_score < 0.6),
            ("bottom>=0.8", s.bottom_score >= 0.8), ("bottom<0.8", s.bottom_score < 0.8),
        ]:
            v = f120[mask].dropna()
            lines.append(f"  {label:<12} n={len(v):>4}  mean={v.mean():>7.2%}  "
                         f"median={v.median():>7.2%}  P(<-10%)={(v < -0.10).mean():>6.1%}")
        print("\n".join(lines[-14:]))

    report = "\n".join(lines)
    (OUT / "backtest_report.txt").write_text(report, encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT / "backtest_metrics.csv", index=False, encoding="utf-8-sig")
    print(f"\nwritten {OUT / 'backtest_report.txt'}")
    print(f"written {OUT / 'backtest_metrics.csv'}")


if __name__ == "__main__":
    main()
