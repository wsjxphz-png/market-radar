#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""稳健性检查：参数敏感性、去掉单次危机、成本、以及板块组合层面的趋势规则。"""
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_strategy import (COST, EPISODES, confirm, load, make_strategies,
                               metrics, state_machine)

BASE = Path(__file__).parent
OUT = BASE / "results"


def prep(name):
    s = load(name)
    start = s[["ma200", "top_score", "bottom_score"]].dropna().index.min()
    s = s.loc[start:].reset_index(drop=True)
    s["top_score"] = s["top_score"].fillna(0.0)
    s["bottom_score"] = s["bottom_score"].fillna(0.0)
    s["ma20_up"] = s["ma20"] > s["ma20"].shift(5)
    return s


def topnormal(s, t_exit, t_entry):
    above = confirm(s["close"] > s["ma200"], 3)
    return state_machine((s["top_score"] >= t_exit) | (~above),
                         above & (s["top_score"] < t_entry), 3, 5)


def grid():
    lines = ["\n[参数敏感性] topnormal 结构：退出 top>=X 或跌破200日线；"
             "再入场 站上200日线5日 且 top<Y（3日确认）"]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        lines.append(f"\n{name.upper()}")
        lines.append(f"{'X\\Y':<6}" + "".join(f"{y:>16}" for y in [0.2, 0.3, 0.4, 0.5]))
        for x in [0.5, 0.6, 0.7, 0.8]:
            cells = []
            for y in [0.2, 0.3, 0.4, 0.5]:
                w = topnormal(s, x, y)
                m, _, _ = metrics(s, w)
                cells.append(f"{m['CAGR']:>7.1%}/{m['MaxDD']:>7.1%}")
            lines.append(f"{x:<6.1f}" + "".join(f"{c:>16}" for c in cells))
    return "\n".join(lines)


def leave_one_out():
    lines = ["\n[去掉单次危机] topnormal(0.6/0.4) 全样本 vs 剔除该窗口"]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        w = topnormal(s, 0.6, 0.4)
        m, ret, _ = metrics(s, w)
        lines.append(f"\n{name.upper()}  全样本 CAGR {m['CAGR']:.1%}  MaxDD {m['MaxDD']:.1%}")
        for label, a, b in EPISODES:
            mask = (ret.index >= pd.Timestamp(a)) & (ret.index <= pd.Timestamp(b))
            if mask.sum() < 10:
                continue
            r2 = ret[~mask]
            c = (1 + r2).prod() ** (252 / len(r2)) - 1
            lines.append(f"  剔除 {label:<12} 剩余 CAGR {c:.1%}  "
                         f"(该窗口策略收益 {(1+ret[mask]).prod()-1:+.1%})")
    return "\n".join(lines)


def cost_sens():
    lines = ["\n[成本敏感性] topnormal(0.6/0.4)"]
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        w = topnormal(s, 0.6, 0.4)
        vals = []
        for c in [0.0005, 0.0010, 0.0020]:
            m, _, _ = metrics(s, w, cost=c)
            vals.append(f"{c*1e4:.0f}bp: CAGR {m['CAGR']:.1%} MaxDD {m['MaxDD']:.1%}")
        lines.append(f"  {name.upper():<6} " + " | ".join(vals))
    return "\n".join(lines)


def sector_test():
    files = sorted((BASE / "cache" / "sectors").glob("*.csv"))
    closes = {}
    for f in files:
        df = pd.read_csv(f, parse_dates=["date"])
        df = df.dropna(subset=["close"]).sort_values("date")
        if len(df) < 500:
            continue
        closes[f.stem] = df.set_index("date")["close"]
    px = pd.DataFrame(closes).sort_index()
    ret = px.pct_change(fill_method=None)
    ma200 = px.rolling(200).mean()
    avail = ma200.notna()
    above = (px > ma200) & avail

    hs = prep("hs300")
    top = (hs.set_index("date")["top_score"].reindex(px.index).ffill().fillna(0.0))

    w_hold = avail.astype(float)
    w_ma = above.astype(float)
    w_top = above.astype(float).mul((top < 0.6).astype(float), axis=0)
    mkt_state = state_machine(top >= 0.6, top < 0.4, 1, 1, start=0.0)
    w_market = avail.astype(float).mul(mkt_state, axis=0)

    # 每个板块独立状态机：跌破自身200日线或市场 top>=0.6 退出；
    # 站上自身200日线且市场 top<0.4 再入场
    wn = pd.DataFrame(index=px.index, columns=px.columns, dtype=float)
    for c in px.columns:
        ex = (~above[c].fillna(False)) | (top >= 0.6)
        en = above[c].fillna(False) & (top < 0.4)
        wn[c] = state_machine(ex, en, 1, 1, start=0.0).astype(float)

    def port(w, cost=COST):
        denom = w.sum(axis=1).replace(0, np.nan)
        norm = w.div(denom, axis=0).fillna(0.0)
        e = norm.shift(1)
        daily = (e * ret).sum(axis=1)
        turn = (e - e.shift(1)).abs().sum(axis=1).fillna(0)
        daily = (daily - cost * turn).dropna()
        # 去掉极端早期样本，统一从 2013-01 开始
        daily = daily[daily.index >= "2013-01-01"]
        nav = (1 + daily).cumprod()
        n = len(daily)
        cagr = nav.iloc[-1] ** (252 / n) - 1
        mdd = (nav / nav.cummax() - 1).min()
        sd = daily.std()
        exp = ((w > 0).sum(axis=1) / avail.sum(axis=1).replace(0, np.nan))
        return dict(CAGR=cagr, MaxDD=mdd, Sharpe=daily.mean() / sd * np.sqrt(252),
                    Exposure=exp.loc[daily.index].mean(), Turnover=turn.sum())

    lines = [f"\n[板块组合] {px.shape[1]} 个同花顺行业指数，等权，2013-01 起，单边 5bp"]
    lines.append(f"{'规则':<34}{'CAGR':>8}{'MaxDD':>8}{'Sharpe':>8}{'Exposure':>9}{'Turnover':>10}")
    for label, w in [
        ("买入持有", w_hold),
        ("各自站上200日线", w_ma),
        ("纯市场top退出/top<0.4再入", w_market),
        ("200日线 + 市场top<0.6", w_top),
        ("200日线 + top退出/top<0.4再入", wn),
    ]:
        m = port(w)
        lines.append(f"{label:<34}{m['CAGR']:>8.1%}{m['MaxDD']:>8.1%}"
                     f"{m['Sharpe']:>8.2f}{m['Exposure']:>9.0%}{m['Turnover']:>10.1f}")

    # 单板块层面：MA200 是否普遍有效
    rec = []
    for c in px.columns:
        sub = pd.DataFrame({"close": px[c], "above": above[c]}).dropna()
        sub = sub[sub.index >= "2013-01-01"]
        if len(sub) < 500:
            continue
        r = sub["close"].pct_change()
        pos = sub["above"].shift(1).astype(float)
        hold_nav = (1 + r).cumprod()
        ma_nav = (1 + pos * r).cumprod()
        rec.append({
            "sector": c,
            "hold_cagr": hold_nav.iloc[-1] ** (252 / len(r)) - 1,
            "ma_cagr": ma_nav.iloc[-1] ** (252 / len(r)) - 1,
            "hold_mdd": (hold_nav / hold_nav.cummax() - 1).min(),
            "ma_mdd": (ma_nav / ma_nav.cummax() - 1).min(),
        })
    r = pd.DataFrame(rec)
    lines.append(f"\n单板块 MA200：{len(r)} 个板块，2013-01 起")
    lines.append(f"  CAGR 中位数  持有 {r.hold_cagr.median():.1%}  vs MA200 {r.ma_cagr.median():.1%}")
    lines.append(f"  MaxDD 中位数 持有 {r.hold_mdd.median():.1%}  vs MA200 {r.ma_mdd.median():.1%}")
    lines.append(f"  MA200 提升 CAGR 的板块占比 {(r.ma_cagr > r.hold_cagr).mean():.0%}")
    lines.append(f"  MA200 降低回撤的板块占比 {(r.ma_mdd > r.hold_mdd).mean():.0%}")
    return "\n".join(lines), r


def entry_test():
    """再入场规则：哪种“趋势修好”的确认更能区分反弹与反转。"""
    lines = ["\n[再入场规则] 2011-01 起；退出统一为 top>=0.6 或跌破200日线（3日确认）"]
    lines.append(f"{'规则':<26}{'指数':<8}{'CAGR':>7}{'MaxDD':>8}{'Turns':>7}"
                 f"{'入场':>5}{'60日>+5%':>9}{'60日<-5%':>9}{'中位':>8}")
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        s = s[s["date"] >= "2011-01-01"].reset_index(drop=True)
        s["top_score"] = s["top_score"].fillna(0.0)
        above3 = confirm(s["close"] > s["ma200"], 3)
        above5 = confirm(s["close"] > s["ma200"], 5)
        exit_cond = (s["top_score"] >= 0.6) | (~above3)
        base = above3 & (s["top_score"] < 0.4)
        variants = [
            ("站上200日线5日", base, 5),
            ("站上200日线10日", base, 10),
            ("+广度20日改善>5pp", base & (s["breadth_ma200_chg20"].fillna(1) > 0.05), 5),
            ("+广度站上45%", base & (s["breadth_ma200"].fillna(1) > 0.45), 5),
            ("+60日新高（higher low）", base & (s["close"] > s["close"].shift(60)), 5),
            ("不看top，仅200日线5日", above5, 5),
            ("不看top，仅200日线10日", confirm(s["close"] > s["ma200"], 10), 10),
        ]
        for label, en, k in variants:
            w = state_machine(exit_cond, en, 3, k, start=0.0)
            m, _, _ = metrics(s, w)
            e = w.shift(1).fillna(0)
            ent = (e > 0.01) & (e.shift(1) <= 0.01)
            fwd = (s["open"].shift(-60) / s["open"] - 1)[ent.fillna(False)].dropna()
            lines.append(
                f"{label:<26}{name:<8}{m['CAGR']:>7.1%}{m['MaxDD']:>8.1%}"
                f"{m['Turns']:>7d}{len(fwd):>5d}"
                f"{(fwd > 0.05).mean():>9.0%}{(fwd < -0.05).mean():>9.0%}"
                f"{fwd.median():>8.1%}")
    return "\n".join(lines)


def common_window():
    lines = ["\n[同期对比] 2013-01 起，买入持有 vs topnormal(0.6/0.4)"]
    lines.append(f"{'指数':<8}{'持有CAGR':>10}{'持有MaxDD':>11}"
                 f"{'规则CAGR':>10}{'规则MaxDD':>11}{'持有Sharpe':>11}{'规则Sharpe':>11}")
    for name in ["hs300", "zz500", "zz1000", "cyb"]:
        s = prep(name)
        s = s[s["date"] >= "2013-01-01"].reset_index(drop=True)
        s["top_score"] = s["top_score"].fillna(0.0)
        w = topnormal(s, 0.6, 0.4)
        mh, _, _ = metrics(s, pd.Series(1.0, index=s.index))
        mw, _, _ = metrics(s, w)
        lines.append(f"{name:<8}{mh['CAGR']:>10.1%}{mh['MaxDD']:>11.1%}"
                     f"{mw['CAGR']:>10.1%}{mw['MaxDD']:>11.1%}"
                     f"{mh['Sharpe']:>11.2f}{mw['Sharpe']:>11.2f}")
    return "\n".join(lines)


def main():
    text = "\n".join([grid(), leave_one_out(), cost_sens(), entry_test(),
                     common_window(), sector_test()[0]])
    (OUT / "robustness.txt").write_text(text, encoding="utf-8")
    print(text.encode("utf-8", "replace").decode("utf-8"))


if __name__ == "__main__":
    main()
