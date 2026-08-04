# ltc_verify.py
"""验证闭环：历史标签 vs 板块后续涨跌（10/20 交易日），相关性验证非因果证明"""
import json, logging, os
from typing import Dict, List, Optional
import ltc_data, ltc_store

logger = logging.getLogger(__name__)
ACTIVE_SIGNALS = ["资金关注", "逆势吸筹嫌疑", "派发嫌疑", "资金撤离"]
CONFIG_FILE = "data/ltc/signals_config.json"
VERIFY_FILE = "data/ltc/verification.jsonl"
STATE_FILE = "data/ltc/state.json"

def _trading_dates() -> List[str]:
    """用新浪上证指数日K取交易日序列"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000001")
    return [str(d)[:10] for d in df["date"].tolist()]

def forward_returns(hist: List[dict], price: Dict[str, Dict[str, float]], td: List[str]) -> List[dict]:
    """对每条留痕：取 data_date 在 td 中的位置，+10/+20 交易日的收盘价算收益"""
    idx = {d: i for i, d in enumerate(td)}
    out = []
    for h in hist:
        dd = h.get("data_date")
        if dd not in idx:
            continue
        i = idx[dd]
        for ind, meta in (h.get("tags") or {}).items():
            rets = {}
            for n in (10, 20):
                j = i + n
                if j < len(td) and dd in price and ind in price.get(dd, {}):
                    p0 = price[dd].get(ind)
                    p1 = price.get(td[j], {}).get(ind)
                    rets[f"ret_{n}td"] = round((p1 / p0 - 1) * 100, 2) if p0 and p1 else None
                else:
                    rets[f"ret_{n}td"] = None
            out.append({"data_date": dd, "industry": ind, "tag": meta.get("tag", ""),
                        "ret_10td": rets["ret_10td"], "ret_20td": rets["ret_20td"]})
    return out

def differentiation(results: List[dict]) -> Dict:
    """信号组（tag 非空）20日收益均值 vs 全部均值 → 区分度"""
    tagged = [r for r in results if r.get("tag") and r.get("ret_20td") is not None]
    allv = [r for r in results if r.get("ret_20td") is not None]
    if not tagged or not allv:
        return {"verdict": "数据不足"}
    avg_tagged = sum(r["ret_20td"] for r in tagged) / len(tagged)
    avg_all = sum(r["ret_20td"] for r in allv) / len(allv)
    diff = avg_tagged - avg_all
    return {"verdict": "有效" if diff > 2.0 else "无效", "diff_20": round(diff, 2),
            "n_tagged": len(tagged), "n_all": len(allv)}

def update_signal_config(verdict: str, diff: float) -> None:
    cfg_path = os.path.join(os.path.dirname(CONFIG_FILE), os.path.basename(CONFIG_FILE))
    cfg = ltc_store.load_state(cfg_path) or {"active": ACTIVE_SIGNALS, "consecutive_invalid": 0}
    if verdict == "有效":
        cfg["consecutive_invalid"] = 0
    else:
        cfg["consecutive_invalid"] = cfg.get("consecutive_invalid", 0) + 1
        if cfg["consecutive_invalid"] >= 2:
            cfg["active"] = ["资金关注", "逆势吸筹嫌疑", "派发嫌疑"]  # 资金撤离信号降级移除
            cfg["removed"] = cfg.get("removed", []) + ["资金撤离"]
    ltc_store.save_state(cfg_path, cfg)

def main() -> int:
    history = ltc_store.load_history("data/ltc/history.jsonl")
    if not history:
        print("无留痕数据，跳过验证")
        return 0
    td = _trading_dates()
    price: Dict[str, Dict[str, float]] = {}
    industries = {ind for h in history for ind in (h.get("tags") or {})}
    for ind in industries:
        k = ltc_data.fetch_board_kline(ind)
        if k is not None:
            for _, r in k.iterrows():
                price.setdefault(str(r["date"])[:10], {})[ind] = float(r["close"])
    results = forward_returns(history, price, td)
    results = [r for r in results if r.get("ret_20td") is not None]
    diff = differentiation(results)
    report = {"date": ltc_store.load_state(STATE_FILE).get("last_pushed_date", ""),
              "n_records": len(results), **diff}
    ltc_store.append_history("data/ltc/verification.jsonl", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    update_signal_config(diff.get("verdict", "数据不足"), diff.get("diff_20", 0.0))
    return 0

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
