"""新闻降级卡（Level 2）：主数据源失效时，用 RSSHub 快讯做定性兜底"""
import json, logging
from datetime import datetime
from typing import List
import feedparser, requests

logger = logging.getLogger(__name__)

RSSHUB_FEEDS = [
    ("财联社电报", "cls/telegraph"),
    ("金十数据", "jin10"),
    ("格隆汇快讯", "gelonghui/live"),
]

def _rsshub_instances() -> List[str]:
    try:
        with open("sources.json", "r", encoding="utf-8") as f:
            return json.load(f).get("sources", {}).get("rsshub_instances",
                ["https://rsshub.app", "https://rsshub.pseudoyu.com"])
    except Exception:
        return ["https://rsshub.app", "https://rsshub.pseudoyu.com"]

def filter_today(titles: List[dict], today_cn: str) -> List[dict]:
    return [t for t in titles if t.get("date", "")[:10] == today_cn]

def fetch_news_titles(today_cn: str, limit: int = 15) -> List[dict]:
    """多 RSSHub 实例 × 多源，取当天标题；任一源成功即返回"""
    seen, out = set(), []
    for inst in _rsshub_instances():
        if len(out) >= limit:
            break
        for name, path in RSSHUB_FEEDS:
            try:
                resp = requests.get(f"{inst}/{path}", timeout=10)
                if resp.status_code != 200:
                    continue
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:limit]:
                    d = entry.get("published") or entry.get("updated") or ""
                    date = ""
                    try:
                        date = datetime(*entry.get("published_parsed", (0, 0, 0, 0, 0, 0, 0, 0, 0))[:6]).strftime("%Y-%m-%d")
                    except Exception:
                        pass
                    title = entry.get("title", "").strip()
                    if title and title not in seen and date[:10] == today_cn:
                        seen.add(title)
                        out.append({"title": title, "date": date, "source": name})
            except Exception as e:
                logger.warning("rsshub %s/%s failed: %s", inst, path, str(e)[:80])
    return out[:limit]

def format_news_card(data_date: str, titles: List[dict]) -> str:
    lines = [f"{'⚠️'} 每日资金观察 · {data_date}（新闻摘要版）", "",
             "⚠️ 数据源暂时不可用，以下为当日新闻摘要，不含数据监测。", ""]
    if not titles:
        lines.append("今日未能获取新闻。")
        return "\n".join(lines)
    for t in titles[:12]:
        lines.append(f"- [{t.get('source','')}] {t['title']}")
    lines.append("")
    lines.append("新闻仅作定性参考；本内容不构成任何买卖建议。")
    return "\n".join(lines)
