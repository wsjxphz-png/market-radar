#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 失败时发飞书：说清“到底发没发、哪一步失败”。

判断依据（按可靠性排序）：
1. 本次运行写的 cache/last_run_status.json（仅当日期是今天）；
2. 仓库里已提交的推送标记 cache/last_daily_push.json / last_notice_push.json /
   last_weekly_push.json —— 重试班次在生成前就失败时，本班次没有 status，
   但早班次可能已经把卡片发出去了，标记里的 pushed_at 能证明这一点。

发送只用标准库：pip install 失败时 GitHub 上仍然能把告警发出来。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

try:  # Windows 控制台默认 GBK，避免 emoji 让通知在打印阶段崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).parent
CACHE = BASE / "cache"
STATUS_FILE = CACHE / "last_run_status.json"
CRED_PATH = Path.home() / ".market-radar" / "feishu.env"

KIND_LABELS = {"daily": "日报", "weekly": "周报"}

MARKERS = {
    "daily": ("last_daily_push.json", "last_notice_push.json"),
    "weekly": ("last_weekly_push.json",),
}

STEP_NAMES = {
    "daily": {
        "DEPS_OUTCOME": "安装依赖",
        "CALENDAR_OUTCOME": "休市检查",
        "SMOKE_OUTCOME": "数据源冒烟测试",
        "TESTS_OUTCOME": "状态机测试",
        "REPORT_OUTCOME": "生成并推送日报",
        "SAVE_OUTCOME": "保存状态",
    },
    "weekly": {
        "DEPS_OUTCOME": "安装依赖",
        "SMOKE_OUTCOME": "数据源冒烟测试",
        "TESTS_OUTCOME": "状态机测试",
        "REPORT_OUTCOME": "生成并推送周报",
        "SAVE_OUTCOME": "保存推送标记",
    },
}


def failed_steps(env: dict[str, str], kind: str) -> str:
    names = [name for key, name in STEP_NAMES[kind].items()
             if env.get(key) == "failure"]
    return "、".join(names) or "未知步骤"


def _marker_sent_today(kind: str, today: str) -> dict | None:
    """早班次已成功推送的证据：标记文件只有推送成功才会写。"""
    for name in MARKERS[kind]:
        try:
            d = json.loads((CACHE / name).read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("pushed_at") == today:
            return d
    return None


def build_message(kind: str, status: dict, failed: str, url: str,
                  today: str | None = None, marker: dict | None = None) -> str:
    label = KIND_LABELS.get(kind, kind)
    today = today or date.today().isoformat()
    status_today = status.get("kind") == kind and status.get("date") == today

    pushed = bool(status_today and status.get("pushed"))
    day_key = status.get("day_key") if status_today else None
    if not pushed and marker:
        pushed = True
        day_key = day_key or marker.get("key")

    if pushed:
        when = (f"（{'数据日' if kind == 'daily' else '数据周'} {day_key}）"
                if day_key else "（休市提示）")
        shadow = "【干跑】卡片" if status_today and status.get("mode") == "shadow" else "卡片"
        return (f"⚠️ 美股状态{label}已发出{when}，"
                f"但「{failed}」失败，{shadow}不用补看。\n{url}")
    if status_today:
        return (f"⚠️ 美股状态{label}未发出：{status.get('reason') or '未知原因'}"
                f"（失败步骤：{failed}）。\n{url}")
    return (f"⚠️ 美股状态{label}今天没有生成，也没有发出："
            f"「{failed}」失败，生成步骤未运行。\n{url}")


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
        "uuid": "us-notify-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20],
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
    ap = argparse.ArgumentParser(description="workflow 失败通知")
    ap.add_argument("--kind", choices=tuple(KIND_LABELS), required=True)
    args = ap.parse_args()

    status: dict = {}
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    marker = _marker_sent_today(args.kind, date.today().isoformat())

    url = (f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    msg = build_message(args.kind, status, failed_steps(os.environ, args.kind), url,
                        marker=marker)
    print(msg)
    return 0 if _send_text(msg, _load_creds()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
