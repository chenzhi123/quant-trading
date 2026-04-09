"""IndicatorEngine 单元测试"""

import numpy as np
import pandas as pd
import pytest

from core.indicators import IndicatorEngine

CONFIG = {
    "strategy": {
        "lookback_days": 252,
        "macd": {"fast": 12, "slow": 26, "signal": 9, "min_expand_days": 2},
    }
}


@pytest.fixture
def engine():
    return IndicatorEngine(CONFIG)


class TestRollingPercentile:
    def test_basic(self):
        s = pd.Series(range(1, 254))
        result = IndicatorEngine.calc_rolling_percentile(s, window=252)
        last = result.iloc[-1]
        assert last == pytest.approx(100.0, abs=1)

    def test_low_value(self):
        data = list(range(1, 253))
        data.append(0)
        s = pd.Series(data)
        result = IndicatorEngine.calc_rolling_percentile(s, window=252)
        assert result.iloc[-1] == pytest.approx(0.0, abs=1)

    def test_mid_value(self):
        data = list(range(1, 253))
        data.append(126)
        s = pd.Series(data)
        result = IndicatorEngine.calc_rolling_percentile(s, window=252)
        assert 45 < result.iloc[-1] < 55


class TestCurrentPercentile:
    def test_extreme_low(self):
        history = pd.Series(range(10, 110))
        pct = IndicatorEngine.calc_current_percentile(history, 5)
        assert pct == 0.0

    def test_extreme_high(self):
        history = pd.Series(range(10, 110))
        pct = IndicatorEngine.calc_current_percentile(history, 200)
        assert pct == 100.0

    def test_mid(self):
        history = pd.Series(range(0, 100))
        pct = IndicatorEngine.calc_current_percentile(history, 50)
        assert 45 < pct < 55

    def test_empty(self):
        pct = IndicatorEngine.calc_current_percentile(pd.Series(dtype=float), 10)
        assert pct == 50.0


class TestMACD:
    def test_macd_columns(self, engine):
        np.random.seed(42)
        df = pd.DataFrame({
            "close": 4.0 + np.cumsum(np.random.randn(100) * 0.02),
            "date": pd.bdate_range("2025-01-01", periods=100),
        })
        result = engine.calc_macd(df)
        for col in ["dif", "dea", "hist", "hist_flip", "hist_expanding", "macd_trigger"]:
            assert col in result.columns

    def test_hist_flip_detection(self, engine):
        close = [4.0] * 30
        for i in range(30):
            close.append(close[-1] - 0.01)
        for i in range(30):
            close.append(close[-1] + 0.015)

        df = pd.DataFrame({
            "close": close,
            "date": pd.bdate_range("2025-01-01", periods=len(close)),
        })
        result = engine.calc_macd(df)
        assert result["hist_flip"].any()

    def test_trigger_is_bool(self, engine):
        df = pd.DataFrame({
            "close": 4.0 + np.cumsum(np.random.randn(100) * 0.02),
            "date": pd.bdate_range("2025-01-01", periods=100),
        })
        result = engine.calc_macd(df)
        assert result["macd_trigger"].dtype == bool
