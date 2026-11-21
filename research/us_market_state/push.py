# -*- coding: utf-8 -*-
"""飞书纯文本推送 + 每周去重。

凭据优先环境变量，其次 ~/.market-radar/feishu.env（和 A 股周期面板共用）。
纯文本而不是卡片：历史上卡片 ID 在 bridge 环境出过问题，文本更稳。
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
    keys = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID")
    vals = {k: os.environ.get(k, "") for k in keys}
    if CRED_PATH.exists():
        for line in CRED_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in vals and not vals[k.strip()]:
                vals[k.strip()] = v.strip()
    missing = [k for k, v in vals.items() if not v]
    if missing:
        raise RuntimeError(f"飞书凭据缺失: {', '.join(missing)}")
    return vals


def _uuid(text: str) -> str:
    return "us-state-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def send_text(text: str, creds: dict[str, str]) -> bool:
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
    payload = {
        "receive_id": creds["FEISHU_CHAT_ID"],
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "uuid": _uuid(text),
    }
    for attempt in range(3):
        try:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json=payload, headers=headers, timeout=30,
            )
            body = r.json()
            if r.status_code == 200 and body.get("code") == 0:
                print(f"[push] 飞书推送成功（{len(text)} 字符）")
                return True
            print(f"[push] 飞书推送失败: HTTP {r.status_code} code={body.get('code')} {str(body.get('msg'))[:100]}")
        except Exception as exc:
            print(f"[push] 飞书推送异常: {type(exc).__name__} {exc}")
        time.sleep(2 + attempt * 2)
    return False


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
