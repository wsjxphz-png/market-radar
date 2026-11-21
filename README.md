# 📊 A股周期面板 + 美股状态日报

> 两套规则驱动的系统：A 股看周期面板（估值、资金、广度、情绪、趋势，三个底走到哪一步）；美股看多市场状态日报（五个市场 + 七姐妹，风险在积累还是在释放，现在该怎么做）。

---

## 系统构成

### 📈 A股周期面板 `research/cycle_timing/`（当前 A 股每日主推送）
**每个交易日收盘后自动运行，飞书推送。** 跑在 GitHub Actions 上，电脑关机也照跑。
定时触发是北京时间 19:00，但 GitHub 的定时任务实际常晚 5~10 小时；系统按“最近一个已收盘交易日”出面板，收盘 16 小时内送达都算正常，超过窗口或休市日不补发。

```
🧭 一句话        — 现在是什么阶段（磨底/便宜区/趋势正常/顶部风险）
📊 指数状态      — 四个主要指数逐个解释“这意味着什么”
⚠️ 顶部风险      — 五项风险条件亮了几项，涨上去以后每天看这个
🕳️ 三个底        — 政策底 → 市场底 → 盈利底 的先后状态机
💰 大资金动向    — 宽基 ETF 份额变化（国家队托底代理）
🩺 五维体检      — 估值 / 流动性 / 广度 / 情绪 / 趋势
📖 名词小抄      — 200日线、PE、社融等白话解释
```

### 🏠 全景仪表盘 `market_dashboard.py`（定时已停用，保留手动）
**原每个交易日推送的交易手册式看板；2026-11-12 起由周期面板接管每日 A 股推送。**

```
🔥 市场温度   — 涨跌家数 · 涨停生态 · 成交额放缩
💰 资金地图   — 北向资金 · 主力净流入 TOP5 板块
📖 交易手册   — 五大模块速查(仓位/选股/入场/止损/止盈) + 量价八诀表
⚠️ 信号翻转   — 与昨日对比，标注状态变化
📊 今日信号   — 手册规则 × 当前数据，每个指标标注所属模块
⚔️ 冲突裁决   — 指标打架时按优先级裁决(三周期 > 量价 > 520 > MACD > RSI)
🔍 板块操作   — 具体买/卖/等 + 条件 + 止损 + 理论依据
👀 板块观察池 — 差一个条件就满足入场的板块
📋 板块全貌   — 23 个行业逐项诊断
🎯 综合策略   — 仓位建议 + 关键观察点
🤖 决策审计   — AI 五大模块逐项评分 /10，不发表观点只做合规检查
💰 模拟账户   — 100 万模拟资金，每日持仓追踪 + 累计收益率
📖 每日一得   — 按市场状态轮换的投资格言摘录
```

### 🇺🇸 美股多市场状态日报 `research/us_market_state/`
**美股每个交易日收盘后推送一张飞书卡片，默认干跑（标题带【干跑】）。**
覆盖五个市场（SPY/QQQ/SOXQ/KWEB/FXI）+ 七姐妹逐只 + 广度/信用/波动 20 天变化 + 今天怎么做 + 关键价位。
详见 `research/us_market_state/README.md`。

**旧的美股 AI 日报（`main.py`）已删除**，不再有任何定时任务运行它；旧卡片不再产生。

### 📊 月度复盘 `backtest_review.py`
**每月 1 号自动运行，飞书推送。** 不统计预测准确率——只检查五大模块纪律执行情况，违例标 ❌。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 指数数据 | akshare → yfinance 双源自动降级 |
| 板块数据 | akshare (23 个申万行业) |
| 个股数据 | adata → akshare 双源 |
| AI 分析 | Agnes AI（环境变量兼容 DeepSeek 旧key） |
| M1 宏观 | akshare / FRED |
| 消息推送 | 飞书 Bot API + 交互卡片 |
| 定时运行 | GitHub Actions (UTC 11:00 = 北京时间 19:00) |
| 数据持久化 | JSON (portfolio / trade_log / position_state) |

---

## GitHub Actions

| Workflow | 触发时间 | 内容 |
|----------|---------|------|
| `cycle-panel.yml` | 工作日 19:00（19:10 重试） | A 股周期面板 → 飞书；只推最近一个已收盘交易日 |
| `daily-report.yml` | 北京周二~周五 07:30（08:00 重试） | 美股状态日报 → 内部群 + 外部群；默认【干跑】 |
| `us-market-state.yml` | 周六 09:00 | 美股状态周报 → 内部群 + 外部群；默认【干跑】 |
| `monthly-review.yml` | 每月 1 号 09:00 | 月度纪律复盘 → 飞书 |
| `rally-health.yml` | 仅手动触发 | 旧 A 股全景仪表盘（定时已停用） |

> ⚠️ GitHub 定时任务常延迟几分钟到几小时；美股系统按“数据日期”判断，卡片标题写的是数据日，不是送达时刻。周五美股由周六的周报覆盖。

---

## 本地使用

```bash
pip install -r requirements.txt
pip install -r research/us_market_state/requirements.txt

# 周期面板（A 股每日推送）
cd research/cycle_timing
python run_daily.py --no-push                 # 生成面板不推送
python run_daily.py --force                   # 忽略交易日/时间限制，调试用
python run_daily.py --skip-refresh --no-push  # 不联网，用缓存

# 美股状态日报 / 周报（默认干跑）
cd research/us_market_state
python smoke_test.py                          # 先确认数据源
python run_daily.py --dry-run                 # 只打印
python run_daily.py --shadow                  # 推送带【干跑】前缀
python run_weekly.py --dry-run                # 周报全文只打印

# 旧 A 股仪表盘（手动）
python market_dashboard.py --dry-run
python market_dashboard.py --force --dry-run

# 月度复盘
python backtest_review.py --days 30 --dry-run
```

---

## GitHub Secrets

| Secret | 用途 |
|--------|------|
| `AI_API_KEY` | AI 分析 + 决策审计（兼容旧名 `DEEPSEEK_API_KEY`） |
| `FEISHU_APP_ID` | 飞书 Bot 推送（内部群） |
| `FEISHU_APP_SECRET` | 飞书 Bot 推送（内部群） |
| `FEISHU_CHAT_ID` | 飞书内部群聊 ID |
| `FEISHU_WEBHOOK_URL` | 飞书外部群自定义机器人（两个群都推需要） |
| `FRED_API_KEY` | 美国宏观数据 (可选) |
| `TAVILY_API_KEY` | 实时搜索 (可选) |

---

## 设计原则

- **规则驱动, 不是情绪驱动**。每次买卖前过一遍规则
- **状态判断，不是顶底预测**。只回答“现在处于什么阶段、风险在积累还是释放”，不猜哪天涨跌
- **每条判断都带依据和动作**。先说“是什么”，再说“这意味着什么”，最后说“现在怎么做”
- **美股双群推送**。内部群走 Bot，外部群走 Webhook；卡片标题/颜色区分阶段
- **复盘不看准确率, 看纪律执行**。违例标 ❌，无违例标 ✅

---

## 文件结构

```
market-radar/
├── market_dashboard.py      # A股全景仪表盘（手动）
├── sector_monitor.py        # A股板块技术分析
├── stock_data.py            # A股个股数据层
├── portfolio.py             # A股模拟账户
├── backtest_review.py       # 月度复盘
├── research/cycle_timing/   # A股周期面板
├── research/us_market_state/ # 美股多市场状态日报/周报
│   ├── run_daily.py         # 美股日报（短卡）
│   ├── run_weekly.py        # 美股周报（全文）
│   ├── state.py             # 状态机/风险趋势/动作
│   ├── render.py            # 飞书卡片 markdown
│   ├── push.py              # 内部群 Bot + 外部群 Webhook
│   ├── smoke_test.py        # 数据源冒烟
│   └── test_state.py        # 20 个测试
├── sources.json             # LTC 等信息源（sources.json 仍被 ltc_news 使用）
├── requirements.txt
├── .github/workflows/
│   ├── cycle-panel.yml      # A股周期面板
│   ├── daily-report.yml     # 美股状态日报（默认干跑）
│   ├── us-market-state.yml  # 美股状态周报（默认干跑）
│   ├── monthly-review.yml   # 月度复盘
│   └── rally-health.yml     # 旧 A 股仪表盘（仅手动）
└── docs/
```

---

*每日自动 | 仅供参考, 不构成投资建议*
