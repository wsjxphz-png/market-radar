#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日频面板的渲染层：自包含 HTML + 纯文本摘要。"""
from __future__ import annotations

import html
from typing import Any

import pandas as pd

from panel_data import Panel, state_key

TONE_LABEL = {
    "risk": "偏风险", "ok": "偏健康", "watch": "待确认", "bottom": "底部区",
    "neutral": "中性", "down": "偏弱", "cheap": "偏低",
    "support": "托底", "pause": "暂停", "distribution": "派发",
    "outflow": "流出", "inflow": "流入",
}


def esc(s: Any) -> str:
    return html.escape(str(s))


def yi(v: float, digits: int = 0) -> str:
    return f"{v:+,.{digits}f} 亿"


def pct(v: float, digits: int = 1) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.{digits}f}%"


def score(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.2f}"


def prob(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.0%}"


def num0(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.0f}"


def barw(v: float) -> float:
    # Python 的 min(1, nan) 会返回 1，NaN 分数会画成满格；必须先判空
    return 0.0 if pd.isna(v) else max(0.0, min(1.0, float(v))) * 100


def badge(tone: str, text: str) -> str:
    return f'<span class="badge t-{esc(tone)}">{esc(text)}</span>'


def cls_sign(v: float) -> str:
    return "up" if v > 0 else ("down" if v < 0 else "")


# ──────────────────────────────────────────────────────────────
# HTML 片段
# ──────────────────────────────────────────────────────────────

def _index_card(i) -> str:
    chg = f'<span class="chg {cls_sign(i.chg1)}">{pct(i.chg1, 2)}</span>'
    top_flags = "、".join(k for k, v in i.top_flags.items() if v) or "无"
    bot_flags = "、".join(k for k, v in i.bottom_flags.items() if v) or "无"
    raw_note = ""
    if abs(i.top_adj - i.top_raw) > 1e-9 or abs(i.bottom_adj - i.bottom_raw) > 1e-9:
        raw_note = f'<span class="muted">（原始 top {score(i.top_raw)} / bottom {score(i.bottom_raw)}；已剔除停更维度）</span>'

    zones = []
    if i.last_top:
        z = i.last_top
        zones.append(
            f'上次 top≥0.6：{z["start"]} → {z["end"]}（{z["days"]} 日，'
            f'起点后最大回撤 {pct(z["max_dd"])}{"，进行中" if z["is_current"] else ""}）')
    if i.last_bottom:
        z = i.last_bottom
        zones.append(
            f'最近 bottom≥0.8：{z["start"]} → {z["end"]}（{z["days"]} 日，'
            f'起点后最大回撤 {pct(z["max_dd"])}{"，进行中" if z["is_current"] else ""}）')
    if i.top_zone.get("n"):
        t = i.top_zone
        nfull = t.get("n_full", t["n"])
        ntxt = f'（完整120日窗口 {nfull} 次）' if nfull < t["n"] else ""
        zones.append(f'历史 top 区间 {t["n"]} 次{ntxt}：区间起点至结束后120日，'
                     f'最大回撤中位 {pct(t["fwd_dd_med"])}，最大反弹中位 {pct(t["fwd_up_med"])}，'
                     f'见底中位 {num0(t["days_low_med"])} 日（自区间起点）')
    if i.bottom_zone.get("n"):
        b = i.bottom_zone
        nfull = b.get("n_full", b["n"])
        ntxt = f'（完整120日窗口 {nfull} 次）' if nfull < b["n"] else ""
        zones.append(f'历史 bottom 区间 {b["n"]} 次{ntxt}：区间结束后120日再跌中位 {pct(b["post_dd_med"])}，'
                     f'跌超10%概率 {prob(b["post_dd_10"])}；区间起点后最大反弹中位 {pct(b["fwd_up_med"])}')

    return f"""
    <div class="card index-card">
      <div class="card-head">
        <span class="idx-name">{esc(i.name)}</span>
        {badge(i.state_tone, i.state)}
      </div>
      <div class="big">{i.close:,.2f} {chg}</div>
      <div class="kv">MA200 {i.ma200:,.2f}（{pct(i.close/i.ma200-1)}） · 距52周高点 {pct(i.drawdown_252)} · 60日 {pct(i.ret_60)}</div>
      <div class="score-row">
        <div class="score-box"><div class="score-label">顶部分</div><div class="score-val">{score(i.top_adj)}</div>
          <div class="bar"><i class="fill risk" style="width:{barw(i.top_adj):.0f}%"></i></div></div>
        <div class="score-box"><div class="score-label">底部分</div><div class="score-val">{score(i.bottom_adj)}</div>
          <div class="bar"><i class="fill bottom" style="width:{barw(i.bottom_adj):.0f}%"></i></div></div>
      </div>
      <div class="small">顶部触发：{esc(top_flags)} · 底部触发：{esc(bot_flags)} {raw_note}</div>
      <div class="action"><b>动作：</b>{esc(i.action)}</div>
      <div class="small"><b>接回条件：</b>{esc(i.reentry_need)}{'（已满足）' if i.reentry_ready else ''}</div>
      <div class="zone">{'<br>'.join(esc(z) for z in zones) if zones else '历史区间样本不足'}</div>
    </div>"""


def _dim_card(d) -> str:
    rows = "".join(
        f"<tr><td class='nowrap'>{esc(it.label)}</td><td class='nowrap num'>{esc(it.value)}</td>"
        f"<td class='nowrap'>{esc(it.change)}</td><td>{esc(it.meaning)}</td></tr>"
        for it in d.items)
    return f"""
    <div class="dim-card">
      <div class="card-head"><h3>{esc(d.name)}</h3>{badge(d.tone, TONE_LABEL.get(d.tone, d.tone))}</div>
      <p class="summary">{esc(d.summary)}</p>
      <div class="table-wrap"><table class="dim-table">
        <thead><tr><th>指标</th><th>当前</th><th>变化</th><th>这意味着什么</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>"""


def _bottom_card(b) -> str:
    rows = "".join(f"<tr><td class='nowrap muted'>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in b.detail)
    return f"""
    <div class="bottom-card">
      <div class="card-head"><h3>{esc(b.name)}</h3>{badge(b.tone, b.status)}</div>
      <p class="evidence">{esc(b.evidence)}</p>
      <div class="table-wrap"><table class="mini-table"><tbody>{rows}</tbody></table></div>
      <p class="meaning"><b>意味着什么：</b>{esc(b.meaning)}</p>
    </div>"""


def _flow_chart(recent: pd.DataFrame) -> str:
    if recent.empty:
        return ""
    w, h, pad = 720, 150, 24
    vals = recent["total"].astype(float).tolist()
    dates = [d.strftime("%m-%d") for d in recent["date"]]
    vmax = max(abs(v) for v in vals) or 1.0
    n = len(vals)
    slot = (w - 2 * pad) / n
    bw = slot * 0.62
    zero = h / 2
    bars = []
    for k, v in enumerate(vals):
        x = pad + k * slot + (slot - bw) / 2
        bh = abs(v) / vmax * (h / 2 - 18)
        y = zero - bh if v >= 0 else zero
        color = "#c62828" if v >= 0 else "#1b7f3b"
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}" rx="2"/>')
        bars.append(f'<text x="{x+bw/2:.1f}" y="{h-4}" font-size="10" text-anchor="middle" fill="#777">{dates[k]}</text>')
        bars.append(f'<text x="{x+bw/2:.1f}" y="{y-4 if v>=0 else y+bh+11:.1f}" font-size="9" text-anchor="middle" fill="{color}">{v:+.0f}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" class="flow-chart" role="img" aria-label="最近10日宽基ETF净申赎">'
            f'<line x1="{pad}" y1="{zero}" x2="{w-pad}" y2="{zero}" stroke="#ccc" stroke-width="1"/>'
            + "".join(bars) + "</svg>")


def _entity_table() -> str:
    rows = [
        ("中央汇金 / 汇金资管", "基金半年报、年报前十大持有人", "间接日频",
         "宽基 ETF 每日份额；本面板用份额变化看大资金净申购"),
        ("证金公司", "上市公司季报前十大股东", "低",
         "无 ETF 日频持仓；只能季度看，日频不可观测"),
        ("中证金融", "转融通/两融流动性提供者", "不适用",
         "不是直接 ETF 买家；其行为体现在两融数据，不在 ETF 份额"),
        ("国新投资 / 诚通金控", "央企/主题 ETF 及直接持股，季报", "低",
         "相关 ETF 份额可侧面看，无法逐日归因"),
        ("社保 / 养老金", "季报/半年报，长线资金", "不适用",
         "按用户定义不纳入“应急托底”判断"),
    ]
    body = "".join(
        f"<tr><td class='nowrap'>{esc(a)}</td><td>{esc(b)}</td><td class='nowrap'>{esc(c)}</td><td>{esc(d)}</td></tr>"
        for a, b, c, d in rows)
    return f"""
    <div class="table-wrap"><table class="dim-table">
      <thead><tr><th>主体</th><th>公开披露方式</th><th>日频可观测性</th><th>本面板如何处理</th></tr></thead>
      <tbody>{body}</tbody>
    </table></div>"""


def _health_rows(pan: Panel) -> str:
    out = []
    for h in pan.health:
        d = h.date.date() if h.date is not None else "—"
        out.append(f"<tr><td class='nowrap'>{esc(h.name)}</td><td class='nowrap'>{esc(d)}</td>"
                   f"<td class='nowrap'>{esc(h.note)}</td>"
                   f"<td>{badge('ok' if h.status=='ok' else ('watch' if h.status=='lag' else 'risk'), h.status)}</td></tr>")
    return "".join(out)


# ──────────────────────────────────────────────────────────────
# HTML 总装
# ──────────────────────────────────────────────────────────────

def render_html(pan: Panel) -> str:
    alerts = "".join(f"<li>{esc(a)}</li>" for a in pan.alerts)
    index_cards = "".join(_index_card(i) for i in pan.indices)
    dim_cards = "".join(_dim_card(d) for d in pan.dims)
    bottom_cards = "".join(_bottom_card(b) for b in
                           (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings))

    e = pan.etf
    basket_rows = "".join(
        f"<tr><td class='nowrap'>{esc(b.name)}</td>"
        f"<td class='num {cls_sign(b.today)}'>{b.today:+,.0f}</td>"
        f"<td class='num {cls_sign(b.d5)}'>{b.d5:+,.0f}</td>"
        f"<td class='num {cls_sign(b.d20)}'>{b.d20:+,.0f}</td>"
        f"<td class='num'>{prob(b.d20_pct)}</td>"
        f"<td class='num {cls_sign(b.price5)}'>{pct(b.price5)}</td>"
        f"<td class='num {cls_sign(b.price20)}'>{pct(b.price20)}</td>"
        f"<td>{badge(b.tone, b.read)}</td></tr>"
        for b in e.baskets)

    top_in = "、".join(f"{esc(n)} {yi(v)}" for _, n, v in e.top_in) or "无"
    top_out = "、".join(f"{esc(n)} {yi(v)}" for _, n, v in e.top_out) or "无"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>趋势周期面板 {pan.date.date()}</title>
<style>
  :root {{
    --bg:#f6f5f1; --card:#fff; --ink:#1d1d1b; --muted:#6f6b63; --line:#e4e0d8;
    --up:#c62828; --down:#1b7f3b; --risk:#b3261e; --ok:#1b6e3a;
    --watch:#a15c00; --bottom:#1f5fa8; --neutral:#5f5b53;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 60px; }}
  header {{ border-bottom:2px solid var(--ink); padding-bottom:16px; margin-bottom:22px; }}
  h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:.5px; }}
  h2 {{ font-size:19px; margin:34px 0 12px; padding-left:10px; border-left:4px solid var(--ink); }}
  h3 {{ font-size:16px; margin:0; }}
  .meta {{ color:var(--muted); font-size:13px; }}
  .headline {{ font-size:18px; font-weight:600; margin:12px 0 6px; }}
  .alertbox {{ background:#fff8e6; border:1px solid #e8cf9a; border-radius:8px; padding:10px 14px; margin-top:10px; }}
  .alertbox ul {{ margin:4px 0 0 18px; padding:0; }}
  .grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }}
  .index-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .card, .dim-card, .bottom-card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .card-head {{ display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap; }}
  .idx-name {{ font-weight:700; font-size:17px; }}
  .big {{ font-size:30px; font-weight:700; margin:8px 0 2px; font-variant-numeric:tabular-nums; }}
  .chg {{ font-size:15px; margin-left:6px; }}
  .kv, .small, .zone {{ color:var(--muted); font-size:12.5px; margin-top:4px; }}
  .zone {{ border-top:1px dashed var(--line); padding-top:6px; margin-top:8px; }}
  .score-row {{ display:flex; gap:14px; margin:10px 0 6px; }}
  .score-box {{ flex:1; }}
  .score-label {{ font-size:12px; color:var(--muted); }}
  .score-val {{ font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .bar {{ height:6px; background:#eee9df; border-radius:3px; overflow:hidden; margin-top:2px; }}
  .bar .fill {{ display:block; height:100%; }}
  .bar .fill.risk {{ background:var(--risk); }}
  .bar .fill.bottom {{ background:var(--bottom); }}
  .action {{ margin-top:8px; font-size:13.5px; background:#f4f1ea; border-radius:6px; padding:7px 9px; }}
  .badge {{ display:inline-block; font-size:12px; font-weight:600; padding:2px 8px; border-radius:999px; white-space:nowrap; }}
  .t-risk {{ background:#fbe4e2; color:var(--risk); }}
  .t-ok {{ background:#e3f2e7; color:var(--ok); }}
  .t-watch {{ background:#fff0d6; color:var(--watch); }}
  .t-bottom {{ background:#e3edf9; color:var(--bottom); }}
  .t-neutral, .t-down {{ background:#eeeae2; color:var(--neutral); }}
  .t-support {{ background:#e3edf9; color:var(--bottom); }}
  .t-pause {{ background:#fff0d6; color:var(--watch); }}
  .t-distribution {{ background:#fbe4e2; color:var(--risk); }}
  .t-inflow {{ background:#fbe9e7; color:var(--up); }}
  .t-outflow {{ background:#e3f2e7; color:var(--down); }}
  .t-cheap {{ background:#e3f2e7; color:var(--ok); }}
  .dim-card {{ margin-bottom:14px; }}
  .summary {{ margin:8px 0 10px; font-size:14px; }}
  .evidence {{ margin:8px 0; font-size:13.5px; }}
  .meaning {{ margin:10px 0 0; color:var(--muted); font-size:13px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; background:#faf8f3; }}
  .mini-table td {{ border-bottom:1px dotted var(--line); }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .nowrap {{ white-space:nowrap; }}
  .muted {{ color:var(--muted); }}
  .up {{ color:var(--up); }}
  .down {{ color:var(--down); }}
  .flow-summary {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); margin-bottom:10px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .stat .label {{ color:var(--muted); font-size:12px; }}
  .stat .value {{ font-size:24px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .flow-chart {{ width:100%; height:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; margin:6px 0 12px; }}
  .sequence {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:12px 0; font-size:14px; }}
  .phase {{ font-weight:600; margin:8px 0 0; }}
  .caveat {{ background:#f4f1ea; border-left:3px solid var(--neutral); padding:10px 12px; font-size:12.5px; color:#4a463f; }}
  .method {{ font-size:13px; color:#4a463f; }}
  .method li {{ margin:4px 0; }}
  footer {{ margin-top:36px; padding-top:12px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; }}
  @media (max-width:820px) {{
    .index-grid {{ grid-template-columns:1fr; }}
  }}
  @media (max-width:640px) {{
    .wrap {{ padding:18px 12px 40px; }}
    .big {{ font-size:26px; }}
    h1 {{ font-size:22px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>趋势周期面板</h1>
  <div class="meta">数据日 {pan.date.date()} · 生成 {pan.generated.strftime('%Y-%m-%d %H:%M')} · 上一基线 {esc(pan.prev_date or '—')}</div>
  <div class="headline">{esc(pan.headline)}</div>
  <div class="alertbox"><b>本次提醒</b><ul>{alerts}</ul></div>
</header>

<h2>指数状态</h2>
<div class="grid index-grid">{index_cards}</div>

<h2>五维读数（以沪深300为主）</h2>
{dim_cards}

<h2>三个底</h2>
<div class="grid">{bottom_cards}</div>
<div class="sequence"><b>典型顺序：</b>{esc(pan.bottoms.sequence)}</div>
<div class="phase">当前阶段：{esc(pan.bottoms.phase)}</div>

<h2>国家队 / 大资金日频观察（宽基 ETF 份额代理）</h2>
<div class="flow-summary">
  <div class="stat"><div class="label">最新交易日（{e.date.date()}）净申赎</div><div class="value {cls_sign(e.total_today)}">{yi(e.total_today)}</div></div>
  <div class="stat"><div class="label">近5日</div><div class="value {cls_sign(e.total_d5)}">{yi(e.total_d5)}</div>
    <div class="meta">历史分位 {prob(e.total_d5_pct)}</div></div>
  <div class="stat"><div class="label">近20日</div><div class="value {cls_sign(e.total_d20)}">{yi(e.total_d20)}</div>
    <div class="meta">历史分位 {prob(e.total_d20_pct)}</div></div>
</div>
{_flow_chart(e.recent)}
<div class="table-wrap"><table class="dim-table">
  <thead><tr><th>篮子</th><th class="num">最新日</th><th class="num">5日</th><th class="num">20日</th>
  <th class="num">20日分位</th><th class="num">5日价格</th><th class="num">20日价格</th><th>读数</th></tr></thead>
  <tbody>{basket_rows}</tbody>
</table></div>
<p class="small">单位：亿元。正=净申购（份额增加），负=净赎回。ETF 份额为 T+1 披露，数据截止 {e.date.date()}。</p>
<p class="small"><b>最新日申购前列：</b>{top_in}<br><b>最新日赎回前列：</b>{top_out}</p>
<p class="small">样本：{e.history_days} 个交易日，{len(e.baskets)} 个宽基篮子；分位为样本内最近 250 个交易日。金额=份额变化×当日收盘价，为近似值。</p>

<h3 style="margin-top:14px">主体披露现实</h3>
{_entity_table()}
<p class="caveat"><b>归因边界：</b>ETF 份额变化是“所有大资金”的净结果，包含国家队、保险、外资、量化与一级市场套利，公开数据无法逐日归因到单一主体。份额增加只能来自一级市场申购（一篮子股票或现金换份额），二级市场买入不改变份额。因此“看到国家队当天买入”在公开数据里做不到；本面板能做到的是“看到大资金当天净申购了哪只宽基 ETF”，并用“下跌中逆势大增”提高托底概率。ETF 份额为 T+1，收盘后发布。另注意：ETF 拆分/合并会机械改变份额数量（如 1 拆 2 份额翻倍但净值减半），跨境 ETF 还受 QDII 额度影响；本面板已按“份额×价格”折算金额，但单日异常跳变仍应先核对公告再解读。</p>

<h2>数据健康</h2>
<div class="table-wrap"><table class="dim-table">
  <thead><tr><th>数据源</th><th>最新日期</th><th>说明</th><th>状态</th></tr></thead>
  <tbody>{_health_rows(pan)}</tbody>
</table></div>

<h2>规则与口径</h2>
<div class="method">
<ul>
  <li><b>分数：</b>每个维度触发记 1，未触发记 0；至少 3 个维度可用时取均值。顶部/底部分数相互独立，不是对称的买卖信号。</li>
  <li><b>状态：</b>top≥0.6 且站上200日线 → 顶部风险区；bottom≥0.8 → 底部观察（强）；bottom≥0.6 且跌破200日线 → 底部观察（弱）；站上200日线 → 趋势正常；否则下跌趋势。</li>
  <li><b>动作：</b>top≥0.6 → 退出/防守；跌破200日线 → 防守；站上且 top&lt;0.6 → 持有。接回条件：站上200日线连续5日且 top&lt;0.4。</li>
  <li><b>停更维度：</b>数据源停更或过期时该维度不参与分数，面板同时显示原始分与剔除后分数；披露滞后的数据源（两融、ETF 份额等）在读数中标注实际截止日。</li>
  <li><b>区间统计：</b>top≥0.6 / bottom≥0.8 的触发区间，最短 5 个交易日；间隔不超过 20 个交易日的两段合并为同一轮。统计只用结束后仍有完整 120 个交易日的区间（否则样本偏乐观），表内同时给出总次数与完整窗口次数。历史分数与当日同口径：信用维度按当时数据是否过期逐日纳入或剔除。</li>
  <li><b>盈利底：</b>用沪深300收盘/PE_TTM 反推隐含 EPS，月度数据，受指数成分调整影响，是盈利周期的粗略代理，不是官方盈利。</li>
  <li><b>适用边界：</b>本面板判断市场大周期（宽基指数），不替代具体板块 ETF 的个体趋势；板块自身跌破200日线时，即使宽基状态正常也应防守。</li>
  <li><b>用途：</b>参考信息，不是自动交易指令；所有判断都可能错，仓位与执行由使用者自己决定。</li>
</ul>
</div>

<footer>数据来源：交易所 ETF 份额（SSE/SZSE）、Sina 指数/ETF 行情、Legulegu PE、PBOC/交易所宏观与两融、QVIX。仅作研究记录。</footer>
</div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# 纯文本
# ──────────────────────────────────────────────────────────────

def render_text(pan: Panel) -> str:
    L: list[str] = []
    L.append(f"趋势周期面板  {pan.date.date()}")
    L.append(f"结论：{pan.headline}")
    L.append("提醒：" + "；".join(pan.alerts))
    L.append("")

    L.append("【指数】")
    for i in pan.indices:
        L.append(f"{i.name}  {i.close:,.2f}  {pct(i.chg1,2)}  [{i.state}]")
        raw_note = ("；已剔除停更维度"
                    if abs(i.top_adj - i.top_raw) > 1e-9 or abs(i.bottom_adj - i.bottom_raw) > 1e-9 else "")
        L.append(f"  top {score(i.top_adj)}（原始 {score(i.top_raw)}）  "
                 f"bottom {score(i.bottom_adj)}（原始 {score(i.bottom_raw)}）{raw_note}  "
                 f"MA200 {i.ma200:,.2f}（{pct(i.close/i.ma200-1)}）  回撤 {pct(i.drawdown_252)}")
        L.append(f"  顶部触发：{'、'.join(k for k,v in i.top_flags.items() if v) or '无'}  |  "
                 f"底部触发：{'、'.join(k for k,v in i.bottom_flags.items() if v) or '无'}")
        L.append(f"  动作：{i.action}")
        L.append(f"  接回：{i.reentry_need}{'（已满足）' if i.reentry_ready else ''}")
        if i.last_top:
            z = i.last_top
            L.append(f"  上次 top≥0.6：{z['start']}→{z['end']}（{z['days']}日，"
                     f"起点后最大回撤 {pct(z['max_dd'])}）")
        if i.last_bottom:
            z = i.last_bottom
            L.append(f"  最近 bottom≥0.8：{z['start']}→{z['end']}（{z['days']}日，"
                     f"起点后最大回撤 {pct(z['max_dd'])}）")
        if i.top_zone.get("n"):
            t = i.top_zone
            nfull = t.get("n_full", t["n"])
            ntxt = f"，完整窗口 {nfull} 次" if nfull < t["n"] else ""
            L.append(f"  历史 top 区间 {t['n']} 次{ntxt}（区间起点至结束后120日）："
                     f"最大回撤中位 {pct(t['fwd_dd_med'])}，最大反弹中位 {pct(t['fwd_up_med'])}")
        if i.bottom_zone.get("n"):
            b = i.bottom_zone
            nfull = b.get("n_full", b["n"])
            ntxt = f"，完整窗口 {nfull} 次" if nfull < b["n"] else ""
            L.append(f"  历史 bottom 区间 {b['n']} 次{ntxt}：区间结束后120日再跌中位 {pct(b['post_dd_med'])}，"
                     f"跌超10%概率 {prob(b['post_dd_10'])}；区间起点后最大反弹中位 {pct(b['fwd_up_med'])}")
    L.append("")

    L.append("【五维】")
    for d in pan.dims:
        L.append(f"{d.name} [{TONE_LABEL.get(d.tone, d.tone)}] {d.summary}")
        for it in d.items:
            L.append(f"  - {it.label}: {it.value} | {it.change} | {it.meaning}")
    L.append("")

    L.append("【三个底】")
    for b in (pan.bottoms.policy, pan.bottoms.market, pan.bottoms.earnings):
        L.append(f"{b.name} [{b.status}]")
        L.append(f"  证据：{b.evidence}")
        L.append(f"  意义：{b.meaning}")
        for k, v in b.detail:
            L.append(f"    {k}: {v}")
    L.append(f"  顺序：{pan.bottoms.sequence}")
    L.append(f"  阶段：{pan.bottoms.phase}")
    L.append("")

    e = pan.etf
    L.append(f"【国家队/大资金 ETF 份额代理】数据截止 {e.date.date()}（T+1）")
    L.append(f"最新日 {yi(e.total_today)} | 5日 {yi(e.total_d5)}（分位 {prob(e.total_d5_pct)}）"
             f" | 20日 {yi(e.total_d20)}（分位 {prob(e.total_d20_pct)}）")
    for b in e.baskets:
        L.append(f"  {b.name}: 最新日 {b.today:+,.0f} | 5日 {b.d5:+,.0f} | 20日 {b.d20:+,.0f}"
                 f"（分位 {prob(b.d20_pct)}）| 5日价格 {pct(b.price5)} | 20日价格 {pct(b.price20)} | {b.read}")
    L.append(f"  最新日申购前列：{'、'.join(f'{n} {yi(v)}' for _,n,v in e.top_in) or '无'}")
    L.append(f"  最新日赎回前列：{'、'.join(f'{n} {yi(v)}' for _,n,v in e.top_out) or '无'}")
    L.append("  归因边界：份额变化是所有大资金的净结果，无法逐日归因到汇金/证金等单一主体；"
             "份额增加只能来自一级市场申购，T+1 披露。ETF 拆分/合并会机械改变份额，异常跳变先查公告。")
    L.append("")

    L.append("【数据健康】")
    for h in pan.health:
        d = h.date.date() if h.date is not None else "—"
        L.append(f"  {h.name}: {d} | {h.note} [{h.status}]")
    L.append("")
    L.append("参考信息，不是交易指令。")
    return "\n".join(L)
