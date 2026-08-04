# tests/test_ltc_quarterly.py
"""季度背景自动刷新：聚合/生成/校验/加载回退（不测网络）"""
import sys, os, json
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_config import load_quarterly_context, QUARTERLY_CONTEXT_FALLBACK
from ltc_quarterly import aggregate_by_type, generate_context, validate

# ── 假数据：社保/养老 3、保险 1、基金 2、其他 2（个人/证券公司，应被忽略）、1 个未映射代码 ──
def _fake_holdings() -> pd.DataFrame:
    return pd.DataFrame([
        {"股东类型": "全国社保基金", "股东名称": "全国社保基金一一八组合", "股票代码": "600001",
         "期末持股-数量变化": 1000.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 10.0e8},
        {"股东类型": "基本养老基金", "股东名称": "基本养老保险基金八零二组合", "股票代码": "600002",
         "期末持股-数量变化": None, "期末持股-持股变动": "新进", "期末持股-流通市值": 5.0e8},
        {"股东类型": "全国社保基金", "股东名称": "全国社保基金一零一组合", "股票代码": "600003",
         "期末持股-数量变化": -500.0, "期末持股-持股变动": "减少", "期末持股-流通市值": 3.0e8},
        {"股东类型": "保险公司", "股东名称": "中国人寿", "股票代码": "600004",
         "期末持股-数量变化": 2000.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 20.0e8},
        {"股东类型": "证券投资基金", "股东名称": "易方达蓝筹精选", "股票代码": "600001",
         "期末持股-数量变化": 300.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 1.5e8},
        {"股东类型": "证券投资基金", "股东名称": "某某基金", "股票代码": "600005",
         "期末持股-数量变化": -100.0, "期末持股-持股变动": "减少", "期末持股-流通市值": 2.0e8},
        {"股东类型": "证券投资基金", "股东名称": "华夏成长", "股票代码": "600777",
         "期末持股-数量变化": 100.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 8.0e8},  # 未映射代码
        {"股东类型": "个人", "股东名称": "张三", "股票代码": "600006",
         "期末持股-数量变化": 100.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 1.0e8},
        {"股东类型": "证券公司", "股东名称": "某券商", "股票代码": "600999",
         "期末持股-数量变化": 10.0, "期末持股-持股变动": "增加", "期末持股-流通市值": 1.0e8},
    ])

MAP = {"600001": "医药生物", "600002": "医药生物", "600003": "银行",
       "600004": "银行", "600005": "电子", "600006": "电子"}

# ── aggregate_by_type ──
def test_aggregate_grouping_and_add_counts():
    agg = aggregate_by_type(_fake_holdings(), MAP)
    assert agg["social_security"]["institutions"] == 3
    assert agg["insurance"]["institutions"] == 1
    assert agg["mutual_funds"]["institutions"] == 3
    assert "other" not in agg                     # 个人/证券公司 被忽略
    # 增持 + 新进算新增；减持/不变不算
    assert agg["social_security"]["industries"]["医药生物"]["add_or_increase"] == 2
    assert agg["social_security"]["industries"]["银行"]["add_or_increase"] == 0
    assert agg["mutual_funds"]["industries"]["电子"]["add_or_increase"] == 0

def test_aggregate_mktcap_sum():
    agg = aggregate_by_type(_fake_holdings(), MAP)
    assert agg["social_security"]["industries"]["医药生物"]["total_mktcap_yi"] == 15.0
    assert agg["insurance"]["industries"]["银行"]["total_mktcap_yi"] == 20.0

def test_aggregate_new_position_counts_as_add():
    # 数据口径：数量变化为 NaN 的行全部是"新进"（已实测），新进与增持一样算新增
    agg = aggregate_by_type(_fake_holdings(), MAP)
    assert agg["social_security"]["industries"]["医药生物"]["add_or_increase"] == 2

def test_aggregate_coverage_meta():
    agg = aggregate_by_type(_fake_holdings(), MAP)
    assert agg["_meta"]["total"] == 7 and agg["_meta"]["mapped"] == 6 and agg["_meta"]["unmapped"] == 1
    assert abs(agg["_meta"]["coverage"] - 6 / 7) < 1e-6

# ── generate_context ──
def _agg_full() -> dict:
    return {
        "social_security": {"institutions": 3, "industries": {
            "医药生物": {"add_or_increase": 2, "total_mktcap_yi": 15.0},
            "银行": {"add_or_increase": 0, "total_mktcap_yi": 3.0}}},
        "insurance": {"institutions": 1, "industries": {
            "银行": {"add_or_increase": 1, "total_mktcap_yi": 20.0}}},
        "mutual_funds": {"institutions": 2, "industries": {
            "电子": {"add_or_increase": 0, "total_mktcap_yi": 2.0},
            "医药生物": {"add_or_increase": 1, "total_mktcap_yi": 1.5},
            "银行": {"add_or_increase": 0, "total_mktcap_yi": 3.0},
            "国防军工": {"add_or_increase": 0, "total_mktcap_yi": 4.0}}},
        "_meta": {"total": 6, "mapped": 6, "unmapped": 0, "coverage": 1.0},
    }

def test_generate_context_structure():
    ctx = generate_context(_agg_full(), "20260630", "2026-08-05")
    assert ctx["updated"] == "2026-08-05"
    assert ctx["report_date"] == "20260630"
    assert ctx["next_update"] == "2026年11月中（三季报披露后）"
    assert ctx["sources"] == ["东方财富数据中心-机构持股明细", "报告期 20260630"]
    for k in ("social_security", "insurance", "mutual_funds"):
        assert k in ctx["key_facts"]

def test_generate_context_social_text():
    ctx = generate_context(_agg_full(), "20260630", "2026-08-05")
    t = ctx["key_facts"]["social_security"]
    assert "社保/养老基金 3 家持仓 20260630 报告期" in t
    assert "医药生物（2家）" in t
    assert "银行" not in t  # 无增持的行业不进"新增/增持集中在"

def test_generate_context_top3_order_and_tiebreak():
    ctx = generate_context(_agg_full(), "20260630", "2026-08-05")
    t = ctx["key_facts"]["mutual_funds"]
    # top3 按增持家数降序，同家数按流通市值降序
    assert t.index("医药生物") < t.index("国防军工") < t.index("银行")
    assert "医药生物（1家）" in t and "国防军工（0家）" in t and "银行（0家）" in t

def test_generate_context_no_fabricated_percent():
    ctx = generate_context(_agg_full(), "20260630", "2026-08-05")
    for v in ctx["key_facts"].values():
        assert "%" not in v  # 不编造基金仓位百分比类数据

def test_generate_context_next_update_mapping():
    assert generate_context(_agg_full(), "20260331", "2026-05-06")["next_update"] == "2026年8月底（中报披露后）"
    assert generate_context(_agg_full(), "20261231", "2027-01-15")["next_update"] == "2027年4月底（一季报披露后）"

# ── validate ──
def _agg_with(coverage: float = 1.0, ss_inst: int = 5) -> dict:
    return {
        "social_security": {"institutions": ss_inst, "industries": {"医药生物": {"add_or_increase": 2, "total_mktcap_yi": 100.0}}},
        "insurance": {"institutions": 2, "industries": {"银行": {"add_or_increase": 1, "total_mktcap_yi": 50.0}}},
        "mutual_funds": {"institutions": 3, "industries": {"电子": {"add_or_increase": 3, "total_mktcap_yi": 60.0}}},
        "_meta": {"total": 10, "mapped": round(coverage * 10), "unmapped": 10 - round(coverage * 10), "coverage": coverage},
    }

def test_validate_low_coverage_warns():
    warns = validate(_agg_with(coverage=0.9), MAP, "20260630")
    assert any("覆盖率" in w for w in warns)

def test_validate_full_coverage_passes():
    assert validate(_agg_with(coverage=1.0), MAP, "20260630") == []

def test_validate_social_security_zero_warns():
    warns = validate(_agg_with(ss_inst=0), MAP, "20260630")
    assert any("社保" in w for w in warns)

def test_validate_empty_agg_warns():
    empty = {"social_security": {"institutions": 0, "industries": {}},
             "insurance": {"institutions": 0, "industries": {}},
             "mutual_funds": {"institutions": 0, "industries": {}},
             "_meta": {"total": 0, "mapped": 0, "unmapped": 0, "coverage": 1.0}}
    warns = validate(empty, MAP, "20260630")
    assert any("全空" in w for w in warns)

def test_validate_bad_report_date_warns():
    warns = validate(_agg_with(coverage=1.0), MAP, "2026/06/30")
    assert any("报告期格式" in w for w in warns)

def test_validate_empty_industry_map_warns():
    warns = validate(_agg_with(coverage=1.0), {}, "20260630")
    assert any("行业映射为空" in w for w in warns)

# ── load_quarterly_context（ltc_config）──
def test_load_quarterly_context_from_file(tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps({"updated": "2026-08-05", "key_facts": {"a": "b"}, "extra": 1}), encoding="utf-8")
    ctx = load_quarterly_context(str(p))
    assert ctx["updated"] == "2026-08-05" and ctx["extra"] == 1

def test_load_quarterly_context_missing_falls_back(tmp_path):
    assert load_quarterly_context(str(tmp_path / "nope.json")) == QUARTERLY_CONTEXT_FALLBACK

def test_load_quarterly_context_corrupt_falls_back(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_quarterly_context(str(p)) == QUARTERLY_CONTEXT_FALLBACK

def test_load_quarterly_context_missing_key_facts_falls_back(tmp_path):
    p = tmp_path / "nokf.json"
    p.write_text(json.dumps({"updated": "2026-08-05"}), encoding="utf-8")
    assert load_quarterly_context(str(p)) == QUARTERLY_CONTEXT_FALLBACK
