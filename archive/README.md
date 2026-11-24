# 归档目录

存放已弃用、不再被任何 workflow 或代码引用的文件。保留是为了追溯历史，不要直接复用。

| 文件 | 归档日期 | 原因 |
|---|---|---|
| `rally_health_monitor_legacy.py` | 2026-11-24 | 2026-08-19 功能已并入 `market_dashboard.py`；无 workflow / 代码引用；五个指标（价格偏离 MA60、量能、RSI14、波动率、M1）从未单独回测验证。A股每日推送已由 `research/cycle_timing` 周期面板和 `market_dashboard.py --merge` 接管。 |

恢复方法：`git mv archive/<file> <原路径>`，并确认恢复后确实有调用方和验证依据。
