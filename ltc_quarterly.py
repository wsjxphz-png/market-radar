# ltc_quarterly.py
"""季度背景自动刷新：机构持仓 → 行业聚合 → 自动校验 → JSON 落地（替代手工维护 QUARTERLY_CONTEXT）
用户零介入：workflow 每季度披露窗口触发，有告警只打印不写文件（异常才需人工看日志）"""
import json, logging, os, re, sys
from typing import Dict, List, Optional
import pandas as pd
import akshare as ak
from ltc_config import bj_now

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = "data/ltc/quarterly_context.json"
GROUPS = ("social_security", "insurance", "mutual_funds")

# 列名兼容：不同 akshare 版本的差异名 → 规范名（实测 1.18.x 为"期末持股-数量/数量变化/持股变动/流通市值"）
_COL_ALIASES = {
    "期末持股-数量变化": ("期末持股-数量变化", "期末持股-股数变化", "数量变化"),
    "期末持股-持股变动": ("期末持股-持股变动", "持股变动"),
    "期末持股-流通市值": ("期末持股-流通市值", "流通市值"),
}

def fetch_holdings(report_date: str) -> pd.DataFrame:
    """东方财富数据中心-机构持股明细；失败返回空 DataFrame（不阻塞，校验会告警）"""
    try:
        df = ak.stock_gdfx_free_holding_detail_em(date=report_date)
    except Exception as e:
        logger.warning("机构持仓拉取失败 %s: %s", report_date, str(e)[:80])
        return pd.DataFrame()
    if df is None or len(df) == 0:
        logger.warning("机构持仓为空: %s", report_date)
        return pd.DataFrame()
    # 列名归一（跨版本防御）
    rename = {}
    for canon, aliases in _COL_ALIASES.items():
        for a in aliases:
            if a in df.columns:
                rename[a] = canon
                break
    out = df.rename(columns=rename)
    keep = [c for c in ("股东类型", "股东名称", "股票代码", "股票简称", "期末持股-数量变化",
                        "期末持股-持股变动", "期末持股-流通市值") if c in out.columns]
    return out[keep].copy()

# 国民经济行业分类（GB/T 4754）门类码前缀：如 "C39计算机、通信和其他电子设备制造业" → "计算机、通信和其他电子设备制造业"
_PREFIX_RE = re.compile(r"^[A-Z]\d{2}")

def _bse_list() -> tuple:
    """北交所官网上市公司列表（akshare 包装，官方数据源）→ (代码→行业, 简称→代码)。
    失败 → ({}, {})，不阻塞主流程（校验会告警覆盖率不足）"""
    try:
        bj = ak.stock_info_bj_name_code()
    except Exception as e:
        logger.warning("北交所行业列表失败: %s", str(e)[:100])
        return {}, {}
    ind_by_code: Dict[str, str] = {}
    code_by_name: Dict[str, str] = {}
    for _, r in bj.iterrows():
        ind = str(r["所属行业"])
        code = str(r["证券代码"]).zfill(6)
        name = str(r["证券简称"])
        if ind != "nan" and ind:
            ind_by_code[code] = ind
        if name != "nan" and name:
            code_by_name.setdefault(name, code)
    return ind_by_code, code_by_name

def _merge_supplement(mapping: Dict[str, str], name_to_code: Dict[str, str],
                      df: Optional[pd.DataFrame]) -> Dict[str, str]:
    """补充两处 BaoStock 覆盖不到的场景（失败均静默，覆盖率校验兜底）：
    1) 北交所新代码段（920xxx，2024 换码后）— 北交所官网列表直接映射；
    2) 东财持仓明细里的旧代码（43x/83x/87x：换码前北交所、转板/退市前新三板）
       — 按"股票简称"翻译到当前代码后取行业（名称冲突取首个，找不到则跳过）"""
    out = dict(mapping)
    bse_ind, bse_name = _bse_list()
    out.update(bse_ind)
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        code = str(r.get("股票代码", "")).zfill(6)
        if code in out or code[0] not in "48":
            continue
        name = str(r.get("股票简称", ""))
        new_code = bse_name.get(name) or name_to_code.get(name)
        if new_code and new_code in out:
            out[code] = out[new_code]
    return out

def build_industry_map(df: Optional[pd.DataFrame] = None) -> dict:
    """行业映射：BaoStock 官方免费服务（沪深，不走爬虫、不封 IP）+ 北交所官网列表。
    返回 {6位股票代码: 行业名}：代码带交易所前缀（sh./sz.）统一剥离为 6 位，与东财
    持仓明细的"股票代码"对齐；行业取国民经济行业分类名并剥离开头"字母+两位数字"
    门类码（如 C39），空行业跳过。df 为东财持仓明细（含股票简称）时补翻译旧代码。
    登录失败/拉取异常 → {}（校验会告警覆盖率不足）"""
    import baostock as bs
    try:
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning("BaoStock 登录失败: %s", lg.error_msg)
            return {}
        try:
            rs = bs.query_stock_industry()
            mapping: Dict[str, str] = {}
            name_to_code: Dict[str, str] = {}   # 简称 → 当前代码（换码/转板股翻译用）
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                # fields: updateDate, code(sh.600000), code_name, industry, industryClassification
                code = row[1]
                industry = row[3]
                if not industry:
                    continue
                plain = code[3:]
                mapping[plain] = _PREFIX_RE.sub("", industry)
                name_to_code.setdefault(row[2], plain)
            out = _merge_supplement(mapping, name_to_code, df)
            logger.info("行业映射：BaoStock %d 条，合并补充后共 %d 条", len(mapping), len(out))
            return out
        finally:
            bs.logout()
    except Exception as e:
        logger.warning("BaoStock 行业映射失败: %s", str(e)[:120])
        return {}

def _group_of(row: pd.Series) -> Optional[str]:
    """股东类型归类：社保|养老 / 保险 / 基金；其他忽略（类型列缺失时看股东名称）"""
    t = str(row.get("股东类型", ""))
    if t == "nan" or not t:
        t = str(row.get("股东名称", ""))
    if "社保" in t or "养老" in t:
        return "social_security"
    if "保险" in t:
        return "insurance"
    if "基金" in t:
        return "mutual_funds"
    return None

def aggregate_by_type(df: pd.DataFrame, industry_map: dict) -> dict:
    """按股东类型分组聚合：机构家数 + 行业分布（增持家数 / 流通市值合计）
    add_or_increase 口径：股数变化 > 0 或（变化为 NaN 且变动=新进）——已实测 NaN 变化行全部为"新进"，
    与"新增/增持"语义一致；total_mktcap = 流通市值合计（亿元，1 位小数）"""
    groups: Dict[str, dict] = {g: {"institutions": 0, "industries": {}} for g in GROUPS}
    mapped = unmapped = total = 0
    for _, row in df.iterrows():
        g = _group_of(row)
        if g is None:
            continue
        total += 1
        groups[g]["institutions"] += 1  # 家数按全部同类机构计，与行业映射无关
        code = str(row.get("股票代码", "")).zfill(6)
        ind = industry_map.get(code)
        if ind is None or str(ind) in ("nan", ""):
            unmapped += 1
            continue
        mapped += 1
        delta = pd.to_numeric(row.get("期末持股-数量变化"), errors="coerce")
        mkt = pd.to_numeric(row.get("期末持股-流通市值"), errors="coerce")
        add = bool((pd.notna(delta) and delta > 0) or
                   (pd.isna(delta) and str(row.get("期末持股-持股变动", "")) == "新进"))
        stat = groups[g]["industries"].setdefault(str(ind), {"add_or_increase": 0, "total_mktcap_yi": 0.0})
        if add:
            stat["add_or_increase"] += 1
        if pd.notna(mkt):
            stat["total_mktcap_yi"] = round(stat["total_mktcap_yi"] + mkt / 1e8, 1)
    groups["_meta"] = {"total": total, "mapped": mapped, "unmapped": unmapped,
                       "coverage": mapped / total if total else 1.0}
    return groups

def _next_update(report_date: str) -> str:
    """按报告期推算下次披露窗口（本次数据披露完成后，下一个披露期）"""
    y, m = int(report_date[:4]), int(report_date[4:6])
    if m <= 3:
        return f"{y}年8月底（中报披露后）"
    if m <= 6:
        return f"{y}年11月中（三季报披露后）"
    if m <= 9:
        return f"{y + 1}年4月底（年报披露后）"
    return f"{y + 1}年4月底（一季报披露后）"

def _top_industries(group: dict, min_add: int = 0, top_n: int = 3) -> List[tuple]:
    """行业 top3：按增持家数降序，同家数按流通市值降序；min_add=1 时只保留有增持的行业"""
    ranked = sorted(group.get("industries", {}).items(),
                    key=lambda kv: (-kv[1]["add_or_increase"], -kv[1]["total_mktcap_yi"], kv[0]))
    return [(k, v) for k, v in ranked if v["add_or_increase"] >= min_add][:top_n]

def _industry_text(group: dict, min_add: int = 0) -> str:
    top = _top_industries(group, min_add=min_add)
    return "、".join(f"{k}（{v['add_or_increase']}家）" for k, v in top)

def generate_context(agg: dict, report_date: str, today: str) -> dict:
    """生成与 QUARTERLY_CONTEXT 兼容的季度背景结构（只写可计算的持仓分布，不编造仓位百分比）"""
    ss, ins, mf = (agg.get(g, {}) for g in GROUPS)
    ss_top = _top_industries(ss, min_add=1)
    ss_text = f"社保/养老基金 {ss.get('institutions', 0)} 家持仓 {report_date} 报告期，新增/增持集中在：" + _industry_text(ss, min_add=1) \
        if ss_top else f"社保/养老基金 {ss.get('institutions', 0)} 家持仓 {report_date} 报告期，本期无新增/增持记录"
    ins_text = "保险资金持仓集中：" + _industry_text(ins) if _top_industries(ins) else "保险资金本期无有效持仓数据"
    mf_text = "公募基金持仓集中：" + _industry_text(mf) if _top_industries(mf) else "公募基金本期无有效持仓数据"
    return {
        "updated": today,
        "report_date": report_date,
        "next_update": _next_update(report_date),
        "key_facts": {"social_security": ss_text, "insurance": ins_text, "mutual_funds": mf_text},
        "sources": ["东方财富数据中心-机构持股明细", "BaoStock/北交所官网 行业映射", f"报告期 {report_date}"],
    }

def validate(agg: dict, industry_map: dict, report_date: str) -> List[str]:
    """校验告警列表（空 = 通过）；任何告警 → 不写文件"""
    warnings = []
    if not re.match(r"^\d{8}$", str(report_date or "")):
        warnings.append(f"报告期格式非 YYYYMMDD：{report_date}")
    meta = agg.get("_meta", {})
    total = meta.get("total", 0)
    coverage = meta.get("coverage", 1.0) if total else 1.0
    if total and coverage < 0.95:
        warnings.append(f"行业映射覆盖率 {coverage:.0%} < 95%（映射 {meta.get('mapped')}/{total}）")
    if industry_map is None or len(industry_map) == 0:
        warnings.append("行业映射为空（BaoStock 拉取失败）")
    if not agg.get("social_security", {}).get("institutions"):
        warnings.append("社保|养老机构数为 0，季度背景缺失社保数据")
    if not any(agg.get(g, {}).get("institutions") for g in GROUPS):
        warnings.append("聚合结果全空（无有效机构持仓数据）")
    return warnings

def _latest_report_date(today: str) -> str:
    """最近披露完成的季度末（今天 2026-08-05 → 20260630）"""
    y, m = int(today[:4]), int(today[5:7])
    if m <= 3:
        return f"{y - 1}1231"
    if m <= 6:
        return f"{y}0331"
    if m <= 9:
        return f"{y}0630"
    return f"{y}0930"

def main(report_date: Optional[str] = None, output_path: str = DEFAULT_OUTPUT,
         today: Optional[str] = None) -> int:
    """全流程：抓取 → 行业映射 → 聚合 → 生成 → 校验 → 写 JSON
    有告警：打印并返回 1（不写文件）；无告警：写文件返回 0"""
    today = today or bj_now().strftime("%Y-%m-%d")
    report_date = report_date or _latest_report_date(today)
    print(f"[ltc_quarterly] report_date={report_date} today={today} output={output_path}")
    df = fetch_holdings(report_date)
    industry_map = build_industry_map(df)
    agg = aggregate_by_type(df, industry_map)
    print(f"[ltc_quarterly] 机构持仓 {len(df)} 行 → 聚合 {agg['_meta']['mapped']}/{agg['_meta']['total']} 已映射（覆盖率 {agg['_meta']['coverage']:.0%}）")
    warnings = validate(agg, industry_map, report_date)
    for w in warnings:
        print(f"[ltc_quarterly] ⚠️ 告警：{w}")
    if warnings:
        print("[ltc_quarterly] 存在告警，不写文件")
        return 1
    ctx = generate_context(agg, report_date, today)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)
    print(f"[ltc_quarterly] 已写入 {output_path}")
    print("[ltc_quarterly] key_facts:")
    for k, v in ctx["key_facts"].items():
        print(f"  {k}: {v}")
    print("[ltc_quarterly] validate：通过（无告警）")
    return 0

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = [a for a in sys.argv[1:] if a]
    raise SystemExit(main(args[0] if args else None, args[1] if len(args) > 1 else DEFAULT_OUTPUT))
