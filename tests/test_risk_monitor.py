"""RiskMonitor 单元测试"""

from datetime import date, timedelta

import pytest

from models.dto import (
    AccountInfo,
    Direction,
    PositionDTO,
    PositionStatus,
    RiskEventType,
)

CONFIG = {
    "broker": {"qmt_path": "", "account_id": "", "account_type": "STOCK"},
    "strategy": {
        "underlying": "510300.SH",
        "vix_code": "000188.SH",
        "lookback_days": 252,
        "risk": {
            "stop_loss": -0.50,
            "take_profit": 0.80,
            "margin_limit": 0.60,
            "signal_cooldown_days": 3,
            "expiry_warning_days": 3,
        },
    },
}


def _make_position(entry_price=0.15, days_to_expiry=20, margin=0) -> PositionDTO:
    return PositionDTO(
        contract_code="10001234.SHO",
        direction=Direction.LONG,
        entry_avg_price=entry_price,
        quantity=10,
        entry_date=date.today() - timedelta(days=10),
        expiry_date=date.today() + timedelta(days=days_to_expiry),
        margin_used=margin,
    )


class TestRiskRules:
    def setup_method(self):
        from core.data_loader import DataLoader
        from core.risk_monitor import RiskMonitor
        self.dl = DataLoader(CONFIG)
        self.rm = RiskMonitor(CONFIG, self.dl)

    def test_stop_loss(self):
        pos = _make_position(entry_price=0.20)
        event = self.rm._check_single(pos, current_mid=0.09, pnl_pct=-0.55)
        assert event is not None
        assert event.event_type == RiskEventType.STOP_LOSS

    def test_take_profit(self):
        pos = _make_position(entry_price=0.10)
        event = self.rm._check_single(pos, current_mid=0.19, pnl_pct=0.90)
        assert event is not None
        assert event.event_type == RiskEventType.TAKE_PROFIT

    def test_expiry_warning(self):
        pos = _make_position(days_to_expiry=2)
        event = self.rm._check_single(pos, current_mid=0.15, pnl_pct=0.0)
        assert event is not None
        assert event.event_type == RiskEventType.EXPIRY_WARNING

    def test_no_trigger(self):
        pos = _make_position()
        event = self.rm._check_single(pos, current_mid=0.15, pnl_pct=0.0)
        assert event is None

    def test_margin_breach(self):
        account = AccountInfo(cash=200000, total_asset=1000000)
        event = self.rm._check_margin(700000, account)
        assert event is not None
        assert event.event_type == RiskEventType.MARGIN_LIMIT

    def test_margin_ok(self):
        account = AccountInfo(cash=500000, total_asset=1000000)
        event = self.rm._check_margin(300000, account)
        assert event is None

    def test_priority_stop_loss_over_expiry(self):
        """止损优先于到期预警"""
        pos = _make_position(entry_price=0.20, days_to_expiry=2)
        event = self.rm._check_single(pos, current_mid=0.09, pnl_pct=-0.55)
        assert event.event_type == RiskEventType.STOP_LOSS
