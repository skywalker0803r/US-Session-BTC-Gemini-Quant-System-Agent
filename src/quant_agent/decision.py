from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .models import Decision, MarketSnapshot
from .risk import RiskGate


DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "confidence", "stop_loss_pct", "take_profit_pct", "reasoning"],
    "properties": {
        "action": {"enum": ["LONG", "SHORT", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "stop_loss_pct": {"type": "number", "minimum": 0.008, "maximum": 0.020},
        "take_profit_pct": {"type": "number", "minimum": 0.020, "maximum": 0.040},
        "reasoning": {"type": "string", "maxLength": 100},
    },
}


class DecisionValidationError(ValueError):
    pass


class GeminiDecisionEngine:
    def __init__(self, client: Any, risk_gate: RiskGate, *, prompt_version: str = "v1") -> None:
        self.client = client
        self.risk_gate = risk_gate
        self.prompt_version = prompt_version

    def decide(self, snapshot: MarketSnapshot) -> Decision:
        payload = self._build_prompt(snapshot)
        raw = self.client.complete(payload, model="gemini-2.5-flash")
        decision = self._parse_decision(raw, snapshot)
        risk = self.risk_gate.evaluate(snapshot, decision)
        if not risk.allow:
            return Decision(
                action="HOLD", confidence=decision.confidence,
                stop_loss_pct=decision.stop_loss_pct,
                take_profit_pct=decision.take_profit_pct,
                reasoning=decision.reasoning,
                model=decision.model,
                prompt_version=decision.prompt_version,
                generated_at=decision.generated_at,
                input_snapshot_id=decision.input_snapshot_id,
                risk_reason_codes=risk.reason_codes,
            )
        return decision

    def _build_prompt(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        return {
            "system": "You are a strict BTC/USDT trading decision engine. Return only JSON matching the schema.",
            "contents": [
                {"role": "user", "parts": [{"text": json.dumps({
                    "snapshot_id": snapshot.timestamp.isoformat(),
                    "instrument": "BTC/USDT:USDT",
                    "price": snapshot.price,
                    "macro_summary": snapshot.macro_summary,
                    "orderbook_imbalance": snapshot.orderbook_imbalance,
                    "cvd": snapshot.cvd,
                    "news": list(snapshot.news),
                    "quality_flags": list(snapshot.quality_flags),
                })}]}
            ],
            "response_mime_type": "application/json",
            "response_schema": DECISION_SCHEMA,
        }

    def _parse_decision(self, raw: Any, snapshot: MarketSnapshot) -> Decision:
        try:
            text = raw["text"] if isinstance(raw, dict) and "text" in raw else str(raw)
            cleaned = text.strip()
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise DecisionValidationError("Model response did not contain JSON")
            value = json.loads(match.group(0))
            self._validate(value)
            if value["action"] != "HOLD" and value["confidence"] < 0.70:
                value["action"] = "HOLD"
            return Decision(
                action=value["action"], confidence=float(value["confidence"]),
                stop_loss_pct=float(value["stop_loss_pct"]),
                take_profit_pct=float(value["take_profit_pct"]),
                reasoning=value["reasoning"],
                model="gemini-2.5-flash", prompt_version=self.prompt_version,
                generated_at=datetime.now().astimezone(), input_snapshot_id=snapshot.timestamp.isoformat(),
            )
        except Exception as exc:
            raise DecisionValidationError(f"Invalid model decision: {exc}") from exc

    @staticmethod
    def _validate(value: Any) -> None:
        if not isinstance(value, dict):
            raise DecisionValidationError("Decision must be an object")
        missing = [key for key in DECISION_SCHEMA["required"] if key not in value]
        if missing:
            raise DecisionValidationError(f"Missing fields: {', '.join(missing)}")
        if value["action"] not in DECISION_SCHEMA["properties"]["action"]["enum"]:
            raise DecisionValidationError("Invalid action")
        if value["take_profit_pct"] <= value["stop_loss_pct"]:
            raise DecisionValidationError("Take profit must exceed stop loss")
        if len(value["reasoning"]) > 100:
            raise DecisionValidationError("Reasoning exceeds 100 characters")
