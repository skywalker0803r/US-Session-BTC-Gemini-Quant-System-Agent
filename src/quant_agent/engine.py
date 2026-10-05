from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .decision import GeminiDecisionEngine
from .models import Decision, Event, MarketSnapshot, OrderResult, RiskBudget, utc_now
from .risk import RiskGate
from .storage import EventStore, event_id


class MarketDataProvider(Protocol):
    def snapshot(self, *, timestamp: datetime) -> MarketSnapshot: ...


class ExchangeProvider(Protocol):
    def set_leverage(self, leverage: float) -> bool: ...
    def get_snapshot(self) -> Any: ...
    def place_order(self, **kwargs: Any) -> OrderResult: ...
    def close_positions(self) -> list[OrderResult]: ...


class QuantAgent:
    def __init__(self, market_data: MarketDataProvider, gemini: GeminiDecisionEngine,
                 exchange: ExchangeProvider, risk_gate: RiskGate, store: EventStore,
                 *, budget: RiskBudget | None = None) -> None:
        self.market_data = market_data
        self.gemini = gemini
        self.exchange = exchange
        self.risk_gate = risk_gate
        self.store = store
        self.budget = budget or RiskBudget(initial_equity=10_000.0)

    def run_once(self, *, timestamp: datetime | None = None) -> Decision:
        now = timestamp or utc_now()
        snapshot = self.market_data.snapshot(timestamp=now)
        decision = self.gemini.decide(snapshot)
        self._record_event("DECISION", snapshot_id=snapshot.timestamp.isoformat(), decision=decision.as_dict())
        risk = self.risk_gate.evaluate(snapshot, decision)
        if not risk.allow or decision.action == "HOLD":
            self._record_event("RISK_REJECTED", decision=decision.as_dict(), reason_codes=list(risk.reason_codes), reason=risk.reason)
            return decision
        execution = self._execute(decision)
        if execution is not None:
            self._record_event("ORDER_PLACED", order=execution.__dict__, decision=decision.as_dict())
        return decision

    def _execute(self, decision: Decision) -> OrderResult | None:
        if not self.exchange.set_leverage(14.0):
            self._record_event("EXECUTION_REJECTED", reason="Leverage configuration failed")
            return None
        current = self.exchange.get_snapshot()
        if current.margin_mode.lower() != "isolated" or current.leverage != 14.0:
            self._record_event("EXECUTION_REJECTED", reason="Exchange margin or leverage mismatch")
            return None
        if current.position_count > 0:
            self._record_event("EXECUTION_REJECTED", reason="Existing position detected")
            return None
        order_data = {
            "side": "BUY" if decision.action == "LONG" else "SELL",
            "quantity": 1.0,
            "price": 50_000.0,
            "stop_loss_price": 50_000.0 * (1.0 - decision.stop_loss_pct),
            "take_profit_price": 50_000.0 * (1.0 + decision.take_profit_pct),
        }
        try:
            return self.exchange.place_order(**order_data)
        except Exception as exc:
            self._record_event("ORDER_FAILED", error=str(exc), decision=decision.as_dict())
            return None

    def close_all(self) -> list[OrderResult]:
        try:
            results = self.exchange.close_positions()
            self._record_event("CLOSE_ALL", results=[result.__dict__ for result in results])
            return results
        except Exception as exc:
            self._record_event("CLOSE_ALL_FAILED", error=str(exc))
            raise

    def _record_event(self, event_type: str, **attributes: Any) -> None:
        self.store.append(Event(event_id(event_type.lower()), utc_now(), event_type, attributes))


class MarketDataSimulation:
    def __init__(self, *, price: float = 50_000.0) -> None:
        self.price = price

    def snapshot(self, *, timestamp: datetime) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=timestamp, source="simulation", instrument="BTC/USDT:USDT",
            kline_base64=None, orderbook_imbalance=0.01, cvd=0.0,
            macro_summary="Simulation data: no real market feed.",
            news=(), quality_flags=(),
        )
