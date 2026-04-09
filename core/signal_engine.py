"""信号生成引擎 - 开仓条件判断、合约优选、仓位计算"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from core.data_loader import DataLoader
from core.greeks import calc_delta, calc_iv, enrich_greeks
from core.indicators import IndicatorEngine
from models.dto import (
    AccountInfo,
    OptionContract,
    SignalDTO,
    SignalStatus,
    SignalType,
    TriggerReason,
)


class SignalEngine:
    """信号生成器：扫描行情→指标判断→合约优选→仓位计算→输出SignalDTO"""

    def __init__(self, config: Dict[str, Any], data_loader: DataLoader,
                 indicator_engine: IndicatorEngine):
        self.cfg = config["strategy"]
        self.full_cfg = config
        self.dl = data_loader
        self.ie = indicator_engine

        self.last_signal_date: Dict[str, Optional[date]] = {
            "BUY_CALL": None,
            "SELL_PUT": None,
        }

    def scan(self) -> Optional[SignalDTO]:
        """执行一次完整的信号扫描

        流程: 获取实时行情 → 计算指标 → 判断条件 → 优选合约 → 计算仓位 → 返回信号
        """
        today = date.today()

        # 1. 获取实时行情与历史数据
        etf_rt = self.dl.get_etf_realtime()
        etf_hist = self.dl.get_etf_history()
        vix_rt = self.dl.get_vix_realtime()
        vix_hist = self.dl.get_vix_history()

        spot = etf_rt.get("last", 0)
        if spot <= 0:
            logger.warning("ETF实时价格异常，跳过本次扫描")
            return None

        # 2. 计算分位数
        iv_pct = self.ie.calc_current_percentile(vix_hist, vix_rt)
        price_pct = self.ie.calc_current_percentile(etf_hist["close"], spot)

        # 3. 计算MACD
        macd_trigger = self.ie.get_latest_macd_trigger(etf_hist)
        hist_values = self.ie.get_latest_hist_values(etf_hist)

        logger.info(
            f"扫描指标: iVIX={vix_rt:.2f}(P{iv_pct:.1f}), "
            f"ETF={spot:.4f}(P{price_pct:.1f}), "
            f"MACD触发={macd_trigger}"
        )

        # 4. 条件匹配
        sig_type = self._match_signal(iv_pct, price_pct, macd_trigger)
        if sig_type is None:
            logger.info("未触发任何开仓条件")
            return None

        # 5. 冷却期检查
        cooldown = self.cfg["risk"]["signal_cooldown_days"]
        last = self.last_signal_date.get(sig_type.value)
        if last and (today - last).days <= cooldown:
            logger.info(f"{sig_type.value} 处于冷却期 (上次: {last})")
            return None

        # 6. 合约优选
        chain = self.dl.get_option_chain()
        contract = self._select_best_contract(chain, sig_type, spot)
        if contract is None:
            logger.warning(f"{sig_type.value} 无满足条件的合约")
            return None

        # 7. 仓位计算
        account = self.dl.get_account_info()
        quantity = self._calc_quantity(sig_type, contract, account)

        # 8. 构建信号
        mid = contract.mid_price
        stop_loss_pct = self.cfg["risk"]["stop_loss"]
        take_profit_pct = self.cfg["risk"]["take_profit"]

        signal = SignalDTO(
            type=sig_type,
            underlying=self.cfg["underlying"],
            contract_code=contract.contract_code,
            suggest_price=mid,
            stop_loss_price=round(mid * (1 + stop_loss_pct), 4),
            take_profit_price=round(mid * (1 + take_profit_pct), 4),
            suggested_quantity=quantity,
            expiry_date=contract.expiry_date,
            trigger_reason=TriggerReason(
                iv_percentile=round(iv_pct, 2),
                price_percentile=round(price_pct, 2),
                macd_hist=hist_values,
            ),
            status=SignalStatus.PENDING,
        )

        self.last_signal_date[sig_type.value] = today
        logger.info(
            f"生成信号: {sig_type.value} {contract.contract_code} "
            f"建议价={mid:.4f} 张数={quantity} 到期={contract.expiry_date}"
        )
        return signal

    def _match_signal(self, iv_pct: float, price_pct: float,
                      macd_trigger: bool) -> Optional[SignalType]:
        """条件匹配 - BUY_CALL优先级高于SELL_PUT"""
        buy_call_iv = self.cfg["iv_percentile"]["buy_call"]
        sell_put_iv = self.cfg["iv_percentile"]["sell_put"]
        price_threshold = self.cfg["price_percentile"]

        if iv_pct <= buy_call_iv and price_pct <= price_threshold and macd_trigger:
            return SignalType.BUY_CALL

        if iv_pct >= sell_put_iv and price_pct <= price_threshold:
            return SignalType.SELL_PUT

        return None

    def _select_best_contract(self, chain: List[OptionContract],
                              sig_type: SignalType,
                              spot: float) -> Optional[OptionContract]:
        """合约优选算法

        1. 到期日过滤: 剩余交易日 in [25, 35]
        2. 流动性过滤: volume >= min_volume, oi >= min_oi, spread <= 2 * tick_size
        3. 类型过滤: BUY_CALL -> CALL, SELL_PUT -> PUT
        4. Delta排序: |Delta - 0.5| 最小 (平值优先)
        5. 成交量排序: 降序取第1个
        """
        cfg_contract = self.cfg["contract"]
        min_days, max_days = cfg_contract["days_to_expiry_range"]
        min_vol = cfg_contract["min_volume"]
        min_oi = cfg_contract["min_oi"]
        target_delta = cfg_contract["target_delta"]
        r = self.cfg.get("risk_free_rate", 0.02)

        target_type = "CALL" if sig_type == SignalType.BUY_CALL else "PUT"
        today = date.today()

        candidates = []
        filter_log = {"total": len(chain), "type": 0, "expiry": 0, "liquidity": 0}

        for c in chain:
            if c.opt_type != target_type:
                continue
            filter_log["type"] += 1

            days_left = (c.expiry_date - today).days
            if not (min_days <= days_left <= max_days):
                continue
            filter_log["expiry"] += 1

            if c.volume < min_vol or c.oi < min_oi:
                continue
            if c.spread > 2 * c.tick_size and c.tick_size > 0:
                continue
            filter_log["liquidity"] += 1

            T = days_left / 365.0
            hist_vol = 0.3
            if c.mid_price > 0:
                iv = calc_iv(c.mid_price, spot, c.strike, T, r, c.opt_type)
                sigma = iv if iv is not None else hist_vol
            else:
                sigma = hist_vol

            delta = calc_delta(spot, c.strike, T, r, sigma, c.opt_type)
            c.delta = delta
            c.iv = sigma if c.mid_price > 0 else None
            candidates.append(c)

        if not candidates:
            logger.info(
                f"合约过滤结果: 总数={filter_log['total']}, "
                f"类型匹配={filter_log['type']}, "
                f"到期日匹配={filter_log['expiry']}, "
                f"流动性匹配={filter_log['liquidity']}"
            )
            return None

        candidates.sort(key=lambda c: abs(abs(c.delta or 0) - target_delta))

        top_n = candidates[:5]
        top_n.sort(key=lambda c: c.volume, reverse=True)

        best = top_n[0]
        logger.info(
            f"优选合约: {best.contract_code} K={best.strike} "
            f"Delta={best.delta:.3f} Vol={best.volume} OI={best.oi}"
        )
        return best

    def _calc_quantity(self, sig_type: SignalType, contract: OptionContract,
                       account: AccountInfo) -> int:
        """仓位计算: 总可买(卖)张数 / 10"""
        fraction = self.cfg["position"]["size_fraction"]
        cash = account.cash

        if sig_type == SignalType.BUY_CALL:
            cost_per_lot = contract.ask * contract.volume_multiple
            if cost_per_lot <= 0:
                return 1
            total_can_buy = math.floor(cash / cost_per_lot)
            quantity = max(1, math.floor(total_can_buy * fraction))
        else:
            if contract.estimated_margin and contract.estimated_margin > 0:
                margin_per_lot = contract.estimated_margin
            else:
                margin_per_lot = self._estimate_margin(contract)
            if margin_per_lot <= 0:
                return 1
            total_can_sell = math.floor(cash / margin_per_lot)
            quantity = max(1, math.floor(total_can_sell * fraction))

        return quantity

    @staticmethod
    def _estimate_margin(contract: OptionContract) -> float:
        """简化保证金估算 (卖方)

        期权卖方保证金 ≈ 权利金 + max(标的价值*12% - 虚值额, 标的价值*7%)
        这里使用简化公式进行冗余校验。
        """
        premium = contract.mid_price * contract.volume_multiple
        underlying_value = contract.strike * contract.volume_multiple

        if contract.opt_type == "PUT":
            otm = max(0, contract.strike - contract.last) * contract.volume_multiple
        else:
            otm = max(0, contract.last - contract.strike) * contract.volume_multiple

        margin = premium + max(underlying_value * 0.12 - otm, underlying_value * 0.07)
        return margin
