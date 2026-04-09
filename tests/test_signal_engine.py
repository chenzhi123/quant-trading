"""SignalEngine 单元测试"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.data_loader import DataLoader
from core.indicators import IndicatorEngine
from core.signal_engine import SignalEngine
from models.dto import OptionContract, SignalType

CONFIG = {
    "broker": {"qmt_path": "", "account_id": "", "account_type": "STOCK"},
    "strategy": {
        "underlying": "510300.SH",
        "vix_code": "000188.SH",
        "lookback_days": 252,
        "iv_percentile": {"buy_call": 10, "sell_put": 90},
        "price_percentile": 10,
        "macd": {"fast": 12, "slow": 26, "signal": 9, "min_expand_days": 2},
        "contract": {
            "min_volume": 500,
            "min_oi": 1000,
            "target_delta": 0.5,
            "days_to_expiry_range": [25, 35],
        },
        "risk": {
            "stop_loss": -0.50,
            "take_profit": 0.80,
            "margin_limit": 0.60,
            "signal_cooldown_days": 3,
            "expiry_warning_days": 3,
        },
        "cost": {
            "commission_per_lot": 2.0,
            "slippage_tick_buy": 1,
            "slippage_tick_sell": 2,
        },
        "position": {"size_fraction": 0.1},
        "risk_free_rate": 0.02,
    },
}


class TestSignalMatching:
    def setup_method(self):
        self.dl = DataLoader(CONFIG)
        self.ie = IndicatorEngine(CONFIG)
        self.se = SignalEngine(CONFIG, self.dl, self.ie)

    def test_buy_call_condition(self):
        result = self.se._match_signal(iv_pct=5.0, price_pct=5.0, macd_trigger=True)
        assert result == SignalType.BUY_CALL

    def test_sell_put_condition(self):
        result = self.se._match_signal(iv_pct=95.0, price_pct=5.0, macd_trigger=False)
        assert result == SignalType.SELL_PUT

    def test_no_signal(self):
        result = self.se._match_signal(iv_pct=50.0, price_pct=50.0, macd_trigger=False)
        assert result is None

    def test_buy_call_priority(self):
        """BUY_CALL优先级高于SELL_PUT（理论上不会同时满足）"""
        result = self.se._match_signal(iv_pct=5.0, price_pct=5.0, macd_trigger=True)
        assert result == SignalType.BUY_CALL

    def test_macd_required_for_buy_call(self):
        result = self.se._match_signal(iv_pct=5.0, price_pct=5.0, macd_trigger=False)
        assert result is None

    def test_macd_not_required_for_sell_put(self):
        result = self.se._match_signal(iv_pct=95.0, price_pct=5.0, macd_trigger=False)
        assert result == SignalType.SELL_PUT


class TestContractSelection:
    def setup_method(self):
        self.dl = DataLoader(CONFIG)
        self.ie = IndicatorEngine(CONFIG)
        self.se = SignalEngine(CONFIG, self.dl, self.ie)

    def _make_chain(self, days_offset=30, volume=1000, oi=2000) -> list:
        expiry = date.today() + timedelta(days=days_offset)
        return [
            OptionContract(
                contract_code="10001234.SHO",
                underlying="510300.SH",
                opt_type="CALL",
                strike=3.95,
                expiry_date=expiry,
                tick_size=0.0001,
                volume_multiple=10000,
                bid=0.1500,
                ask=0.1501,
                last=0.1500,
                volume=volume,
                oi=oi,
            )
        ]

    def test_valid_contract(self):
        chain = self._make_chain(days_offset=30, volume=1000, oi=2000)
        result = self.se._select_best_contract(chain, SignalType.BUY_CALL, 3.95)
        assert result is not None
        assert result.contract_code == "10001234.SHO"

    def test_expiry_filter(self):
        chain = self._make_chain(days_offset=10)
        result = self.se._select_best_contract(chain, SignalType.BUY_CALL, 3.95)
        assert result is None

    def test_volume_filter(self):
        chain = self._make_chain(volume=100)
        result = self.se._select_best_contract(chain, SignalType.BUY_CALL, 3.95)
        assert result is None

    def test_oi_filter(self):
        chain = self._make_chain(oi=500)
        result = self.se._select_best_contract(chain, SignalType.BUY_CALL, 3.95)
        assert result is None

    def test_type_filter(self):
        chain = self._make_chain()
        result = self.se._select_best_contract(chain, SignalType.SELL_PUT, 3.95)
        assert result is None


class TestPositionSizing:
    def setup_method(self):
        self.dl = DataLoader(CONFIG)
        self.ie = IndicatorEngine(CONFIG)
        self.se = SignalEngine(CONFIG, self.dl, self.ie)

    def test_buy_call_quantity(self):
        from models.dto import AccountInfo
        contract = OptionContract(
            contract_code="test",
            underlying="510300.SH",
            opt_type="CALL",
            strike=3.95,
            expiry_date=date.today() + timedelta(days=30),
            volume_multiple=10000,
            bid=0.148,
            ask=0.150,
            last=0.149,
        )
        account = AccountInfo(cash=500000, total_asset=1000000)
        qty = self.se._calc_quantity(SignalType.BUY_CALL, contract, account)
        expected_total = 500000 // (0.150 * 10000)
        expected_qty = max(1, int(expected_total * 0.1))
        assert qty == expected_qty
