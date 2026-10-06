from datetime import datetime, timezone

from quant_agent.engine import QuantAgent
from quant_agent.models import Decision, ExchangeSnapshot, MarketSnapshot, OrderResult, RiskBudget
from quant_agent.risk import RiskGate
from quant_agent.storage import EventStore


class Market:
    def snapshot(self, *, timestamp):
        return MarketSnapshot(
            timestamp,
            "test",
            "BTC/USDT:USDT",
            price=50_000.0,
            orderbook_imbalance=0.01,
        )


class Exchange:
    def __init__(self):
        self.calls = []

    def set_leverage(self, leverage):
        self.calls.append(("set_leverage", leverage))
        return True

    def get_snapshot(self):
        return ExchangeSnapshot(14.0, "isolated", 0, ())

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        return OrderResult(
            "SIM-1", kwargs["side"], kwargs["quantity"], kwargs["price"], "SIMULATED",
            request_payload=kwargs, exchange_response={"id": "SIM-1", "status": "SIMULATED"},
        )

    def close_positions(self):
        return [OrderResult(
            "SIM-CLOSE", "CLOSE", 0.004, 0.0, "FILLED",
            request_payload={"side": "SELL", "reduceOnly": True},
            exchange_response={"id": "SIM-CLOSE", "status": "FILLED"},
        )]


class Gemini:
    def decide(self, snapshot):
        return Decision("LONG", 0.82, 0.012, 0.028, "test", "gemini-2.5-flash", "v1", datetime.now(timezone.utc), snapshot.timestamp.isoformat())


def test_agent_records_decision_and_order_without_live_exchange(tmp_path):
    exchange = Exchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)
    result = agent.run_once()
    assert result.action == "LONG"
    assert exchange.calls[0] == ("set_leverage", 14.0)
    assert exchange.calls[1][0] == "place_order"
    events = store.list_events()
    assert {event["type"] for event in events} == {"DECISION", "ORDER_PLACED"}


def test_agent_event_chain_contains_order_request_and_exchange_response(tmp_path):
    exchange = Exchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)

    agent.run_once()

    order_event = next(event for event in store.list_events() if event["type"] == "ORDER_PLACED")
    order = order_event["attributes"]["order"]
    assert order["request_payload"]["side"] == "BUY"
    assert order["exchange_response"] == {"id": "SIM-1", "status": "SIMULATED"}
    assert order_event["attributes"]["decision"]["action"] == "LONG"


def test_agent_records_close_request_and_exchange_response(tmp_path):
    exchange = Exchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)

    agent.close_all()

    close_event = next(event for event in store.list_events() if event["type"] == "CLOSE_ALL")
    close = close_event["attributes"]["results"][0]
    assert close["request_payload"]["side"] == "SELL"
    assert close["request_payload"]["reduceOnly"] is True
    assert close["exchange_response"] == {"id": "SIM-CLOSE", "status": "FILLED"}


def test_agent_records_close_failure_and_keeps_position_managed(tmp_path):
    class FailingExchange(Exchange):
        def close_positions(self):
            self.close_failures = [{
                "position_id": "pos-1",
                "error": "Gate.io returned HTTP 503",
                "status_code": 503,
                "attempts": 3,
            }]
            return []

    exchange = FailingExchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)

    results = agent.close_all()

    assert results == []
    event_types = [event["type"] for event in store.list_events()]
    assert event_types == ["CLOSE_ALL", "CLOSE_POSITION_FAILED"]
    failure = store.list_events("CLOSE_POSITION_FAILED")[0]["attributes"]["failures"][0]
    assert failure["position_id"] == "pos-1"
    assert failure["error"] == "Gate.io returned HTTP 503"


def test_agent_blocks_existing_position(tmp_path):
    class PositionExchange(Exchange):
        def get_snapshot(self):
            return ExchangeSnapshot(14.0, "isolated", 1, ({"size": 0.1},))

    exchange = PositionExchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)
    agent.run_once()
    assert len([event for event in store.list_events() if event["type"] == "EXECUTION_REJECTED"]) == 1


def test_agent_uses_live_price_and_risk_calculated_quantity(tmp_path):
    exchange = Exchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), Gemini(), exchange, RiskGate(RiskBudget(10_000)), store)
    agent.run_once()
    order = exchange.calls[1][1]
    assert order["price"] == 50_000.0
    assert order["quantity"] == 0.004


def test_agent_rejects_new_trade_before_close_window(tmp_path):
    class RecordingGemini:
        def decide(self, snapshot):
            raise AssertionError("Gemini must not be called")

    exchange = Exchange()
    store = EventStore(tmp_path)
    agent = QuantAgent(Market(), RecordingGemini(), exchange, RiskGate(RiskBudget(10_000)), store)

    result = agent.run_once(timestamp=datetime.fromisoformat("2026-10-06T19:55:00+00:00"))

    assert result.action == "HOLD"
    assert exchange.calls == []
    assert [event["type"] for event in store.list_events()] == ["RISK_REJECTED"]
    assert store.list_events()[0]["attributes"]["reason_codes"] == ["CLOSE_WINDOW_PENDING"]
