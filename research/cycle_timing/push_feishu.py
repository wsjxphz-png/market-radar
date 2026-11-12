#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把日频面板摘要推送到飞书群。

设计原则：
- 用纯文本，不用卡片（历史上卡片 ID 在 bridge 环境出过问题）。
- 指标名可以专业，但每个冒号后面都要让股票新手看懂“这意味着什么”。
- 同一天同一份面板只推一次；失败不写标记，下次会重试。
- 凭据优先读环境变量，其次读 ~/.market-radar/feishu.env（ACL 仅当前用户）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).parent
CACHE = BASE / "cache"
MARK = CACHE / "last_push.json"
CRED_PATH = Path.home() / ".market-radar" / "feishu.env"

TONE = {
    "risk": "要小心", "ok": "健康", "watch": "要观察", "bottom": "底部区域",
    "neutral": "中性", "down": "偏弱", "cheap": "偏便宜",
}

# 顶部风险的五个来源，翻成新手能看懂的话
TOP_LABELS = {
    "估值": "估值偏贵",
    "信用": "信用环境收紧",
    "广度": "指数涨但多数行业没跟上",
    "情绪": "借钱炒股/交易过热",
    "趋势": "涨得离200日线太远",
}


def _q(v) -> str:
    """分位：0.49 → 49%"""
    return "—" if pd.isna(v) else f"{v:.0%}"


def _flow(v: float) -> str:
    """把净申购说成人话：正数=净买入，负数=净卖出。"""
    if pd.isna(v):
        return "—"
    return f"净买入 {abs(v):,.0f} 亿元" if v >= 0 else f"净卖出 {abs(v):,.0f} 亿元"


def _date(d) -> str:
    return "—" if d is None or pd.isna(d) else str(pd.Timestamp(d).date())


def _uuid(text: str) -> str:
    """飞书消息去重键：同一条内容重试时 uuid 不变，服务端 1 小时内只发一次。"""
    return "cyclepanel-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _plain_alert(a: str) -> str:
    """面板提示面向命令行用户；推送面向普通人，把黑话和“请运行某命令”换成人话。"""
    a = re.sub(
        r"数据日 (\d{4}-\d{2}-\d{2}) 落后于最近交易日 (\d{4}-\d{2}-\d{2})，可能未更新；",
        r"今天的数据只更新到 \1，比最近该有的交易日 \2 还晚，可能还没更新完；", a)
    a = re.sub(r"请运行 [^。]+。", "自动任务会继续重试，连续多日如此说明数据源延迟。", a)
    m = re.match(r"状态无变化（上次基线 (.+?)）。", a)
    if m:
        return f"和上次检查相比，各项状态没有变化（上次检查是 {m.group(1)}）。"
    if a.startswith("首次运行"):
        return "这是第一次运行，还没有上次记录；从下一次开始，这里会写“和上次比哪里变了”。"
    m = re.match(r"^(.+?)：(.+?) → (.+)$", a)
    if m:
        return f"{m.group(1)}：从“{m.group(2)}”变成“{m.group(3)}”（具体含义见下文）。"
    return a


def _note_plain(h) -> str:
    """把健康检查里的 T-5 / 停更 这类内部记号翻成人话。"""
    note = h.note or ""
    m = re.fullmatch(r"T-(\d+)", note)
    if m:
        return f"比行情晚 {m.group(1)} 个交易日"
    m = re.fullmatch(r"滞后 (\d+) 个交易日", note)
    if m:
        return f"比行情晚 {m.group(1)} 个交易日"
    if note == "当日":
        return "和行情同一天"
    if note == "无数据":
        return "没有数据"
    m = re.match(r"停更/过期 (\d+) 个交易日", note)
    if m:
        return f"已经 {m.group(1)} 个交易日没更新，这次判断不用它"
    if note.startswith("停更"):
        return "已经很久没更新，这次判断不用它"
    return note


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


def _index_meaning(i) -> str:
    """一句话说清“这个状态对我意味着什么”，不出现 top<0.4 / MA20 / 分位。"""
    hit = sum(1 for v in getattr(i, "bottom_flags", {}).values() if v)
    thit = sum(1 for v in getattr(i, "top_flags", {}).values() if v)

    if pd.isna(i.ma200):
        trend = ""
    else:
        gap = i.close / i.ma200 - 1
        side = "低" if gap < 0 else "高"
        trend = f"现在价格比200日线（过去一年的平均成本）{side} {abs(gap) * 100:.1f}%，"
    dd = ("" if pd.isna(i.drawdown_252)
          else f"比一年内最高点低 {abs(i.drawdown_252) * 100:.1f}%。")

    if i.state.startswith("顶部"):
        return (f"顶部 5 项风险条件里有 {thit} 项亮了红灯。意思是涨得偏高、买的人偏拥挤，"
                "规则上要防守/减仓，不是加仓的时候。")
    if "强" in i.state:
        if pd.isna(i.ma200):
            trend_txt = "趋势数据不足。"
        elif i.close < i.ma200:
            trend_txt = "趋势还没修复，价格仍在200日线下方。"
        else:
            trend_txt = "价格已站上200日线，但还要连续5个交易日站稳才算确认。"
        return (f"“强观察”的意思是：5 项见底条件里满足了 {hit} 项，便宜和超跌基本都到位了。"
                f"{trend}{dd}{trend_txt}"
                "观察区只说明“跌得深、变便宜”，不是买点。")
    if "弱" in i.state:
        return (f"“弱观察”的意思是：5 项见底条件里只满足了 {hit} 项，便宜和超跌的条件还不齐。"
                f"{trend}{dd}趋势也没修复，确定性比“强观察”低，不是买点。")
    if i.state == "趋势正常":
        return "价格在200日线上方，趋势正常，按规则可以持有。"
    return "价格在200日线下方，属于中期下跌趋势，规则上以防守为主。"


def _etf_read(e) -> str:
    """按实际数值给结论，避免把某一天的读数写死成永久说明。"""
    out = []
    if pd.notna(e.total_d20_pct):
        if e.total_d20 > 0 and e.total_d20_pct >= 0.8:
            out.append("最近一个月大资金在持续大额买入，在历史上属于极高水平，像是在托底。")
        elif e.total_d20 < 0 and e.total_d20_pct <= 0.2:
            out.append("最近一个月大资金在持续卖出，在历史上属于极低水平，资金在撤。")
        else:
            out.append("最近一个月大资金有买有卖，没有明显的托底或撤退。")
    if pd.notna(e.total_d5):
        if e.total_d5 > 0:
            out.append("最近5个交易日仍是净买入，托底还在继续。")
        elif e.total_d5 < 0:
            out.append("最近5个交易日变成净卖出，这波托底暂停了。")
        else:
            out.append("最近5个交易日买卖基本持平。")
    return "".join(out)


def _top_verdict(i, lit: int, total: int) -> str:
    if i.state.startswith("顶部"):
        return "已经进入顶部风险区，规则上减仓/防守。"
    if pd.notna(i.ma200) and i.close < i.ma200:
        return "趋势还没修复，先防守。"
    if total and lit / total >= 0.4:
        return "接近警戒线，不加仓。"
    return "还没到警戒线，按趋势规则持有。"


def _top_risk_lines(pan) -> list[str]:
    """每天把“顶部风险”摆出来：上涨阶段这才是主菜，不是等跌了才看。"""
    out = []
    for i in pan.indices:
        flags = getattr(i, "top_flags", {}) or {}
        lit = [k for k, v in flags.items() if v]
        names = "、".join(TOP_LABELS.get(k, k) for k in lit) or "无"
        if not flags:
            out.append(f"{i.name}：风险数据不足，这次不判断。")
            continue
        out.append(f"{i.name}：风险条件亮了 {len(lit)} 项（{names}）。"
                   f"{_top_verdict(i, len(lit), len(flags))}")
    return out


def _bottom_state_plain(b) -> str:
    """把“疑似 / 观察（未确认；强：X）”翻成一句人话。"""
    if b.name == "政策底":
        return {"确认": "确认，三个迹象都出现了",
                "疑似": "疑似，出现了两个迹象"}.get(b.status, "还没出现，托底迹象不足")
    if b.name == "市场底":
        if b.status.startswith("确认"):
            if "顶部风险" in b.status:
                return "已确认，下跌趋势修复（但顶部风险升高，不宜追买）"
            return "已确认，下跌趋势修复"
        if b.status.startswith("观察"):
            m = re.search(r"强：(.+?)）", b.status)
            return f"观察区，还没确认（强观察：{m.group(1)}）" if m else "观察区，还没确认"
        return "还没出现，下跌可能没走完"
    if b.name == "盈利底":
        if b.status.startswith("确认"):
            return "已确认，盈利在改善"
        if b.status.startswith("疑似"):
            return "疑似，盈利跌得少了"
        return "还没出现，盈利还在变差"
    return b.status


def _plain_headline(pan) -> str:
    top = [i.name for i in pan.indices if i.state.startswith("顶部")]
    strong = [i.name for i in pan.indices if "强" in i.state]
    weak = [i.name for i in pan.indices if "弱" in i.state]
    below = [i.name for i in pan.indices if pd.isna(i.ma200) or i.close < i.ma200]
    if top:
        return f"现在不是买的时候：{'、'.join(top)} 已经进入“顶部风险区”，规则上要防守/减仓。"
    if strong:
        txt = (f"现在是“便宜但还没转强”的阶段：{'、'.join(strong)}已经跌到便宜、超跌的位置"
               "（强观察区），但趋势还没修复")
        if below:
            txt += f"（{'、'.join(below)}还在200日线下方）"
        return txt + "。规则上先观察，不急着买。"
    if weak:
        return (f"现在是下跌趋势里的“便宜区”阶段：{'、'.join(weak)}刚开始变便宜（弱观察），"
                "但趋势没修复，还不是买点。")
    warn = [i.name for i in pan.indices if getattr(i, "top_adj", 0.0) >= 0.4]
    if warn:
        return (f"趋势还没坏，但顶部风险开始累积：{'、'.join(warn)}的风险条件已经亮了 2 项以上。"
                "规则上不加仓，连续5个交易日跌破200日线就防守。")
    return "现在没有明显的顶部或底部信号，按趋势规则持有或防守。"


def build_message(pan) -> str:
    L: list[str] = []
    L.append(f"【A股周期面板】{pan.date.date()} 收盘")
    L.append("")
    L.append(f"一句话：{_plain_headline(pan)}")
    L.append("")

    L.append("■ 今日变化（和上次比）")
    if not pan.alerts:
        L.append("· 无")
    for a in pan.alerts:
        L.append(f"· {_plain_alert(a)}")
    L.append("")

    L.append("■ 主要指数现在是什么状态")
    for i in pan.indices:
        move = "涨" if i.chg1 >= 0 else "跌"
        L.append(f"{i.name}：{i.close:,.2f} 点（{move} {abs(i.chg1) * 100:.2f}%）。")
        L.append(_index_meaning(i))
    L.append("")

    L.append("■ 顶部风险（涨上去以后，每天看这个）")
    L.extend(_top_risk_lines(pan))
    if any("信用" not in (getattr(i, "top_flags", {}) or {}) for i in pan.indices):
        L.append("说明：社融数据太旧，“信用”这一项不参与，亮灯数按剩下的条件算。")
    L.append("规则：风险条件亮到 2 项开始谨慎（不加仓）；亮到 3 项、并且价格在200日线上方，"
             "就是“顶部风险区”，规则上减仓/防守。最硬的防守线：价格连续5个交易日跌破200日线。")
    L.append("")

    L.append("■ 三个底：政策底 → 市场底 → 盈利底（历史上按这个顺序出现）")
    market_confirmed = pan.bottoms.market.status.startswith("确认")
    for b in (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings):
        if b.name == "政策底" and market_confirmed:
            # 市场已修复后，政策底的三个条件自然回落，再说“未出现”会让人误以为还要等底
            L.append("政策底：当前不适用（市场已修复，政策底只在下跌/磨底阶段看）")
            L.append("政策底是“政策开始托底”的信号；现在市场已经修复，这一项不用每天盯。")
        else:
            L.append(f"{b.name}：{_bottom_state_plain(b)}")
            L.append(b.plain or b.meaning)
        L.append("")
    L.append(f"现在走到哪一步：{pan.bottoms.phase.replace('磨底', '磨底（在底部附近反复震荡、还没转强）')}")
    L.append("")

    e = pan.etf
    L.append("■ 大资金动向（用宽基ETF的份额变化当代理，数据比行情晚1天）")
    L.append(f"最新 {e.date.date()}：当天{_flow(e.total_today)}；"
             f"最近5个交易日{_flow(e.total_d5)}（历史排名 {_q(e.total_d5_pct)}）；"
             f"最近20个交易日{_flow(e.total_d20)}（历史排名 {_q(e.total_d20_pct)}）。")
    L.append("怎么理解：ETF的份额只有通过“申购”才会变多，所以份额大增，说明有大资金真金白银在买。"
             "但这是所有大资金加在一起（国家队、保险、外资、量化等），公开数据分不清具体是谁。")
    L.append(_etf_read(e))
    top_in = "、".join(f"{n} {v:,.0f} 亿元" for _, n, v in e.top_in) or "无"
    top_out = "、".join(f"{n} {abs(v):,.0f} 亿元" for _, n, v in e.top_out) or "无"
    L.append(f"最新一天买入最多：{top_in}；卖出最多：{top_out}。")
    L.append("提醒：历史排名只看最近20个交易日。如果某天的大额买入慢慢滚出这20天，"
             "即使没有新卖出，排名也会掉下来；所以要同时看最近5天的方向。")
    L.append("")

    L.append("■ 五维体检（从五个角度看市场）")
    for d in pan.dims:
        L.append(f"{d.name}：{TONE.get(d.tone, d.tone)}")
        L.append(d.plain or d.summary)
    L.append("")

    below = [i.name for i in pan.indices if pd.isna(i.ma200) or i.close < i.ma200]
    strong = [i.name for i in pan.indices if "强" in i.state]
    L.append("■ 接下来盯什么")
    if pan.bottoms.market.status.startswith("确认"):
        L.append("1. 市场底已经确认：接下来重点是顶部风险（见上方“顶部风险”），"
                 "连续5个交易日跌破200日线就回到防守。")
    else:
        txt = (f"1. 市场底确认：{'、'.join(below) or '主要指数'}需要连续5个交易日站上200日线；"
               "顶部风险分数低于0.4（0到1，越低越安全）才适合加仓，分数高说明涨得太急，先别追。")
        if strong:
            txt += f"{'、'.join(strong)}已经进入强观察区，也在等这一项。"
        L.append(txt)
    if pd.notna(e.total_d5) and e.total_d5 > 0:
        L.append("2. 托底能不能持续：最近5个交易日是净买入，接下来看这个方向能不能延续。")
    else:
        L.append("2. 托底能不能恢复：看宽基ETF最近5个交易日能不能重新变成净买入。")
    L.append("3. 企业盈利：看沪深300成分股的整体盈利同比能不能止跌回升（这个数据每月更新一次）。")
    L.append("")

    L.append("■ 名词小抄（看不懂时对照）")
    for line in [
        "200日线：过去约一年（200个交易日）收盘价的平均值，常被当作中期牛熊分界线。"
        "价格在它上面=中期偏强，在下面=偏弱。",
        "市盈率（PE）：股价÷每股盈利，可以粗略理解为“按现在的赚钱速度，多少年能赚回买入价”。越高越贵。",
        "20日线：过去20个交易日（约一个月）收盘价的平均值，代表短期的平均成本。",
        "社融（社会融资规模）：企业从银行、债券等渠道借到的钱。同比转正=企业借钱扩张的意愿在回升。",
        "历史位置/历史排名：把现在的数值和过去几年比。"
        "90%=比过去90%的时间都高，10%=比过去90%的时间都低。",
        "净买入/净卖出：买ETF的金额减去看卖的金额。净买入为正，说明有大资金在往里进。",
        "宽基ETF：跟踪沪深300、中证500这类大盘指数的基金，常被国家队用来托底。",
        "每股盈利（EPS）：公司每一股股票赚的钱。同比=和去年同期比。",
    ]:
        L.append(f"· {line}")
    L.append("")

    hd = {h.name: h for h in pan.health}

    def _src(name: str) -> str:
        # 数据日期一律取实时健康检查结果，避免把“T-5”“停更”这类当天状态写成永久说明
        h = hd.get(name)
        if h is None:
            return "—"
        note = _note_plain(h)
        return f"{_date(h.date)}（{note}）" if note else _date(h.date)

    L.append("数据日期："
             f"指数 {_src('指数行情')}；PE估值 {_src('PE估值')}；"
             f"ETF份额 {_src('ETF份额')}；两融 {_src('两融')}；社融 {_src('社融')}。")
    L.append("参考信息，不是交易指令。")
    return "\n".join(L)


def send_text(text: str, creds: dict[str, str]) -> bool:
    try:
        tok = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": creds["FEISHU_APP_ID"],
                  "app_secret": creds["FEISHU_APP_SECRET"]}, timeout=15).json()
    except Exception as exc:
        print(f"飞书 token 请求失败: {type(exc).__name__} {exc}")
        return False
    token = tok.get("tenant_access_token")
    if not token:
        print(f"飞书 token 失败: code={tok.get('code')} msg={tok.get('msg')}")
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "receive_id": creds["FEISHU_CHAT_ID"],
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        # 超时/重试时内容相同则 uuid 相同，飞书只会落一条，避免刷屏
        "uuid": _uuid(text),
    }
    for attempt in range(3):
        try:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json=payload, headers=headers, timeout=30)
            body = r.json()
            if r.status_code == 200 and body.get("code") == 0:
                print(f"飞书推送成功（{len(text)} 字符）")
                return True
            print(f"飞书推送失败: HTTP {r.status_code} code={body.get('code')} {str(body.get('msg'))[:100]}")
        except Exception as exc:
            print(f"飞书推送异常: {type(exc).__name__} {exc}")
        time.sleep(2 + attempt * 2)
    return False


def already_pushed(panel_date, run_date: date | None = None, fresh: bool = True) -> bool:
    """同一天、同一份面板只推一次。
    必须同时比对“运行日”：若今天的数据源没更新、面板日期还是昨天，
    不能因为昨天推过就静默跳过今天的推送。
    若上次是刷新失败/数据滞后时推的（fresh=False），本次数据已新鲜则允许补发一次。"""
    try:
        d = json.loads(MARK.read_text(encoding="utf-8"))
        run_date = run_date or date.today()
        if (d.get("panel_date") != str(pd.Timestamp(panel_date).date())
                or d.get("run_date") != str(run_date)):
            return False
        return not (fresh and d.get("fresh", True) is False)
    except Exception:
        return False


def mark_pushed(panel_date, run_date: date | None = None, fresh: bool = True) -> None:
    MARK.parent.mkdir(exist_ok=True)
    MARK.write_text(json.dumps(
        {"panel_date": str(pd.Timestamp(panel_date).date()),
         "run_date": str(run_date or date.today()),
         "fresh": bool(fresh),
         "pushed_at": pd.Timestamp.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2), encoding="utf-8")


def push_panel(pan, force: bool = False, dry_run: bool = False, fresh: bool = True) -> str:
    """返回 sent / skipped / dry-run / failed，便于调用方写准确的日志。
    fresh=False 表示本次数据未更新（刷新失败或数据源滞后），用于让之后的新数据补发。"""
    if dry_run:
        print(build_message(pan))
        return "dry-run"
    if not force and already_pushed(pan.date, fresh=fresh):
        print(f"面板 {pan.date.date()} 已推送过，跳过（--force 可强制重发）")
        return "skipped"
    text = build_message(pan)
    if not send_text(text, load_credentials()):
        return "failed"
    mark_pushed(pan.date, fresh=fresh)
    return "sent"


def main() -> int:
    ap = argparse.ArgumentParser(description="推送日频周期面板到飞书")
    ap.add_argument("--dry-run", action="store_true", help="只打印消息，不发送")
    ap.add_argument("--force", action="store_true", help="忽略“今天已推送”标记")
    args = ap.parse_args()

    import panel_data
    pan = panel_data.build_panel()
    status = push_panel(pan, force=args.force, dry_run=args.dry_run)
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
