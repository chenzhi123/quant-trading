"""JSON文件持久化存储 - 信号/持仓/回测状态跨重启保留"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, List

from loguru import logger

from models.dto import PositionDTO, SignalDTO

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SIGNALS_FILE = DATA_DIR / "signals.json"
POSITIONS_FILE = DATA_DIR / "positions.json"


def _json_serial(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def save_signals(signals: List[SignalDTO]) -> None:
    data = [s.model_dump(mode="json") for s in signals]
    SIGNALS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, default=_json_serial, indent=2),
        encoding="utf-8",
    )


def load_signals() -> List[SignalDTO]:
    if not SIGNALS_FILE.exists():
        return []
    try:
        data = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
        return [SignalDTO.model_validate(d) for d in data]
    except Exception as e:
        logger.warning(f"加载信号记录失败: {e}")
        return []


def save_positions(positions: List[PositionDTO]) -> None:
    data = [p.model_dump(mode="json") for p in positions]
    POSITIONS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, default=_json_serial, indent=2),
        encoding="utf-8",
    )


def load_positions() -> List[PositionDTO]:
    if not POSITIONS_FILE.exists():
        return []
    try:
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        return [PositionDTO.model_validate(d) for d in data]
    except Exception as e:
        logger.warning(f"加载持仓记录失败: {e}")
        return []
