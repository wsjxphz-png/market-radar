# -*- coding: utf-8 -*-
"""飞书推送：内部群（Bot API）+ 外部群（Webhook），支持卡片和纯文本。

凭据优先环境变量，其次 ~/.market-radar/feishu.env（和 A 股面板共用）。
- FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID：内部群 Bot
- FEISHU_WEBHOOK_URL：外部群自定义机器人（可选）
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

import requests

BASE = Path(__file__).parent
CACHE = BASE / "cache"
CRED_PATH = Path.home() / ".market-radar" / "feishu.env"


def _mark_path(kind: str) -> Path:
    return CACHE / f"last_{kind}_push.json"


def load_credentials() -> dict[str, str]:
    keys = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_WEBHOOK_URL")
    vals = {k: os.environ.get(k, "") for k in keys}
    if CRED_PATH.exists():
        for line in CRED_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in vals and not vals[k.strip()]:
                vals[k.strip()] = v.strip()
    missing = [k for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID") if not vals[k]]
    if missing:
        raise RuntimeError(f"飞书凭据缺失: {', '.join(missing)}")
    return vals


def _uuid(text: str) -> str:
    return "us-state-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _post_bot(payload: dict, creds: dict[str, str]) -> bool:
    try:
        tok = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": creds["FEISHU_APP_ID"], "app_secret": creds["FEISHU_APP_SECRET"]},
            timeout=15,
        ).json()
    except Exception as exc:
        print(f"[push] 飞书 token 请求失败: {type(exc).__name__} {exc}")
        return False
    token = tok.get("tenant_access_token")
    if not token:
        print(f"[push] 飞书 token 失败: code={tok.get('code')} msg={tok.get('msg')}")
        return False

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json=payload, headers=headers, timeout=30,
            )
            body = r.json()
            if r.status_code == 200 and body.get("code") == 0:
                print("[push] 内部群 Bot 推送成功")
                return True
            print(f"[push] 内部群失败: HTTP {r.status_code} code={body.get('code')} {str(body.get('msg'))[:100]}")
        except Exception as exc:
            print(f"[push] 内部群异常: {type(exc).__name__} {exc}")
        time.sleep(2 + attempt * 2)
    return False


def _post_webhook(payload: dict, creds: dict[str, str]) -> bool:
    url = creds.get("FEISHU_WEBHOOK_URL", "")
    if not url:
        return True
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30)
            body = r.json()
            if r.status_code == 200 and (body.get("code") == 0 or body.get("StatusCode") == 0):
                print("[push] 外部群 Webhook 推送成功")
                return True
            print(f"[push] 外部群失败: HTTP {r.status_code} {str(body)[:120]}")
        except Exception as exc:
            print(f"[push] 外部群异常: {type(exc).__name__} {exc}")
        time.sleep(2 + attempt * 2)
    return False


def _send(bot_payload: dict, hook_payload: dict, creds: dict[str, str]) -> bool:
    """内部群是主通道：它成功就按“已送达”处理。

    外部群 Webhook 是可选的第二通道；它失败时只记日志，不返回失败——
    否则 run_daily 不写去重标记，重试班次会把同一条消息在内部群里再发一遍。
    """
    ok_bot = _post_bot(bot_payload, creds)
    ok_hook = _post_webhook(hook_payload, creds)
    if ok_bot and not ok_hook:
        print("[push] 外部群 Webhook 失败；内部群已送达，按成功处理，避免重复推送")
    return ok_bot


def send_card(title: str, content: str, template: str, creds: dict[str, str]) -> bool:
    card = {
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": [{"tag": "markdown", "content": content}],
    }
    bot_payload = {
        "receive_id": creds["FEISHU_CHAT_ID"],
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
        "uuid": _uuid(title + content),
    }
    hook_payload = {"msg_type": "interactive", "card": card}
    return _send(bot_payload, hook_payload, creds)


def send_text(text: str, creds: dict[str, str]) -> bool:
    bot_payload = {
        "receive_id": creds["FEISHU_CHAT_ID"],
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "uuid": _uuid(text),
    }
    hook_payload = {"msg_type": "text", "content": {"text": text}}
    return _send(bot_payload, hook_payload, creds)


def already_pushed(key: str, kind: str = "daily") -> bool:
    try:
        d = json.loads(_mark_path(kind).read_text(encoding="utf-8"))
        return (d.get("key") or d.get("week_key")) == key
    except Exception:
        return False


def mark_pushed(key: str, kind: str = "daily") -> None:
    path = _mark_path(kind)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"key": key, "pushed_at": date.today().isoformat()},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    os.replace(tmp, path)
