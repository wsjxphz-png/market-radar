# 📊 A股周期面板 + 市场机会发现系统

> 一套规则驱动的 A 股投资决策工具。每天收盘后推送一张看得懂的周期面板：估值、资金、广度、情绪、趋势五个角度，三个底走到哪一步，顶部风险亮了几项。

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

### 🔍 市场机会发现 `main.py`
**每天收盘后自动运行，飞书推送。** RSS + FRED + AKShare + Polymarket + Tavily 多源抓取 → AI 8 层分析 → 飞书。
定时触发是 19:00，实际常在次日凌晨送达；日报按“最近一个已收盘交易日”生成，休市日不推、已推送不重复。

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
| 定时运行 | GitHub Actions (UTC 08:00) |
| 数据持久化 | JSON (portfolio / trade_log / position_state) |

---

## GitHub Actions

| Workflow | 触发时间 | 内容 |
|----------|---------|------|
| `cycle-panel.yml` | 工作日 19:00 触发（19:10 重试） | 周期面板 → 飞书；只推最近一个已收盘交易日，超过 16 小时不补发 |
| `daily-report.yml` | 每天 19:00 触发（19:10 重试） | RSS 市场机会发现 → 飞书；同样按最近一个已收盘交易日送达，已推送则跳过 |
| `monthly-review.yml` | 每月 1 号 09:00 | 月度纪律复盘 → 飞书 |
| `rally-health.yml` | 仅手动触发 | 旧全景仪表盘（定时已停用，由周期面板取代） |

> ⚠️ GitHub 对该仓库的定时任务实测会延迟 5~10 小时（19:00 的班次常在北京时间次日凌晨才启动）。两个日报都按“数据日期”而不是“到达时刻”判断，所以凌晨送达的仍是上一交易日收盘后的内容；休市日及超过 16 小时的旧数据不补发。

---

## 本地使用

```bash
pip install -r requirements.txt

# 周期面板（当前每日推送）
cd research/cycle_timing
python run_daily.py --no-push                 # 生成面板不推送
python run_daily.py --force                   # 忽略交易日/时间限制，调试用
python run_daily.py --skip-refresh --no-push  # 不联网，用缓存

# 旧仪表盘（手动）
python market_dashboard.py --dry-run          # 仅打印
python market_dashboard.py --force --dry-run  # 跳过日期检查

# 市场机会发现
python main.py --dry-run

# 月度复盘
python backtest_review.py --days 30 --dry-run

# 个股数据测试
python stock_data.py
```

---

## GitHub Secrets

| Secret | 用途 |
|--------|------|
| `AI_API_KEY` | AI 分析 + 决策审计（兼容旧名 `DEEPSEEK_API_KEY`） |
| `FEISHU_APP_ID` | 飞书 Bot 推送 |
| `FEISHU_APP_SECRET` | 飞书 Bot 推送 |
| `FEISHU_CHAT_ID` | 飞书群聊 ID (可选) |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook (可选) |
| `FRED_API_KEY` | 美国宏观数据 (可选) |
| `TAVILY_API_KEY` | 实时搜索 (可选) |

---

## 设计原则

- **规则驱动, 不是情绪驱动**。每次买卖前过一遍五大模块规则
- **手册和代码一致**。入场三条件(金叉+放量+不追高) 由代码硬校验，不满足就降级
- **每条建议附带依据**。不只说"买什么"，还说"为什么"
- **复盘不看准确率, 看纪律执行**。违例标 ❌，无违例标 ✅
- **系统帮你排除错误, 不帮你做决策**。告诉你什么时候不该买、哪些不该碰、什么仓位太危险

---

## 文件结构

```
market-radar/
├── market_dashboard.py      # 全景仪表盘主控
├── sector_monitor.py        # 23 板块技术分析
├── stock_data.py            # 个股数据层 (adata/akshare)
├── portfolio.py             # 100万模拟账户
├── backtest_review.py       # 月度复盘 (纪律检查)
├── main.py                  # RSS 市场机会发现
├── research/cycle_timing/   # A股周期面板（当前每日主推送）
│   ├── run_daily.py         # 交易日/收盘时间闸门 + 调度入口
│   ├── panel_data.py        # 五维/三个底/顶部风险计算
│   ├── push_feishu.py       # 白话推送
│   └── test_panel.py        # 规则测试
├── sources.json             # 信息源配置
├── requirements.txt
├── .github/workflows/
│   ├── cycle-panel.yml      # 周期面板定时（工作日 19:00）
│   ├── rally-health.yml     # 旧仪表盘（仅手动）
│   ├── daily-report.yml     # 市场发现定时
│   └── monthly-review.yml   # 月度复盘
└── docs/
    └── superpowers/specs/   # 设计文档
```

---

*每日自动 | 仅供参考, 不构成投资建议*
