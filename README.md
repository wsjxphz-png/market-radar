# 📊 A股全景仪表盘 + 市场机会发现系统

> 一套规则驱动的 A 股投资决策工具。不是炒股机器人，是一本可以每天翻阅的交易手册 + 练习本——帮你记规则、练纪律、看后果。

---

## 系统构成

### 🏠 全景仪表盘 `market_dashboard.py`
**每个交易日 16:00 自动运行，飞书推送。**

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
**每个交易日 15:00 自动运行，飞书推送。** RSS + FRED + AKShare + Polymarket + Tavily 多源抓取 → DeepSeek 8 层分析 → 飞书。

### 📊 月度复盘 `backtest_review.py`
**每月 1 号自动运行，飞书推送。** 不统计预测准确率——只检查五大模块纪律执行情况，违例标 ❌。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 指数数据 | akshare → yfinance 双源自动降级 |
| 板块数据 | akshare (23 个申万行业) |
| 个股数据 | adata → akshare 双源 |
| AI 分析 | DeepSeek (chat) |
| M1 宏观 | akshare / FRED |
| 消息推送 | 飞书 Bot API + 交互卡片 |
| 定时运行 | GitHub Actions (UTC 08:00) |
| 数据持久化 | JSON (portfolio / trade_log / position_state) |

---

## GitHub Actions

| Workflow | 触发时间 | 内容 |
|----------|---------|------|
| `rally-health.yml` | 工作日 16:00 北京时间 | 全景仪表盘 → 飞书 |
| `daily-report.yml` | 每天 15:00 北京时间 | RSS 市场机会发现 → 飞书 |
| `monthly-review.yml` | 每月 1 号 09:00 | 月度纪律复盘 → 飞书 |

三个 workflow 均有开市检查，周末 / 节假日 / 未收盘自动跳过，不浪费配额。

---

## 本地使用

```bash
pip install -r requirements.txt

# 仪表盘
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
| `DEEPSEEK_API_KEY` | AI 分析 + 决策审计 |
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
├── sources.json             # 信息源配置
├── requirements.txt
├── .github/workflows/
│   ├── rally-health.yml     # 仪表盘定时
│   ├── daily-report.yml     # 市场发现定时
│   └── monthly-review.yml   # 月度复盘
└── docs/
    └── superpowers/specs/   # 设计文档
```

---

*每日自动 | 仅供参考, 不构成投资建议*
