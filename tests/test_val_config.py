# tests/test_val_config.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from val_config import INDEX_MAP, CYCLICAL_SECTORS, GROWTH_SECTORS, PCT_LOW, PCT_HIGH, PB_VETO_GAP, is_cyclical, index_code_for

def test_index_map_covers_dashboard_boards():
    # 仪表盘 SECTOR_RULES 的 29 板块中，估值表至少覆盖 12 个核心板块
    core = ["半导体", "银行", "医药生物", "食品饮料", "有色金属", "通信设备",
            "计算机", "电力设备", "煤炭", "非银金融", "汽车", "国防军工"]
    for b in core:
        assert b in INDEX_MAP, f"{b} 缺映射"

def test_index_codes_are_6_digit():
    for code in INDEX_MAP.values():
        assert code.isdigit() and len(code) == 6

def test_classification_exhaustive_and_disjoint():
    # 分类表覆盖 INDEX_MAP 全部板块，且周期/成长不重叠
    all_boards = set(INDEX_MAP.keys())
    assert all_boards <= (CYCLICAL_SECTORS | GROWTH_SECTORS)
    assert CYCLICAL_SECTORS.isdisjoint(GROWTH_SECTORS)

def test_is_cyclical():
    assert is_cyclical("银行") is True
    assert is_cyclical("半导体") is False

def test_index_code_for():
    assert index_code_for("银行") == "399986"
    assert index_code_for("不存在板块") is None

def test_thresholds():
    assert PCT_LOW == 25 and PCT_HIGH == 75 and PB_VETO_GAP == 40

# ═══════ Task 1: 口径对齐层 — THS↔东财 12 板块映射 ═══════

def test_ths_em_mapping_covers_12_boards():
    # 估值表 12 个东财板块，每个都有同花顺对应名（仪表盘 SECTOR_RULES 口径）
    from val_config import INDEX_MAP, THS_TO_EM
    for em in INDEX_MAP:
        assert em in THS_TO_EM.values(), f"{em} 缺同花顺映射"

def test_ths_em_keys_all_from_sector_rules():
    # 防 typo：THS_TO_EM 的所有同花顺名必须真实存在于 SECTOR_RULES 名单
    from val_config import THS_TO_EM
    from sector_monitor import SECTOR_RULES
    real = {r["name"] for r in SECTOR_RULES}
    fake = set(THS_TO_EM) - real
    assert not fake, f"映射含非 SECTOR_RULES 名: {fake}"

def test_ths_em_known_diffs():
    # 锁定实际映射（含全部差异名）
    from val_config import em_name_for
    # 同名直映
    assert em_name_for("半导体") == "半导体"
    assert em_name_for("银行") == "银行"
    assert em_name_for("通信设备") == "通信设备"
    # 差异名（同花顺 → 东财一级）
    assert em_name_for("汽车整车") == "汽车"
    assert em_name_for("汽车零部件") == "汽车"
    assert em_name_for("煤炭开采加工") == "煤炭"
    assert em_name_for("证券") == "非银金融"
    assert em_name_for("保险") == "非银金融"
    assert em_name_for("军工装备") == "国防军工"
    assert em_name_for("工业金属") == "有色金属"
    assert em_name_for("白酒") == "食品饮料"
    assert em_name_for("食品加工制造") == "食品饮料"
    assert em_name_for("化学制药") == "医药生物"
    assert em_name_for("中药") == "医药生物"
    assert em_name_for("医疗服务") == "医药生物"
    assert em_name_for("医疗器械") == "医药生物"
    assert em_name_for("IT服务") == "计算机"
    assert em_name_for("软件开发") == "计算机"
    assert em_name_for("电池") == "电力设备"
    assert em_name_for("光伏设备") == "电力设备"
    assert em_name_for("电网设备") == "电力设备"

def test_ths_em_roundtrip_for_all_boards():
    # 每个东财板块反查同花顺名后，必须能映射回自身
    from val_config import INDEX_MAP, em_name_for, ths_name_for
    for em in INDEX_MAP:
        ths = ths_name_for(em)
        assert ths is not None, f"{em} 无反查同花顺名"
        assert em_name_for(ths) == em, f"{em} 反查'{ths}'后无法回到自身"

def test_em_name_for_unknown():
    from val_config import em_name_for, ths_name_for
    assert em_name_for("不存在的板块") is None
    assert ths_name_for("不存在的板块") is None
