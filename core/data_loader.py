"""数据加载模块 - 基于方正证券XtQuant API

依赖MiniQMT客户端运行，通过xtdata获取实时/历史行情。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from models.dto import AccountInfo, OptionContract

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


class DataLoader:
    """封装XtQuant xtdata模块，提供统一数据接口"""

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        self.underlying = config["strategy"]["underlying"]
        self.vix_code = config["strategy"]["vix_code"]
        self.lookback = config["strategy"]["lookback_days"]
        self._xtdata = None
        self._connected = False

    def connect(self) -> None:
        """连接MiniQMT数据服务"""
        try:
            from xtquant import xtdata
            self._xtdata = xtdata
            self._xtdata.connect()
            self._connected = True
            logger.info("XtQuant数据服务连接成功")
        except ImportError:
            logger.warning("xtquant未安装，将使用模拟数据模式")
            self._connected = False
        except Exception as e:
            logger.error(f"XtQuant连接失败: {e}")
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── ETF行情 ──────────────────────────────────────────────

    def get_etf_realtime(self) -> Dict[str, float]:
        """获取沪深300ETF实时tick"""
        if not self._connected:
            return self._mock_etf_realtime()

        tick = self._xtdata.get_full_tick([self.underlying])
        if self.underlying in tick:
            t = tick[self.underlying]
            return {
                "last": float(t.get("lastPrice", 0)),
                "bid1": float(t.get("bidPrice", [0])[0]) if isinstance(t.get("bidPrice"), list) else float(t.get("bidPrice", 0)),
                "ask1": float(t.get("askPrice", [0])[0]) if isinstance(t.get("askPrice"), list) else float(t.get("askPrice", 0)),
                "volume": int(t.get("volume", 0)),
                "amount": float(t.get("amount", 0)),
            }
        logger.warning(f"未获取到{self.underlying}的实时数据")
        return self._mock_etf_realtime()

    def get_etf_history(self, days: Optional[int] = None) -> pd.DataFrame:
        """获取ETF历史日线"""
        days = days or self.lookback
        cache_file = DATA_DIR / f"etf_{self.underlying}_{_today_str()}.csv"

        if cache_file.exists():
            df = pd.read_csv(cache_file, parse_dates=["date"])
            if len(df) >= days:
                return df.tail(days).reset_index(drop=True)

        if not self._connected:
            return self._mock_etf_history(days)

        self._xtdata.download_history_data(self.underlying, period="1d", incrementally=True)
        raw = self._xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[self.underlying],
            period="1d",
            count=days + 50,
        )
        if self.underlying not in raw or raw[self.underlying].empty:
            logger.warning("ETF历史数据获取失败，使用模拟数据")
            return self._mock_etf_history(days)

        df = raw[self.underlying].copy()
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        rename_map = {"time": "date", "index": "date"}
        df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)
        if "date" not in df.columns:
            df["date"] = df.index

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = 0.0

        df["date"] = pd.to_datetime(df["date"])
        df = df[["date", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)
        df.to_csv(cache_file, index=False)
        return df

    # ── 波动率指数(iVIX) ────────────────────────────────────

    def get_vix_realtime(self) -> float:
        """获取iVIX实时值"""
        if not self._connected:
            return self._mock_vix_realtime()

        tick = self._xtdata.get_full_tick([self.vix_code])
        if self.vix_code in tick:
            return float(tick[self.vix_code].get("lastPrice", 0))

        logger.warning(f"无法订阅{self.vix_code}，尝试备选方案")
        return self._calc_vix_from_options()

    def get_vix_history(self, days: Optional[int] = None) -> pd.Series:
        """获取近1年iVIX历史序列"""
        days = days or self.lookback
        cache_file = DATA_DIR / f"vix_{self.vix_code}_{_today_str()}.csv"

        if cache_file.exists():
            df = pd.read_csv(cache_file, parse_dates=["date"])
            if len(df) >= days:
                return df.set_index("date")["close"].tail(days)

        if not self._connected:
            return self._mock_vix_history(days)

        self._xtdata.download_history_data(self.vix_code, period="1d", incrementally=True)
        raw = self._xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[self.vix_code],
            period="1d",
            count=days + 50,
        )
        if self.vix_code in raw and not raw[self.vix_code].empty:
            df = raw[self.vix_code].copy().reset_index()
            df.columns = [c.lower() for c in df.columns]
            rename_map = {"time": "date", "index": "date"}
            df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)
            if "date" not in df.columns:
                df["date"] = df.index
            df["date"] = pd.to_datetime(df["date"])
            df = df[["date", "close"]].dropna().tail(days).reset_index(drop=True)
            df.to_csv(cache_file, index=False)
            return df.set_index("date")["close"]

        logger.warning("VIX历史数据不可用，使用ETF历史波动率替代")
        return self._calc_hv_as_vix(days)

    # ── 期权链 ──────────────────────────────────────────────

    def get_option_chain(self) -> List[OptionContract]:
        """获取当前期权链全部合约实时数据"""
        if not self._connected:
            return self._mock_option_chain()

        contracts: List[OptionContract] = []
        try:
            undl_data = self._xtdata.get_option_undl_data(self.underlying)
            if not undl_data:
                logger.warning("期权品种列表为空")
                return self._mock_option_chain()

            all_codes: List[str] = []
            if isinstance(undl_data, dict):
                for month_codes in undl_data.values():
                    if isinstance(month_codes, list):
                        all_codes.extend(month_codes)
            elif isinstance(undl_data, list):
                all_codes = undl_data

            if not all_codes:
                return self._mock_option_chain()

            ticks = self._xtdata.get_full_tick(all_codes)

            for code in all_codes:
                try:
                    detail = self._xtdata.get_option_detail_data(code)
                    if not detail:
                        continue

                    tick_data = ticks.get(code, {})
                    bid_prices = tick_data.get("bidPrice", [0])
                    ask_prices = tick_data.get("askPrice", [0])
                    bid1 = float(bid_prices[0]) if isinstance(bid_prices, list) else float(bid_prices)
                    ask1 = float(ask_prices[0]) if isinstance(ask_prices, list) else float(ask_prices)

                    expiry_raw = detail.get("ExpireDate", "")
                    if isinstance(expiry_raw, str) and len(expiry_raw) >= 8:
                        expiry = date(int(expiry_raw[:4]), int(expiry_raw[4:6]), int(expiry_raw[6:8]))
                    elif isinstance(expiry_raw, (int, float)):
                        expiry = datetime.fromtimestamp(expiry_raw / 1000).date() if expiry_raw > 1e9 else date.today()
                    else:
                        continue

                    contract = OptionContract(
                        contract_code=code,
                        underlying=detail.get("OptUndlCode", self.underlying),
                        opt_type="CALL" if detail.get("optType", "").upper() in ("CALL", "C", "认购") else "PUT",
                        strike=float(detail.get("OptExercisePrice", 0)),
                        expiry_date=expiry,
                        tick_size=float(detail.get("PriceTick", 0.0001)),
                        volume_multiple=int(detail.get("VolumeMultiple", 10000)),
                        bid=bid1,
                        ask=ask1,
                        last=float(tick_data.get("lastPrice", 0)),
                        volume=int(tick_data.get("volume", 0)),
                        oi=int(tick_data.get("openInterest", 0)),
                        estimated_margin=detail.get("OptEstimatedMargin"),
                    )
                    contracts.append(contract)
                except Exception as e:
                    logger.debug(f"解析合约{code}失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"获取期权链失败: {e}")
            return self._mock_option_chain()

        logger.info(f"获取期权链合约 {len(contracts)} 个")
        return contracts

    # ── 账户信息 ─────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        """查询账户资产"""
        account_id = self.cfg["broker"].get("account_id", "")
        if not self._connected or not account_id:
            return AccountInfo(cash=500000, total_asset=1000000)

        try:
            from xtquant import xttrader
            trader = xttrader.XtQuantTrader(
                self.cfg["broker"]["qmt_path"], account_id
            )
            asset = trader.query_stock_asset()
            if asset:
                return AccountInfo(
                    cash=asset.cash,
                    total_asset=asset.total_asset,
                    frozen_cash=asset.frozen_cash,
                    market_value=asset.market_value,
                )
        except Exception as e:
            logger.error(f"账户查询失败: {e}")

        return AccountInfo(cash=500000, total_asset=1000000)

    def get_option_realtime(self, contract_code: str) -> Dict[str, float]:
        """获取单个期权合约实时报价"""
        if not self._connected:
            return {"bid1": 0.15, "ask1": 0.16, "last": 0.155, "volume": 1000}

        tick = self._xtdata.get_full_tick([contract_code])
        if contract_code in tick:
            t = tick[contract_code]
            bid_prices = t.get("bidPrice", [0])
            ask_prices = t.get("askPrice", [0])
            return {
                "bid1": float(bid_prices[0]) if isinstance(bid_prices, list) else float(bid_prices),
                "ask1": float(ask_prices[0]) if isinstance(ask_prices, list) else float(ask_prices),
                "last": float(t.get("lastPrice", 0)),
                "volume": int(t.get("volume", 0)),
            }
        return {"bid1": 0.0, "ask1": 0.0, "last": 0.0, "volume": 0}

    # ── 备选方案 ─────────────────────────────────────────────

    def _calc_vix_from_options(self) -> float:
        """从期权链计算近似VIX（方差互换法简化版）"""
        try:
            chain = self.get_option_chain()
            if not chain:
                return self._mock_vix_realtime()

            etf = self.get_etf_realtime()
            spot = etf.get("last", 4.0)
            r = self.cfg["strategy"].get("risk_free_rate", 0.02)

            from core.greeks import calc_iv
            ivs = []
            for c in chain:
                if c.mid_price > 0:
                    days_left = (c.expiry_date - date.today()).days
                    if 10 < days_left < 60:
                        T = days_left / 365.0
                        iv = calc_iv(c.mid_price, spot, c.strike, T, r, c.opt_type)
                        if iv is not None and 0.05 < iv < 2.0:
                            ivs.append(iv)
            if ivs:
                return float(np.mean(ivs) * 100)
        except Exception as e:
            logger.error(f"期权链VIX计算失败: {e}")

        return self._mock_vix_realtime()

    def _calc_hv_as_vix(self, days: int) -> pd.Series:
        """使用标的ETF历史波动率替代VIX"""
        etf_hist = self.get_etf_history(days + 30)
        returns = np.log(etf_hist["close"] / etf_hist["close"].shift(1)).dropna()
        hv20 = returns.rolling(20).std() * np.sqrt(252) * 100
        hv20.index = etf_hist["date"].iloc[1: len(returns) + 1]
        return hv20.dropna().tail(days)

    # ── 模拟数据（开发/测试用） ──────────────────────────────

    def _mock_etf_realtime(self) -> Dict[str, float]:
        import random
        base = 3.95 + random.gauss(0, 0.005)
        base = round(base, 4)
        return {
            "last": base,
            "bid1": round(base - 0.001, 4),
            "ask1": round(base + 0.001, 4),
            "volume": 50000000 + random.randint(-5000000, 5000000),
            "amount": 1e9,
        }

    def _mock_vix_realtime(self) -> float:
        import random
        return round(18.5 + random.gauss(0, 0.3), 2)

    def _mock_etf_history(self, days: int) -> pd.DataFrame:
        np.random.seed(42)
        dates = pd.bdate_range(end=pd.Timestamp.now(), periods=days, freq="B")
        close = 4.0 + np.cumsum(np.random.randn(days) * 0.02)
        close = np.maximum(close, 2.5)
        return pd.DataFrame({
            "date": dates,
            "open": close + np.random.randn(days) * 0.005,
            "high": close + abs(np.random.randn(days) * 0.01),
            "low": close - abs(np.random.randn(days) * 0.01),
            "close": close,
            "volume": np.random.randint(30_000_000, 80_000_000, days),
        })

    def _mock_vix_history(self, days: int) -> pd.Series:
        np.random.seed(123)
        dates = pd.bdate_range(end=pd.Timestamp.now(), periods=days, freq="B")
        vix = 20 + np.cumsum(np.random.randn(days) * 0.5)
        vix = np.clip(vix, 10, 50)
        return pd.Series(vix, index=dates, name="vix")

    def _mock_option_chain(self) -> List[OptionContract]:
        spot = 3.95
        today = date.today()
        expiry1 = today + timedelta(days=30)
        expiry2 = today + timedelta(days=60)
        contracts = []

        for exp in [expiry1, expiry2]:
            for opt_type in ["CALL", "PUT"]:
                for i, offset in enumerate([-0.2, -0.1, 0, 0.1, 0.2]):
                    strike = round(spot + offset, 2)
                    base_price = max(0.01, 0.15 - abs(offset) * 0.3)
                    code = f"1000{i}{0 if opt_type == 'CALL' else 5}{exp.strftime('%y%m')}.SHO"
                    contracts.append(OptionContract(
                        contract_code=code,
                        underlying="510300.SH",
                        opt_type=opt_type,
                        strike=strike,
                        expiry_date=exp,
                        tick_size=0.0001,
                        volume_multiple=10000,
                        bid=round(base_price - 0.002, 4),
                        ask=round(base_price + 0.002, 4),
                        last=round(base_price, 4),
                        volume=np.random.randint(500, 5000),
                        oi=np.random.randint(1000, 10000),
                    ))
        return contracts
