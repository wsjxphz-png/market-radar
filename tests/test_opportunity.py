# tests/test_opportunity.py — 市场机会发现系统（main.py）纯函数测试，全部不触网
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

import pytest

import main

# ============================================================
# O10-1: 预过滤逻辑（注入假条目断言过滤规则）
# ============================================================

def _item(**kw):
    base = {"pub_date": datetime.now(timezone.utc).isoformat(),
            "url": "https://example.com/x", "title": "测试条目",
            "description": "", "platform": "rss_global", "source_name": "测试",
            "category": "macro"}
    base.update(kw)
    return base


def test_pre_filter_keeps_fresh_non_spam():
    items = [
        _item(title="这是一个正常内容条目", url="https://a.com/1"),
        _item(title="过期内容条目", pub_date=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()),
        _item(title="荐股大师带你飞", url="https://a.com/2"),
        _item(title="重复条目内容", url="https://a.com/3"),
        _item(title="重复条目内容", url="https://a.com/3"),
    ]
    filtered, old_c, spam_c = main.pre_filter(items)
    # 去重保留一份重复条目（URL 去重），其余被正确剔除
    assert [i["title"] for i in filtered] == ["这是一个正常内容条目", "重复条目内容"]
    assert old_c == 1
    assert spam_c == 1


def test_pre_filter_removes_spam_keywords():
    items = [_item(title="日入过万 扫码进群", url="https://a.com/1")]
    filtered, _, spam_c = main.pre_filter(items)
    assert filtered == []
    assert spam_c == 1


# ============================================================
# O10-2: RSS 解析降级 / RSSHub 实例熔断（mock feedparser + requests）
# ============================================================

def _reset_rss_state():
    main._RSS_INSTANCE_BLOCKED.clear()
    main._RSS_INSTANCE_FAILS.clear()


def test_fetch_rss_fallback_success(monkeypatch):
    _reset_rss_state()
    entries = [types.SimpleNamespace(published_parsed=(2026, 8, 5, 12, 0, 0),
                                     title="t", link="l", id="i", summary="s")]
    monkeypatch.setattr(main.feedparser, "parse", lambda content: types.SimpleNamespace(entries=entries))
    calls = []
    def fake_get(url, headers=None, timeout=30):
        calls.append(url)
        if "rsshub.app" in url:
            raise main.requests.exceptions.ConnectionError("conn refused")
        return types.SimpleNamespace(status_code=200, content=b"")
    monkeypatch.setattr(main.requests, "get", fake_get)
    task = {"url": "https://rsshub.app/cls/telegraph",
            "alt_urls": ["https://rsshub2.example/cls/telegraph"],
            "source_name": "财联社", "platform": "rss_cn", "category": "news_cn"}
    feed = main.fetch_rss(task)
    assert feed is not None
    assert task["_used_fallback"] == "https://rsshub2.example/cls/telegraph"
    # 连接错误不重试烧时间：只请求了 2 次（第一实例失败立即换）
    assert len(calls) == 2


def test_fetch_rss_circuit_breaker_blocks_dead_instance(monkeypatch):
    _reset_rss_state()
    entries = [_FakeEntry(published_parsed=(2026, 8, 5, 12, 0, 0), title="t", link="l", id="i", summary="s")]
    monkeypatch.setattr(main.feedparser, "parse", lambda content: types.SimpleNamespace(entries=entries))
    calls = []
    def fake_get(url, headers=None, timeout=30):
        calls.append(url)
        if "rsshub2.example" in url:
            return types.SimpleNamespace(status_code=200, content=b"")
        raise main.requests.exceptions.ConnectionError("conn refused")
    monkeypatch.setattr(main.requests, "get", fake_get)
    t1 = {"url": "https://rsshub.app/a", "alt_urls": ["https://rsshub2.example/a"],
          "source_name": "s1", "platform": "rss_cn", "category": "news_cn"}
    t2 = {"url": "https://rsshub.app/b", "alt_urls": ["https://rsshub2.example/b"],
          "source_name": "s2", "platform": "rss_cn", "category": "news_cn"}
    assert main.fetch_rss(t1) is not None  # 坏实例失败 → 备用实例成功
    assert main.fetch_rss(t2) is not None
    # 连续 2 个源在 rsshub.app 失败 → 该实例熔断；备用实例正常不被熔断
    assert "rsshub.app" in main._RSS_INSTANCE_BLOCKED
    assert "rsshub2.example" not in main._RSS_INSTANCE_BLOCKED
    # 第 3 个源：已熔断实例直接跳过，只请求备用实例
    t3 = {"url": "https://rsshub.app/c", "alt_urls": ["https://rsshub2.example/c"],
          "source_name": "s3", "platform": "rss_cn", "category": "news_cn"}
    assert main.fetch_rss(t3) is not None
    assert calls[-1] == "https://rsshub2.example/c"
    # 全程 rsshub.app 只被请求了 2 次（t1/t2 各 1 次），t3 直接跳过
    assert sum(1 for c in calls if "rsshub.app" in c) == 2


def test_fetch_rss_200_empty_feed_is_not_instance_fault(monkeypatch):
    _reset_rss_state()
    monkeypatch.setattr(main.requests, "get", lambda url, headers=None, timeout=30: types.SimpleNamespace(status_code=200, content=b""))
    monkeypatch.setattr(main.feedparser, "parse", lambda content: types.SimpleNamespace(entries=[]))
    t = {"url": "https://rsshub.app/x", "alt_urls": [], "source_name": "s", "platform": "rss_global"}
    assert main.fetch_rss(t) is None
    assert "rsshub.app" not in main._RSS_INSTANCE_BLOCKED
    assert main._RSS_INSTANCE_FAILS.get("rsshub.app", 0) == 0


def test_fetch_all_records_source_stats(monkeypatch):
    monkeypatch.setattr(main, "fetch_rss", lambda task: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    tasks = [
        {"url": "https://a", "source_name": "S1", "platform": "rss_global", "category": "macro"},
        {"url": "https://b", "source_name": "S2", "platform": "rss_cn", "category": "news_cn", "optional": True},
    ]
    items = main.fetch_all(tasks)
    assert items == []
    st = main._SOURCE_STATS["rss"]
    assert st["fail"] == 1
    assert st["opt_fail"] == 1
    assert st["failed_sources"] == ["S1"]


class _FakeEntry(dict):
    """dict 子类 + 属性访问：同时满足 _parse 的 .get() 与 getattr() 两种访问"""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def test_fetch_all_ok_path(monkeypatch):
    entries = [_FakeEntry(published_parsed=(2026, 8, 5, 12, 0, 0),
                          title="T", link="L", id="I", summary="S")]
    monkeypatch.setattr(main, "fetch_rss", lambda task: types.SimpleNamespace(entries=entries))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    tasks = [{"url": "https://a", "source_name": "S1", "platform": "rss_global", "category": "macro"}]
    items = main.fetch_all(tasks)
    assert len(items) == 1
    assert main._SOURCE_STATS["rss"]["ok"] == 1


# ============================================================
# O10-3: AI 输出后置校验三态（缺字段 / 含禁用词 / 正常）
# ============================================================

def _valid_ai():
    return {"meta": {},
            "key_changes": [],
            "expectation_gaps": [],
            "opportunity_ranking": [{
                "rank": 1, "name": "AI芯片", "category": "A成长", "signal_strength": "中",
                "core_logic": "推理需求增长，国产替代加速", "market_consensus": "x", "reality": "y",
                "expectation_gap": "扩大", "time_horizon": "1-6个月", "risk_reward": "优秀",
                "falsification_condition": "若推理需求增速低于20%",
                "verification_checkpoints": [], "cross_validated": False, "benchmark_ticker": ""}],
            "deep_dives": [],
            "barbell": {"offense": [], "defense": [], "environment_judgment": "j", "bias": "平衡", "rationale": "r"},
            "logic_tracker": {"still_active": [], "newly_falsified": []},
            "cross_sectional_patterns": [],
            "watchlist_30d": [],
            "final_advice": {"top_priority": "研究AI芯片", "core_logic": "l", "expectation_gap": "e",
                             "catalyst": "c", "verification": "v", "failure_condition": "f", "category": "A成长"}}


def test_validate_ai_result_ok():
    r = main.validate_ai_result(_valid_ai())
    assert r is not None
    assert len(r["opportunity_ranking"]) == 1


def test_validate_ai_result_missing_field_degrades():
    r = _valid_ai()
    del r["watchlist_30d"]
    assert main.validate_ai_result(r) is None
    assert main.validate_ai_result({"opportunity_ranking": []}) is None
    assert main.validate_ai_result("not-a-dict") is None


def test_validate_ai_result_filters_banned_advice():
    r = _valid_ai()
    r["opportunity_ranking"].append({"rank": 2, "name": "白酒", "core_logic": "稳赚不赔",
                                     "falsification_condition": "x"})
    r["opportunity_ranking"].append({"rank": 3, "name": "银行", "core_logic": "分红稳定",
                                     "falsification_condition": "y"})
    r["final_advice"]["top_priority"] = "建议买入白酒"
    out = main.validate_ai_result(r)
    assert [x["name"] for x in out["opportunity_ranking"]] == ["AI芯片", "银行"]
    assert out["final_advice"] == {}


def test_validate_ai_result_drops_items_without_falsification():
    r = _valid_ai()
    r["opportunity_ranking"].append({"rank": 2, "name": "无失效条件", "core_logic": "x",
                                     "falsification_condition": ""})
    out = main.validate_ai_result(r)
    assert len(out["opportunity_ranking"]) == 1


def test_validate_ai_result_rejects_wrong_field_types():
    # O3-1：类型契约强校验 —— 畸形形状不能穿透到 format_feishu（那里无兜底，
    # 会抛 AttributeError 整卡不发）。dict 字段来了 list / list 字段来了 dict → None。
    r = _valid_ai()
    r["barbell"] = ["offense", "defense"]   # 应为 dict，却给了 list
    assert main.validate_ai_result(r) is None
    r2 = _valid_ai()
    r2["key_changes"] = {"change": "x"}     # 应为 list，却给了 dict
    assert main.validate_ai_result(r2) is None
    r3 = _valid_ai()
    r3["logic_tracker"] = []                # 应为 dict，却给了 list
    assert main.validate_ai_result(r3) is None


def test_contains_banned_advice():
    assert main.contains_banned_advice("建议买入白酒")
    assert main.contains_banned_advice("稳赚不赔")
    assert main.contains_banned_advice("必然上涨")
    assert main.contains_banned_advice("一定赚钱")
    assert main.contains_banned_advice("请尽快加仓")
    # 描述性语句不误伤
    assert not main.contains_banned_advice("机构加仓白酒，属于正常现象")
    assert not main.contains_banned_advice("基本面稳健")


# ============================================================
# O10-4: 卡片渲染关键区块（失败源清单 / 研究假设标注 / 告警卡）
# ============================================================

# 全零统计：任何源都没跑过 → 渲染空串，卡片不出现该区块
_CLEAN_STATS = {
    "rss": {"ok": 0, "fail": 0, "opt_fail": 0, "failed_sources": []},
    "fred": {"ok": 0, "total": 0, "failed": []},
    "akshare": {"ok": 0, "total": 0, "failed": []},
    "polymarket": {"ok": 0, "total": 0, "failed": []},
    "tavily": {"ok": 0, "total": 0, "failed": []},
}


def _card_content(ai, stats):
    card = main.format_feishu(ai, stats)
    return card["card"]["elements"][0]["content"]


def test_render_source_failure_block_clean():
    main._SOURCE_STATS = {k: dict(v) for k, v in _CLEAN_STATS.items()}
    assert main.render_source_failure_block() == ""


def test_format_feishu_renders_failure_block():
    main._SOURCE_STATS = {
        "rss": {"ok": 10, "fail": 2, "opt_fail": 1, "failed_sources": ["财联社", "金十数据"]},
        "fred": {"ok": 15, "total": 20, "failed": ["美国GDP"]},
        "akshare": {"ok": 1, "total": 4, "failed": ["A股涨跌家数"]},
        "polymarket": {"ok": 6, "total": 8, "failed": []},
        "tavily": {"ok": 5, "total": 7, "failed": ["macro"]},
    }
    ai = {"key_changes": [], "expectation_gaps": [], "opportunity_ranking": [], "deep_dives": [],
          "barbell": {}, "logic_tracker": {}, "watchlist_30d": [], "cross_sectional_patterns": [],
          "final_advice": {}}
    content = _card_content(ai, {"filtered": 10, "ok_sources": 10, "total_sources": 12, "calibration": {}})
    assert "数据源状态" in content
    assert "失败源" in content
    assert "财联社" in content
    assert "RSS 10✅/2❌" in content


def test_format_feishu_skips_failure_block_when_clean():
    main._SOURCE_STATS = {k: dict(v) for k, v in _CLEAN_STATS.items()}
    ai = {"key_changes": [], "expectation_gaps": [], "opportunity_ranking": [], "deep_dives": [],
          "barbell": {}, "logic_tracker": {}, "watchlist_30d": [], "cross_sectional_patterns": [],
          "final_advice": {}}
    content = _card_content(ai, {"filtered": 10, "ok_sources": 10, "total_sources": 12, "calibration": {}})
    assert "失败源" not in content


def test_format_feishu_annotates_advisory_fields():
    main._SOURCE_STATS = {k: dict(v) for k, v in _CLEAN_STATS.items()}
    ai = {"key_changes": [], "expectation_gaps": [], "opportunity_ranking": [],
          "deep_dives": [{"name": "X", "category": "A成长", "core_logic": "l",
                          "one_year_return_potential": "30%"}],
          "barbell": {"offense": ["成长股"], "defense": ["黄金"], "environment_judgment": "j",
                      "bias": "平衡", "rationale": "r"},
          "logic_tracker": {}, "watchlist_30d": [], "cross_sectional_patterns": [],
          "final_advice": {}}
    content = _card_content(ai, {"filtered": 10, "ok_sources": 10, "total_sources": 12, "calibration": {}})
    assert "研究假设，非配置建议" in content  # 深度研究回报潜力标注
    assert "研究观察，非配置建议" in content   # 杠铃板块标注


def test_build_alert_card():
    card = main.build_alert_card("⚠️ 市场机会日报 · 今日无内容", "抓取 5 条，过滤后为 0。")
    assert card["card"]["header"]["title"]["content"] == "⚠️ 市场机会日报 · 今日无内容"
    assert "过滤后为 0" in card["card"]["elements"][0]["content"]


# ============================================================
# O10-5: A股 baostock 概况（mock baostock 模块，不触网）
# ============================================================

class FakeResult:
    """模拟 baostock ResultData：error_code + next()/get_row_data() 迭代"""
    def __init__(self, rows):
        self.error_code = "0"
        self._it = iter(rows)
        self._row = None
    def next(self):
        try:
            self._row = next(self._it)
            return True
        except StopIteration:
            return False
    def get_row_data(self):
        return self._row


class _FakeBS:
    def login(self):
        return types.SimpleNamespace(error_code="0", error_msg="")
    def logout(self):
        pass
    def query_all_stock(self, day):
        return FakeResult([["sh.600000", "1", "浦发银行"],
                           ["sz.000001", "1", "平安银行"],
                           ["bj.430047", "1", "北交所股"]])
    def query_hs300_stocks(self):
        return FakeResult([["2026-08-05", "sh.600000", "浦发银行"]])
    def query_zz500_stocks(self):
        return FakeResult([["2026-08-05", "sz.000001", "平安银行"]])
    def query_history_k_data_plus(self, code, fields, start_date, end_date, frequency="d"):
        # pctChg: 浦发 +1.2（上涨）、平安 -0.8（下跌）
        pct = "1.2" if code == "sh.600000" else "-0.8"
        return FakeResult([["2026-08-05", "10.0", pct]])


class _FakeBSLoginFail:
    def login(self):
        return types.SimpleNamespace(error_code="1", error_msg="认证失败")


def test_fetch_a_share_stats_success(monkeypatch):
    monkeypatch.setitem(sys.modules, "baostock", _FakeBS())
    stats = main.fetch_a_share_market_stats()
    assert stats is not None
    assert stats["total"] == 2          # 只统计 sh.60/sz.00 前缀 A 股，剔除北交所
    assert stats["sample_total"] == 2
    assert stats["up"] == 1
    assert stats["down"] == 1
    assert stats["flat"] == 0


def test_fetch_a_share_stats_login_fail(monkeypatch):
    monkeypatch.setitem(sys.modules, "baostock", _FakeBSLoginFail())
    assert main.fetch_a_share_market_stats() is None


def test_fetch_akshare_data_baostock_fail_marks_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "baostock", _FakeBSLoginFail())
    fake_ak = types.SimpleNamespace(macro_china_gdp=lambda: None,
                                    macro_china_cpi_monthly=lambda: None,
                                    macro_china_pmi=lambda: None)
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    text = main.fetch_akshare_data({"enabled": True})
    assert "暂不可用" in text           # O4：不再输出"上涨0只/下跌0只"假数据
    assert "A股涨跌家数" in main._SOURCE_STATS["akshare"]["failed"]


# ============================================================
# O10-6: Polymarket 解析失败跳过（不输出 0% 假概率）+ Tavily 日期占位符
# ============================================================

def test_fetch_polymarket_skips_unparseable_prices(monkeypatch):
    def fake_get(url, timeout=15):
        data = [{"question": "Will X happen?", "outcomePrices": "not-json!!", "volume": 100, "slug": "x"},
                {"question": "Will Y happen?", "outcomePrices": "[0.3, 0.7]", "volume": 200, "slug": "y"}]
        return types.SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(main.requests, "get", fake_get)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    mkts = main.fetch_polymarket_data()
    assert len(mkts) == 1              # 坏价格被跳过
    assert mkts[0]["top_probability"] == 70.0
    assert "Polymarket:macro" in main._SOURCE_STATS["polymarket"]["failed"]


def test_expand_query_template():
    now = datetime.now()
    out = main.expand_query_template("Federal Reserve interest rate policy {month} {year}")
    assert out == (f"Federal Reserve interest rate policy {main.MONTH_NAMES[now.month - 1]} {now.year}")
    assert main.expand_query_template("no placeholder") == "no placeholder"
