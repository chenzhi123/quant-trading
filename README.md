# OptionGuard - 期权纪律交易系统

基于 **"波动率极值 + 价格极值 + 动量确认"** 的期权方向性交易框架。  
系统化监控、刚性风控、WebSocket 实时推送，杜绝主观扛单。

---

## 目录

- [系统状态](#系统状态)
- [快速启动](#快速启动)
- [页面说明](#页面说明)
- [策略逻辑](#策略逻辑)
- [实时数据架构](#实时数据架构)
- [接入实盘数据](#接入实盘数据)
- [配置文件详解](#配置文件详解)
- [API 接口](#api-接口)
- [Docker 部署](#docker-部署)
- [云端部署](#云端部署)
- [项目结构](#项目结构)

---

## 系统状态

启动后系统运行在本地，通过浏览器访问：

| 项目 | 值 |
|------|-----|
| 本地地址 | **http://localhost:8000** |
| 默认模式 | 模拟数据（无需券商客户端即可体验全部功能） |
| 数据刷新 | 每 **3 秒** 实时推送（WebSocket） |
| 指标计算 | 每 **60 秒** 更新分位数/MACD |

> 如果修改了端口（通过 `PORT` 环境变量），请访问对应端口。

---

## 快速启动

### 前置要求

- Python 3.10+
- pip

### 3 步启动

```bash
# 1. 克隆项目
git clone https://github.com/chenzhi123/quant-trading.git
cd quant-trading

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动系统
python main.py
```

启动后在浏览器打开 **http://localhost:8000** 即可进入系统。

> 无参数默认启动 Web 仪表盘。也可以指定子命令：

```bash
python main.py web        # 启动Web仪表盘（默认）
python main.py scan       # 执行一次信号扫描
python main.py backtest   # 运行历史回测
python main.py monitor    # 启动持仓风控定时监控
```

### 自定义端口

```bash
# Windows PowerShell
$env:PORT="9000"; python main.py

# Linux/Mac
PORT=9000 python main.py
```

---

## 页面说明

系统包含 5 个页面，通过顶部导航栏切换：

### 1. 首页 (`/`)

系统介绍着陆页，展示策略逻辑、功能模块、交易规则速查和技术架构。适合分享给他人了解系统全貌。

### 2. 仪表盘 (`/dashboard`)

**核心操作页面**，所有数据通过 WebSocket 每 3 秒实时更新，无需刷新页面。

- **实时行情卡片**：沪深300ETF 价格、iVIX 波动率指数、MACD 状态
- **分位数进度条**：当前价格/波动率在近 1 年历史中的位置
- **开仓条件矩阵**：实时判断 BUY_CALL / SELL_PUT 各项条件是否满足
- **最新信号卡片**：显示最新的交易信号，支持一键确认执行或跳过
- **风控警告**：止损/止盈/到期/保证金警告实时弹出
- **连接状态徽章**：右上角显示 WebSocket "实时" / "已断开" 状态

### 3. 信号管理 (`/signals`)

- 查看所有历史信号记录（类型、合约、建议价、止损/止盈、张数）
- 点击 **"立即扫描"** 手动触发一次信号扫描
- 对待确认信号执行 **确认** 或 **跳过** 操作
- 确认执行后自动在持仓中创建对应持仓记录

### 4. 持仓监控 (`/positions`)

- **账户概览**：总资产、可用资金、持仓市值、保证金占比
- **活跃持仓表**：合约、方向、开仓价、盈亏%（WebSocket 实时更新）、剩余天数
- **手动录入**：点击 "手动录入持仓" 可添加已有持仓
- **一键平仓**：每个持仓可直接标记为已平仓
- **已平仓记录**：归档历史交易
- **风控警告**：WebSocket 实时推送

### 5. 回测报告 (`/backtest`)

- 点击 **"运行回测"** 一键回测近 1 年历史数据
- 输出：累计收益率、年化收益率、最大回撤、Calmar 比率、胜率、盈亏比
- 显示净值曲线图和交易明细表

---

## 策略逻辑

### 开仓信号

| 信号 | 条件 | 说明 |
|------|------|------|
| **BUY_CALL** | iVIX分位 ≤ 10% **且** 价格分位 ≤ 10% **且** MACD翻红放大 | 波动率低 + 价格低 + 动量反转 |
| **SELL_PUT** | iVIX分位 ≥ 90% **且** 价格分位 ≤ 10% | 波动率极高（恐慌见顶）+ 价格低 |

- 同类型信号 **3 个交易日冷却期**
- BUY_CALL 优先级 > SELL_PUT
- 建议张数 = 总可买(卖)张数 × **1/10**

### 合约优选

1. 到期日筛选：剩余 25~35 天（次月合约）
2. 流动性筛选：日成交量 ≥ 500、持仓量 ≥ 1000、买卖价差 ≤ 2 tick
3. Delta 接近 0.5（平值优先）
4. 按成交量降序排列，取第一个

### 风控规则

| 规则 | 触发条件 | 动作 |
|------|----------|------|
| 止损 | 盈亏 ≤ **-50%** | 强制提示平仓 |
| 止盈 | 盈亏 ≥ **+80%** | 强制提示锁定利润 |
| 到期预警 | 剩余 ≤ **3 天** | 提示移仓或平仓 |
| 保证金熔断 | 占净资产 ≥ **60%** | 禁止新开卖仓 |

> 所有风控规则仅提示，**不自动下单**，由用户显式确认操作。

---

## 实时数据架构

```
数据源 (XtQuant/模拟) ──[3秒]──> 后端 AppState ──[WebSocket]──> 浏览器 DOM 局部更新
                                                                (无整页刷新, 零闪烁)
```

- **快速路径（3 秒）**：实时行情（ETF价格、VIX点位）+ 持仓盈亏更新 + 风控检查
- **慢速路径（60 秒）**：滚动分位数计算、MACD 指标（依赖历史数据，无需高频）
- **前端推送**：WebSocket `/ws/market` 端点，自动重连，断线感知

---

## 接入实盘数据

默认使用模拟数据，无需任何配置即可体验。接入方正证券实盘：

### 步骤

1. **安装 MiniQMT 客户端**并保持运行
2. **安装 xtquant**：
   ```bash
   pip install xtquant
   ```
3. **编辑配置** `config/strategy.yaml`：
   ```yaml
   broker:
     qmt_path: "C:/你的MiniQMT路径/userdata_mini"
     account_id: "你的资金账号"
   ```
4. 重启系统，数据将自动从模拟切换为实盘

> 如果 xtquant 未安装或 MiniQMT 未运行，系统会自动降级到模拟数据模式。

---

## 配置文件详解

所有策略参数在 `config/strategy.yaml` 中配置：

```yaml
broker:
  qmt_path: "MiniQMT客户端路径"
  account_id: "资金账号"

strategy:
  underlying: "510300.SH"          # 标的ETF
  vix_code: "000188.SH"            # 波动率指数代码
  lookback_days: 252                # 滚动窗口（交易日）
  iv_percentile:
    buy_call: 10                    # BUY_CALL的iVIX分位阈值
    sell_put: 90                    # SELL_PUT的iVIX分位阈值
  price_percentile: 10              # 价格分位阈值
  macd:
    fast: 12
    slow: 26
    signal: 9
    min_expand_days: 2              # MACD连续放大天数
  contract:
    min_volume: 500                 # 最低日成交量
    min_oi: 1000                    # 最低持仓量
    target_delta: 0.5               # 目标Delta（平值）
    days_to_expiry_range: [25, 35]  # 到期日范围
  risk:
    stop_loss: -0.50                # 止损阈值
    take_profit: 0.80               # 止盈阈值
    margin_limit: 0.60              # 保证金上限
    signal_cooldown_days: 3         # 信号冷却期
    expiry_warning_days: 3          # 到期预警天数
  cost:
    commission_per_lot: 2.0         # 手续费（元/张）
    slippage_tick_buy: 1            # 买方滑点（tick）
    slippage_tick_sell: 2           # 卖方滑点（tick）
  position:
    size_fraction: 0.1              # 仓位比例（1/10）
  refresh_interval:
    fast_seconds: 3                 # 快速刷新间隔（行情）
    slow_seconds: 60                # 慢速刷新间隔（指标）
```

---

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 系统状态概要（价格、分位数、持仓数等） |
| `/api/signals` | GET | 全部信号记录 JSON |
| `/api/positions` | GET | 全部持仓记录 JSON |
| `/ws/market` | WebSocket | 实时行情推送（每 3 秒一帧） |

### WebSocket 数据帧示例

```json
{
  "etf_price": 3.9512,
  "vix_value": 18.47,
  "price_pct": 74.6,
  "vix_pct": 8.7,
  "macd_trigger": false,
  "macd_hist": 0.001234,
  "active_positions": [
    {
      "contract_code": "10001234.SHO",
      "direction": "LONG",
      "current_pnl_pct": 12.5,
      "days_left": 15
    }
  ],
  "risk_alerts": [],
  "pending_signals": 0,
  "update_time": "2026-04-13 14:06:22"
}
```

---

## Docker 部署

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

数据和日志通过 volume 挂载持久化，重启不丢失。

---

## 云端部署（Render）

项目已包含 `render.yaml` 配置，可一键部署到 Render 免费版：

1. 将代码推送到 GitHub
2. 访问 https://dashboard.render.com/blueprint/new?repo=https://github.com/chenzhi123/quant-trading
3. 点击 Apply 开始部署
4. 部署完成后获得公网 URL，分享给任何人即可访问

> Render 免费版 15 分钟无访问后休眠，首次访问需等几秒冷启动。

---

## 项目结构

```
quant-trading/
├── config/
│   └── strategy.yaml           # 策略参数配置（所有阈值可调）
├── core/
│   ├── data_loader.py          # 方正证券 XtQuant 数据获取 + 模拟数据
│   ├── greeks.py               # Black-Scholes Greeks 计算
│   ├── indicators.py           # 滚动分位数 + MACD 指标引擎
│   ├── signal_engine.py        # 信号匹配 + 合约优选 + 仓位计算
│   ├── risk_monitor.py         # 四规则实时风控
│   ├── backtest.py             # T+1 回测引擎（含滑点/手续费建模）
│   └── store.py                # JSON 文件持久化
├── models/
│   └── dto.py                  # Pydantic 数据模型
├── web/
│   ├── app.py                  # FastAPI 应用 + WebSocket 端点
│   ├── static/style.css        # 暗色主题样式
│   └── templates/              # Jinja2 HTML 模板
│       ├── base.html           # 基础布局
│       ├── home.html           # 着陆首页
│       ├── dashboard.html      # 实时仪表盘（WebSocket）
│       ├── signals.html        # 信号管理
│       ├── positions.html      # 持仓监控（WebSocket）
│       └── backtest.html       # 回测报告
├── notifier/
│   └── console_notifier.py     # 控制台通知 + JSONL 日志
├── tests/                      # 单元测试（29 个用例）
├── main.py                     # 主入口（默认启动 Web）
├── requirements.txt            # Python 依赖
├── Dockerfile                  # Docker 镜像
├── docker-compose.yml          # Docker Compose 编排
├── render.yaml                 # Render 云部署配置
└── README.md                   # 本文档
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 实时推送 | WebSocket（原生，无额外依赖） |
| 定时任务 | APScheduler（快/慢两档） |
| 数据处理 | Pandas + NumPy |
| 期权定价 | Black-Scholes（SciPy） |
| 数据源 | 方正证券 XtQuant / 模拟数据自动降级 |
| 前端 | Jinja2 模板 + 原生 JS |
| 持久化 | JSON 文件（信号/持仓跨重启保留） |
| 容器化 | Docker + Docker Compose |
| 测试 | Pytest（29 个用例） |
