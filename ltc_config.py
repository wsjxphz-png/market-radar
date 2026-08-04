# ltc_config.py
"""长线资金观察 — 配置中心：术语表/禁用词/板块/季度背景/阈值"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# 北京时间
def bj_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))

GLOSSARY = {
    "超大单": "单笔 100 万以上的大资金交易，通常被认为代表机构行为",
    "大单": "单笔 20-100 万的中大资金交易，通常被认为代表大户或游资",
    "主力": "超大单+大单的合计",
    "回购": "上市公司用自己的钱从市场买回自家股票，通常被认为代表管理层认为股价被低估",
    "南向": "内地资金买入港股市场的净额（通过港股通）",
    "估值分位": "当前价格在历史区间里的位置，数值越低越便宜",
    "承接": "资金买入行为的持续性，短期/中期/长期",
}

BANNED_PHRASES = [
    "建议买入", "建议加仓", "可以买入", "考虑买入", "考虑加仓", "你要", "你应该",
    "推荐买入", "立即买入", "逢低买入", "重仓买入", "减仓", "赶紧买", "快买",
]

# 估值板块列表（东财行业板块，代码备用）
BOARDS = [
    ("医药生物", "BK0438"), ("银行", "BK0439"), ("非银金融", "BK0440"),
    ("半导体", "BK0441"), ("食品饮料", "BK0442"), ("电力设备", "BK0443"),
    ("煤炭", "BK0444"), ("家电", "BK0445"), ("汽车", "BK0446"), ("国防军工", "BK0447"),
]

REF_RATIO_HIGH = 1.2   # 今日/近20日均值 >= 1.2 → 比平时多
REF_RATIO_LOW = 0.8    # <= 0.8 → 比平时少

# 季报实名背书的行业关键词（季度背景未过期时，作为承接周期判断的背书信号）
BACKING_SECTORS = ["医药", "医疗", "生物", "银行", "公用", "交通"]

CARD_EMOJIS = {"header": "📊", "daily": "📅", "long": "🗓", "warn": "⚠️",
               "bridge": "👉", "honest": "🔎", "up": "📈", "down": "📉", "money": "💰"}

def PHASE_LABEL(text: str) -> str:
    if not text or str(text) in ("nan", "None"):
        return "阶段未知"
    t = str(text)
    if "预案" in t: return "董事会预案"
    if "完成" in t: return "已完成实施"
    if "实施中" in t or "进行中" in t: return "实施中"
    return "阶段未知"

def is_expired(updated: str, days: int = 14) -> bool:
    """季度背景是否过期：updated + days 之后判定过期"""
    try:
        d = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return (bj_now() - d).days > days
    except (ValueError, TypeError):
        return True  # 无法解析/为空视为过期

# 季度背景知识库 — 内置默认（手工维护的历史版本，作为回退兜底）
# 生产使用由 ltc_quarterly.py 每季度自动刷新生成的 data/ltc/quarterly_context.json（FR：季度背景自动刷新）
QUARTERLY_CONTEXT_FALLBACK = {
    "updated": "2026-07-23",
    "next_update": "2026年8月底（半年报全部披露后）",
    "key_facts": {
        "social_security": "社保/养老金 Q2 新增/增持 14 只个股，其中 7 只（50%）为医药生物",
        "insurance": "五大上市险企 Q1 新增仓位集中于银行（+347 亿股）、公用事业、交通运输",
        "mutual_funds": "公募 Q2 电子仓位 43.4%（历史极值），多位明星基金经理警告泡沫风险",
        "buybacks": "药明康德完成 10 亿元回购，美的集团累计回购 67 亿元",
        "national_platform": "中国国新（500亿+）+ 中国诚通（近100亿）入场，使用央行专项再贷款资金",
    },
    "valuation_note": {
        "医药生物": "医药 PE 28-30x（5年第26分位），PB 处 2010 年以来第 3 分位",
        "非银金融": "沪深300 非银 PB 1.26x（近十年 14% 分位）",
        "电子": "公募仓位 43.4%（历史极值），不是便宜，是贵且满仓",
    },
}

def load_quarterly_context(path: str = "data/ltc/quarterly_context.json") -> dict:
    """优先读季度刷新生成的 JSON；缺失/损坏/缺 key_facts 键 → 回退内置默认"""
    try:
        with open(path, encoding="utf-8") as f:
            ctx = json.load(f)
        if isinstance(ctx, dict) and "key_facts" in ctx:
            return ctx
    except (OSError, ValueError):
        pass
    return QUARTERLY_CONTEXT_FALLBACK
