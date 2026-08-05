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
