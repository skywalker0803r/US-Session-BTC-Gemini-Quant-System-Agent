import json
from pathlib import Path

import httpx

from quant_agent.adapters import GateIoAdapter


class StubTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/positions"):
            return httpx.Response(200, json=[{
                "positionId": "pos-1",
                "side": "LONG",
                "size": 0.25,
            }])
        if request.method == "POST" and request.url.path.endswith("/orders"):
            return httpx.Response(200, json={"id": "order-1", "status": "FILLED"})
        return httpx.Response(404)


class RetryTransport(httpx.MockTransport):
    def __init__(self, *, status_code: int = 503, failures_before_success: int = 2) -> None:
        self.requests: list[httpx.Request] = []
        self.order_attempts = 0
        self.status_code = status_code
        self.failures_before_success = failures_before_success
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/positions"):
            return httpx.Response(200, json=[{
                "positionId": "pos-1",
                "side": "LONG",
                "size": 0.25,
            }])
        if request.method == "POST" and request.url.path.endswith("/orders"):
            self.order_attempts += 1
            if self.order_attempts <= self.failures_before_success:
                return httpx.Response(self.status_code)
            return httpx.Response(200, json={"id": "order-1", "status": "FILLED"})
        return httpx.Response(404)


def test_close_positions_uses_reduce_only_contract(monkeypatch, tmp_path: Path) -> None:
    transport = StubTransport()
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(
        api_key="key",
        secret="secret",
        position_store=tmp_path / "positions.json",
    )
    adapter.managed_position_ids.add("pos-1")

    results = adapter.close_positions()

    assert len(results) == 1
    assert results[0].order_id == "order-1"
    assert len(transport.requests) == 2
    close_request = transport.requests[1]
    payload = json.loads(close_request.content)
    assert payload["side"] == "SELL"
    assert payload["reduceOnly"] is True
    assert payload["closePosition"] is True


def test_place_order_records_request_and_exchange_response(monkeypatch) -> None:
    transport = StubTransport()
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(api_key="key", secret="secret")

    result = adapter.place_order(
        side="BUY", quantity=0.004, price=50_000.0,
        stop_loss_price=49_400.0, take_profit_price=51_000.0,
    )

    assert result.request_payload["side"] == "BUY"
    assert result.exchange_response == {"id": "order-1", "status": "FILLED"}
    assert len(transport.requests) == 1


def test_close_positions_retries_transient_order_failure(monkeypatch, tmp_path: Path) -> None:
    transport = RetryTransport()
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(
        api_key="key",
        secret="secret",
        position_store=tmp_path / "positions.json",
    )
    adapter.managed_position_ids.add("pos-1")

    results = adapter.close_positions()

    assert len(results) == 1
    assert results[0].order_id == "order-1"
    assert transport.order_attempts == 3
    assert len(transport.requests) == 4
    assert transport.requests[3].method == "POST"
    assert adapter.managed_position_ids == set()


def test_close_positions_retries_rate_limit_and_preserves_position(monkeypatch, tmp_path: Path) -> None:
    transport = RetryTransport(status_code=429)
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(
        api_key="key",
        secret="secret",
        position_store=tmp_path / "positions.json",
    )
    adapter.managed_position_ids.add("pos-1")

    results = adapter.close_positions()

    assert len(results) == 1
    assert results[0].status == "FILLED"
    assert transport.order_attempts == 3
    assert adapter.managed_position_ids == set()


def test_close_positions_does_not_retry_client_error(monkeypatch, tmp_path: Path) -> None:
    transport = RetryTransport(status_code=400)
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(
        api_key="key",
        secret="secret",
        position_store=tmp_path / "positions.json",
    )
    adapter.managed_position_ids.add("pos-1")

    results = adapter.close_positions()

    assert results == []
    assert transport.order_attempts == 1
    assert adapter.managed_position_ids == {"pos-1"}


def test_close_positions_stops_after_retry_limit_and_records_failure(monkeypatch, tmp_path: Path) -> None:
    transport = RetryTransport(status_code=503, failures_before_success=99)
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    adapter = GateIoAdapter(
        api_key="key",
        secret="secret",
        position_store=tmp_path / "positions.json",
        max_close_attempts=3,
        retry_delay_seconds=0.0,
    )
    adapter.managed_position_ids.add("pos-1")

    results = adapter.close_positions()

    assert results == []
    assert transport.order_attempts == 3
    assert adapter.managed_position_ids == {"pos-1"}
