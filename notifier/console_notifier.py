"""控制台通知模块 - 信号与风控事件的结构化输出"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from loguru import logger

from models.dto import RiskEvent, SignalDTO

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class ConsoleNotifier:
    """控制台+结构化JSON日志通知器"""

    def __init__(self):
        self._event_log = LOG_DIR / "events.jsonl"

    def notify_signal(self, signal: SignalDTO) -> None:
        """推送信号通知"""
        msg = (
            f"\n{'='*60}\n"
            f"  [新信号] {signal.type.value}\n"
            f"  合约: {signal.contract_code}\n"
            f"  建议价: {signal.suggest_price:.4f}\n"
            f"  止损价: {signal.stop_loss_price:.4f}\n"
            f"  止盈价: {signal.take_profit_price:.4f}\n"
            f"  建议张数: {signal.suggested_quantity}\n"
            f"  到期日: {signal.expiry_date}\n"
            f"  触发原因: iVIX分位={signal.trigger_reason.iv_percentile:.1f}% "
            f"价格分位={signal.trigger_reason.price_percentile:.1f}%\n"
            f"  状态: {signal.status.value} (请确认 [已执行]/[暂不执行])\n"
            f"{'='*60}"
        )
        logger.info(msg)
        self._write_event("SIGNAL", signal.model_dump(mode="json"))

    def notify_risk(self, event: RiskEvent) -> None:
        """推送风控事件通知"""
        msg = (
            f"\n{'!'*60}\n"
            f"  [风控] {event.event_type.value}\n"
            f"  {event.message}\n"
            f"{'!'*60}"
        )
        if event.event_type.value in ("STOP_LOSS_TRIGGERED", "MARGIN_LIMIT_BREACH"):
            logger.critical(msg)
        else:
            logger.warning(msg)

        self._write_event("RISK", event.model_dump(mode="json"))

    def notify_backtest(self, summary: Dict[str, Any]) -> None:
        """推送回测结果"""
        logger.info(f"\n[回测完成] {json.dumps(summary, ensure_ascii=False, indent=2)}")
        self._write_event("BACKTEST", summary)

    def notify_user_action(self, signal_id: str, action: str) -> None:
        """记录用户确认操作"""
        logger.info(f"[用户操作] 信号 {signal_id} -> {action}")
        self._write_event("USER_ACTION", {"signal_id": signal_id, "action": action})

    def _write_event(self, event_type: str, payload: Any) -> None:
        record = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        with open(self._event_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
