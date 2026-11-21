# -*- coding: utf-8 -*-
"""市场声音：抓公开卖方/首席经济学家观点，用 Agnes 做摘要。

边界：
- AI 只做“信息整理”，不做预测、不改状态、不参与打分。
- 每条观点必须有材料里出现的来源 URL；没有来源的直接丢弃。
- 任何一步失败都返回 unavailable，卡片照常生成，只是少这一块。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import requests

BASE = Path(__file__).parent
CACHE = BASE / "cache"
CACHE.mkdir(exist_ok=True)

QUERIES = [
    "Goldman Sachs US equity strategist stocks outlook this week",
    "Bank of America chief investment strategist US stocks outlook",
    "Morgan Stanley US equity strategist stocks outlook",
    "UBS chief investment officer US equities outlook",
    "JPMorgan market strategist US stocks outlook",
    "Deutsche Bank US equity strategist outlook",
    "Citi US equity strategist outlook",
    "chief economist US recession stock market outlook",
]

SYSTEM_PROMPT = """你是信息整理员，不是分析师。
只从给定材料中提取卖方/首席经济学家对美股的公开观点。
硬规则：
1. 只能使用材料里出现的内容；禁止编造机构、人名、引语、目标价、日期。
2. 每条观点必须带材料里出现过的来源 URL；没有 URL 或 URL 不在材料里的，不要输出。
3. 立场只能是：多 / 中 / 空 / 未知；不确定就写“未知”。
4. point 不超过 40 个汉字；change 只能是：上调 / 下调 / 不变 / 未知。
5. 不要给买卖建议，不要预测涨跌。
6. 如果材料里没有可用观点，views 输出空数组。
输出 JSON：
{"views":[{"house":"","person":"","stance":"多/中/空/未知","point":"","change":"上调/下调/不变/未知","source":"https://..."}],"consensus":"","divergence":""}"""


@dataclass
class View:
    house: str
    person: str
    stance: str
    point: str
    change: str
    source: str


@dataclass
class Digest:
    views: list[View] = field(default_factory=list)
    consensus: str = ""
    divergence: str = ""
    status: str = "ok"          # ok / unavailable
    reason: str = ""
    as_of: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Digest":
        return cls(
            views=[View(**v) for v in d.get("views", [])],
            consensus=d.get("consensus", ""),
            divergence=d.get("divergence", ""),
            status=d.get("status", "ok"),
            reason=d.get("reason", ""),
            as_of=d.get("as_of", ""),
        )


def _cache_path() -> Path:
    return CACHE / f"voices_{date.today().isoformat()}.json"


def _tavily(query: str, api_key: str) -> list[dict]:
    r = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "topic": "news",
            "search_depth": "advanced",
            "max_results": 4,
            "days": 7,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def _collect_sources() -> list[dict]:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return []
    out: list[dict] = []
    for q in QUERIES:
        try:
            out.extend(_tavily(q, key))
        except Exception as exc:
            print(f"[voices] Tavily 查询失败: {type(exc).__name__} {exc}")
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in out:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped[:12]


def _source_text(sources: list[dict]) -> str:
    blocks = []
    for i, s in enumerate(sources, 1):
        title = str(s.get("title", ""))[:120]
        url = str(s.get("url", ""))
        content = str(s.get("content", ""))[:700]
        blocks.append(f"[{i}] {title}\n{url}\n{content}")
    return "\n\n".join(blocks)


def _clean_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    return text.strip()


def _call_agnes(source_text: str) -> dict | None:
    key = os.getenv("AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return None
    base = os.getenv("AI_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL", "agnes-2.5-flash")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "材料：\n\n" + source_text + "\n\n请只输出 JSON。"},
    ]
    for attempt in range(2):
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 3000,
        }
        if attempt == 0:
            body["response_format"] = {"type": "json_object"}
        else:
            messages[1]["content"] += "\n\n只输出 JSON，不要解释，不要 markdown 代码块。"
        try:
            r = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
            if r.status_code != 200:
                print(f"[voices] Agnes HTTP {r.status_code}: {r.text[:300]}")
                continue
            try:
                data = r.json()
            except Exception:
                print(f"[voices] Agnes 非 JSON 响应: {r.text[:300]}")
                continue
            choices = data.get("choices") or []
            if not choices:
                print(f"[voices] Agnes 无 choices: {str(data)[:300]}")
                continue
            choice = choices[0]
            content = choice.get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
            content = str(content).strip()
            finish = choice.get("finish_reason")
            usage = data.get("usage", {})
            print(f"[voices] Agnes 第 {attempt + 1} 次: finish={finish} "
                  f"completion={usage.get('completion_tokens')} chars={len(content)}")
            if not content:
                print(f"[voices] Agnes 空内容: {str(data)[:300]}")
                continue
            text = _clean_json(content)
            try:
                return json.loads(text)
            except Exception:
                print(f"[voices] Agnes 返回非 JSON: {content[:300]}")
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[voices] Agnes 摘要异常（第 {attempt + 1} 次）: {type(exc).__name__} {exc}")
    return None


def _match_source(source: str, allowed: set[str]) -> str:
    if source in allowed:
        return source
    for url in allowed:
        if url and (source.startswith(url) or url.startswith(source)):
            return url
    return ""


def _norm_stance(raw: str) -> str:
    s = raw.strip()
    if s in ("多", "看多", "偏多", "bullish", "Bullish"):
        return "多"
    if s in ("空", "看空", "偏空", "bearish", "Bearish"):
        return "空"
    if s in ("中", "中性", "neutral", "Neutral"):
        return "中"
    return "未知"


def _norm_change(raw: str) -> str:
    s = raw.strip()
    if "上调" in s:
        return "上调"
    if "下调" in s:
        return "下调"
    if "不变" in s:
        return "不变"
    return "未知"


def _validate(data: dict, sources: list[dict], as_of: str) -> Digest:
    allowed = {str(s.get("url", "")).strip() for s in sources if s.get("url")}
    views: list[View] = []
    for x in data.get("views", []):
        if not isinstance(x, dict):
            continue
        house = str(x.get("house", "")).strip()
        point = str(x.get("point", "")).strip()
        source = _match_source(str(x.get("source", "")).strip(), allowed)
        if not house or not point or not source:
            continue
        views.append(View(
            house=house[:30], person=str(x.get("person", "")).strip()[:30],
            stance=_norm_stance(str(x.get("stance", ""))),
            point=point[:60], change=_norm_change(str(x.get("change", ""))),
            source=source,
        ))
    views = views[:6]
    if not views:
        return Digest(status="unavailable", reason="没有找到可引用的公开观点", as_of=as_of)
    return Digest(
        views=views,
        consensus=str(data.get("consensus", "")).strip()[:100],
        divergence=str(data.get("divergence", "")).strip()[:100],
        status="ok",
        as_of=as_of,
    )


def fetch_digest(weekly: bool = False) -> Digest:
    """返回市场声音摘要；任何失败都不抛异常。weekly 目前只影响调用方展示条数。"""
    today = date.today().isoformat()
    path = _cache_path()
    if path.exists():
        try:
            return Digest.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if not os.getenv("TAVILY_API_KEY"):
        return Digest(status="unavailable", reason="未配置 TAVILY_API_KEY，市场声音跳过", as_of=today)
    if not (os.getenv("AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
        return Digest(status="unavailable", reason="未配置 AI_API_KEY，市场声音跳过", as_of=today)

    sources = _collect_sources()
    if not sources:
        return Digest(status="unavailable", reason="没有搜到可引用的公开观点", as_of=today)

    data = _call_agnes(_source_text(sources))
    if data is None:
        return Digest(status="unavailable", reason="Agnes 返回异常，本次跳过", as_of=today)

    digest = _validate(data, sources, today)
    if digest.status == "ok":
        try:
            path.write_text(json.dumps(digest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[voices] 缓存写入失败: {type(exc).__name__} {exc}")
    return digest
