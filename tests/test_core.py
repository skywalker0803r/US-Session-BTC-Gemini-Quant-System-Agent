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
    def __init__(self, client: Any, risk_gate: RiskGate, *, prompt_version: str = "v1", enable_grounding: bool = True) -> None:
        self.client = client
        self.risk_gate = risk_gate
        self.prompt_version = prompt_version
        self.enable_grounding = enable_grounding

    def decide(self, snapshot: MarketSnapshot) -> Decision:
        payload = self._build_prompt(snapshot)
        raw = self.client.complete(payload, model="gemini-2.5-flash", enable_grounding=self.enable_grounding)
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
        system_prompt = """You are a professional BTC/USDT futures trading decision engine for US stock market hours (09:30-16:00 EST).

CRITICAL INSTRUCTIONS:
1. You MUST search for real-time Bitcoin market conditions, news, and sentiment BEFORE making a decision.
2. Focus your search on:
   - Current Bitcoin price and 24-hour price movements
   - Major news affecting Bitcoin (Fed policy, SEC regulations, institutional flows, ETF flows)
   - Market sentiment and technical levels
   - Macroeconomic events (US inflation, employment reports, interest rate expectations)
   - Any significant events from the last 24-48 hours

3. After research, provide ONLY a JSON decision matching the exact schema provided.
4. Your reasoning must be concise (max 100 chars) but incorporate findings from your search.
5. Set confidence based on signal strength and news sentiment alignment.
6. Return JSON with no additional text."""

        user_content = f"""Current market snapshot:
- Price: ${snapshot.price} USD
- Timestamp: {snapshot.timestamp.isoformat()}
- Instrument: BTC/USDT:USDT

Search for current Bitcoin market conditions and recent news, then decide whether to:
1. LONG (bullish) - if strong positive signals and supportive news
2. SHORT (bearish) - if strong negative signals and adverse news  
3. HOLD - if uncertain, contradictory signals, or weak conviction

Provide decision as JSON only."""

        return {
            "system": system_prompt,
            "contents": [
                {"role": "user", "parts": [{"text": user_content}]}
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

