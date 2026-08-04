# tests/test_ltc_news.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ltc_news import format_news_card, filter_today

def test_format_news_card_marked_degraded():
    card = format_news_card("2026-08-04", [{"title": "A股收评：沪指涨0.33%", "date": "2026-08-04"}])
    assert "数据源暂时不可用" in card
    assert "新闻摘要" in card
    assert "A股收评" in card

def test_filter_today_only():
    titles = [{"title": "a", "date": "2026-08-04"}, {"title": "b", "date": "2026-08-01"}, {"title": "c", "date": ""}]
    out = filter_today(titles, "2026-08-04")
    assert [x["title"] for x in out] == ["a"]
