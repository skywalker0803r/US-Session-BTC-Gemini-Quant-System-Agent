from datetime import datetime, timedelta, timezone

from quant_agent.models import MarketSnapshot, RiskBudget
from quant_agent.risk import RiskGate


def test_risk_gate_rejects_stale_market_data() -> None:
    now = datetime.now(timezone.utc)
    snapshot = MarketSnapshot(
        timestamp=now - timedelta(minutes=6),
        source="gateio",
        instrument="BTC/USDT:USDT",
        price=50_000.0,
        orderbook_imbalance=0.01,
        cvd=0.0,
        quality_flags=(),
    )
    decision = type("Decision", (), {
        "confidence": 0.82,
        "stop_loss_pct": 0.012,
        "take_profit_pct": 0.028,
        "action": "LONG",
    })()

    result = RiskGate(RiskBudget(10_000), max_data_age_minutes=5).evaluate(snapshot, decision)

    assert not result.allow
    assert "DATA_STALE" in result.reason_codes
