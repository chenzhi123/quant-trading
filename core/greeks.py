"""Black-Scholes Greeks本地计算模块

XtQuant不直接提供Delta等Greeks指标，需通过BS模型本地计算。
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = "CALL") -> float:
    """BS模型理论价格"""
    if T <= 0:
        if option_type.upper() == "CALL":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)

    if option_type.upper() == "CALL":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def calc_delta(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = "CALL") -> float:
    """计算单个合约的Delta"""
    if T <= 0 or sigma <= 0:
        if option_type.upper() == "CALL":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0

    d1 = _d1(S, K, T, r, sigma)
    if option_type.upper() == "CALL":
        return norm.cdf(d1)
    return norm.cdf(d1) - 1.0


def calc_iv(market_price: float, S: float, K: float, T: float, r: float,
            option_type: str = "CALL", max_iter: int = 100,
            tol: float = 1e-6) -> Optional[float]:
    """牛顿法反算隐含波动率"""
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None

    intrinsic = max(S - K, 0.0) if option_type.upper() == "CALL" else max(K - S, 0.0)
    if market_price < intrinsic - tol:
        return None

    sigma = 0.3  # 初始猜测
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type)
        vega = _vega(S, K, T, r, sigma)
        if vega < 1e-12:
            break
        diff = price - market_price
        if abs(diff) < tol:
            return sigma
        sigma -= diff / vega
        if sigma <= 0.001:
            sigma = 0.001
        if sigma > 5.0:
            return None
    return sigma if abs(bs_price(S, K, T, r, sigma, option_type) - market_price) < tol * 100 else None


def _vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return S * math.sqrt(T) * norm.pdf(d1)


def enrich_greeks(option_chain: pd.DataFrame, spot_price: float,
                  risk_free_rate: float) -> pd.DataFrame:
    """批量为期权链DataFrame添加delta和iv列

    要求输入DataFrame含列: strike, expiry_date, opt_type, mid_price(或bid+ask)
    以及一个 years_to_expiry 列（年化剩余时间）
    """
    df = option_chain.copy()

    deltas = []
    ivs = []

    for _, row in df.iterrows():
        S = spot_price
        K = row["strike"]
        T = row.get("years_to_expiry", 0.0)
        opt_type = row["opt_type"]

        mid = row.get("mid_price", 0.0)
        if mid <= 0:
            bid = row.get("bid", 0.0)
            ask = row.get("ask", 0.0)
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0

        iv = calc_iv(mid, S, K, T, risk_free_rate, opt_type) if mid > 0 and T > 0 else None
        sigma_for_delta = iv if iv is not None else row.get("hist_vol", 0.3)
        delta = calc_delta(S, K, T, risk_free_rate, sigma_for_delta, opt_type)

        deltas.append(delta)
        ivs.append(iv)

    df["delta"] = deltas
    df["iv"] = ivs
    return df
