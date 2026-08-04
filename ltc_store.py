# ltc_store.py
"""留痕/状态/参照计算/推送决策（JSONL + state.json）"""
import json, os
from typing import Dict, List, Optional
from ltc_config import REF_RATIO_HIGH, REF_RATIO_LOW

def _ensure_dir(path: str) -> None:
    """裸文件名（无目录）时跳过 makedirs，避免 makedirs('') 崩溃"""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(path: str, state: dict) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_history(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows

def append_history(path: str, entry: dict) -> None:
    _ensure_dir(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def should_push(data_date: str, state: dict) -> tuple:
    last = state.get("last_pushed_date", "")
    if not last:
        return True, "首次运行"
    if data_date > last:
        return True, "新数据"
    if data_date == last:
        return False, "重复"
    return False, "数据落后"

def compute_reference(history: List[dict], key: str, days: int = 20) -> Optional[float]:
    """取最近 days 条含非空 key 的记录求均值"""
    vals = [float(h[key]) for h in reversed(history) if h.get(key) is not None]
    if not vals:
        return None
    vals = vals[:days]
    return sum(vals) / len(vals) if vals else None

def reference_label(today: float, ref: Optional[float]) -> str:
    if ref is None or ref == 0:
        return "参照积累中"
    ratio = today / ref
    if ratio >= REF_RATIO_HIGH:
        return "比平时多"
    if ratio <= REF_RATIO_LOW:
        return "比平时少"
    return "正常"
