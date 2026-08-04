# ltc_main.py
"""编排入口：日期判定 → 抓取 → 推送决策 → 卡片/降级 → 发送 → 留痕"""
import json, logging, os, sys
from typing import Dict, Optional
import ltc_analysis, ltc_data, ltc_format, ltc_narrative, ltc_news, ltc_store
from ltc_config import bj_now

logger = logging.getLogger(__name__)

DATA_DIR = "data/ltc"
STATE_FILE = "data/ltc/state.json"
HISTORY_FILE = "data/ltc/history.jsonl"

def _safe_fetch(func, *args, default=None, **kwargs):
    """抓取兜底（FR-6.3）：任何异常视为该源失败返回 default，不阻塞整体编排"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning("抓取失败 %s: %s", getattr(func, "__name__", func), str(e)[:80])
        return default

def _send_or_dry(msg: str, env: dict, dry_run: bool) -> bool:
    """发送；LTC_DRY_RUN=1 时跳过发送视为成功（内容打印到日志，照常留痕）"""
    if dry_run:
        logger.info("LTC_DRY_RUN 生效，跳过发送（dry-run 视为成功）：\n%s", msg[:800])
        return True
    return send_feishu(msg, env)

def send_feishu(msg: str, env: dict) -> bool:
    """发送卡片到飞书；缺少凭据 = 发送失败（False，告警路径）。dry-run 用 env 的 LTC_DRY_RUN 标记"""
    app_id, app_secret, chat_id = env.get("FEISHU_APP_ID", ""), env.get("FEISHU_APP_SECRET", ""), env.get("FEISHU_CHAT_ID", "")
    if not (app_id and app_secret and chat_id):
        logger.warning("飞书环境变量缺失，跳过发送")
        return False
    import requests
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
    token = r.json().get("tenant_access_token")
    if not token:
        logger.error("获取飞书 token 失败"); return False
    card = {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "每日资金观察"}},
            "elements": [{"tag": "markdown", "content": msg}]}
    r = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                      json={"receive_id": chat_id, "msg_type": "interactive",
                            "content": json.dumps(card, ensure_ascii=False)},
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
    return r.status_code == 200 and r.json().get("code") == 0

def run_once(env: dict, data_dir: str = DATA_DIR) -> int:
    state_path = os.path.join(data_dir, "state.json")
    hist_path = os.path.join(data_dir, "history.jsonl")
    state = ltc_store.load_state(state_path)
    dry_run = bool(env.get("LTC_DRY_RUN"))

    trading_date = _safe_fetch(ltc_data.get_trading_date)
    southbound = _safe_fetch(ltc_data.fetch_southbound)
    data_date = (southbound or {}).get("date") or trading_date
    if not data_date:
        # Level 3 边界：交易日都无法判定 → 新闻兜底，新闻也失败则跳过+告警
        today_cn = bj_now().strftime("%Y-%m-%d")
        titles = _safe_fetch(ltc_news.fetch_news_titles, today_cn, 15, default=[])
        if titles:
            _send_or_dry(ltc_news.format_news_card(today_cn, titles), env, dry_run)
            return 0
        state["alert_streak"] = state.get("alert_streak", 0) + 1
        ltc_store.save_state(state_path, state)
        return 0 if state["alert_streak"] < 2 else 1

    push, reason = ltc_store.should_push(data_date, state)
    if not push:
        logger.info("跳过推送（%s）：data_date=%s", reason, data_date)
        return 0

    flow = _safe_fetch(ltc_data.fetch_sector_flow)
    if flow is None:
        # Level 2 降级：板块流失败 → 新闻兜底，新闻也失败则跳过+告警
        titles = _safe_fetch(ltc_news.fetch_news_titles, data_date, 15, default=[])
        if titles:
            _send_or_dry(ltc_news.format_news_card(data_date, titles), env, dry_run)
        state["alert_streak"] = state.get("alert_streak", 0) + 1
        ltc_store.save_state(state_path, state)
        return 0 if state["alert_streak"] < 2 else 1
    state["alert_streak"] = 0

    analyses = ltc_analysis.analyze_flows(flow)
    focus = ltc_analysis.pick_focus(analyses, top_n=6)
    repurchase = _safe_fetch(ltc_data.fetch_repurchase, weeks=4,
                             default={"period": "近4周", "items": []})
    valuation = ltc_data.fetch_valuation()  # 内部逐板块失败兜底，无需外层再包

    # 承接周期（每板块 K 线 + 背书；背书来自季度实名数据，季度背景过期则不计）
    from ltc_config import BACKING_SECTORS, QUARTERLY_CONTEXT, is_expired
    backing_active = not is_expired(QUARTERLY_CONTEXT.get("updated", ""))
    for f in focus:
        kline = _safe_fetch(ltc_data.fetch_board_kline, f["industry"])
        backing = backing_active and any(b in f["industry"] for b in BACKING_SECTORS)
        acc = ltc_analysis.compute_accumulation(kline, f["chg_pct"], f["sl_net"], backing, None)
        f["accum"] = acc

    history = ltc_store.load_history(hist_path)
    sb_ref = ltc_store.compute_reference(history, "southbound_net_yi")
    sb_label = ltc_store.reference_label((southbound or {}).get("southbound_net_yi", 0), sb_ref)
    refs = {"southbound_label": sb_label}

    facts = ltc_narrative.build_facts(
        {"data_date": data_date, "southbound": southbound, "repurchase": repurchase}, focus, refs)
    interp = ltc_narrative.interpretation(facts, env.get("DEEPSEEK_API_KEY", ""))

    # 过期警告由 format_card 内部 build_quarterly_block 自带，不重复传 quarterly_expired
    card = ltc_format.format_card(data_date, interp, focus, southbound, valuation,
                                  repurchase, refs)
    logger.info("卡片内容:\n%s", card[:2000])
    if _send_or_dry(card, env, dry_run):
        state["last_pushed_date"] = data_date
        ltc_store.save_state(state_path, state)
        ltc_store.append_history(hist_path, {
            "date": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": data_date,
            "southbound_net_yi": (southbound or {}).get("southbound_net_yi"),
            "tags": {f["industry"]: {"tag": f["tag"], "accum": f.get("accum", {}).get("period"),
                                     "sl_net": f["sl_net"]} for f in focus},
        })
        return 0
    logger.error("飞书发送失败")
    return 1

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    env = dict(os.environ)
    code = run_once(env)
    sys.exit(code)

if __name__ == "__main__":
    main()
