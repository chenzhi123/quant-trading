"""风控监控模块 - 持仓盈亏/保证金/到期监控"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from loguru import logger

from core.data_loader import DataLoader
from models.dto import (
    AccountInfo,
    PositionDTO,
    PositionStatus,
    RiskEvent,
    RiskEventType,
)


class RiskMonitor:
    """实时风控监控器

    基于实时mid价检查四条风控规则:
    1. 止损: PnL% <= -50%
    2. 止盈: PnL% >= +80%
    3. 到期预警: 剩余天数 <= 3
    4. 保证金熔断: 卖方保证金占用 / 净资产 >= 60%
    """

    def __init__(self, config: Dict[str, Any], data_loader: DataLoader):
        risk_cfg = config["strategy"]["risk"]
        self.stop_loss = risk_cfg["stop_loss"]
        self.take_profit = risk_cfg["take_profit"]
        self.margin_limit = risk_cfg["margin_limit"]
        self.expiry_days = risk_cfg["expiry_warning_days"]
        self.dl = data_loader
        self._margin_breached = False

    def check_all_positions(self, positions: List[PositionDTO],
                            account: Optional[AccountInfo] = None) -> List[RiskEvent]:
        """检查所有活跃持仓的风控状态"""
        events: List[RiskEvent] = []
        if account is None:
            account = self.dl.get_account_info()

        total_margin = 0.0

        for pos in positions:
            if pos.status != PositionStatus.ACTIVE:
                continue

            rt = self.dl.get_option_realtime(pos.contract_code)
            bid1 = rt.get("bid1", 0)
            ask1 = rt.get("ask1", 0)
            current_mid = (bid1 + ask1) / 2 if bid1 > 0 and ask1 > 0 else rt.get("last", 0)

            if current_mid <= 0:
                logger.warning(f"无法获取{pos.contract_code}实时报价")
                continue

            pnl_pct = (current_mid - pos.entry_avg_price) / abs(pos.entry_avg_price)
            pos.current_pnl_pct = pnl_pct

            total_margin += pos.margin_used

            event = self._check_single(pos, current_mid, pnl_pct)
            if event:
                events.append(event)

        margin_event = self._check_margin(total_margin, account)
        if margin_event:
            events.append(margin_event)

        return events

    def _check_single(self, pos: PositionDTO, current_mid: float,
                      pnl_pct: float) -> Optional[RiskEvent]:
        """检查单个持仓的止损/止盈/到期"""
        today = date.today()

        if pnl_pct <= self.stop_loss:
            logger.warning(
                f"止损触发: {pos.contract_code} PnL={pnl_pct:.2%} "
                f"(阈值={self.stop_loss:.0%})"
            )
            return RiskEvent(
                event_type=RiskEventType.STOP_LOSS,
                contract_code=pos.contract_code,
                triggered_value=round(pnl_pct, 4),
                threshold=self.stop_loss,
                message=(
                    f"[止损提示] {pos.contract_code} "
                    f"当前盈亏 {pnl_pct:.2%}，已触及止损线 {self.stop_loss:.0%}，"
                    f"当前中间价 {current_mid:.4f}，开仓均价 {pos.entry_avg_price:.4f}"
                ),
            )

        if pnl_pct >= self.take_profit:
            logger.info(
                f"止盈触发: {pos.contract_code} PnL={pnl_pct:.2%} "
                f"(阈值={self.take_profit:.0%})"
            )
            return RiskEvent(
                event_type=RiskEventType.TAKE_PROFIT,
                contract_code=pos.contract_code,
                triggered_value=round(pnl_pct, 4),
                threshold=self.take_profit,
                message=(
                    f"[止盈提示] {pos.contract_code} "
                    f"当前盈亏 {pnl_pct:.2%}，已触及止盈线 {self.take_profit:.0%}，"
                    f"建议平仓锁定利润"
                ),
            )

        days_left = (pos.expiry_date - today).days
        if days_left <= self.expiry_days:
            logger.warning(
                f"到期预警: {pos.contract_code} 剩余{days_left}天"
            )
            return RiskEvent(
                event_type=RiskEventType.EXPIRY_WARNING,
                contract_code=pos.contract_code,
                triggered_value=float(days_left),
                threshold=float(self.expiry_days),
                message=(
                    f"[到期预警] {pos.contract_code} "
                    f"距到期仅剩 {days_left} 天(到期日: {pos.expiry_date})，"
                    f"请考虑平仓或移仓至下月合约"
                ),
            )

        return None

    def _check_margin(self, total_margin: float,
                      account: AccountInfo) -> Optional[RiskEvent]:
        """检查保证金占用比例"""
        if account.total_asset <= 0:
            return None

        ratio = total_margin / account.total_asset
        if ratio >= self.margin_limit:
            self._margin_breached = True
            logger.critical(
                f"保证金熔断: 占用比={ratio:.2%} (阈值={self.margin_limit:.0%})"
            )
            return RiskEvent(
                event_type=RiskEventType.MARGIN_LIMIT,
                contract_code="ACCOUNT",
                triggered_value=round(ratio, 4),
                threshold=self.margin_limit,
                message=(
                    f"[保证金熔断] 卖方保证金占用 {ratio:.2%} "
                    f"(阈值 {self.margin_limit:.0%})，"
                    f"已禁止新开卖方仓位，请立即减仓"
                ),
            )

        if self._margin_breached and ratio < self.margin_limit:
            self._margin_breached = False
            logger.info("保证金恢复正常水平")

        return None

    @property
    def is_margin_breached(self) -> bool:
        return self._margin_breached
