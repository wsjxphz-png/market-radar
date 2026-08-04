"""数据驱动分类 + 资金归因 + 承接周期判断（全部推断，不带实名表述）"""
from typing import List, Optional
import pandas as pd

TAG_PRIORITY = {"逆势吸筹嫌疑": 0, "派发嫌疑": 1, "资金关注": 2, "资金撤离": 3}

def analyze_flows(df: pd.DataFrame) -> List[dict]:
    if df is None or len(df) == 0:
        return []
    sl = pd.to_numeric(df["super_large_net_yi"], errors="coerce")
    sl_rank = sl.rank(pct=True) * 100
    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        chg = float(row.get("chg_pct", 0) or 0)
        sl_net = float(row.get("super_large_net_yi", 0) or 0)
        sl_pct = float(sl_rank.iloc[i])
        tag = ""
        if chg <= -1 and sl_pct >= 80 and sl_net > 0:
            tag = "逆势吸筹嫌疑"
        elif chg >= 1 and sl_pct <= 20 and sl_net < 0:
            tag = "派发嫌疑"
        elif sl_pct >= 80:
            tag = "资金关注"
        elif sl_pct <= 20:
            tag = "资金撤离"
        if sl_pct >= 80:
            signal = "TOP20% 🔵"
        elif sl_pct >= 60:
            signal = "60-80% 🟢"
        elif sl_pct >= 40:
            signal = "40-60% 🟡"
        elif sl_pct >= 20:
            signal = "20-40% 🟠"
        else:
            signal = "BOTTOM20% 🔴"
        results.append({
            "industry": str(row.get("industry", "")),
            "signal": signal, "sl_percentile": round(sl_pct, 0),
            "chg_pct": round(chg, 2), "sl_net": round(sl_net, 2),
            "large_net": round(float(row.get("large_net_yi", 0) or 0), 2),
            "tag": tag,
        })
    results.sort(key=lambda x: x["sl_net"], reverse=True)
    return results

def compute_accumulation(kline: Optional[pd.DataFrame], chg: float, sl_net: float,
                         backing: bool, pos_1y: Optional[float]) -> dict:
    """承接周期：K线(量价历史)+当日资金+背书+位置 → 短期/中期/长期（推断）"""
    if kline is None or len(kline) < 20:
        return {"period": "持续性数据积累中", "reasons": ["量价历史数据积累中"]}
    close = kline["close"].values
    amount = kline["amount"].values if "amount" in kline.columns else kline["volume"].values
    vol_ratio = float(amount[-1]) / float(amount[-20:].mean()) if float(amount[-20:].mean()) > 0 else 1.0
    pct5 = float(close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0.0
    pos = pos_1y if pos_1y is not None else (
        float((close[-1] - close[-250:].min()) / (close[-250:].max() - close[-250:].min()) * 100)
        if close[-250:].max() > close[-250:].min() else 50.0)
    reasons = []
    if vol_ratio >= 1.2:
        reasons.append(f"近20日成交额放大 {vol_ratio:.1f} 倍")
    if pct5 >= 2:
        reasons.append(f"近5日累计涨 {pct5:+.1f}%")
    if backing:
        reasons.append("有回购/季报实名背书")
    if pos < 40:
        reasons.append(f"价格处于 1 年低位区（{pos:.0f}% 分位）")
    elif pos > 75:
        reasons.append(f"价格处于 1 年高位区（{pos:.0f}% 分位）")
    if backing and pos < 60 and (pct5 > 0 or vol_ratio >= 1.2):
        return {"period": "偏长期布局特征", "reasons": reasons}
    if vol_ratio >= 1.2 and -3 <= pct5 <= 3:
        return {"period": "有中期承接迹象", "reasons": reasons}
    if backing and pos < 60:
        return {"period": "有中期承接迹象", "reasons": reasons}
    return {"period": "短期行为特征", "reasons": reasons or ["单日资金行为，持续性不足"]}

def pick_focus(analyses: List[dict], top_n: int = 6) -> List[dict]:
    """优先级：逆势吸筹 > 派发嫌疑 > 资金关注 > 资金撤离 > 待观察，按 industry 去重取前 N"""
    tagged = [a for a in analyses if a.get("tag")]
    tagged.sort(key=lambda x: TAG_PRIORITY.get(x["tag"], 9))
    others = [a for a in analyses if not a.get("tag")]
    others.sort(key=lambda x: x.get("sl_percentile", 50), reverse=True)
    seen, out = set(), []
    for a in tagged + others:
        key = a.get("industry")
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= top_n:
            break
    return out
