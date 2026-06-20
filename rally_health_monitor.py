#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股上涨持续性监测 — 每日运行
=============================
设计原则:
  - 指标极少 (5个)，每个有经济学逻辑，不过拟合
  - 三元判断: 健康/谨慎/警告
  - 不预测顶，只判断"当前上涨的质量在改善还是恶化"
  - M1 是锚：宏观恶化时所有技术信号升级

用法:
  python rally_health_monitor.py           # 正常模式
  python rally_health_monitor.py --dry-run # 不推送，仅打印
"""
import os, sys, json, argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import requests
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# 配置
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_94e85ee81df40d0ac71c358861427b06")
REQUEST_TIMEOUT = 30

# 阈值 — 基于经济学逻辑，不是数据挖掘
MA60_DEVIATION_WARN = 0.12   # 偏离60日均线12%+ 算过热
MA60_DEVIATION_EXTREME = 0.20  # 20%+ 极度偏离
RSI_OVERBOUGHT = 70
RSI_EXTREME = 85
VOL_DECLINE_WARN = 0.85       # 量能降至60日均量85%以下
VOL_SURGE_EXTREME = 2.0       # 量能超60日均量2倍
M1_DECLINE_WARN = -0.5        # M1 3月均变化 < -0.5pp
M1_DECLINE_SEVERE = -1.5      # M1 3月均变化 < -1.5pp

# ============================================================
# 数据获取
# ============================================================

def fetch_index_data(index_code: str = "sh000001", days: int = 250) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(days).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[!!] Index fetch failed: {e}")
        return None


def fetch_m1_data() -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        df = df.rename(columns={df.columns[0]: "month"})
        m1c = [c for c in df.columns if "m1" in c.lower() and ("同比" in str(c) or "增速" in str(c))]
        keep = ["month"] + m1c[:1]
        result = df[keep].copy()
        result.columns = ["month", "m1"]
        result["month"] = pd.to_datetime(
            result["month"].astype(str).str.strip().str.replace("年","-").str.replace("月份",""),
            errors="coerce")
        result = result.dropna(subset=["month"]).sort_values("month")
        result["m1"] = pd.to_numeric(result["m1"], errors="coerce")
        return result
    except Exception as e:
        print(f"[!!] M1 fetch failed: {e}")
        return None


def is_trading_day(df: pd.DataFrame) -> bool:
    """检查最新数据是否是今天"""
    if df is None or len(df) == 0:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
    return last_date == today


# ============================================================
# 指标计算与评估
# ============================================================

def compute_indicators(idx: pd.DataFrame, m1: pd.DataFrame) -> Dict:
    """计算 5 个核心指标并评估状态"""
    indicators = {}

    # ── 1. 价格偏离度 ──
    ma60_series = idx["close"].rolling(60).mean()
    ma60 = ma60_series.iloc[-1]
    last_close = idx["close"].iloc[-1]
    dev60 = (last_close - ma60) / ma60 if pd.notna(ma60) and ma60 > 0 else 0
    if dev60 > MA60_DEVIATION_EXTREME:
        dev_state = "warning"
        dev_label = f"极度偏离MA60 {dev60:.1%}"
    elif dev60 > MA60_DEVIATION_WARN:
        dev_state = "caution"
        dev_label = f"偏高 MA60 {dev60:.1%}"
    elif dev60 < -0.05:
        dev_state = "caution"
        dev_label = f"跌破MA60 {dev60:.1%}"
    else:
        dev_state = "healthy"
        dev_label = f"正常 MA60 {dev60:+.1%}"
    indicators["price_deviation"] = {"state": dev_state, "value": dev60, "label": dev_label}

    # ── 2. 量能趋势 ──
    vol_ma20_series = idx["volume"].rolling(20).mean()
    vol_ma60_series = idx["volume"].rolling(60).mean()
    vol20 = vol_ma20_series.iloc[-1]
    vol60 = vol_ma60_series.iloc[-1]
    vol_ratio = vol20 / vol60 if pd.notna(vol60) and vol60 > 0 else 1
    if vol_ratio > VOL_SURGE_EXTREME:
        vol_state = "caution"
        vol_label = f"异常放量 {vol_ratio:.1f}x"
    elif vol_ratio < VOL_DECLINE_WARN:
        vol_state = "caution"
        vol_label = f"持续缩量 {vol_ratio:.1%}"
    else:
        vol_state = "healthy"
        vol_label = f"正常 {vol_ratio:.1%}"
    indicators["volume_trend"] = {"state": vol_state, "value": vol_ratio, "label": vol_label}

    # ── 3. RSI ──
    delta = idx["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    rsi = rsi_series.iloc[-1]
    if rsi > RSI_EXTREME:
        rsi_state = "warning"
    elif rsi > RSI_OVERBOUGHT:
        rsi_state = "caution"
    elif rsi < 30:
        rsi_state = "caution"
    else:
        rsi_state = "healthy"
    indicators["rsi"] = {"state": rsi_state, "value": rsi, "label": f"RSI14={rsi:.0f}"}

    # ── 4. 波动率 ──
    ret_series = idx["close"].pct_change()
    vol20_series = ret_series.rolling(20).std()
    vol60_series = ret_series.rolling(60).std()
    v20 = vol20_series.iloc[-1]
    v60 = vol60_series.iloc[-1]
    if pd.notna(v60) and v60 > 0:
        vol_ratio_v = v20 / v60
    else:
        vol_ratio_v = 1.0
    if vol_ratio_v > 1.8:
        volv_state = "warning"
        volv_label = f"剧烈放大 {vol_ratio_v:.1f}x"
    elif vol_ratio_v > 1.3:
        volv_state = "caution"
        volv_label = f"在放大 {vol_ratio_v:.1f}x"
    elif vol_ratio_v < 0.6:
        volv_state = "caution"
        volv_label = f"异常低波 {vol_ratio_v:.1f}x"
    else:
        volv_state = "healthy"
        volv_label = f"正常 {vol_ratio_v:.1f}x"
    indicators["volatility"] = {"state": volv_state, "value": vol_ratio_v, "label": volv_label}

    # ── 5. M1 宏观锚 ──
    if m1 is not None and len(m1) >= 6:
        recent = m1.tail(6).copy()
        early_m1 = recent["m1"].head(3).mean()
        late_m1 = recent["m1"].tail(3).mean()
        m1_change = late_m1 - early_m1
        m1_latest = recent["m1"].iloc[-1]
        if m1_change < M1_DECLINE_SEVERE:
            m1_state = "warning"
            m1_label = f"快速下滑 {m1_change:+.1f}pp (当前{m1_latest:.1f}%)"
        elif m1_change < M1_DECLINE_WARN:
            m1_state = "caution"
            m1_label = f"边际走弱 {m1_change:+.1f}pp (当前{m1_latest:.1f}%)"
        elif m1_change > 0.5:
            m1_state = "healthy"
            m1_label = f"改善中 {m1_change:+.1f}pp (当前{m1_latest:.1f}%)"
        else:
            m1_state = "healthy"
            m1_label = f"稳定 {m1_change:+.1f}pp (当前{m1_latest:.1f}%)"
    else:
        m1_state = "healthy"
        m1_change = 0
        m1_label = "M1数据不可用"
    indicators["m1"] = {"state": m1_state, "value": m1_change if m1 is not None else None, "label": m1_label}

    return indicators


def assess_rally_health(indicators: Dict) -> Dict:
    """
    综合评估：检查关键背离组合。
    最危险的组合 = 价格偏贵 + M1 恶化（宏观-价格背离）
    """
    states = {k: v["state"] for k, v in indicators.items()}

    warnings = [k for k, v in states.items() if v == "warning"]
    cautions = [k for k, v in states.items() if v == "caution"]

    # M1 锚逻辑：如果 M1 恶化，所有 caution 升级为 warning 级别关注
    m1_bad = states.get("m1") in ("warning", "caution")
    price_elevated = states.get("price_deviation") in ("warning", "caution")

    # 最危险: 宏观-价格背离
    if m1_bad and price_elevated and states.get("volume_trend") == "caution":
        overall = "WARNING"
        summary = "宏观-价格-量能三重背离：M1走弱 + 价格偏高 + 量能异常。这是历史大顶最常见的组合(2007/2015/2021均出现)。上涨持续性严重存疑。"
    elif m1_bad and price_elevated:
        overall = "WARNING"
        summary = "宏观-价格背离：M1在走弱但指数位置偏高。流动性已经在边际收紧，价格还没反映。2007和2015年大顶前都出现过类似组合。建议降低风险敞口。"
    elif len(warnings) >= 2:
        overall = "WARNING"
        summary = f"多重警告信号同时触发: {', '.join(warnings)}。短期内上涨的可持续性显著下降。"
    elif m1_bad and len(warnings) >= 1:
        overall = "CAUTION"
        summary = f"M1走弱 + {', '.join(warnings)}触发。宏观环境在恶化，即使价格尚未极端偏离，也需谨慎。"
    elif len(warnings) >= 1 or len(cautions) >= 3:
        overall = "CAUTION"
        summary = f"部分指标发出谨慎信号: {', '.join(warnings + cautions)}。上涨仍在但质量在下降，建议跟踪这些信号是否恶化。"
    elif m1_bad:
        overall = "CAUTION"
        summary = "M1在走弱但价格尚未过热。宏观先行指标恶化，如果价格继续上涨，背离会加剧。关注但不急于行动。"
    elif len(cautions) >= 1:
        overall = "HEALTHY"
        summary = f"轻微关注: {', '.join(cautions)}。整体上涨环境健康，这些信号在正常牛市中也会偶尔出现。"
    else:
        overall = "HEALTHY"
        summary = "所有指标健康。宏观流动性、价格位置、量能、波动率均无异常。上涨具备持续性的条件。"

    # 关键跟踪点
    key_watch = []
    if m1_bad:
        key_watch.append(f"M1趋势是否继续恶化(当前{indicators['m1']['label']})")
    if states.get("price_deviation") in ("warning",):
        key_watch.append("价格若继续偏离MA60将进入极端区域")
    if states.get("volume_trend") == "caution":
        key_watch.append("量能趋势需要回到正常水平")
    if states.get("rsi") == "warning":
        key_watch.append("RSI极端超买后的回调风险")
    if not key_watch:
        key_watch.append("下一个M1数据发布")
        key_watch.append("指数是否出现连续3日不创新高")

    return {
        "overall": overall,
        "summary": summary,
        "key_watch": key_watch[:3],
        "states": states,
        "indicators": indicators,
    }


# ============================================================
# AI 解读 (DeepSeek) — 可选，提供人类可读的说明
# ============================================================

def ai_interpret(assessment: Dict, idx_tail: pd.DataFrame) -> Optional[str]:
    """用 DeepSeek 生成一段简短的解读"""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        recent_data = idx_tail.tail(5)[["date","close","volume"]].to_string()
        inds = assessment["indicators"]
        prompt = f"""你是A股市场监测系统的一部分。基于以下数据，用2-3句话解读当前上涨的健康状况。

整体评估: {assessment['overall']}
指标状态: {json.dumps({k: v['label'] for k, v in inds.items()}, ensure_ascii=False)}
最近5日指数数据: {recent_data}

要求:
- 不超过3句话
- 如果整体是HEALTHY，说明为什么
- 如果是CAUTION或WARNING，指出最需要关注的1个问题
- 不要预测方向，只描述条件和风险
- 用中文"""
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [
                {"role": "system", "content": "你是简洁的市场监测助手。"},
                {"role": "user", "content": prompt}
            ], "temperature": 0.3, "max_tokens": 300},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[!!] AI interpret failed: {e}")
    return None


# ============================================================
# 飞书推送
# ============================================================

def _get_feishu_token() -> Optional[str]:
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("tenant_access_token")
    except Exception as e:
        print(f"[!!] Feishu token: {e}")
    return None


def send_feishu(content: str) -> bool:
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("[!!] Feishu credentials missing")
        return False
    token = _get_feishu_token()
    if not token:
        return False
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red", "title": {"tag": "plain_text", "content": "A股上涨持续性监测"}},
        "elements": [{"tag": "markdown", "content": content}]
    }
    payload = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False)
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30
        )
        return resp.status_code == 200 and resp.json().get("code") == 0
    except Exception as e:
        print(f"[!!] Feishu send: {e}")
    return False


# ============================================================
# 格式化输出
# ============================================================

def format_message(assessment: Dict, ai_text: Optional[str], idx: pd.DataFrame) -> str:
    d = idx["date"].iloc[-1]
    date_str = d.strftime("%Y.%m.%d") if hasattr(d, 'strftime') else str(d)[:10]
    weekday = ["一","二","三","四","五","六","日"][d.weekday()] if hasattr(d, 'weekday') else ""
    close = idx["close"].iloc[-1]
    chg = (idx["close"].iloc[-1] / idx["close"].iloc[-2] - 1) if len(idx) >= 2 else 0

    overall = assessment["overall"]
    emoji = {"HEALTHY": "🟢", "CAUTION": "🟡", "WARNING": "🔴"}.get(overall, "⚪")
    cn_label = {"HEALTHY": "健康", "CAUTION": "谨慎", "WARNING": "警告"}.get(overall, overall)

    lines = [
        f"**{date_str} 周{weekday}**  上证 {close:.0f} ({chg:+.2%})  评级: {emoji} {cn_label}",
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # 指标表
    inds = assessment["indicators"]
    icon_map = {"healthy":"🟢","caution":"🟡","warning":"🔴"}
    lines.append("**核心指标:**")
    lines.append("")
    names = {"price_deviation":"价格位置","volume_trend":"量能趋势","rsi":"RSI动量","volatility":"波动率","m1":"M1宏观锚"}
    for key, name in names.items():
        v = inds.get(key)
        if v:
            icon = icon_map.get(v["state"], "⚪")
            lines.append(f"{icon} {name}: {v['label']}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    # 综合判断
    lines.append(f"**{emoji} {assessment['summary']}**")
    lines.append("")

    # AI 解读
    if ai_text:
        lines.append(f"> {ai_text}")
        lines.append("")

    # 关键跟踪
    lines.append("**跟踪要点:**")
    for w in assessment.get("key_watch", []):
        lines.append(f"- {w}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("*本监测不构成投资建议 | 每日收盘后自动生成*")

    return "\n".join(lines)


# ============================================================
# 主逻辑
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Send even if data is not from today")
    args = parser.parse_args()

    print("=" * 50)
    print("  A股上涨持续性监测")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # 获取数据
    print("\n[>] Fetching data...")
    idx = fetch_index_data("sh000001", days=250)
    m1 = fetch_m1_data()
    print(f"    Index: {len(idx) if idx is not None else 0} days")
    print(f"    M1: {len(m1) if m1 is not None else 0} months")

    if idx is None:
        print("[!!] Cannot fetch index data, aborting")
        return

    # 检查是否交易日
    if not args.force and not is_trading_day(idx):
        print("[i] Not a trading day (latest data not from today), skipping")
        return

    # 计算指标
    print("\n[>] Computing indicators...")
    indicators = compute_indicators(idx, m1)
    assessment = assess_rally_health(indicators)

    # AI 解读
    print("[>] AI interpretation...")
    ai_text = ai_interpret(assessment, idx)

    # 格式化
    msg = format_message(assessment, ai_text, idx)
    print("\n" + msg)

    if args.dry_run:
        print("\n[i] Dry run — not sending")
        return

    # 推送
    print("\n[>] Sending to Feishu...")
    ok = send_feishu(msg)
    print(f"    {'[OK] Sent' if ok else '[!!] Failed'}")


if __name__ == "__main__":
    main()
