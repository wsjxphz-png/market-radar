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

# 同花顺资金流 90 行业实测名单快照（ak.stock_fund_flow_industry，2026-08-06 实测核对）：
# THS_TO_EM 扩展键必须真实存在于此名单或 SECTOR_RULES——防 typo 的完整口径。
# （任务源 90 个；若同花顺改名单，运行一次 stock_fund_flow_industry 更新此快照）
THS_INDUSTRY_UNIVERSE_90 = {
    "IT服务", "专用设备", "中药", "互联网电商", "保险", "元件", "光伏设备", "光学光电子",
    "公路铁路运输", "其他电子", "其他电源设备", "其他社会服务", "养殖业", "军工电子",
    "军工装备", "农产品加工", "农化制品", "包装印刷", "化学制品", "化学制药", "化学原料",
    "化学纤维", "医疗器械", "医疗服务", "医药商业", "半导体", "厨卫电器", "塑料制品",
    "多元金融", "家居用品", "小家电", "小金属", "工业金属", "工程机械", "建筑材料",
    "建筑装饰", "影视院线", "房地产", "教育", "文化传媒", "旅游及酒店", "服装家纺",
    "机场航运", "橡胶制品", "汽车整车", "汽车服务及其他", "汽车零部件", "油气开采及服务",
    "消费电子", "港口航运", "游戏", "煤炭开采加工", "燃气", "物流", "环保设备",
    "环境治理", "生物制品", "电力", "电子化学品", "电机", "电池", "电网设备", "白色家电",
    "白酒", "石油加工贸易", "种植业与林业", "纺织制造", "综合", "美容护理", "能源金属",
    "自动化设备", "计算机设备", "证券", "贵金属", "贸易", "轨交设备", "软件开发",
    "通信服务", "通信设备", "通用设备", "造纸", "金属新材料", "钢铁", "银行", "零售",
    "非金属材料", "风电设备", "食品加工制造", "饮料制造", "黑色家电",
}


def test_ths_em_keys_all_from_real_industries():
    # 防 typo（Task 6 审查 Important 补全后扩展为双口径）：
    # THS_TO_EM 的每个同花顺名必须真实存在于 SECTOR_RULES（会议口径）
    # 或 stock_fund_flow_industry 90 行业实测名单（2026-08-06 快照）
    from val_config import THS_TO_EM
    from sector_monitor import SECTOR_RULES
    real = {r["name"] for r in SECTOR_RULES} | THS_INDUSTRY_UNIVERSE_90
    fake = set(THS_TO_EM) - real
    assert not fake, f"映射含非真实同花顺行业名: {fake}"
    # 反向：12 个东财核心板块每个都必须有同花顺映射（防漏映射）
    from val_config import THS_TO_EM, INDEX_MAP
    for em in INDEX_MAP:
        members = {t for t, e in THS_TO_EM.items() if e == em}
        assert members, f"{em} 无同花顺映射"
    # 已知覆盖边界（90 名单里非 12 核心板块成分的细分允许不映射）：
    # 消费电子/其他电子/军工电子/计算机设备/白色家电/黑色家电/多元金融/医药商业/
    # 生物制品 等留给各自板块监控，不强制

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
    assert em_name_for("汽车服务及其他") == "汽车"        # Task 6 补全
    assert em_name_for("煤炭开采加工") == "煤炭"
    assert em_name_for("证券") == "非银金融"
    assert em_name_for("保险") == "非银金融"
    assert em_name_for("军工装备") == "国防军工"
    assert em_name_for("工业金属") == "有色金属"
    assert em_name_for("贵金属") == "有色金属"             # Task 6 补全
    assert em_name_for("小金属") == "有色金属"
    assert em_name_for("能源金属") == "有色金属"
    assert em_name_for("金属新材料") == "有色金属"
    assert em_name_for("白酒") == "食品饮料"
    assert em_name_for("食品加工制造") == "食品饮料"
    assert em_name_for("饮料制造") == "食品饮料"           # Task 6 补全
    assert em_name_for("化学制药") == "医药生物"
    assert em_name_for("中药") == "医药生物"
    assert em_name_for("医疗服务") == "医药生物"
    assert em_name_for("医疗器械") == "医药生物"
    assert em_name_for("IT服务") == "计算机"
    assert em_name_for("软件开发") == "计算机"
    assert em_name_for("电池") == "电力设备"
    assert em_name_for("光伏设备") == "电力设备"
    assert em_name_for("电网设备") == "电力设备"
    assert em_name_for("风电设备") == "电力设备"           # Task 6 补全
    assert em_name_for("电机") == "电力设备"
    assert em_name_for("其他电源设备") == "电力设备"
    assert em_name_for("电子化学品") == "半导体"           # Task 6 补全：半导体材料（光刻胶/电子特气）

def test_ths_em_no_wrong_level_mapping():
    # 复审 Important（口径纯度，2026-08-06）：面板（京东方/TCL）、PCB/被动元件是
    # "电子"下的二级行业，不是半导体子集——并入会让半导体的资金确认信号被非半导体
    # 资金驱动（每日推送的判断输入）。光学光电子/元件 等必须不映射（None）
    from val_config import em_name_for
    for ths in ("光学光电子", "元件", "消费电子", "其他电子", "军工电子", "计算机设备"):
        assert em_name_for(ths) is None, f"{ths} 不应映射到 12 核心板块（同级错配）"

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
