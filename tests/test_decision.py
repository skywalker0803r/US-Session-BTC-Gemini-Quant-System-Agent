from datetime import datetime, timezone

from quant_agent.decision import GeminiDecisionEngine
from quant_agent.models import MarketSnapshot, RiskBudget
from quant_agent.risk import RiskGate


class ContractGeminiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, str]] = []

    def complete(self, payload: dict, *, model: str) -> dict:
        self.calls.append((payload, model))
        return {
            "text": '{"action":"LONG","confidence":0.82,'
                    '"stop_loss_pct":0.012,"take_profit_pct":0.028,'
                    '"reasoning":"verified signal"}'
        }


def test_decision_engine_uses_the_real_gemini_client_contract() -> None:
    client = ContractGeminiClient()
    engine = GeminiDecisionEngine(client, RiskGate(RiskBudget(10_000)))
    snapshot = MarketSnapshot(
        timestamp=datetime.now(timezone.utc),
        source="test",
        instrument="BTC/USDT:USDT",
        price=50_000.0,
    )

    decision = engine.decide(snapshot)

    assert decision.action == "LONG"
    assert client.calls[0][1] == "gemini-2.5-flash"
