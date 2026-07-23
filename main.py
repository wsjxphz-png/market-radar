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
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID") or "oc_94e85ee81df40d0ac71c358861427b06"
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
# Polymarket 预测市场数据（市场共识基准）
# ============================================================

POLYMARKET_TAGS = ["macro", "fed", "crypto", "china", "trade-war", "recession", "elections", "geopolitics"]


def fetch_polymarket_data() -> List[Dict]:
    """拉取 Polymarket 上与宏观/市场相关的预测市场数据"""
    markets = []
    for tag in POLYMARKET_TAGS:
        try:
            url = f"https://gamma-api.polymarket.com/markets?tag={tag}&limit=5&closed=false"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for m in data[:3]:
                    q = m.get("question", "")
                    if len(q) < 10:
                        continue
                    outcome_prices = m.get("outcomePrices", "[]")
                    try:
                        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                        top_price = max(float(p) for p in prices) if prices else 0
                    except Exception:
                        top_price = 0
                    markets.append({
                        "question": q[:200],
                        "top_probability": round(top_price * 100, 1),
                        "volume": m.get("volume", 0),
                        "tag": tag,
                        "url": f"https://polymarket.com/event/{m.get('slug','')}"
                    })
            time.sleep(0.3)
        except Exception:
            continue
    # 按成交量降序，去重
    seen = set()
    unique = []
    for m in sorted(markets, key=lambda x: float(x.get("volume", 0)), reverse=True):
        if m["question"] not in seen:
            seen.add(m["question"])
            unique.append(m)
    log(f"🎲 Polymarket: {len(unique)} 个预测市场")
    return unique[:15]


def format_polymarket_for_prompt(markets: List[Dict]) -> str:
    if not markets:
        return ""
    parts = ["\n## 🎲 预测市场共识 (Polymarket)\n"]
    parts.append("以下是预测市场当前对关键事件的概率定价。这不是预测，而是市场参与者用真金白银表达的共识。你可以用这些数据来校准「市场共识是什么」的判断。\n")
    for m in markets:
        parts.append(f"- {m['question']}")
        parts.append(f"  市场定价概率: {m['top_probability']}% | 成交量: ${float(m.get('volume',0)):,.0f}")
    return "\n".join(parts)


# ============================================================
# Tavily 实时搜索
# ============================================================

def fetch_tavily_data(tavily_config: Dict) -> List[Dict]:
    """使用 Tavily API 进行定向实时搜索，补充 RSS 之外的增量信息。"""
    api_key = os.environ.get(tavily_config.get("api_key_env", "TAVILY_API_KEY"), "")
    if not api_key:
        log("⚠️ Tavily API Key 未配置，跳过实时搜索")
        return []

    try:
        from tavily import TavilyClient
    except ImportError:
        log("⚠️ tavily-python 未安装，跳过实时搜索")
        return []

    client = TavilyClient(api_key=api_key)
    queries = tavily_config.get("queries", [])
    max_results = tavily_config.get("max_results_per_query", 5)
    all_items = []

    for q in queries:
        try:
            result = client.search(q["query"], max_results=max_results, search_depth="basic")
            for r in result.get("results", [])[:max_results]:
                all_items.append({
                    "title": r.get("title", ""),
                    "description": r.get("content", "")[:500],
                    "url": r.get("url", ""),
                    "platform": "tavily",
                    "source_name": f"web:{q.get('category', 'search')}",
                    "category": q.get("category", "market"),
                    "pub_date_display": datetime.now().strftime("%m-%d %H:%M"),
                    "pub_date": datetime.now(timezone.utc).isoformat()
                })
            time.sleep(0.3)
        except Exception as e:
            log(f"⚠️ Tavily 搜索失败 [{q['query'][:40]}...]: {e}")

    log(f"🔍 Tavily 实时搜索: {len(queries)} 个查询 → {len(all_items)} 条结果")
    return all_items


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
# 第四步：DeepSeek AI 分析 —— 8层分析 Prompt
# ============================================================

SYSTEM_PROMPT = """你是世界顶级买方基金研究员、产业分析师和机会猎手。

你的目标不是总结新闻。不是预测涨跌方向。
你的目标是：识别可检验的条件判断——"在什么条件下，会发生什么变化"。

## 核心原则

1. 不要问「今天发生了什么」，要问「今天什么发生了变化」
2. 不要问「哪些新闻最重要」，要问「哪些变化可能改变未来现金流、行业格局或市场预期」
3. 不要从行业出发寻找机会。不要预设"AI有机会、消费没机会"。让数据决定机会在哪里
4. 机会来源于：变化 × 预期差 × 催化剂 三者的交集
5. 不做预测，做条件判断。你的输出应该是："如果X持续发生，那么Y方向概率更高。但如果Z出现，这个判断就失效了。"

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

### 第七层：信号强度评定

在评分时对照以下维度，诚实评估：

| 维度 | 需要问自己的问题 |
|------|-----------------|
| 变化强度 | 这个变化有多大？是渐进还是突变？ |
| 预期差 | 市场共识和现实的差距有多大？差距在扩大吗？ |
| 可持续性 | 这个变化是临时的还是结构性的？ |
| 催化剂 | 近期有没有可识别的事件会推动重定价？ |
| 交叉验证 | 有多少个独立信源指向同一方向？ |

综合这些维度，给出信号强度：

**强信号**：至少2个独立信源交叉验证，预期差清晰且在扩大，有明确催化剂。这是"如果不发生极端意外，逻辑大概率成立"的判断。
**中信信号**：有预期差，但交叉验证不足，或催化剂时间不确定。这是"值得跟踪但还不够确定"的判断。
**弱信号**：单一信源，预期差不够清晰。这是"有一个有趣的方向，需要更多证据"的判断。弱信号不应进入机会排名，可以放在 watchlist 中跟踪。

**诚实原则**：宁可把"强"标成"中"，也不要把"中"标成"强"。信号强度的信誉比数量重要。

### 第八层：跨公司模式识别

当同一天或同一批输入中出现多条来自不同公司的信息时，必须进行横截面对比。

单独一家公司的信号可能是噪音或特例。但当多家独立公司指向同一方向时，这很可能是系统性的、真实的趋势。

**不限定任何行业。** 让数据决定模式在哪里出现。

#### 寻找以下模式类型：

**1. 同向共振（最强信号）**
多个同行业公司独立报告相同变化 → 这是行业级别的趋势，不是个股噪音。

**2. 产业链传导（上下游印证）**
上游供应商和下游客户从不同角度报告同一趋势 → 双重验证。

**3. 行业分化（寻找赢家）**
同行业内，有的公司变好、有的变差 → 市场份额在转移。

**4. 资本配置趋同（机构级信号）**
多家公司不约而同地把资金投向同一方向 → 这个方向有结构性机会。

**5. 风险披露重叠（预警信号）**
多家公司的风险披露都新增了同一类风险 → 这个风险正在变成系统性风险。

**6. 管理层措辞趋同（叙事变化）**
多个CEO/CFO在讨论中用相似的措辞描述同一现象 → 一个正在形成的市场共识。

#### 模式如何影响信号强度：

- 单一公司信号 → 不能评为"强信号"
- 2-3家公司同向共振 → 可以评为"强信号"
- 产业链上下游双重验证 → 自动升级为"强信号"
- 4家以上独立公司指向同一方向 → 这是系统级发现

**关键原则：不要为了凑模式而强行关联。** 如果今天的数据中没有明显的跨公司模式，cross_sectional_patterns 输出空数组。

---

### 第九层：自对抗验证（必须执行）

这是最关键的一步。在你输出最终结果前，必须对自己的判断进行攻击。

**步骤A：初判**
完成前8层分析，形成初步的判断列表。

**步骤B：自我攻击**
对每一个拟输出的机会，切换身份为"最严厉的批评者"。追问：

1. 这个逻辑链条中最薄弱的环节是什么？
2. 有没有替代解释可以同样解释观察到的信号？（例如：不是需求下降，而是季节性因素；不是技术突破，而是公关炒作）
3. 如果这个判断是错的，最可能的原因是什么？
4. 这个信号的来源本身是否有偏差或利益冲突？（例如Twitter账号有持仓倾向、RSS源是二手信息）
5. 我是否因为最近看到了类似主题而过度重视这个信号？（近因偏差）

**步骤C：修正输出**
基于攻击发现，对每个机会进行修正：
- 逻辑推断过度的 → 收紧表述，明确是"条件判断"而非"确定预测"
- 经不起攻击的 → 降级信号强度，或丢弃
- 缺少可证伪条件的 → 补全
- 交叉验证不足的 → 从"强信号"降为"中信信号"或更低

如果某条判断在被自我攻击后无法存活，它就不应该出现在最终输出中。

---

# A股监测已迁移至 market_dashboard.py（Mi姐框架）| 旧版: rally_health_monitor_legacy.py

## 输出纪律

1. 如果某个板块今天没有值得输出的内容，输出空数组 []。空数组不是失败——它说明你今天认真做了筛选。
2. 不要为了凑数而降低标准。一个真信号胜过十个填充物。
3. 如果你不能写出一条具体的、可检验的失效条件，这个判断就不够成熟。要么继续完善它，要么丢弃它。
4. 信号强度评定必须诚实。"中"或"弱"不丢人。把"弱"标成"强"才是对读者的欺骗。

---

## 输出 JSON 格式

{
  "meta": {"total_scanned": 0, "kept": 0, "discarded": 0},

  "key_changes": [
    {"change": "", "type": "加速/减速/拐点/反转/从0到1", "sector": "", "importance": "高/中", "why_matters": "", "source": ""}
  ],

  "expectation_gaps": [
    {"topic": "", "market_consensus": "", "reality": "", "gap": "扩大/缩小", "verification_event": ""}
  ],

  "opportunity_ranking": [
    {
      "rank": 1,
      "name": "",
      "category": "A成长/B周期/C价值/D反转/E防御",
      "signal_strength": "强/中/弱",
      "core_logic": "",
      "market_consensus": "",
      "reality": "",
      "expectation_gap": "",
      "time_horizon": "",
      "risk_reward": "优秀/一般/较差",
      "falsification_condition": "",
      "verification_checkpoints": [
        {"date": "", "event": "", "what_to_watch": ""}
      ],
      "cross_validated": true,
      "cross_validation_sources": [""],
      "benchmark_ticker": ""
    }
  ],

  "deep_dives": [
    {
      "name": "", "why_worth_studying": "", "core_logic": "",
      "what_market_misses": "", "catalyst": "",
      "failure_condition": "", "falsification_condition": "",
      "verification_checkpoints": [{"date": "", "event": "", "what_to_watch": ""}],
      "category": "A成长/B周期/C价值/D反转/E防御",
      "one_year_return_potential": ""
    }
  ],

  "barbell": {
    "offense": [""], "defense": [""], "environment_judgment": "", "bias": "进攻/防守/平衡", "rationale": ""
  },

  "logic_tracker": {
    "still_active": [
      {"thesis_id": "", "name": "", "status": "加强/削弱/不变", "evidence": "", "next_checkpoint": ""}
    ],
    "newly_falsified": [
      {"thesis_id": "", "name": "", "why_falsified": ""}
    ]
  },

  "cross_sectional_patterns": [
    {
      "pattern_type": "同向共振/产业链传导/行业分化/资本配置趋同/风险披露重叠/管理层措辞趋同",
      "companies": [""], "sectors": [""],
      "signal": "", "what_makes_it_compelling": "",
      "conviction": "高/中（来自几家独立公司交叉印证）",
      "failure_condition": ""
    }
  ],

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

1. 所有机会必须经过预期差验证。没有预期差的「机会」只是噪音。
2. 单一公司信号不能评为"强信号"。只有被至少2家独立公司交叉印证的信号，才能评为"强信号"。
3. 每个机会必须包含具体的 falsification_condition——"什么情况下这个判断是错的"。
4. 每个机会必须有至少一个 verification_checkpoint——"未来什么时候、通过什么事件可以检验这个判断"。
5. 不要输出任何买入/卖出建议。只输出条件判断和推理过程。
6. 英文→中文翻译，保留关键英文术语。
7. 看多和看空的机会都要找。市场下跌也是机会。
8. 如果 prompt 中提供了"活跃判断追踪"，你必须基于今天的信息，在 logic_tracker 中更新每条活跃判断的状态。不要说"没有足够信息"——你有责任基于现有信息做出最好的判断。
"""


def build_ai_input(items: List[Dict], fred_items: List[Dict], akshare_text: str, polymarket_markets: List[Dict] = None, active_theses: List[Dict] = None, calibration_stats: Dict = None, tavily_items: List[Dict] = None) -> str:
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

    parts = ["以下是从全网信息中筛选的待分析条目。请以「买方基金研究员」视角进行8层分析。\n"]

    # 经济数据优先
    if fred_items:
        parts.append("\n## 📊 美国经济指标（FRED）\n")
        for fi in fred_items:
            direction = "↑" if fi["change"] > 0 else "↓" if fi["change"] < 0 else "→"
            parts.append(f"- {fi['indicator']}({fi['series_id']}): {fi['value']} {direction}{fi['change']:+g} ({fi['change_pct']:+.2f}%) @{fi['last_date']}")

    if akshare_text:
        parts.append(f"\n## 📊 A股/中国宏观数据\n{akshare_text[:2000]}\n")

    # 预测市场数据
    if polymarket_markets:
        poly_text = format_polymarket_for_prompt(polymarket_markets)
        if poly_text:
            parts.append(poly_text)

    # 活跃判断追踪
    if active_theses:
        tracker_text = format_active_theses_for_prompt(active_theses)
        if tracker_text:
            parts.append(tracker_text)

    # 系统校准数据
    if calibration_stats:
        cal_text = format_calibration_for_prompt(calibration_stats)
        if cal_text:
            parts.append(cal_text)

    # 信息条目
    idx = 0

    # Tavily 实时搜索（放在最前面，AI 优先参考最新搜索）
    if tavily_items:
        parts.append(f"\n## 🔍 实时搜索补充 ({len(tavily_items)}条)\n")
        for item in tavily_items:
            idx += 1
            parts.append(f"[{idx}]【{item['platform']}】{item['source_name']} | {item.get('pub_date_display','?')}\n    {item['title']}\n    {item['description'][:500]}\n    {item.get('url','')}\n")
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


# ============================================================
# 活跃判断追踪
# ============================================================

THESES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "active_theses.json")
ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive")


def _parse_time_horizon_months(horizon: str) -> int:
    """从时间维度文本中估算月数上限。如 '1-6个月' → 6, '2-3周' → 1, '1年' → 12"""
    text = horizon.strip()
    year_m = re.search(r"(\d+)\s*年", text)
    if year_m: return int(year_m.group(1)) * 12
    month_m = re.findall(r"(\d+)\s*个?\s*月", text)
    if month_m: return max(int(m) for m in month_m)
    week_m = re.findall(r"(\d+)\s*周", text)
    if week_m: return max(1, int(max(week_m)) // 4)
    return 6  # 默认6个月


def load_active_theses() -> List[Dict]:
    """读取活跃判断，过滤已过期的"""
    if not os.path.exists(THESES_FILE):
        return []
    try:
        with open(THESES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    theses = data.get("theses", [])
    today = datetime.now().strftime("%Y-%m-%d")
    active = [t for t in theses if t.get("expiry_date", "") >= today]
    expired = [t for t in theses if t.get("expiry_date", "") < today]
    if expired:
        os.makedirs(os.path.join(ARCHIVE_DIR, "expired"), exist_ok=True)
        verified = 0
        for t in expired:
            v = verify_expired_thesis(t)
            if v:
                t["_verified"] = v
                verified += 1
                status = "✅" if v["correct"] else "❌"
                log(f"   {status} {t['name']}: {v['expected_direction']} vs {v['actual_direction']} ({v['price_change_pct']:+.1f}%)")
            arc_path = os.path.join(ARCHIVE_DIR, "expired", f"{t['id']}.json")
            try:
                with open(arc_path, "w", encoding="utf-8") as f:
                    json.dump(t, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        log(f"📦 归档 {len(expired)} 条到期判断 ({verified}条已价格验证)")
    return active


def format_active_theses_for_prompt(theses: List[Dict]) -> str:
    """将活跃判断格式化为 prompt 注入文本"""
    if not theses:
        return ""
    parts = ["\n## 📋 活跃判断追踪（此前识别、仍在有效期内的判断）\n"]
    parts.append("请基于今天的信息，更新以下每条判断的状态：逻辑在加强、削弱还是不变。")
    parts.append("如果某条判断已被今天的证据明确推翻，请标注为'证伪'并说明原因。")
    parts.append("不要在最终输出的 logic_tracker 中说'信息不足'——你必须基于现有信息给出最好的判断。\n")
    for i, t in enumerate(theses):
        signal_icon = {"强": "[强信号]", "中": "[中信信号]", "弱": "[弱信号]"}.get(t.get("signal_strength", ""), "")
        parts.append(f"[{i+1}] {signal_icon} **{t['name']}** | 创建:{t.get('created_date','?')} | 期限:{t.get('time_horizon','?')}")
        parts.append(f"    ID: {t['id']}")
        parts.append(f"    核心逻辑: {t.get('core_logic','')}")
        fc = t.get("falsification_condition", "")
        if fc: parts.append(f"    失效条件: {fc}")
        # 上次状态
        hist = t.get("history", [])
        if hist:
            last = hist[-1]
            parts.append(f"    上次状态({last.get('date','?')}): {last.get('status','?')} — {last.get('evidence','暂无记录')}")
        parts.append("")
    return "\n".join(parts)


def update_active_theses(ai_result: Dict, existing_theses: List[Dict]) -> List[Dict]:
    """基于 AI 输出更新活跃判断状态，并从 opportunity_ranking 提取新判断"""
    today = datetime.now().strftime("%Y-%m-%d")
    lt = ai_result.get("logic_tracker", {})
    still_active = lt.get("still_active", [])
    newly_falsified = lt.get("newly_falsified", [])

    existing_map = {t["id"]: t for t in existing_theses}

    # 更新现有判断状态
    for item in still_active:
        tid = item.get("thesis_id", "")
        if tid in existing_map:
            t = existing_map[tid]
            t["history"].append({
                "date": today,
                "status": item.get("status", "不变"),
                "evidence": item.get("evidence", "")
            })
            nc = item.get("next_checkpoint", "")
            if nc: t["next_checkpoint"] = nc

    # 处理证伪
    for item in newly_falsified:
        tid = item.get("thesis_id", "")
        if tid in existing_map:
            t = existing_map.pop(tid)
            t["history"].append({
                "date": today,
                "status": "证伪",
                "evidence": item.get("why_falsified", "")
            })
            t["falsified_date"] = today
            os.makedirs(os.path.join(ARCHIVE_DIR, "falsified"), exist_ok=True)
            arc_path = os.path.join(ARCHIVE_DIR, "falsified", f"{tid}.json")
            try:
                with open(arc_path, "w", encoding="utf-8") as f:
                    json.dump(t, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            log(f"❌ 证伪: {t['name']}")

    # 从 opportunity_ranking 提取新判断（之前不存在的）
    new_theses = list(existing_map.values())
    existing_names = {t["name"] for t in new_theses}
    ranking = ai_result.get("opportunity_ranking", [])
    for r in ranking:
        name = r.get("name", "").strip()
        if not name or name in existing_names:
            continue
        signal = r.get("signal_strength", "弱")
        if signal == "弱":
            continue  # 弱信号不进入追踪
        tid = hashlib.md5(name.encode()).hexdigest()[:12]
        horizon = r.get("time_horizon", "1-6个月")
        months = _parse_time_horizon_months(horizon)
        expiry = (datetime.now() + timedelta(days=months * 30)).strftime("%Y-%m-%d")
        thesis = {
            "id": tid,
            "name": name,
            "category": r.get("category", ""),
            "signal_strength": signal,
            "core_logic": r.get("core_logic", ""),
            "time_horizon": horizon,
            "falsification_condition": r.get("falsification_condition", ""),
            "verification_checkpoints": r.get("verification_checkpoints", []),
            "created_date": today,
            "expiry_date": expiry,
            "history": [{"date": today, "status": "新识别", "evidence": f"信号强度:{signal}"}]
        }
        new_theses.append(thesis)
        existing_names.add(name)
        log(f"🆕 新判断: {name} (ID:{tid}, 到期:{expiry})")

    # 保存
    with open(THESES_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": today, "theses": new_theses}, f, ensure_ascii=False, indent=2)
    log(f"📋 活跃判断追踪: {len(new_theses)}条 (初始{len(existing_theses)}条)")

    return new_theses


# ============================================================
# 价格验证 + 系统校准（自我进化）
# ============================================================

def _fetch_price_data(ticker: str, start_date: str, end_date: str) -> Optional[Dict]:
    """拉取 yfinance 历史价格，返回起止价格和方向"""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.strip())
        hist = t.history(start=start_date, end=end_date)
        if hist is None or len(hist) < 2:
            return None
        start_price = float(hist["Close"].iloc[0])
        end_price = float(hist["Close"].iloc[-1])
        pct_change = round((end_price - start_price) / start_price * 100, 1)
        direction = "up" if pct_change > 0 else "down" if pct_change < 0 else "flat"
        return {"ticker": ticker, "start_price": round(start_price, 2),
                "end_price": round(end_price, 2), "pct_change": pct_change,
                "direction": direction, "start": start_date, "end": end_date}
    except ImportError:
        return None
    except Exception:
        return None


def verify_expired_thesis(thesis: Dict) -> Optional[Dict]:
    """验证一条到期判断：判断方向 vs 实际价格方向"""
    ticker = thesis.get("benchmark_ticker", "").strip()
    if not ticker:
        return None
    created = thesis.get("created_date", "")
    expiry = thesis.get("expiry_date", "")
    if not created or not expiry:
        return None
    price = _fetch_price_data(ticker, created, expiry)
    if price is None:
        return None
    # 判断 thesis 的方向
    name = thesis.get("name", "")
    core = thesis.get("core_logic", "")
    text = (name + " " + core).lower()
    is_bearish = any(kw in text for kw in ["做空", "看空", "下行", "下跌", "short", "空头", "衰退", "放缓", "下降", "减少", "恶化", "弱势", "空"])
    is_bullish = any(kw in text for kw in ["做多", "看多", "上行", "上涨", "long", "多头", "增长", "加速", "上升", "增加", "改善", "强势", "多"])
    if is_bearish:
        expected = "down"
    elif is_bullish:
        expected = "up"
    else:
        return None
    actual = price["direction"]
    correct = (expected == actual)
    return {"ticker": ticker, "expected_direction": expected, "actual_direction": actual,
            "price_change_pct": price["pct_change"], "correct": correct,
            "thesis_name": name, "created": created, "expiry": expiry}


def get_calibration_stats() -> Dict:
    """从归档中统计校验结果"""
    stats = {"total_verified": 0, "correct": 0, "by_signal": {}, "by_category": {}}
    archive_dir = os.path.join(ARCHIVE_DIR, "expired")
    if not os.path.exists(archive_dir):
        return stats
    for fname in os.listdir(archive_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(archive_dir, fname), "r", encoding="utf-8") as f:
                t = json.load(f)
        except Exception:
            continue
        # 检查是否有验证结果
        verified = t.get("_verified")
        if not verified:
            # 尝试现场验证
            v = verify_expired_thesis(t)
            if v:
                t["_verified"] = v
                try:
                    with open(os.path.join(archive_dir, fname), "w", encoding="utf-8") as f:
                        json.dump(t, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                verified = v
            else:
                continue
        stats["total_verified"] += 1
        if verified["correct"]:
            stats["correct"] += 1
        signal = t.get("signal_strength", "未知")
        cat = t.get("category", "未知")
        stats["by_signal"].setdefault(signal, {"total": 0, "correct": 0})
        stats["by_signal"][signal]["total"] += 1
        if verified["correct"]:
            stats["by_signal"][signal]["correct"] += 1
        stats["by_category"].setdefault(cat, {"total": 0, "correct": 0})
        stats["by_category"][cat]["total"] += 1
        if verified["correct"]:
            stats["by_category"][cat]["correct"] += 1
    return stats


def format_calibration_for_prompt(stats: Dict) -> str:
    """将校准统计格式化为 prompt 注入文本"""
    if stats["total_verified"] < 3:
        return ""  # 样本不足，不显示
    overall = round(stats["correct"] / stats["total_verified"] * 100) if stats["total_verified"] > 0 else 0
    parts = [f"\n## 🎯 系统历史校准 (基于 {stats['total_verified']} 条到期判断)\n"]
    parts.append(f"总体命中率: {overall}% ({stats['correct']}/{stats['total_verified']})")
    parts.append("")
    parts.append("### 按信号强度")
    for signal in ["强", "中", "弱"]:
        s = stats["by_signal"].get(signal)
        if s and s["total"] > 0:
            rate = round(s["correct"] / s["total"] * 100)
            parts.append(f"- {signal}信号: {rate}% ({s['correct']}/{s['total']})")
    parts.append("")
    parts.append("### 按机会类别")
    for cat in ["A成长", "B周期", "C价值", "D反转", "E防御"]:
        s = stats["by_category"].get(cat)
        if s and s["total"] > 0:
            rate = round(s["correct"] / s["total"] * 100)
            parts.append(f"- {cat}: {rate}% ({s['correct']}/{s['total']})")
    parts.append("")
    parts.append("请在分析时参考以上校准数据。如果你的历史命中率在某些类别或信号强度上系统性偏高或偏低，请在评估新判断时做出相应调整。")
    return "\n".join(parts)


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


def send_feishu_image(image_key: str, chat_id: str) -> bool:
    """通过 Bot API 发送图片消息到指定群聊"""
    token = _get_feishu_token()
    if not token:
        return False
    payload = {
        "receive_id": chat_id,
        "msg_type": "image",
        "content": json.dumps({"image_key": image_key}, ensure_ascii=False)
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30
        )
        return resp.status_code == 200 and resp.json().get("code") == 0
    except Exception as e:
        log(f"   ⚠️ 图片发送异常: {e}")
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
            lines.append(f"**{e.get('topic','')}** {gap_dir}")
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
            signal = r.get("signal_strength", "?")
            signal_icon = {"强":"🟢","中":"🟡","弱":"🔴"}.get(signal, "⚪")
            lines.append(f"**#{r.get('rank','?')} {r.get('name','')}** [{r.get('category','?')}] {signal_icon} {signal}信号")
            lines.append(f"   核心逻辑: {r.get('core_logic','')}")
            lines.append(f"   预期差: {r.get('expectation_gap','')} | 期限: {r.get('time_horizon','?')} | 风险收益比: {r.get('risk_reward','?')}")
            fc = r.get("falsification_condition", "")
            if fc: lines.append(f"   ⚠️ 失效条件: {fc}")
            vcs = r.get("verification_checkpoints", [])
            if vcs:
                vc_strs = [f"{v.get('date','?')} {v.get('event','?')}" for v in vcs[:3]]
                lines.append(f"   📅 检验节点: {' | '.join(vc_strs)}")
            if r.get("cross_validated"):
                srcs = r.get("cross_validation_sources", [])
                lines.append(f"   🔗 交叉验证: {len(srcs)}个独立信源")
            lines.append("")
        md.append(section("三、全市场机会排行榜", "\n".join(lines)))

    # 跨公司模式发现（放在机会排行榜和深度研究之间，作为机会的交叉验证基础）
    csp = ai.get("cross_sectional_patterns", [])
    if csp:
        lines = []
        for p in csp:
            pattern_icon = {"同向共振":"🔴","产业链传导":"🔗","行业分化":"⚔️","资本配置趋同":"💰","风险披露重叠":"⚠️","管理层措辞趋同":"🗣️"}.get(p.get("pattern_type",""),"📊")
            lines.append(f"{pattern_icon} **{p.get('pattern_type','')}** | 胜率提升: {p.get('conviction','?')}")
            lines.append(f"   信号: {p.get('signal','')}")
            lines.append(f"   涉及: {'、'.join(p.get('companies',[])[:6])} | 行业: {'、'.join(p.get('sectors',[])[:4])}")
            lines.append(f"   为什么重要: {p.get('what_makes_it_compelling','')}")
            lines.append(f"   评分影响: {p.get('score_impact','')}")
            fc = p.get("failure_condition","")
            if fc: lines.append(f"   失效条件: {fc}")
            lines.append("")
        md.append(section("🔍 跨公司模式发现（多源交叉验证，提升胜率）", "\n".join(lines)))

    # 四、深度研究机会
    deep = ai.get("deep_dives", ai.get("top3_deep_dives", []))
    if deep:
        lines = []
        for t in deep:
            lines.append(f"**{t.get('name','')}** [{t.get('category','?')}]")
            lines.append(f"   为什么: {t.get('why_worth_studying','')}")
            lines.append(f"   核心逻辑: {t.get('core_logic','')}")
            lines.append(f"   市场可能错在哪: {t.get('what_market_misses','')}")
            lines.append(f"   催化剂: {t.get('catalyst','')}")
            lines.append(f"   失效条件: {t.get('failure_condition','')}")
            fc2 = t.get("falsification_condition", "")
            if fc2 and fc2 != t.get("failure_condition", ""):
                lines.append(f"   可证伪条件: {fc2}")
            vcs = t.get("verification_checkpoints", [])
            if vcs:
                vc_strs = [f"{v.get('date','?')} {v.get('event','?')}" for v in vcs[:3]]
                lines.append(f"   检验节点: {' | '.join(vc_strs)}")
            pot = t.get("one_year_return_potential","")
            if pot: lines.append(f"   一年回报潜力: {pot}")
            lines.append("")
        md.append(section("四、深度研究机会", "\n".join(lines)))

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

    # 六、活跃判断追踪
    lt = ai.get("logic_tracker", {})
    if lt:
        lines = []
        still = lt.get("still_active", [])
        falsified = lt.get("newly_falsified", [])
        if still:
            for item in still:
                status_icon = {"加强":"✅","削弱":"⚠️","不变":"➡️"}.get(item.get("status",""), "📌")
                lines.append(f"{status_icon} {item.get('status','')}: **{item.get('name','')}** — {item.get('evidence','')}")
                nc = item.get("next_checkpoint","")
                if nc: lines.append(f"   → 下个检验节点: {nc}")
        if falsified:
            for item in falsified:
                lines.append(f"❌ 证伪: **{item.get('name','')}** — {item.get('why_falsified','')}")
        if lines: md.append(section("六、活跃判断追踪", "\n".join(lines)))

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

    # 系统校准
    cal = stats.get("calibration", {})
    if cal and cal.get("total_verified", 0) >= 3:
        overall = round(cal["correct"] / cal["total_verified"] * 100)
        md.append(f"🎯 历史命中率: {overall}% ({cal['correct']}/{cal['total_verified']})")
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
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    # 开市检查
    if not args.force:
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:
            log("周末休市，不推送"); return
        if 1 <= weekday <= 4 and now.hour < 16:
            log(f"交易日未收盘(当前{now.hour}:{now.minute:02d})，不推送"); return
        if weekday == 0 and now.hour < 10:
            log("周一早于10点数据可能未更新，不推送"); return

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

    tasks = build_rss_urls(sources_config)
    log(f"📋 {len(tasks)} 个 RSS 抓取任务")

    all_items = fetch_all(tasks)

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


    # Tavily 实时搜索
    tavily_config = sources_config.get("tavily", {}) if isinstance(sources_config, dict) else {}
    tavily_items = fetch_tavily_data(tavily_config) if tavily_config.get("enabled") else []

    ai_input = build_ai_input(filtered, fred_items, akshare_text, fetch_polymarket_data(), load_active_theses(), get_calibration_stats(), tavily_items)
    ai_result = call_deepseek(ai_input)

    # 更新活跃判断追踪
    if ai_result:
        update_active_theses(ai_result, load_active_theses())

    stats = {"filtered": len(filtered),
             "ok_sources": len(set(it["source_name"] for it in all_items)),
             "total_sources": len(tasks),
             "calibration": get_calibration_stats()}

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
