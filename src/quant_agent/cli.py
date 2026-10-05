from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .decision import GeminiDecisionEngine
from .engine import MarketDataSimulation, QuantAgent
from .models import ExchangeSnapshot, OrderResult, RiskBudget
from .risk import RiskGate
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    budget = RiskBudget(initial_equity=10_000.0)
    store = EventStore(args.events)
    decision_engine = GeminiDecisionEngine(FakeGeminiClient(), RiskGate(budget))
    agent = QuantAgent(
        MarketDataSimulation(price=args.market_price), decision_engine,
        NoopExchange(), RiskGate(budget), store,
    )
    decision = agent.run_once()
    print(json.dumps(decision.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
