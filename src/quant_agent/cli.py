from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import GeminiClient, GateIoAdapter
from .decision import GeminiDecisionEngine
from .engine import MarketDataSimulation, QuantAgent
from .models import ExchangeSnapshot, OrderResult, RiskBudget
from .risk import RiskGate
from .scheduler import is_close_window, is_market_open
from .storage import EventStore


class FakeGeminiClient:
    def complete(self, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        return {"text": json.dumps({
            "action": "HOLD", "confidence": 0.82,
            "stop_loss_pct": 0.012, "take_profit_pct": 0.028,
            "reasoning": "Simulation decision held for safety.",
        })}


class NoopExchange:
    def set_leverage(self, leverage: float) -> bool:
        return True

    def get_snapshot(self) -> ExchangeSnapshot:
        return ExchangeSnapshot(leverage=14.0, margin_mode="isolated", position_count=0, positions=())

    def place_order(self, **kwargs: Any) -> OrderResult:
        return OrderResult("SIM-ORDER", kwargs["side"], kwargs["quantity"], kwargs["price"], "SIMULATED")

    def close_positions(self) -> list[OrderResult]:
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="US-Session BTC Gemini Quant Agent")
    parser.add_argument("--events", type=Path, default=Path("events"), help="Directory for audit events")
    parser.add_argument("--market-price", type=float, default=50_000.0)
    parser.add_argument("--mode", choices=["simulate", "dry-run", "live"], default="simulate")
    parser.add_argument("--action", choices=["open", "close"], default="open", help="Open a trade or close managed positions")
    parser.add_argument("--now", type=datetime.fromisoformat, help="Override the current timestamp for deterministic checks")
    parser.add_argument("--position-store", type=Path, help="Persistent file containing managed Gate.io position IDs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = args.now or datetime.now(timezone.utc)
    budget = RiskBudget(initial_equity=10_000.0)
    store = EventStore(args.events)
    if args.mode == "live":
        required = ("GEMINI_API_KEY", "GATEIO_API_KEY", "GATEIO_SECRET")
        if not all(os.environ.get(name) for name in required):
            raise SystemExit("Live mode requires GEMINI_API_KEY, GATEIO_API_KEY, and GATEIO_SECRET")
        if os.environ.get("ALLOW_LIVE_TRADING", "").lower() not in {"1", "true", "yes"}:
            raise SystemExit("ALLOW_LIVE_TRADING must be explicitly enabled")
        executor = GateIoAdapter(position_store=args.position_store)
        market_data = executor
        decision_engine = GeminiDecisionEngine(GeminiClient(), RiskGate(budget))
        exchange = executor
    else:
        decision_engine = GeminiDecisionEngine(FakeGeminiClient(), RiskGate(budget))
        market_data = MarketDataSimulation(price=args.market_price)
        exchange = NoopExchange()
    agent = QuantAgent(market_data, decision_engine, exchange, RiskGate(budget), store)
    if args.action == "close":
        if not is_close_window(now):
            print(json.dumps({"status": "SKIPPED", "reason": "Outside America/New_York close window"}))
            return 0
        results = agent.close_all()
        print(json.dumps([result.__dict__ for result in results], indent=2))
        return 0
    if not is_market_open(now):
        print(json.dumps({"status": "SKIPPED", "reason": "Outside America/New_York market open window"}))
        return 0
    decision = agent.run_once(timestamp=now)
    print(json.dumps(decision.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
