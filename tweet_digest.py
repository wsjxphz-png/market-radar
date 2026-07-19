#!/usr/bin/env python3
"""每日推文摘要 — 抓取Mimiwftt当日新推文，分类整理后推送飞书。"""
import json, os, sys, io, re
from datetime import datetime, timedelta
from collections import defaultdict
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_FILE = r"C:\Users\Administrator\Mimiwftt_clean.json"
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID") or "oc_94e85ee81df40d0ac71c358861427b06"

# 分类关键词
CATEGORIES = {
    "交易方法论": ["选股", "买点", "卖点", "止损", "止盈", "仓位", "均线", "MACD", "RSI",
                   "量价", "背离", "回踩", "突破", "支撑", "压力", "趋势", "主升浪",
                   "EXPMA", "斜率", "筹码", "缠论", "背驰", "中枢", "缺口", "复盘"],
    "板块与行业": ["半导体", "芯片", "科技", "AI", "算力", "机器人", "军工", "医药",
                   "消费", "新能源", "金融", "证券", "银行", "保险", "周期", "有色",
                   "农业", "电力", "科创", "恒生", "创业板"],
    "宏观与策略": ["流动性", "M1", "M2", "降息", "加息", "政策", "牛市", "熊市",
                   "美联储", "汇率", "美元", "通胀", "GDP", "PMI", "社融"],
    "心态与纪律": ["心态", "耐心", "纪律", "恐惧", "贪婪", "情绪", "人性",
                   "割肉", "追涨杀跌", "等待", "执行", "坚持", "认知"],
    "实盘与案例": ["举例", "案例", "比如", "像.*一样", "这个票", "关注.*标的"],
    "其他": []
}

def categorize(text):
    for cat, keywords in CATEGORIES.items():
        if cat == "其他": continue
        for kw in keywords:
            if kw in text:
                return cat
    return "其他"

def send_feishu_card(content):
    if not FEISHU_APP_ID:
        return False
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=15)
        if resp.status_code != 200: return False
        token = resp.json().get("tenant_access_token")
    except: return False

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📡 Mi姐今日推文摘要"}},
        "elements": [{"tag": "markdown", "content": content}]
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            json={"receive_id": FEISHU_CHAT_ID, "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
        return resp.status_code == 200 and resp.json().get("code") == 0
    except: return False

def main():
    # 加载数据
    if not os.path.exists(DATA_FILE):
        print("No data file"); return
    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)

    # 筛选今日推文（北京时间）
    today = (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(hours=32)).strftime("%Y-%m-%d")
    todays = [t for t in data if t.get('created_at_iso', '')[:10] in (today, yesterday)]
    # 取最近24小时内
    cutoff = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M")
    todays = [t for t in todays if t.get('created_at_iso', '')[:16] >= cutoff]

    if not todays:
        print(f"No new tweets for {today}"); return

    # 分类
    by_cat = defaultdict(list)
    for t in todays:
        cat = categorize(t['text'])
        by_cat[cat].append(t)

    total = len(todays)
    date_str = today

    lines = [
        f"**{date_str}**  |  {total} 条新推文",
        "",
    ]

    cat_order = ["交易方法论", "板块与行业", "宏观与策略", "实盘与案例", "心态与纪律", "其他"]
    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items: continue
        # 按收藏数排序
        items.sort(key=lambda x: x.get('bookmarks', 0), reverse=True)
        lines.append(f"**{cat}** ({len(items)}条)")
        lines.append("")
        for t in items[:8]:  # 每类最多8条
            bk = t.get('bookmarks', 0)
            txt = t['text'].replace('\n', ' ')[:120]
            bk_str = f"🔖{bk} " if bk >= 10 else ""
            lines.append(f"- {bk_str}{txt}")
            if len(t['text']) > 120:
                lines.append(f"  ...({len(t['text'])}字)")
        lines.append("")

    lines.append("---")
    lines.append(f"*数据来源：Mimiwftt (@Mimiwftt) | 每日自动整理*")

    content = "\n".join(lines)
    print(content)

    # 推送飞书
    ok = send_feishu_card(content)
    print(f"\n[>] Feishu: {'OK' if ok else 'FAIL (no API key locally, works in GitHub Actions)'}")


if __name__ == "__main__":
    main()
