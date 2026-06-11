# 市场机会发现系统 (Market Opportunity Discovery System)

买方基金研究员视角的每日市场机会发现系统。

**不是新闻总结。不是预测短期涨跌。**
**发现未来1个月至5年内可能产生超额收益的投资机会。**

## 架构

```
GitHub Actions (每天 15:00 北京时间)
  → RSS 抓取（金融新闻 / Reddit 投资社区 / YouTube 财经）
  → Twitter GraphQL API（39 个金融账号）
  → FRED 经济数据 / AKShare A股数据
  → 预过滤
  → DeepSeek AI 8层分析（含跨公司模式识别）
  → 飞书 Bot API 推送（内部群）
  → 飞书 Webhook 推送（外部群）
```

## 信息源

- **Twitter**: 53 个金融/宏观/行业研究账号（中英文混合，含美股一线分析师）
- **Reddit**: 10 个投资社区（r/investing, r/stocks, r/SecurityAnalysis, r/biotech 等）
- **SEC EDGAR**: 10-K/10-Q/13F/Form 4/S-1/8-K 一手公司文件
- **中国金融 RSS**: 财联社、金十数据、华尔街见闻、格隆汇、东方财富研报、雪球
- **国际金融 RSS**: CNBC、MarketWatch、Bloomberg、Yahoo Finance、TechCrunch、FierceBiotech、美联储、ECB
- **YouTube**: Bloomberg、CNBC、Real Vision、Patrick Boyle 等财经频道
- **FRED**: 20 个美国经济核心指标
- **AKShare**: A股行情+中国宏观数据

## 配置

### GitHub Secrets

| Secret | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `TWITTER_COOKIES` | Twitter auth_token + ct0（JSON 格式） |
| `FEISHU_APP_ID` | 飞书 Bot App ID |
| `FEISHU_APP_SECRET` | 飞书 Bot App Secret |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook 机器人地址（推送到外部群） |
| `FRED_API_KEY` | FRED API 密钥（可选，从 fred.stlouisfed.org 免费获取） |

## 本地测试

```bash
# 安装依赖
pip install -r requirements.txt

# 测试飞书推送
set FEISHU_APP_ID=xxx
set FEISHU_APP_SECRET=xxx
python main.py --test-feishu

# Dry-run（只抓取不分析不推送）
set TWITTER_COOKIES={"auth_token":"xxx","ct0":"xxx"}
python main.py --dry-run

# 完整运行
set DEEPSEEK_API_KEY=xxx
python main.py
```

## 分析框架

8层分析体系：
1. 变化扫描器（全市场变化类型识别）
2. 预期差扫描器（市场共识 vs 现实）
3. 投资推理引擎（事实→共识→多阶影响→验证）
4. 机会分类器（成长/周期/价值/反转/防御）
5. 杠铃分析（进攻端+防守端+环境判断）
6. 资金与赔率分析
7. 机会评分系统（100分制）
8. 跨公司模式识别（多公司同向共振→提升胜率，所有行业同等对待）
