from datetime import datetime, timezone

import pytest

from quant_agent.decision import DecisionValidationError, GeminiDecisionEngine
from quant_agent.models import Decision, MarketSnapshot, RiskBudget
from quant_agent.risk import RiskGate
from quant_agent.storage import EventStore


class FakeGemini:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, payload, *, model):
        return {"text": self.payload}


def snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime.now(timezone.utc), source="test", instrument="BTC/USDT:USDT",
        orderbook_imbalance=0.01, cvd=0.0, quality_flags=(),
    )


def valid_decision(action="LONG") -> Decision:
    return Decision(
        action=action, confidence=0.82, stop_loss_pct=0.012,
        take_profit_pct=0.028, reasoning="Trend is constructive", model="gemini-2.5-flash",
        prompt_version="v1", generated_at=datetime.now(timezone.utc), input_snapshot_id="test",
    )


def test_decision_schema_and_threshold_validation():
    engine = GeminiDecisionEngine(FakeGemini('{"action":"LONG","confidence":0.82,"stop_loss_pct":0.012,"take_profit_pct":0.028,"reasoning":"ok"}'), RiskGate(RiskBudget(10_000)))
    decision = engine.decide(snapshot())
    assert decision.action == "LONG"
    assert decision.confidence == 0.82

    bad = FakeGemini('{"action":"LONG","confidence":0.5,"stop_loss_pct":0.012,"take_profit_pct":0.028,"reasoning":"ok"}')
    engine = GeminiDecisionEngine(bad, RiskGate(RiskBudget(10_000)))
    decision = engine.decide(snapshot())
    assert decision.action == "HOLD"
    assert decision.risk_reason_codes == ("CONFIDENCE_BELOW_THRESHOLD",)


def test_invalid_json_is_rejected():
    engine = GeminiDecisionEngine(FakeGemini("not-json"), RiskGate(RiskBudget(10_000)))
    with pytest.raises(DecisionValidationError):
        engine.decide(snapshot())


def test_risk_gate_rejects_unsafe_trade():
    gate = RiskGate(RiskBudget(10_000))
    low = valid_decision()
    low = Decision(low.action, 0.69, low.stop_loss_pct, low.take_profit_pct, low.reasoning, low.model, low.prompt_version, low.generated_at, low.input_snapshot_id)
    result = gate.evaluate(snapshot(), low)
    assert not result.allow
    assert "CONFIDENCE_BELOW_THRESHOLD" in result.reason_codes


def test_quantity_and_position_limits():
    gate = RiskGate(RiskBudget(10_000))
    assert gate.calculate_quantity(10_000, 50_000, 0.01) == 0.004
    assert gate.position_limit(10_000, 0.004, 50_000)
    assert not gate.position_limit(10_000, 1.0, 50_000)


def test_event_store_persists_complete_audit_events(tmp_path):
    store = EventStore(tmp_path)
    store.append(type("Event", (), {"event_id": "event-1", "timestamp": datetime.now(timezone.utc), "type": "TEST", "to_dict": lambda self: {"event_id": "event-1", "type": "TEST"}})())
    assert store.list_events("TEST")[0]["event_id"] == "event-1"
