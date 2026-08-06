"""估值数据源：中证 PE 全历史分位 / 中证成分×BaoStock PB 聚合 / 价格位置兜底"""
import io
import logging
import threading
import time
from typing import Dict, List, Optional
import pandas as pd
import requests
import baostock as bs
import ltc_data
from val_config import INDEX_MAP, is_cyclical

logger = logging.getLogger(__name__)
H = {"User-Agent": "Mozilla/5.0"}
PE_LOOKBACK_DAYS = 2440  # 10 年交易日窗口
# PB 聚合性能护栏（2026-08-06：GitHub runner 上 baostock 单只查询实测 ~8.5s，
# 50 只/板块 × 11 板块理论 78 分钟，必爆 workflow 20 分钟上限 → 降样本+板块级预算+单只软超时）
PB_SAMPLE_LIMIT = 20        # 每板块最多采样成分股数
PB_BUDGET_SECONDS = 60      # 每板块 PB 聚合总预算，超时即降级（估值卡有三级兜底链）
PB_QUERY_TIMEOUT = 10       # 单只查询软超时（baostock 阻塞式 API，daemon 线程放弃等待）
_BS_LOCK = threading.Lock()  # baostock 模块级全局 socket 无内部锁：软超时后旧线程仍存活，
                             # 不持锁并发 send/recv 会串包 → PB 静默掺入他股数据（2026-08-06 审查 F1）

def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning("%s 失败: %s", getattr(fn, "__name__", fn), str(e)[:80])
        return None

def fetch_pe_series(index_code: str) -> Optional[pd.DataFrame]:
    """中证 index-perf 全历史：peg 字段=PE(TTM 总股本口径，已验证)"""
    r = requests.get("https://www.csindex.com.cn/csindex-home/perf/index-perf",
                     params={"indexCode": index_code, "startDate": "20100101",
                             "endDate": pd.Timestamp.now().strftime("%Y%m%d")},
                     timeout=30, headers=H)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        return None
    df = pd.DataFrame(data)
    df = df[["tradeDate", "peg"]].rename(columns={"tradeDate": "date", "peg": "pe"})
    df["date"] = pd.to_datetime(df["date"])
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    return df.dropna(subset=["pe"]).sort_values("date")

def pe_percentile(pe_series: Optional[pd.DataFrame], lookback_days: int = PE_LOOKBACK_DAYS) -> Optional[dict]:
    if pe_series is None or len(pe_series) < 60:
        return None
    recent = pe_series.tail(lookback_days)
    cur = float(recent["pe"].iloc[-1])
    pct = float((recent["pe"] < cur).mean() * 100)
    last20 = recent["pe"].tail(20)
    if len(last20) >= 10:
        chg = float(last20.iloc[-1] - last20.iloc[0])
        trend = "up" if chg > 0.05 else ("down" if chg < -0.05 else "flat")
    else:
        trend = "flat"
    return {"pe": round(cur, 2), "pct": round(pct, 1), "trend": trend, "days": len(recent),
            "years": round(len(recent) / 244, 1)}  # 数据窗口诚实性：实际指数历史年数（交易日/年）

def fetch_constituents(index_code: str) -> Optional[List[str]]:
    """中证 cons xls 成分股 → BaoStock 代码格式（sh./sz.）"""
    url = (f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
           f"file/autofile/cons/{index_code}cons.xls")
    # read_excel(url) 直连无超时：连接失败会挂数分钟等服务端关闭（2026-08-06 实测 6 分钟）
    r = requests.get(url, timeout=15, headers=H)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content))
    codes = []
    for raw in df.iloc[:, 4].dropna().astype(str):
        raw = raw.strip().zfill(6)  # 部分 cons 文件数值型存储丢前导零（000983→983）
        if raw.startswith(("6", "9")):
            codes.append(f"sh.{raw}")
        elif raw.startswith(("0", "3")):
            codes.append(f"sz.{raw}")
        elif raw.startswith(("4", "8")):
            codes.append(f"bj.{raw}")
    return codes or None

def _bs_login():
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {lg.error_msg}")
    return lg

def _query_pb(code: str) -> Optional[float]:
    """单只成分股近 10 日 pbMRQ。持 _BS_LOCK 串行化：baostock 阻塞式 API 无法中断，
    软超时后 daemon 线程仍存活，锁保证同一时刻只有一条查询在 socket 上。"""
    with _BS_LOCK:
        rs = bs.query_history_k_data_plus(code, "date,pbMRQ",
                                          start_date=(pd.Timestamp.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                                          end_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
                                          frequency="d")
        row = None
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
        if row and row[1] and row[1] not in ("", "0"):
            return float(row[1])
    return None


def fetch_sector_pb(sector: str) -> Optional[dict]:
    """板块 PB = 中证成分股 pbMRQ 中位数（BaoStock，官方源）"""
    code = INDEX_MAP.get(sector)
    if not code:
        return None
    cons = _safe(fetch_constituents, code)
    if not cons:
        return None
    lg = _safe(_bs_login)
    if lg is None:
        return None
    try:
        pb_vals = []
        deadline = time.time() + PB_BUDGET_SECONDS
        for c in cons[:PB_SAMPLE_LIMIT]:
            if time.time() > deadline:
                logger.warning("板块 %s PB 聚合超预算，已采样 %d 只，降级处理", sector, len(pb_vals))
                break
            box = {}

            def worker(code=c):  # 参数绑定防闭包共享循环变量（超时线程仍存活时下一轮 c 已变）
                try:
                    box["v"] = _query_pb(code)
                except Exception:
                    pass  # 单只查询失败=该只放弃，避免 daemon 线程异常刷 stderr

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            # max(0.0, ...)：预算将尽时 timeout 可能算成微负，Python 3.12 的 join 会抛 ValueError
            t.join(timeout=max(0.0, min(PB_QUERY_TIMEOUT, deadline - time.time())))
            v = box.get("v")
            if v is not None:
                pb_vals.append(v)
        if not pb_vals:
            return None
        import statistics
        return {"pb": round(statistics.median(pb_vals), 2), "method": "成分中位数", "n": len(pb_vals)}
    finally:
        bs.logout()

def fetch_price_position(sector: str) -> Optional[dict]:
    """价格位置分位兜底（复用 ltc_data K 线）"""
    k = ltc_data.fetch_board_kline(sector)
    if k is None or len(k) < 60:
        return None
    close = k["close"].values
    pos = (close[-1] - close.min()) / (close.max() - close.min()) * 100 if close.max() > close.min() else 50
    return {"position_pct": round(float(pos), 1)}

def _pb_reference(history: List[dict], sector: str) -> Optional[float]:
    """留痕近 20 日板块 PB 均值（冷启动 None）"""
    vals = [h["sector_pb"][sector] for h in reversed(history)
            if (h.get("sector_pb") or {}).get(sector) is not None]
    if not vals:
        return None
    vals = vals[:20]
    return sum(vals) / len(vals)

def _pb_percentile(history: List[dict], sector: str, cur_pb: float) -> Optional[float]:
    """板块 PB 留痕分位：当前中位 PB 在近一年留痕中的分位（<20 样本 → None 冷启动）。
    与 val_judge 的契约：pb_pct/main_pct 均为分位（0-100），raw PB 比值仅作参考值。"""
    vals = []
    for h in reversed(history):
        v = (h.get("sector_pb") or {}).get(sector)
        if v is not None:
            vals.append(v)
    if len(vals) < 20:
        return None
    vals = vals[:244]  # 近一年留痕窗口
    return round(sum(1 for v in vals if v < cur_pb) / len(vals) * 100, 1)

def fetch_valuation_snapshot(boards: List[str], history: List[dict]) -> List[dict]:
    """全板块三级降级链：PE 分位 → PB 分位 → 价格位置（标注口径）"""
    out = []
    for board in boards:
        pe_series = _safe(fetch_pe_series, INDEX_MAP.get(board, "")) if board in INDEX_MAP else None
        pe_info = pe_percentile(pe_series) if pe_series is not None else None
        if pe_info is not None:
            pb = _safe(fetch_sector_pb, board)
            pb_pct = _safe(_pb_percentile, history, board, pb["pb"]) if pb else None
            if is_cyclical(board) and pb_pct is not None:
                # 规则1：周期板块主指标=PB 分位（PE 分位仅留痕）
                main_pct = pb_pct
                note = f"主指标=PB 分位（PE 分位 {pe_info['pct']:.0f}% 仅留痕）"
            elif is_cyclical(board):
                # PB 分位冷启动 → 临时以 PE 分位为主指标并标注
                main_pct = pe_info["pct"]
                note = "主指标=PE 分位（PB 分位积累中，冷启动降级）"
            else:
                main_pct = pe_info["pct"]
                note = ""
            entry = {
                "board": board, "source": "pe",
                "main_pct": main_pct, "pe_pct": pe_info["pct"],
                "pb_pct": pb_pct,
                "pb": pb["pb"] if pb else None,  # 原始 PB（留痕写入用，C1：sector_pb 须存原始比值）
                "trend": pe_info["trend"], "note": note,
                "years": pe_info["years"],  # 指数历史实际年数（渲染诚实标注窗口用）
            }
            out.append(entry)
            continue
        pb = _safe(fetch_sector_pb, board)
        if pb is not None:
            ref = _safe(_pb_reference, history, board)
            pb_pct = _safe(_pb_percentile, history, board, pb["pb"])
            note = f"PB={pb['pb']}（成分中位数 n={pb.get('n', '?')}）"
            if ref is not None:
                note += f"，近20日均值 {ref:.2f}"
            else:
                note += "，PB 分位积累中"
            if pb_pct is not None:
                note += f"，分位 {pb_pct}%"
            entry = {
                "board": board, "source": "pb",
                "main_pct": pb_pct, "pe_pct": None, "pb_pct": pb_pct,
                "pb": pb["pb"],  # 原始 PB（留痕写入用，C1：sector_pb 须存原始比值）
                "trend": "flat",
                "note": note,
            }
            out.append(entry)
            continue
        price = _safe(fetch_price_position, board)
        entry = {
            "board": board, "source": "price",
            "main_pct": price["position_pct"] if price else None,
            "pe_pct": None, "pb_pct": None, "pb": None, "trend": "flat",
            "note": "基于价格位置，非 PE/PB" if price else "估值数据源全部不可用",
        }
        out.append(entry)
    return out
