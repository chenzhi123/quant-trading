"""回测引擎 - 历史回放、交易成本建模、绩效统计"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from loguru import logger

from core.indicators import IndicatorEngine

OUTPUT_DIR = Path(__file__).parent.parent / "data"


@dataclass
class BacktestTrade:
    """单笔交易记录"""
    entry_date: date
    exit_date: Optional[date] = None
    signal_type: str = ""
    contract_info: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: int = 1
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    commission: float = 0.0
    slippage_cost: float = 0.0


@dataclass
class BacktestPosition:
    """回测中的模拟持仓"""
    signal_type: str
    entry_date: date
    entry_price: float
    quantity: int
    expiry_date: date
    tick_size: float = 0.0001
    volume_multiple: int = 10000


@dataclass
class BacktestResult:
    """回测结果"""
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trades: List[BacktestTrade] = field(default_factory=list)
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    trading_days: int = 0


class BacktestEngine:
    """回测引擎

    规范:
    - T日收盘后生成信号，T+1日开盘以中间价成交
    - 交易成本: 手续费 + 滑点 + 流动性折扣
    - 仓位: 总可买(卖)数 * size_fraction
    """

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config["strategy"]
        self.lookback = self.cfg["lookback_days"]
        self.ie = IndicatorEngine(config)

        cost = self.cfg["cost"]
        self.commission = cost["commission_per_lot"]
        self.slip_buy = cost["slippage_tick_buy"]
        self.slip_sell = cost["slippage_tick_sell"]
        self.size_fraction = self.cfg["position"]["size_fraction"]
        self.initial_capital = 1_000_000.0

    def run(self, etf_data: pd.DataFrame, vix_data: pd.Series,
            option_sim_func=None) -> BacktestResult:
        """执行回测

        Args:
            etf_data: ETF日线DataFrame (date/open/high/low/close/volume)
            vix_data: iVIX日线Series (index=date, value=vix点位)
            option_sim_func: 可选的期权价格模拟函数，默认用简化模型
        """
        if len(etf_data) < self.lookback + 10:
            logger.error(f"数据不足: 需要至少{self.lookback + 10}条, 实际{len(etf_data)}")
            return BacktestResult()

        macd_data = self.ie.calc_macd(etf_data)

        equity = self.initial_capital
        equity_list: List[Tuple[date, float]] = []
        trades: List[BacktestTrade] = []
        position: Optional[BacktestPosition] = None
        last_signal_dates: Dict[str, Optional[date]] = {"BUY_CALL": None, "SELL_PUT": None}

        cooldown = self.cfg["risk"]["signal_cooldown_days"]
        stop_loss = self.cfg["risk"]["stop_loss"]
        take_profit = self.cfg["risk"]["take_profit"]
        expiry_warn = self.cfg["risk"]["expiry_warning_days"]

        start_idx = self.lookback
        dates = macd_data["date"].values
        closes = macd_data["close"].values

        for t in range(start_idx, len(macd_data)):
            current_date = pd.Timestamp(dates[t]).date()
            current_close = closes[t]

            if position is not None:
                opt_mid = self._simulate_option_price(
                    position, current_close, current_date
                )
                pnl_pct = (opt_mid - position.entry_price) / abs(position.entry_price)
                days_left = (position.expiry_date - current_date).days

                exit_reason = None
                if pnl_pct <= stop_loss:
                    exit_reason = "STOP_LOSS"
                elif pnl_pct >= take_profit:
                    exit_reason = "TAKE_PROFIT"
                elif days_left <= expiry_warn:
                    exit_reason = "EXPIRY"

                if exit_reason:
                    slip = self._calc_slippage(
                        position.tick_size,
                        "SELL" if position.signal_type == "BUY_CALL" else "BUY"
                    )
                    exit_price = opt_mid + slip
                    comm = self.commission * position.quantity
                    pnl = (exit_price - position.entry_price) * position.quantity * position.volume_multiple - comm

                    trade = BacktestTrade(
                        entry_date=position.entry_date,
                        exit_date=current_date,
                        signal_type=position.signal_type,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        commission=comm,
                    )
                    trades.append(trade)
                    equity += pnl
                    position = None

            if position is None:
                vix_window = self._get_vix_window(vix_data, current_date, self.lookback)
                if vix_window is not None and len(vix_window) > 0:
                    current_vix = float(vix_window.iloc[-1])
                    iv_pct = IndicatorEngine.calc_current_percentile(
                        vix_window.iloc[:-1], current_vix
                    )
                else:
                    iv_pct = 50.0

                price_window = pd.Series(closes[max(0, t - self.lookback):t])
                price_pct = IndicatorEngine.calc_current_percentile(
                    price_window, current_close
                )

                macd_trigger = bool(macd_data["macd_trigger"].iloc[t])

                sig_type = self._match_signal(iv_pct, price_pct, macd_trigger)

                if sig_type and self._cooldown_ok(sig_type, current_date, last_signal_dates, cooldown):
                    if t + 1 < len(macd_data):
                        entry_close = closes[t + 1]
                        entry_date = pd.Timestamp(dates[t + 1]).date()
                        opt_entry = self._simulate_initial_option_price(entry_close)
                        slip = self._calc_slippage(0.0001, "BUY" if sig_type == "BUY_CALL" else "SELL")
                        opt_entry += slip

                        cost_per_lot = opt_entry * 10000
                        total_can = math.floor(equity / cost_per_lot) if cost_per_lot > 0 else 0
                        qty = max(1, math.floor(total_can * self.size_fraction))

                        expiry = entry_date + timedelta(days=30)

                        position = BacktestPosition(
                            signal_type=sig_type,
                            entry_date=entry_date,
                            entry_price=opt_entry,
                            quantity=qty,
                            expiry_date=expiry,
                        )
                        last_signal_dates[sig_type] = current_date
                        comm = self.commission * qty
                        equity -= comm

            equity_list.append((current_date, equity))

        if position is not None:
            final_close = closes[-1]
            final_date = pd.Timestamp(dates[-1]).date()
            opt_mid = self._simulate_option_price(position, final_close, final_date)
            pnl = (opt_mid - position.entry_price) * position.quantity * position.volume_multiple
            trade = BacktestTrade(
                entry_date=position.entry_date,
                exit_date=final_date,
                signal_type=position.signal_type,
                entry_price=position.entry_price,
                exit_price=opt_mid,
                quantity=position.quantity,
                pnl=pnl,
                pnl_pct=(opt_mid - position.entry_price) / abs(position.entry_price),
                exit_reason="END_OF_BACKTEST",
            )
            trades.append(trade)
            equity += pnl

        equity_series = pd.Series(
            [e for _, e in equity_list],
            index=pd.DatetimeIndex([d for d, _ in equity_list]),
            name="equity",
        )

        result = self._calc_metrics(equity_series, trades)
        return result

    def _match_signal(self, iv_pct: float, price_pct: float,
                      macd_trigger: bool) -> Optional[str]:
        buy_iv = self.cfg["iv_percentile"]["buy_call"]
        sell_iv = self.cfg["iv_percentile"]["sell_put"]
        price_th = self.cfg["price_percentile"]

        if iv_pct <= buy_iv and price_pct <= price_th and macd_trigger:
            return "BUY_CALL"
        if iv_pct >= sell_iv and price_pct <= price_th:
            return "SELL_PUT"
        return None

    @staticmethod
    def _cooldown_ok(sig_type: str, current: date,
                     last_dates: Dict[str, Optional[date]], days: int) -> bool:
        last = last_dates.get(sig_type)
        if last is None:
            return True
        return (current - last).days > days

    def _simulate_option_price(self, pos: BacktestPosition,
                               spot: float, current_date: date) -> float:
        """简化期权价格模拟 (用于回测)

        基于Delta近似: 期权价格变动 ≈ Delta * 标的价格变动 + 时间衰减
        """
        initial_spot = pos.entry_price * 10000 / 0.5
        delta_spot = spot - initial_spot / 10000 * 0.5 * 10000 / 10000

        days_total = (pos.expiry_date - pos.entry_date).days
        days_elapsed = (current_date - pos.entry_date).days
        if days_total <= 0:
            days_total = 30

        time_decay_ratio = max(0, 1 - days_elapsed / days_total)
        time_value = pos.entry_price * 0.3 * (1 - math.sqrt(time_decay_ratio))

        delta = 0.5
        price_change = delta * (spot - spot) * 0.01

        new_price = max(0.0001, pos.entry_price - time_value + price_change)
        noise = np.random.normal(0, pos.entry_price * 0.02)
        return max(0.0001, new_price + noise)

    @staticmethod
    def _simulate_initial_option_price(spot: float) -> float:
        """模拟平值期权初始价格"""
        return spot * 0.03 + np.random.uniform(0, 0.005)

    def _calc_slippage(self, tick_size: float, direction: str) -> float:
        if direction == "BUY":
            return tick_size * self.slip_buy
        return -tick_size * self.slip_sell

    @staticmethod
    def _get_vix_window(vix_data: pd.Series, current_date: date,
                        window: int) -> Optional[pd.Series]:
        if vix_data.empty:
            return None
        vix_dates = vix_data.index
        mask = vix_dates <= pd.Timestamp(current_date)
        valid = vix_data[mask]
        if len(valid) < window // 2:
            return None
        return valid.tail(window)

    def _calc_metrics(self, equity: pd.Series,
                      trades: List[BacktestTrade]) -> BacktestResult:
        result = BacktestResult()
        result.equity_curve = equity
        result.trades = trades
        result.trading_days = len(equity)
        result.total_trades = len(trades)

        if equity.empty:
            return result

        result.total_return = (equity.iloc[-1] / equity.iloc[0] - 1)

        years = result.trading_days / 252
        if years > 0:
            result.annual_return = (1 + result.total_return) ** (1 / years) - 1

        rolling_max = equity.cummax()
        drawdown = (equity - rolling_max) / rolling_max
        result.max_drawdown = float(drawdown.min())

        if result.max_drawdown < 0:
            result.calmar_ratio = result.annual_return / abs(result.max_drawdown)

        if trades:
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl <= 0]
            result.win_rate = len(wins) / len(trades) if trades else 0

            avg_win = np.mean([t.pnl for t in wins]) if wins else 0
            avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 1
            result.profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

        return result

    def save_report(self, result: BacktestResult, output_dir: Optional[Path] = None) -> Dict[str, str]:
        """保存回测报告: 净值曲线图 + 交易明细CSV + 指标汇总"""
        out = output_dir or OUTPUT_DIR
        out.mkdir(exist_ok=True)
        files: Dict[str, str] = {}

        fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})

        axes[0].plot(result.equity_curve.index, result.equity_curve.values, linewidth=1.5)
        axes[0].set_title("Equity Curve", fontsize=14)
        axes[0].set_ylabel("Equity")
        axes[0].grid(True, alpha=0.3)

        rolling_max = result.equity_curve.cummax()
        dd = (result.equity_curve - rolling_max) / rolling_max
        axes[1].fill_between(dd.index, dd.values, 0, alpha=0.4, color="red")
        axes[1].set_title("Drawdown", fontsize=14)
        axes[1].set_ylabel("Drawdown %")
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        chart_path = out / "backtest_equity.png"
        fig.savefig(chart_path, dpi=150)
        plt.close(fig)
        files["chart"] = str(chart_path)

        if result.trades:
            rows = []
            for t in result.trades:
                rows.append({
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "signal_type": t.signal_type,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": round(t.pnl, 2),
                    "pnl_pct": round(t.pnl_pct, 4),
                    "exit_reason": t.exit_reason,
                    "commission": t.commission,
                })
            trades_df = pd.DataFrame(rows)
            csv_path = out / "backtest_trades.csv"
            trades_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            files["trades_csv"] = str(csv_path)

        summary = {
            "累计收益率": f"{result.total_return:.2%}",
            "年化收益率": f"{result.annual_return:.2%}",
            "最大回撤": f"{result.max_drawdown:.2%}",
            "Calmar比率": f"{result.calmar_ratio:.2f}",
            "胜率": f"{result.win_rate:.2%}",
            "盈亏比": f"{result.profit_loss_ratio:.2f}",
            "交易次数": result.total_trades,
            "交易天数": result.trading_days,
        }
        summary_path = out / "backtest_summary.txt"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("=" * 40 + "\n")
            f.write("    回测绩效报告\n")
            f.write("=" * 40 + "\n")
            for k, v in summary.items():
                f.write(f"  {k}: {v}\n")
            f.write("=" * 40 + "\n")
        files["summary"] = str(summary_path)

        logger.info(f"回测报告已保存: {files}")
        return files
