"""核心数据传输对象定义"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    BUY_CALL = "BUY_CALL"
    SELL_PUT = "SELL_PUT"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    IGNORED = "IGNORED"
    EXPIRED = "EXPIRED"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class RiskEventType(str, Enum):
    STOP_LOSS = "STOP_LOSS_TRIGGERED"
    TAKE_PROFIT = "TAKE_PROFIT_TRIGGERED"
    EXPIRY_WARNING = "EXPIRY_WARNING"
    MARGIN_LIMIT = "MARGIN_LIMIT_BREACH"


class TriggerReason(BaseModel):
    iv_percentile: float
    price_percentile: float
    macd_hist: List[float] = Field(default_factory=list)


class SignalDTO(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    type: SignalType
    underlying: str = "510300.SH"
    contract_code: str
    suggest_price: float
    stop_loss_price: float
    take_profit_price: float
    suggested_quantity: int = 1
    expiry_date: date
    trigger_reason: TriggerReason
    status: SignalStatus = SignalStatus.PENDING


class PositionDTO(BaseModel):
    contract_code: str
    direction: Direction
    entry_avg_price: float
    quantity: int
    entry_date: date
    expiry_date: date
    current_pnl_pct: float = 0.0
    margin_used: float = 0.0
    status: PositionStatus = PositionStatus.ACTIVE


class RiskEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: RiskEventType
    contract_code: str
    triggered_value: float
    threshold: float
    message: str


class AccountInfo(BaseModel):
    """账户资产快照"""
    cash: float = 0.0
    total_asset: float = 0.0
    frozen_cash: float = 0.0
    market_value: float = 0.0


class OptionContract(BaseModel):
    """标准化期权合约信息"""
    contract_code: str
    underlying: str
    opt_type: str  # CALL / PUT
    strike: float
    expiry_date: date
    tick_size: float = 0.0001
    volume_multiple: int = 10000
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    oi: int = 0
    delta: Optional[float] = None
    iv: Optional[float] = None
    estimated_margin: Optional[float] = None

    @property
    def mid_price(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return float("inf")
