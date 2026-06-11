#!/usr/bin/env python3
"""
市场机会发现系统 (Market Opportunity Discovery System)
====================================================

定位：买方基金研究员视角，发现全市场存在的预期差和投资机会。

不是新闻总结，不是预测短期涨跌。
目标是发现未来 1 个月至 5 年内可能产生超额收益的投资机会。

核心原则：
  市场不会奖励知道信息的人，市场只会奖励发现预期差的人。
  不要做信息搬运工，要做机会发现者。
"""

import os, sys, re, json, time, hashlib, argparse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple

import requests
import feedparser

# ============================================================
# 配置
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_94e85ee81df40d0ac71c358861427b06")
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
TIMEZONE_OFFSET = 8
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5
MAX_RETRIES = 2
CARD_MAX_CHARS = 28000

_log_lines: List[str] = []
def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    _log_lines.append(line)
    print(line, file=sys.stderr)


# ============================================================
# Twitter GraphQL API 配置（从 ai-radar 移植）
# ============================================================
TWITTER_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TWITTER_USER_BY_SCREEN_NAME = "IGgvgiOx4QZndDHuD3x9TQ"
TWITTER_USER_TWEETS = "PNd0vlufvrcIwrAnBYKE9g"
TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_FEATURES = json.dumps({
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_enhance_cards_enabled": False
}, separators=(',', ':'))


# ============================================================
# 第一步：构建 RSS URL
# ============================================================

def build_rss_urls(config: Dict) -> List[Dict]:
    tasks = []
    sources = config.get("sources", config)

    # YouTube
    for yt in sources.get("youtube", []):
        cid = yt.get("channel_id", "")
        if cid:
            tasks.append({"url": f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                          "platform": "youtube", "source_name": yt["name"],
                          "category": yt.get("category", "other"), "note": yt.get("note", "")})

    # Reddit
    for rd in sources.get("reddit", []):
        sub = rd.get("subreddit", "")
        if sub:
            tasks.append({"url": f"https://www.reddit.com/r/{sub}/.rss",
                          "platform": "reddit", "source_name": f"r/{sub}",
                          "category": rd.get("category", "other"), "note": rd.get("note", ""),
                          "extra_headers": {"User-Agent": "MarketRadar/1.0"}})

    # 中国 RSS 源（通过 RSSHub，多实例自动降级）
    rsshub_instances = sources.get("rsshub_instances", ["https://rsshub.app"])
    for cn in sources.get("rss_cn", []):
        url = cn.get("url", "")
        if url:
            # 解析 RSSHub 路由，生成多实例降级 URL
            parsed_url = url if "://" in url else "https://" + url
            from urllib.parse import urlparse as _up
            p = _up(parsed_url)
            route = p.path + ("?" + p.query if p.query else "")
            alt_urls = [f"{ins.rstrip('/')}{route}" for ins in rsshub_instances]
            tasks.append({
                "url": alt_urls[0],
                "alt_urls": alt_urls[1:],
                "platform": "rss_cn",
                "source_name": cn["name"],
                "category": cn.get("category", "other"),
                "note": cn.get("note", ""),
                "optional": True
            })

    # 国际 RSS 源
    for gb in sources.get("rss_global", []):
        url = gb.get("url", "")
        if url:
            tasks.append({"url": url, "platform": "rss_global", "source_name": gb["name"],
                          "category": gb.get("category", "other"), "note": gb.get("note", "")})

    return tasks


# ============================================================
# 第二步：RSS 抓取
# ============================================================

def fetch_rss(task: Dict) -> Optional[feedparser.FeedParserDict]:
    urls = [task["url"]] + task.get("alt_urls", [])
    headers = {"User-Agent": "MarketRadar/1.0"}
    headers.update(task.get("extra_headers", {}))
    for url in urls:
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.content)
                    if feed.entries:
                        if url != task["url"]:
                            task["_used_fallback"] = url
                        return feed
                    return None
                elif resp.status_code in (429,):
                    time.sleep((attempt + 1) * 5); continue
                elif resp.status_code in (403, 404):
                    break  # 这个实例挂了，试下一个
                else:
                    time.sleep(REQUEST_DELAY); continue
            except Exception:
                time.sleep(REQUEST_DELAY); continue
        # 这个 URL 的所有重试都失败了，继续试下一个 fallback
    return None


def _parse(entry, task: Dict) -> Optional[Dict]:
    pub_date = None
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val: pub_date = datetime(*val[:6], tzinfo=timezone.utc); break
    if not pub_date: return None
    title = re.sub(r"<[^>]+>", "", entry.get("title", "")).strip()
    title = re.sub(r"\s+", " ", title)
    desc = ""
    if hasattr(entry, "content") and entry.content: desc = entry.content[0].get("value", "")
    if not desc and hasattr(entry, "summary"): desc = entry.get("summary", "")
    if not desc: desc = title
    desc = re.sub(r"<[^>]+>", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    link = entry.get("link", "")
    raw_id = entry.get("id") or link or title + str(pub_date)
    uid = hashlib.md5(raw_id.encode()).hexdigest()[:12]
    return {"id": uid, "source_name": task["source_name"], "platform": task["platform"],
            "category": task.get("category", "other"), "title": title[:200] if title else "(无标题)",
            "description": desc[:2000] if desc else "", "url": link,
            "pub_date": pub_date.isoformat(),
            "pub_date_display": pub_date.astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET))).strftime("%m-%d %H:%M")}


def fetch_all(tasks: List[Dict]) -> List[Dict]:
    all_items = []
    ok, fail, opt_fail = 0, 0, 0
    log(f"\n🔍 抓取 {len(tasks)} 个 RSS 信息源...")
    for i, task in enumerate(tasks):
        is_opt = task.get("optional", False)
        feed = fetch_rss(task)
        if feed is None:
            if is_opt: opt_fail += 1
            else: fail += 1
            continue
        added = 0
        for entry in feed.entries:
            item = _parse(entry, task)
            if item:
                all_items.append(item)
                added += 1
        ok += 1
        if added:
            fb_tag = ""
            if task.get("_used_fallback"):
                fb_host = task["_used_fallback"].split("/")[2] if "://" in task["_used_fallback"] else task["_used_fallback"]
                fb_tag = f" [↪{fb_host}]"
            log(f"   [{i+1}/{len(tasks)}] {task['platform']}:{task['source_name']} ✅ {added}{fb_tag}")
        time.sleep(REQUEST_DELAY)
    log(f"\n📊 {ok}成功 {fail}失败 {opt_fail}可选跳过 → {len(all_items)}条")
    return all_items


# ============================================================
# Twitter/X GraphQL API 抓取
# ============================================================

def _twitter_user_id(session: requests.Session, handle: str) -> Optional[str]:
    variables = json.dumps({"screen_name": handle}, separators=(',', ':'))
    url = f"{TWITTER_API_BASE}/{TWITTER_USER_BY_SCREEN_NAME}/UserByScreenName?variables={variables}&features={TWITTER_FEATURES}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("user", {}).get("result", {}).get("rest_id")
    except Exception:
        pass
    return None


def _twitter_tweets(session: requests.Session, user_id: str, count: int = 30) -> List[Dict]:
    variables = json.dumps({
        "userId": user_id, "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": False,
        "withVoice": False, "withV2Timeline": True
    }, separators=(',', ':'))
    url = f"{TWITTER_API_BASE}/{TWITTER_USER_TWEETS}/UserTweets?variables={variables}&features={TWITTER_FEATURES}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200: return []
        data = resp.json()
        timeline = data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}).get("timeline", {})
        tweets = []
        for inst in timeline.get("instructions", []):
            if inst.get("type") == "TimelineAddEntries":
                for entry in inst.get("entries", []):
                    tr = entry.get("content", {}).get("itemContent", {}).get("tweet_results", {}).get("result", {})
                    if tr.get("__typename") == "Tweet" and "legacy" in tr:
                        tweets.append(tr)
        return tweets
    except Exception:
        return []


def fetch_twitter_via_api(twitter_sources: List[Dict], cookies_json: str) -> List[Dict]:
    if not cookies_json:
        log("⚠️ TWITTER_COOKIES 未设置，跳过 Twitter 抓取")
        return []
    try:
        cookies = json.loads(cookies_json)
    except Exception as e:
        log(f"⚠️ TWITTER_COOKIES JSON 解析失败: {e}")
        return []
    auth_token = cookies.get("auth_token", "")
    ct0 = cookies.get("ct0", "")
    if not auth_token or not ct0:
        log("⚠️ TWITTER_COOKIES 缺少 auth_token 或 ct0")
        return []

    session = requests.Session()
    session.cookies.set("auth_token", auth_token)
    session.cookies.set("ct0", ct0)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Authorization": f"Bearer {TWITTER_BEARER}",
        "X-Csrf-Token": ct0,
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
    })

    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    ok_count = 0
    log(f"\n🐦 抓取 {len(twitter_sources)} 个 Twitter 源...")
    for i, tw in enumerate(twitter_sources):
        handle = tw.get("handle", "")
        if not handle: continue
        try:
            user_id = _twitter_user_id(session, handle)
            if not user_id:
                log(f"   ⚠️ @{handle}: 找不到用户"); continue
            tweets = _twitter_tweets(session, user_id, count=30)
            added = 0
            for tweet in tweets:
                legacy = tweet.get("legacy", {})
                created_str = legacy.get("created_at", "")
                if not created_str: continue
                try:
                    tweet_time = datetime.strptime(created_str, "%a %b %d %H:%M:%S %z %Y")
                except Exception:
                    continue
                if tweet_time < cutoff: continue
                text = (legacy.get("full_text", "") or "").strip()
                if not text: continue
                tweet_id = tweet.get("rest_id", "")
                tweet_url = f"https://x.com/{handle}/status/{tweet_id}"
                uid = hashlib.md5(str(tweet_id).encode()).hexdigest()[:12]
                results.append({
                    "id": uid, "source_name": f"@{handle}", "platform": "twitter",
                    "category": tw.get("category", "other"),
                    "title": text[:200], "description": text[:2000],
                    "url": tweet_url, "pub_date": tweet_time.isoformat(),
                    "pub_date_display": tweet_time.astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET))).strftime("%m-%d %H:%M")
                })
                added += 1
            log(f"   🐦 @{handle} ✅ {added}条")
            ok_count += 1
            time.sleep(0.8)
        except Exception as e:
            log(f"   ⚠️ Twitter @{handle}: {str(e)[:80]}"); continue
    log(f"   Twitter: {ok_count}/{len(twitter_sources)} 成功 → {len(results)}条")
    return results


# ============================================================
# FRED 经济数据
# ============================================================

def fetch_fred_data(fred_config: Dict) -> List[Dict]:
    """获取 FRED 经济指标最新值"""
    if not FRED_API_KEY:
        log("⚠️ FRED_API_KEY 未设置，跳过经济数据")
        return []
    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
        items = []
        for ind in fred_config.get("indicators", []):
            try:
                series = fred.get_series(ind["id"])
                if series is not None and len(series) > 0:
                    latest_val = series.dropna().iloc[-1]
                    prev_val = series.dropna().iloc[-2] if len(series.dropna()) > 1 else latest_val
                    change = latest_val - prev_val
                    change_pct = (change / prev_val * 100) if prev_val != 0 else 0
                    last_date = series.dropna().index[-1]
                    items.append({
                        "indicator": ind["name"], "series_id": ind["id"],
                        "value": round(float(latest_val), 2),
                        "change": round(float(change), 2),
                        "change_pct": round(float(change_pct), 2),
                        "last_date": str(last_date)[:10]
                    })
            except Exception as e:
                log(f"   ⚠️ FRED {ind['id']}: {str(e)[:50]}")
        log(f"📊 FRED: {len(items)}/{len(fred_config.get('indicators',[]))} 指标获取成功")
        return items
    except ImportError:
        log("⚠️ fredapi 未安装"); return []
    except Exception as e:
        log(f"⚠️ FRED 错误: {str(e)[:80]}"); return []


# ============================================================
# AKShare A股数据
# ============================================================

def fetch_akshare_data(ak_config: Dict) -> str:
    """获取 A 股市场概况"""
    if not ak_config.get("enabled"):
        return ""
    try:
        import akshare as ak
        parts = []

        # A股全市场行情概况
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                up_count = int((df["涨跌幅"] > 0).sum()) if "涨跌幅" in df.columns else 0
                down_count = int((df["涨跌幅"] < 0).sum()) if "涨跌幅" in df.columns else 0
                total = len(df)
                top_up = df.nlargest(3, "涨跌幅")[["代码", "名称", "涨跌幅"]].to_dict("records") if "涨跌幅" in df.columns else []
                parts.append(f"A股全市场: {total}只, 上涨{up_count}只, 下跌{down_count}只")
                if top_up:
                    parts.append("涨幅前三: " + ", ".join(f"{r['代码']} {r['名称']} {r['涨跌幅']:.1f}%" for r in top_up))
        except Exception:
            pass

        # 宏观经济数据
        try:
            gdp = ak.macro_china_gdp()
            if gdp is not None and len(gdp) > 0:
                parts.append(f"中国GDP最新: {str(gdp.iloc[-1].to_dict())}")
        except Exception:
            pass

        try:
            cpi = ak.macro_china_cpi_monthly()
            if cpi is not None and len(cpi) > 0:
                parts.append(f"中国CPI最新: {str(cpi.iloc[-1].to_dict())}")
        except Exception:
            pass

        try:
            pmi = ak.macro_china_pmi()
            if pmi is not None and len(pmi) > 0:
                parts.append(f"中国PMI最新: {str(pmi.iloc[-1].to_dict())}")
        except Exception:
            pass

        return "\n".join(parts) if parts else ""
    except ImportError:
        log("⚠️ akshare 未安装"); return ""
    except Exception as e:
        log(f"⚠️ AKShare 错误: {str(e)[:80]}"); return ""


# ============================================================
# 第三步：预过滤
# ============================================================

# 金融噪音关键词
SPAM_KW = [
    r"\bsponsor(ed)?\b", r"\b#ad\b", r"\baffiliate\b",
    r"\bdiscount\s+code\b", r"\bpromo\s+code\b",
    r"\bguaranteed\s+profit\b", r"限时优惠", r"免费领取",
    r"注册即送", r"优惠码", r"佣金",
    r"报名我的课程", r"加入我的社群", r"扫码进群",
    r"日入过?万", r"月入百?万", r"躺赚", r"暴富",
    r"荐股", r"喊单", r"跟单", r"带单"
]
SPAM_RE = [re.compile(kw, re.IGNORECASE) for kw in SPAM_KW]


def pre_filter(items: List[Dict]) -> Tuple[List[Dict], int, int]:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=24)
    filtered, seen = [], set()
    old_c, spam_c = 0, 0
    for item in items:
        try:
            if datetime.fromisoformat(item["pub_date"]) < cutoff: old_c += 1; continue
        except: pass
        url = item.get("url", "")
        if url and url in seen: continue
        if url: seen.add(url)
        th = hashlib.md5(item.get("title", "").encode()).hexdigest()
        if th in seen: continue
        seen.add(th)
        txt = f"{item['title']} {item.get('description','')[:500]}"
        if any(p.search(txt) for p in SPAM_RE): spam_c += 1; continue
        if len(item["title"].strip()) < 5: continue
        filtered.append(item)
    log(f"\n🔎 预过滤: 去旧{old_c} 去噪音{spam_c} | {len(items)}→{len(filtered)}")
    return filtered, old_c, spam_c


# ============================================================
# 第四步：DeepSeek AI 分析 —— 7层分析 Prompt
# ============================================================

SYSTEM_PROMPT = """你是世界顶级买方基金研究员、产业分析师和机会猎手。

你的目标不是总结新闻。不是预测短期涨跌。
你的目标是：发现未来1个月至5年内可能产生超额收益的投资机会。

## 核心原则

1. 不要问「今天发生了什么」，要问「今天什么发生了变化」
2. 不要问「哪些新闻最重要」，要问「哪些变化可能改变未来现金流、行业格局或市场预期」
3. 不要从行业出发寻找机会。不要预设"AI有机会、消费没机会"。让数据决定机会在哪里
4. 机会来源于：变化 × 预期差 × 催化剂 三者的交集

市场不会奖励知道信息的人。市场只会奖励发现预期差的人。
不要做信息搬运工。要做机会发现者。

---

## 分析框架（严格按以下步骤推理）

### 第一层：变化扫描

扫描全市场，不限定行业。寻找以下变化类型：
盈利变化 / 订单变化 / 资本开支变化 / 价格变化 / 库存变化 / 需求变化
政策变化 / 监管变化 / 技术突破 / 竞争格局变化
市场情绪变化 / 资金流向变化 / 叙事变化

重点标注：加速 / 减速 / 拐点 / 反转 / 从0到1 / 从1到10 / 从增长到衰退 / 从衰退到增长

### 第二层：预期差扫描

对每个重要变化，回答：
- 市场共识是什么？
- 现实情况是什么？
- 两者差距在哪里？差距在扩大还是缩小？
- 未来什么事件会验证这种差距？
- 如果没有预期差 → 直接降级处理

### 第三层：投资推理引擎

对每个重要变化，输出：
- 【事实】发生了什么？
- 【市场共识】市场目前如何理解？
- 【现实情况】数据说明什么？
- 【预期差】市场可能忽略了什么？
- 【一阶影响】直接影响谁？
- 【二阶影响】影响哪些行业？
- 【三阶影响】未来可能形成哪些连锁反应？
- 【受益者】哪些企业、行业或资产可能受益？
- 【受损者】哪些企业、行业或资产可能受损？
- 【验证指标】未来应该跟踪哪些数据？
- 【失效条件】什么情况会证明这条逻辑是错误的？

### 第四层：机会分类

所有机会必须归类：

**A类：成长机会** — 新技术/新市场/新需求，从0到1或从1到10，寻找10倍股方向
**B类：周期机会** — 资源/能源/航运/化工/利率/通胀/供需周期
**C类：价值机会** — 低估/被忽视/市场过度悲观/现金流稳定/估值修复空间大
**D类：反转机会** — 市场共识极度悲观但基本面开始改善
**E类：防御机会** — 黄金/国债/公用事业/高股息等防御性资产

### 第五层：杠铃分析

- 【进攻端】哪里出现新的成长机会？哪些主题加速？哪些行业形成新叙事？
- 【防守端】哪里出现新的避险机会？哪些资产提供保护？
- 【当前环境】市场更适合进攻/防守/平衡？为什么？

### 第六层：资金与赔率

对每个机会分析：
- 市场关注度（高/中/低）
- 机构是否拥挤
- 资金流向（流入/流出）
- 收益空间（高/中/低）
- 风险（高/中/低）
- 风险收益比（优秀/一般/较差）

### 第七层：机会评分（100分制）

| 维度 | 权重 |
|------|------|
| 变化强度 | 20分 |
| 预期差 | 25分 |
| 持续时间 | 15分 |
| 催化剂强度 | 15分 |
| 竞争优势 | 10分 |
| 资金支持 | 5分 |
| 估值吸引力 | 10分 |

**总分 ≥ 70 → 高优先级机会。总分 50-69 → 中等机会，值得跟踪。总分 < 50 → 降级或丢弃。**

不要因为行业热门而提高评分。不要因为行业冷门而降低评分。

---

## 输出 JSON 格式

{
  "meta": {"total_scanned": 0, "kept": 0, "discarded": 0},

  "key_changes": [
    {"change": "", "type": "加速/减速/拐点/反转/从0到1", "sector": "", "importance": "高/中", "why_matters": "", "source": ""}
  ],

  "expectation_gaps": [
    {"topic": "", "market_consensus": "", "reality": "", "gap": "扩大/缩小", "verification_event": "", "gap_score": 0}
  ],

  "opportunity_ranking": [
    {
      "rank": 1, "name": "", "category": "A成长/B周期/C价值/D反转/E防御",
      "scores": {"change_intensity": 0, "expectation_gap": 0, "duration": 0, "catalyst": 0, "competitive_advantage": 0, "funding": 0, "valuation": 0, "total": 0},
      "core_logic": "", "expected_return": "高/中/低", "time_horizon": "", "risk_reward": "优秀/一般/较差"
    }
  ],

  "top3_deep_dives": [
    {
      "name": "", "why_worth_studying": "", "core_logic": "",
      "what_market_misses": "", "catalyst": "", "failure_condition": "",
      "category": "A成长/B周期/C价值/D反转/E防御",
      "one_year_return_potential": ""
    }
  ],

  "barbell": {
    "offense": [""], "defense": [""], "environment_judgment": "", "bias": "进攻/防守/平衡", "rationale": ""
  },

  "logic_tracker": {
    "strengthened": [""], "weakened": [""], "falsified": [""]
  },

  "watchlist_30d": [
    {"event": "", "date": "", "why_important": "", "what_to_watch": ""}
  ],

  "final_advice": {
    "top_priority": "", "core_logic": "", "expectation_gap": "",
    "catalyst": "", "verification": "", "failure_condition": "",
    "category": "A成长/B周期/C价值/D反转/E防御"
  }
}

---

## 特别指令

1. 宁缺毋滥。如果某个栏目没有足够好的内容，输出空数组 []。
2. 所有机会必须经过预期差验证。没有预期差的「机会」只是噪音。
3. 不要输出任何买入/卖出建议。只输出研究框架和推理过程。
4. 英文→中文翻译，保留关键英文术语。
5. **看多和看空的机会都要找。** 市场下跌也是机会。
6. 对于中国市场和美国市场，分别标注。
"""


def build_ai_input(items: List[Dict], fred_items: List[Dict], akshare_text: str) -> str:
    # 按类别分组
    cats = {}
    for item in items:
        cats.setdefault(item.get("category", "other"), []).append(item)

    cat_labels = {
        "macro": "宏观/策略", "equity": "股票/个股", "value": "价值投资",
        "industry": "行业研究", "trading": "交易策略", "crypto": "加密货币",
        "tech": "科技/AI", "sentiment": "市场情绪", "income": "收入/股息",
        "news_cn": "中国财经新闻", "news_global": "国际财经新闻",
        "research_cn": "中国研究报告", "policy": "政策/监管",
        "corporate": "公司披露", "community_cn": "中国投资社区",
        "education": "投资教育"
    }

    parts = ["以下是从全网信息中筛选的待分析条目。请以「买方基金研究员」视角进行7层分析。\n"]

    # 经济数据优先
    if fred_items:
        parts.append("\n## 📊 美国经济指标（FRED）\n")
        for fi in fred_items:
            direction = "↑" if fi["change"] > 0 else "↓" if fi["change"] < 0 else "→"
            parts.append(f"- {fi['indicator']}({fi['series_id']}): {fi['value']} {direction}{fi['change']:+g} ({fi['change_pct']:+.2f}%) @{fi['last_date']}")

    if akshare_text:
        parts.append(f"\n## 📊 A股/中国宏观数据\n{akshare_text[:2000]}\n")

    # 信息条目
    idx = 0
    priority_order = ["macro", "policy", "news_cn", "news_global", "research_cn", "equity", "industry", "value", "corporate", "trading", "crypto", "tech", "sentiment", "income", "community_cn", "education"]
    for cat_key in priority_order:
        cat_items = cats.pop(cat_key, [])
        if not cat_items: continue
        label = cat_labels.get(cat_key, cat_key)
        parts.append(f"\n## {label} ({len(cat_items)}条)\n")
        for item in cat_items:
            idx += 1
            parts.append(f"[{idx}]【{item['platform']}】{item['source_name']} | {item.get('pub_date_display','?')}\n    {item['title']}\n    {item['description'][:500]}\n    {item.get('url','')}\n")

    # 剩余类别
    for cat_key, cat_items in cats.items():
        if not cat_items: continue
        label = cat_labels.get(cat_key, cat_key)
        parts.append(f"\n## {label} ({len(cat_items)}条)\n")
        for item in cat_items:
            idx += 1
            parts.append(f"[{idx}]【{item['platform']}】{item['source_name']} | {item.get('pub_date_display','?')}\n    {item['title']}\n    {item['description'][:500]}\n    {item.get('url','')}\n")

    text = "\n".join(parts)
    log(f"📝 AI输入: {idx}条, ~{len(text)//2}tokens")
    return text


def call_deepseek(user_content: str) -> Optional[Dict]:
    if not DEEPSEEK_API_KEY:
        log("❌ DEEPSEEK_API_KEY 未设置"); return None
    payload = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ], "temperature": 0.3, "max_tokens": 8000, "response_format": {"type": "json_object"}}
    log(f"\n🤖 调用 DeepSeek...")
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post("https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"): content = content.split("\n", 1)[1] if "\n" in content else content; content = content[:-3].strip() if content.endswith("```") else content; content = content[4:].strip() if content.startswith("json") else content
                result = json.loads(content)
                log(f"   ✅ Tokens: {data.get('usage',{}).get('total_tokens','?')}")
                return result
            elif resp.status_code == 429: time.sleep((attempt+1)*10); continue
            elif resp.status_code >= 500: time.sleep((attempt+1)*5); continue
            else: log(f"   ❌ HTTP {resp.status_code}"); return None
        except Exception as e: log(f"   ⚠️ {e}"); time.sleep(2); continue
    return None


# ============================================================
# 第五步：飞书 Bot API 推送
# ============================================================

def _get_feishu_token() -> Optional[str]:
    """用 App ID + App Secret 获取 tenant_access_token"""
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tenant_access_token")
    except Exception as e:
        log(f"   ⚠️ 获取飞书Token失败: {e}")
    return None


def send_feishu_card(card: Dict) -> bool:
    """通过飞书 Bot API 发送消息到指定群聊"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        log("❌ FEISHU_APP_ID/FEISHU_APP_SECRET 未设置"); return False
    token = _get_feishu_token()
    if not token:
        log("❌ 无法获取飞书Token"); return False
    log("\n📤 推送飞书...")
    payload = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card.get("card", card), ensure_ascii=False)
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json=payload, headers=headers, timeout=30)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                log("   ✅ 推送成功"); return True
            log(f"   ⚠️ 推送失败: {resp.text[:200]}")
            time.sleep(2)
        except Exception as e:
            log(f"   ⚠️ {e}"); time.sleep(2)
    return False


def send_feishu_webhook(card: Dict) -> bool:
    """通过飞书 Webhook 机器人发送消息到外部群"""
    url = FEISHU_WEBHOOK_URL
    if not url:
        log("ℹ️ FEISHU_WEBHOOK_URL 未设置，跳过 webhook 推送"); return False
    log("\n📤 推送飞书 Webhook（外部群）...")
    payload = {
        "msg_type": "interactive",
        "card": card.get("card", card)
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                log("   ✅ Webhook 推送成功"); return True
            log(f"   ⚠️ Webhook 推送失败: {resp.text[:200]}")
            time.sleep(2)
        except Exception as e:
            log(f"   ⚠️ {e}"); time.sleep(2)
    return False


# ============================================================
# 日报格式化
# ============================================================

def section(title: str, body: str) -> str:
    return f"\n━━━━━━━━━━━━━━━━━━━\n\n{title}\n\n{body}"


def format_feishu(ai: Dict, stats: Dict) -> Dict:
    d = datetime.now()
    date_str = d.strftime("%Y.%m.%d")
    weekday = ["一","二","三","四","五","六","日"][d.weekday()]
    md = [f"📊 **市场机会发现系统** · {date_str} 周{weekday}", f"从 {stats['filtered']} 条信息中为你发现机会", "━━━━━━━━━━━━━━━━━━━"]

    # 一、市场最重要的变化
    kc = ai.get("key_changes", [])
    if kc:
        lines = []
        for c in kc[:5]:
            lines.append(f"**{c.get('change','')}** [{c.get('type','')}] · {c.get('sector','')}")
            lines.append(f"   重要度: {c.get('importance','?')} | {c.get('why_matters','')}")
            src = c.get("source", "")
            if src:
                lines.append(f"   📎 {src}")
            lines.append("")
        md.append(section("一、市场最重要的变化", "\n".join(lines)))

    # 二、预期差排行榜
    eg = ai.get("expectation_gaps", [])
    if eg:
        lines = []
        for e in eg[:5]:
            gap_dir = "📈 扩大" if e.get("gap") == "扩大" else "📉 缩小" if e.get("gap") == "缩小" else "→"
            lines.append(f"**{e.get('topic','')}** {gap_dir} | 预期差得分:{e.get('gap_score','?')}")
            lines.append(f"   共识: {e.get('market_consensus','')}")
            lines.append(f"   现实: {e.get('reality','')}")
            lines.append(f"   验证: {e.get('verification_event','')}")
            lines.append("")
        md.append(section("二、预期差排行榜", "\n".join(lines)))

    # 三、全市场机会排行榜
    ranking = ai.get("opportunity_ranking", [])
    if ranking:
        lines = []
        for r in ranking[:10]:
            s = r.get("scores", {})
            lines.append(f"**#{r.get('rank','?')} {r.get('name','')}** [{r.get('category','?')}] 🎯 {s.get('total','?')}/100")
            lines.append(f"   {r.get('core_logic','')}")
            lines.append(f"   预期收益:{r.get('expected_return','?')} | 期限:{r.get('time_horizon','?')} | 风险收益比:{r.get('risk_reward','?')}")
            lines.append("")
        md.append(section("三、全市场机会排行榜", "\n".join(lines)))

    # 四、最值得研究的3个机会
    top3 = ai.get("top3_deep_dives", [])
    if top3:
        lines = []
        for t in top3:
            lines.append(f"**{t.get('name','')}** [{t.get('category','?')}]")
            lines.append(f"   为什么: {t.get('why_worth_studying','')}")
            lines.append(f"   核心逻辑: {t.get('core_logic','')}")
            lines.append(f"   市场可能错在哪: {t.get('what_market_misses','')}")
            lines.append(f"   催化剂: {t.get('catalyst','')}")
            lines.append(f"   失效条件: {t.get('failure_condition','')}")
            pot = t.get("one_year_return_potential","")
            if pot: lines.append(f"   一年回报潜力: {pot}")
            lines.append("")
        md.append(section("四、最值得研究的3个机会", "\n".join(lines)))

    # 五、杠铃配置观察
    barbell = ai.get("barbell", {})
    if barbell:
        lines = []
        off = barbell.get("offense", [])
        deff = barbell.get("defense", [])
        if off: lines.append(f"⚔️ 进攻端: {' | '.join(off)}")
        if deff: lines.append(f"🛡️ 防守端: {' | '.join(deff)}")
        lines.append(f"🎯 环境判断: {barbell.get('environment_judgment','')}")
        lines.append(f"   倾向: {barbell.get('bias','?')} — {barbell.get('rationale','')}")
        lines.append("")
        md.append(section("五、杠铃配置观察", "\n".join(lines)))

    # 六、逻辑追踪库
    lt = ai.get("logic_tracker", {})
    if lt:
        lines = []
        s = lt.get("strengthened", [])
        w = lt.get("weakened", [])
        f = lt.get("falsified", [])
        if s: lines.append(f"✅ 强化: {' | '.join(s)}")
        if w: lines.append(f"⚠️ 削弱: {' | '.join(w)}")
        if f: lines.append(f"❌ 证伪: {' | '.join(f)}")
        if lines: md.append(section("六、逻辑追踪库更新", "\n".join(lines)))

    # 七、未来30天观察清单 + 最终建议
    wl = ai.get("watchlist_30d", [])
    if wl:
        lines = []
        for w in wl[:5]:
            lines.append(f"📅 {w.get('date','?')}: {w.get('event','')}")
            lines.append(f"   为什么重要: {w.get('why_important','')}")
            lines.append(f"   观察重点: {w.get('what_to_watch','')}")
            lines.append("")
        md.append(section("七、未来30天观察清单", "\n".join(lines)))

    # 最终建议
    fa = ai.get("final_advice", {})
    if fa and fa.get("top_priority"):
        lines = []
        lines.append(f"**🎯 如果今天只能研究一件事：{fa.get('top_priority','')}** [{fa.get('category','?')}]")
        lines.append(f"")
        lines.append(f"核心逻辑: {fa.get('core_logic','')}")
        lines.append(f"预期差: {fa.get('expectation_gap','')}")
        lines.append(f"催化剂: {fa.get('catalyst','')}")
        lines.append(f"验证指标: {fa.get('verification','')}")
        lines.append(f"失效条件: {fa.get('failure_condition','')}")
        md.append(section("💡 最终建议", "\n".join(lines)))

    if len(md) <= 3:
        md.append("⚠️ 今日内容质量未达日报标准，建议直接查看 FRED 经济数据和 SEC 文件发现变化信号。")
        md.append(f"\n今日共处理 {stats['filtered']} 条内容。")

    # 尾部
    md.append("\n━━━━━━━━━━━━━━━━━━━")
    md.append(f"📊 市场机会发现系统 · 每日 {d.strftime('%H:%M')} 自动生成")
    md.append(f"AI: DeepSeek · {stats['ok_sources']}源成功 · 本报告不构成投资建议")
    md.append("")

    content = "\n".join(md)
    if len(content) > CARD_MAX_CHARS:
        content = content[:CARD_MAX_CHARS-200]
        idx = content.rfind("\n")
        if idx > 0: content = content[:idx]
        content += "\n\n⚠️ 内容过长已截断"

    return {"msg_type": "interactive",
            "card": {"header": {"template": "red",
                     "title": {"tag": "plain_text", "content": f"📊 市场机会发现 · {date_str} 周{weekday}"}},
                     "elements": [{"tag": "markdown", "content": content}]}}


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="市场机会发现系统")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-feishu", action="store_true")
    parser.add_argument("--sources", default="sources.json")
    args = parser.parse_args()

    log("="*50)
    log("📊 市场机会发现系统")
    log("="*50)

    if args.test_feishu:
        test_card = {"msg_type":"interactive","card":{"header":{"template":"red","title":{"tag":"plain_text","content":"🧪 市场机会发现系统 · 测试"}},"elements":[{"tag":"markdown","content":"✅ 飞书 Bot API 推送测试成功！\n\n系统已就绪，明天 15:00 你将收到第一份市场机会日报。"}]}}
        ok1 = send_feishu_card(test_card)
        ok2 = send_feishu_webhook(test_card)
        log("✅ 测试成功" if ok1 else "❌ Bot API 失败")
        log("✅ Webhook 测试成功" if ok2 else "ℹ️ Webhook 未配置或失败")
        return

    with open(args.sources, "r", encoding="utf-8") as f:
        config = json.load(f)
    sources_config = config.get("sources", config)

    # 分离 Twitter 源
    twitter_sources = sources_config.get("twitter", []) if isinstance(sources_config, dict) else []

    tasks = build_rss_urls(sources_config)
    log(f"📋 {len(tasks)} 个 RSS 抓取任务 + {len(twitter_sources)} 个 Twitter 源")

    all_items = fetch_all(tasks)

    # 抓取 Twitter
    if twitter_sources:
        twitter_items = fetch_twitter_via_api(
            twitter_sources,
            os.environ.get("TWITTER_COOKIES", "")
        )
        all_items.extend(twitter_items)

    if not all_items: log("\n❌ 无内容"); sys.exit(1)

    filtered, _, _ = pre_filter(all_items)
    if not filtered: log("\n⚠️ 过滤后无内容"); sys.exit(0)

    if args.dry_run:
        log("\n--- DRY RUN 预览 ---")
        for i, item in enumerate(filtered[:30]): log(f"[{i+1}] {item['platform']} | {item['source_name']} | {item['title'][:80]}")
        if len(filtered) > 30: log(f"... 还有 {len(filtered)-30} 条")
        return

    # 经济数据
    fred_config = sources_config.get("fred", {}) if isinstance(sources_config, dict) else {}
    fred_items = fetch_fred_data(fred_config) if fred_config.get("enabled") else []
    ak_config = sources_config.get("akshare", {}) if isinstance(sources_config, dict) else {}
    akshare_text = fetch_akshare_data(ak_config) if ak_config.get("enabled") else ""

    ai_input = build_ai_input(filtered, fred_items, akshare_text)
    ai_result = call_deepseek(ai_input)

    stats = {"filtered": len(filtered),
             "ok_sources": len(set(it["source_name"] for it in all_items)),
             "total_sources": len(tasks) + len(twitter_sources)}

    if ai_result is None:
        content_md = f"📊 市场机会发现系统 · {datetime.now().strftime('%Y.%m.%d')}\n\n⚠️ AI 分析暂不可用。今日抓取 {len(filtered)} 条内容。\n\n请检查 DeepSeek API。"
        card = {"msg_type":"interactive","card":{"header":{"template":"red","title":{"tag":"plain_text","content":"📊 市场机会发现"}},"elements":[{"tag":"markdown","content":content_md}]}}
    else:
        card = format_feishu(ai_result, stats)

    send_feishu_card(card)
    send_feishu_webhook(card)
    log("\n"+"="*50)
    log("✅ 完成")
    log("="*50)


if __name__ == "__main__":
    main()
