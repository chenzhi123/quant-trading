"""期权纪律交易系统 - 主入口

用法:
    python main.py scan       # 执行一次信号扫描
    python main.py monitor    # 启动持仓监控（定时循环）
    python main.py backtest   # 运行回测
    python main.py web        # 启动Web仪表盘（含定时刷新）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from loguru import logger

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")
logger.add(
    LOG_DIR / "system_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="1 day",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {module}:{function}:{line} | {message}",
    encoding="utf-8",
)


def load_config() -> dict:
    config_path = ROOT / "config" / "strategy.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_components(config: dict):
    """初始化所有模块"""
    from core.backtest import BacktestEngine
    from core.data_loader import DataLoader
    from core.indicators import IndicatorEngine
    from core.risk_monitor import RiskMonitor
    from core.signal_engine import SignalEngine
    from notifier.console_notifier import ConsoleNotifier

    dl = DataLoader(config)
    dl.connect()

    ie = IndicatorEngine(config)
    se = SignalEngine(config, dl, ie)
    rm = RiskMonitor(config, dl)
    bt = BacktestEngine(config)
    notifier = ConsoleNotifier()

    return dl, ie, se, rm, bt, notifier


def cmd_scan(config: dict) -> None:
    """执行一次信号扫描"""
    dl, ie, se, rm, bt, notifier = build_components(config)

    logger.info("开始信号扫描...")
    signal = se.scan()

    if signal:
        notifier.notify_signal(signal)
        logger.info(f"信号已生成: {signal.type.value} {signal.contract_code}")
    else:
        logger.info("本次扫描未生成信号")


def cmd_monitor(config: dict) -> None:
    """启动持仓监控（定时循环）"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    dl, ie, se, rm, bt, notifier = build_components(config)

    def _monitor_tick():
        from web.app import get_state
        state = get_state()
        if state.positions:
            events = rm.check_all_positions(state.positions)
            for e in events:
                notifier.notify_risk(e)

    scheduler = BlockingScheduler()
    scheduler.add_job(_monitor_tick, "interval", seconds=30, id="risk_monitor")
    logger.info("持仓监控已启动，每30秒检查一次 (Ctrl+C退出)")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("持仓监控已停止")


def cmd_backtest(config: dict) -> None:
    """运行回测"""
    dl, ie, se, rm, bt, notifier = build_components(config)

    logger.info("开始回测...")
    etf_data = dl.get_etf_history(300)
    vix_data = dl.get_vix_history(300)

    result = bt.run(etf_data, vix_data)
    files = bt.save_report(result)

    summary = {
        "累计收益率": f"{result.total_return:.2%}",
        "年化收益率": f"{result.annual_return:.2%}",
        "最大回撤": f"{result.max_drawdown:.2%}",
        "Calmar比率": f"{result.calmar_ratio:.2f}",
        "胜率": f"{result.win_rate:.2%}",
        "盈亏比": f"{result.profit_loss_ratio:.2f}",
        "交易次数": result.total_trades,
    }
    notifier.notify_backtest(summary)
    logger.info(f"回测报告已保存: {files}")


def cmd_web(config: dict) -> None:
    """启动Web仪表盘"""
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    dl, ie, se, rm, bt, notifier = build_components(config)

    from web.app import get_state
    state = get_state()
    state.signal_engine = se
    state.risk_monitor = rm
    state.data_loader = dl
    state.indicator_engine = ie
    state.backtest_engine = bt
    state.notifier = notifier

    def _refresh_market_data():
        """定时刷新实时行情到Web状态"""
        try:
            etf_rt = dl.get_etf_realtime()
            state.etf_price = etf_rt.get("last", 0)

            state.vix_value = dl.get_vix_realtime()

            vix_hist = dl.get_vix_history()
            etf_hist = dl.get_etf_history()

            state.vix_pct = ie.calc_current_percentile(vix_hist, state.vix_value)
            state.price_pct = ie.calc_current_percentile(etf_hist["close"], state.etf_price)

            state.macd_trigger = ie.get_latest_macd_trigger(etf_hist)
            hist_vals = ie.get_latest_hist_values(etf_hist, 1)
            state.macd_hist = hist_vals[0] if hist_vals else 0.0

            state.account = dl.get_account_info()
        except Exception as e:
            logger.error(f"行情刷新失败: {e}")

    _refresh_market_data()

    scheduler = BackgroundScheduler()
    scheduler.add_job(_refresh_market_data, "interval", seconds=30, id="market_refresh")
    scheduler.start()

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Web仪表盘启动中... 访问 http://{host}:{port}")

    from web.app import app
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    commands = {
        "scan": cmd_scan,
        "monitor": cmd_monitor,
        "backtest": cmd_backtest,
        "web": cmd_web,
    }

    command = sys.argv[1].lower() if len(sys.argv) >= 2 else "web"
    config = load_config()

    if command not in commands:
        print(f"未知命令: {command}")
        print(f"可用命令: {', '.join(commands.keys())}")
        sys.exit(1)

    logger.info(f"期权纪律交易系统 - 执行命令: {command}")
    commands[command](config)


if __name__ == "__main__":
    main()
