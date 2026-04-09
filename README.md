# OptionGuard - 期权纪律交易系统

基于"波动率极值+价格极值+动量确认"的期权方向性交易框架，强制纪律化执行。

## 快速开始

### 方式一：本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动Web仪表盘（默认 http://localhost:8000）
python main.py web

# 其他命令
python main.py scan       # 执行一次信号扫描
python main.py backtest   # 运行策略回测
python main.py monitor    # 启动持仓风控监控
```

### 方式二：Docker部署

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

访问 `http://localhost:8000` 即可使用。

### 方式三：分享给他人

```bash
# 指定端口和外部访问
PORT=9000 python main.py web

# Docker方式
PORT=9000 docker compose up -d
```

将你的 `IP:端口` 分享给他人即可远程访问仪表盘。

## 功能模块

| 页面 | 功能 |
|------|------|
| **仪表盘** | ETF价格/iVIX分位数/MACD状态实时展示，开仓条件矩阵判定 |
| **信号管理** | 自动生成BUY_CALL/SELL_PUT信号，手动确认执行或跳过 |
| **持仓监控** | 实时盈亏监控、保证金占比、到期预警，支持手动录入和平仓 |
| **回测报告** | 一键回测，输出净值曲线、最大回撤、胜率、Calmar比率等 |

## 策略逻辑

```
BUY_CALL: iVIX分位 ≤ 10% AND 价格分位 ≤ 10% AND MACD翻红放大
SELL_PUT: iVIX分位 ≥ 90% AND 价格分位 ≤ 10%
```

### 风控规则
- 止损: 盈亏 ≤ -50%
- 止盈: 盈亏 ≥ +80%
- 到期预警: 剩余 ≤ 3天
- 保证金熔断: 占用 ≥ 60%净资产

### 仓位管理
- 买入/卖出张数 = 总可买(卖)张数 × 1/10

## 数据源配置

默认使用模拟数据运行。接入方正证券实盘数据：

1. 安装MiniQMT客户端并保持运行
2. `pip install xtquant`
3. 编辑 `config/strategy.yaml`：

```yaml
broker:
  qmt_path: "你的MiniQMT路径/userdata_mini"
  account_id: "你的资金账号"
```

## 配置文件

所有策略参数在 `config/strategy.yaml` 中配置，支持热修改重启生效。

## API接口

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 系统状态概要 |
| `GET /api/signals` | 全部信号记录 |
| `GET /api/positions` | 全部持仓记录 |

## 项目结构

```
├── config/strategy.yaml    # 策略参数配置
├── core/                   # 核心引擎
│   ├── data_loader.py      # XtQuant数据获取
│   ├── greeks.py           # BS模型Greeks计算
│   ├── indicators.py       # 分位数/MACD指标
│   ├── signal_engine.py    # 信号生成+合约优选
│   ├── risk_monitor.py     # 风控监控
│   ├── backtest.py         # 回测引擎
│   └── store.py            # JSON持久化
├── web/                    # Web仪表盘
├── models/dto.py           # 数据模型
├── main.py                 # 主入口
├── Dockerfile
└── docker-compose.yml
```
