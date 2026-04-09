"""指标计算引擎 - 滚动分位数与MACD信号"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


class IndicatorEngine:
    """负责计算iVIX分位数、价格分位数和MACD信号"""

    def __init__(self, config: Dict[str, Any]):
        self.lookback = config["strategy"]["lookback_days"]
        macd_cfg = config["strategy"]["macd"]
        self.fast = macd_cfg["fast"]
        self.slow = macd_cfg["slow"]
        self.signal = macd_cfg["signal"]
        self.min_expand = macd_cfg["min_expand_days"]

    # ── 分位数计算 ──────────────────────────────────────────

    @staticmethod
    def calc_rolling_percentile(series: pd.Series, window: int = 252) -> pd.Series:
        """滚动历史分位数 (0~100)

        公式: (窗口内 <= 当前值的样本数) / window * 100
        """
        def _pct(x: np.ndarray) -> float:
            return float(np.sum(x[:-1] <= x[-1]) / (len(x) - 1) * 100)

        return series.rolling(window + 1).apply(_pct, raw=True)

    @staticmethod
    def calc_current_percentile(history: pd.Series, current_value: float) -> float:
        """计算当前实时值在历史序列中的分位数

        用于盘中实时判断，给定近1年历史序列和当前实时点位。
        """
        if history.empty:
            return 50.0
        count_le = int(np.sum(history.values <= current_value))
        return count_le / len(history) * 100

    # ── MACD计算 ────────────────────────────────────────────

    def calc_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算MACD柱状图及翻红放大状态

        输入df需包含'close'列，输出添加:
        - dif, dea, hist: MACD三元素
        - hist_flip: 柱状图由负转正
        - hist_expanding: 连续N日放大
        - macd_trigger: hist_flip AND hist_expanding
        """
        result = df.copy()
        close = result["close"]

        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        result["dif"] = ema_fast - ema_slow
        result["dea"] = result["dif"].ewm(span=self.signal, adjust=False).mean()
        result["hist"] = result["dif"] - result["dea"]

        result["hist_flip"] = (result["hist"] > 0) & (result["hist"].shift(1) <= 0)

        expanding = pd.Series(True, index=result.index)
        for i in range(1, self.min_expand + 1):
            expanding = expanding & (result["hist"].shift(i - 1) > result["hist"].shift(i))
        result["hist_expanding"] = expanding

        result["macd_trigger"] = result["hist_flip"] & result["hist_expanding"]

        return result

    def get_latest_macd_trigger(self, df: pd.DataFrame) -> bool:
        """判断最新一根K线是否触发MACD条件"""
        macd_df = self.calc_macd(df)
        if macd_df.empty:
            return False
        return bool(macd_df["macd_trigger"].iloc[-1])

    def get_latest_hist_values(self, df: pd.DataFrame, n: int = 5) -> list:
        """获取最近N根MACD柱状图值"""
        macd_df = self.calc_macd(df)
        return macd_df["hist"].tail(n).tolist()
