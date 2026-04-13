"""OptionGuard Web应用 - 完整的可分享期权纪律交易系统"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.store import load_positions, load_signals, save_positions, save_signals
from models.dto import (
    AccountInfo,
    Direction,
    PositionDTO,
    PositionStatus,
    RiskEvent,
    SignalDTO,
    SignalStatus,
)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = Path(__file__).parent.parent / "data"

app = FastAPI(title="OptionGuard - 期权纪律交易系统")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class AppState:
    """全局应用状态"""

    def __init__(self):
        self.signals: List[SignalDTO] = load_signals()
        self.positions: List[PositionDTO] = load_positions()
        self.risk_events: List[RiskEvent] = []
        self.account: AccountInfo = AccountInfo(cash=500000, total_asset=1000000)
        self.backtest_result: Optional[Any] = None
        self.backtest_chart: Optional[str] = None

        self.etf_price: float = 0.0
        self.vix_value: float = 0.0
        self.price_pct: float = 50.0
        self.vix_pct: float = 50.0
        self.macd_trigger: bool = False
        self.macd_hist: float = 0.0

        self.signal_engine: Optional[Any] = None
        self.risk_monitor: Optional[Any] = None
        self.data_loader: Optional[Any] = None
        self.indicator_engine: Optional[Any] = None
        self.backtest_engine: Optional[Any] = None
        self.notifier: Optional[Any] = None

    def persist(self):
        save_signals(self.signals)
        save_positions(self.positions)


state = AppState()


def get_state() -> AppState:
    return state


# ── 着陆页 ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"page": "home"})


# ── 仪表盘 ────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    latest_signal = state.signals[-1] if state.signals else None
    active_pos = [p for p in state.positions if p.status == PositionStatus.ACTIVE]
    return templates.TemplateResponse(request, "dashboard.html", {
        "page": "dashboard",
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "etf_price": state.etf_price,
        "price_pct": state.price_pct,
        "vix_value": state.vix_value,
        "vix_pct": state.vix_pct,
        "macd_trigger": state.macd_trigger,
        "macd_hist": state.macd_hist,
        "latest_signal": latest_signal,
        "risk_events": state.risk_events[-5:],
        "active_count": len(active_pos),
        "pending_count": len([s for s in state.signals if s.status == SignalStatus.PENDING]),
    })


# ── 信号管理 ────────────────────────────────────────────

@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request):
    return templates.TemplateResponse(request, "signals.html", {
        "page": "signals",
        "signals": list(reversed(state.signals)),
    })


@app.post("/signals/{signal_id}/action")
async def signal_action(signal_id: str, action: str = Form(...)):
    for s in state.signals:
        if s.id == signal_id:
            if action == "EXECUTED":
                s.status = SignalStatus.EXECUTED
                direction = Direction.LONG if s.type.value == "BUY_CALL" else Direction.SHORT
                pos = PositionDTO(
                    contract_code=s.contract_code,
                    direction=direction,
                    entry_avg_price=s.suggest_price,
                    quantity=s.suggested_quantity,
                    entry_date=date.today(),
                    expiry_date=s.expiry_date,
                )
                state.positions.append(pos)
            elif action == "IGNORED":
                s.status = SignalStatus.IGNORED
            if state.notifier:
                state.notifier.notify_user_action(signal_id, action)
            break
    state.persist()
    return RedirectResponse(url="/signals", status_code=303)


@app.post("/scan")
async def trigger_scan():
    if state.signal_engine:
        signal = state.signal_engine.scan()
        if signal:
            state.signals.append(signal)
            state.persist()
            if state.notifier:
                state.notifier.notify_signal(signal)
    return RedirectResponse(url="/signals", status_code=303)


# ── 持仓管理 ────────────────────────────────────────────

@app.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    active_positions = [p for p in state.positions if p.status == PositionStatus.ACTIVE]
    total_margin = sum(p.margin_used for p in active_positions)

    if state.risk_monitor and active_positions:
        state.risk_events = state.risk_monitor.check_all_positions(
            active_positions, state.account
        )

    return templates.TemplateResponse(request, "positions.html", {
        "page": "positions",
        "positions": active_positions,
        "closed_positions": [p for p in state.positions if p.status == PositionStatus.CLOSED],
        "account": state.account,
        "total_margin": total_margin,
        "risk_events": state.risk_events,
        "today": date.today(),
    })


@app.post("/positions/add")
async def add_position(
    contract_code: str = Form(...),
    direction: str = Form(...),
    entry_price: float = Form(...),
    quantity: int = Form(...),
    expiry_date: str = Form(...),
    margin_used: float = Form(0.0),
):
    pos = PositionDTO(
        contract_code=contract_code,
        direction=Direction(direction),
        entry_avg_price=entry_price,
        quantity=quantity,
        entry_date=date.today(),
        expiry_date=date.fromisoformat(expiry_date),
        margin_used=margin_used,
    )
    state.positions.append(pos)
    state.persist()
    return RedirectResponse(url="/positions", status_code=303)


@app.post("/positions/{contract_code}/close")
async def close_position(contract_code: str):
    for p in state.positions:
        if p.contract_code == contract_code and p.status == PositionStatus.ACTIVE:
            p.status = PositionStatus.CLOSED
            break
    state.persist()
    return RedirectResponse(url="/positions", status_code=303)


# ── 回测报告 ────────────────────────────────────────────

@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request):
    chart_url = None
    chart_file = STATIC_DIR / "backtest_equity.png"
    if chart_file.exists():
        chart_url = "/static/backtest_equity.png"

    return templates.TemplateResponse(request, "backtest.html", {
        "page": "backtest",
        "result": state.backtest_result,
        "chart_url": chart_url,
    })


@app.post("/backtest/run")
async def run_backtest():
    if state.backtest_engine and state.data_loader:
        etf_data = state.data_loader.get_etf_history(300)
        vix_data = state.data_loader.get_vix_history(300)
        result = state.backtest_engine.run(etf_data, vix_data)
        files = state.backtest_engine.save_report(result, STATIC_DIR)
        state.backtest_result = result
        state.backtest_chart = files.get("chart")
    return RedirectResponse(url="/backtest", status_code=303)


# ── JSON API ────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    return {
        "etf_price": state.etf_price,
        "vix_value": state.vix_value,
        "price_pct": round(state.price_pct, 2),
        "vix_pct": round(state.vix_pct, 2),
        "macd_trigger": state.macd_trigger,
        "macd_hist": round(state.macd_hist, 6),
        "active_positions": len([p for p in state.positions if p.status == PositionStatus.ACTIVE]),
        "pending_signals": len([s for s in state.signals if s.status == SignalStatus.PENDING]),
        "risk_events": len(state.risk_events),
        "update_time": datetime.now().isoformat(),
    }


@app.get("/api/signals")
async def api_signals():
    return [s.model_dump(mode="json") for s in state.signals]


@app.get("/api/positions")
async def api_positions():
    return [p.model_dump(mode="json") for p in state.positions]


# ── WebSocket 实时推送 ──────────────────────────────────

@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            active_positions = [
                {
                    "contract_code": p.contract_code,
                    "direction": p.direction.value,
                    "entry_avg_price": p.entry_avg_price,
                    "current_pnl_pct": round(p.current_pnl_pct * 100, 2),
                    "margin_used": p.margin_used,
                    "days_left": (p.expiry_date - date.today()).days,
                }
                for p in state.positions
                if p.status == PositionStatus.ACTIVE
            ]

            risk_alerts = [
                {"type": e.event_type.value, "message": e.message}
                for e in state.risk_events
            ]

            payload = {
                "etf_price": round(state.etf_price, 4),
                "vix_value": round(state.vix_value, 2),
                "price_pct": round(state.price_pct, 2),
                "vix_pct": round(state.vix_pct, 2),
                "macd_trigger": state.macd_trigger,
                "macd_hist": round(state.macd_hist, 6),
                "active_positions": active_positions,
                "risk_alerts": risk_alerts,
                "pending_signals": len([s for s in state.signals if s.status == SignalStatus.PENDING]),
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            await websocket.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
