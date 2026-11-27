#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 失败时发飞书：读推送标记，说清“已发出/未确认发出”。

push_feishu 只有推送成功才写 cache/last_push.json；标记里的
panel_date / pushed_at 能区分“本次已推”“今天推过旧面板”“今天没推”。
发送只用标准库，pip install 失败时 GitHub 上仍然能把告警发出来。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
MARK = BASE / "cache" / "last_push.json"
CRED_PATH = Path.home() / ".market-radar" / "feishu.env"


def build_message(mark: dict, expected, url: str,
                  today: str | None = None) -> str:
    today = today or date.today().isoformat()
    panel_date = mark.get("panel_date")
    pushed_at = str(mark.get("pushed_at") or "")
    target = str(expected) if expected is not None else "无法判断（交易日历不可用）"

    if expected is not None and panel_date == str(expected):
        return (f"⚠️ A股周期面板已发出（面板日期 {panel_date}），"
                f"但收尾步骤失败，面板不用补看。\n{url}")
    if panel_date and pushed_at[:10] == today:
        return (f"⚠️ A股周期面板已发出，但发的是 {panel_date} 的旧面板"
                f"（{pushed_at} 推送），数据没有更新到 {target}；"
                f"请以面板上的数据日期为准。\n{url}")
    if panel_date:
        return (f"⚠️ A股周期面板未发出新面板：最近一次成功推送是 {panel_date} 的面板"
                f"（{pushed_at or '时间未知'}），本次应推 {target}。\n{url}")
    return f"⚠️ A股周期面板未发出：没有找到任何成功推送记录。\n{url}"


def _load_creds() -> dict[str, str]:
    keys = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_WEBHOOK_URL")
    vals = {k: os.environ.get(k, "") for k in keys}
    if CRED_PATH.exists():
        try:
            for line in CRED_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in vals and not vals[k.strip()]:
                    vals[k.strip()] = v.strip()
        except Exception:
            pass
    return vals


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send_text(text: str, creds: dict[str, str]) -> bool:
    app_id, secret = creds.get("FEISHU_APP_ID"), creds.get("FEISHU_APP_SECRET")
    chat = creds.get("FEISHU_CHAT_ID")
    if not (app_id and secret and chat):
        print("失败通知未送达：飞书凭据缺失")
        return False
    try:
        tok = _post_json(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": secret})
    except Exception as exc:
        print("失败通知未送达：token", type(exc).__name__, exc)
        return False
    token = tok.get("tenant_access_token")
    if not token:
        print("失败通知未送达：token", tok.get("code"), tok.get("msg"))
        return False
    payload = {
        "receive_id": chat,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "uuid": "cycle-notify-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20],
    }
    try:
        body = _post_json(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            payload, {"Authorization": f"Bearer {token}"})
        ok = body.get("code") == 0
        if not ok:
            print("失败通知未送达：", body.get("code"), body.get("msg"))
    except Exception as exc:
        print("失败通知未送达：", type(exc).__name__, exc)
        ok = False
    hook = creds.get("FEISHU_WEBHOOK_URL")
    if hook:
        try:
            _post_json(hook, {"msg_type": "text", "content": {"text": text}})
        except Exception as exc:
            print("外部群失败通知失败:", type(exc).__name__, exc)
    return ok


def main() -> int:
    try:
        mark = json.loads(MARK.read_text(encoding="utf-8"))
    except Exception:
        mark = {}
    try:
        import run_daily
        expected = run_daily.latest_completed_trading_day(datetime.now())
    except Exception:
        expected = None

    url = (f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    msg = build_message(mark, expected, url)
    print(msg)
    return 0 if _send_text(msg, _load_creds()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
