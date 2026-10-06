from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any

import httpx

from .models import Decision, ExchangeSnapshot, MarketSnapshot, OrderResult


class GeminiClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://generativelanguage.googleapis.com/v1beta") -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.base_url = base_url.rstrip("/")

    def complete(self, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        endpoint = f"{self.base_url}/models/{model}:generateContent"
        with httpx.Client(timeout=20.0) as client:
            response = client.post(endpoint, params={"key": self.api_key}, json=payload)
            response.raise_for_status()
            data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text")
        if not text:
            raise RuntimeError("Gemini returned empty content")
        return {"text": text}


class GateIoAdapter:
    def __init__(self, api_key: str | None = None, secret: str | None = None, *, base_url: str = "https://api.gateio.ws/api/v4", position_store: Path | None = None) -> None:
        self.api_key = api_key or os.getenv("GATEIO_API_KEY")
        self.secret = secret or os.getenv("GATEIO_SECRET")
        self.base_url = base_url.rstrip("/")
        self.live = bool(os.getenv("ALLOW_LIVE_TRADING", "").lower() in {"1", "true", "yes"})
        self.position_store = position_store
        self.managed_position_ids: set[str] = self._load_position_ids()
        if self.api_key and self.secret:
            self.live = self.live

    def _load_position_ids(self) -> set[str]:
        if self.position_store is None:
            return set()
        try:
            data = json.loads(self.position_store.read_text())
            return {str(position_id) for position_id in data.get("position_ids", [])}
        except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
            return set()

    def _save_position_ids(self) -> None:
        if self.position_store is None:
            return
        self.position_store.parent.mkdir(parents=True, exist_ok=True)
        self.position_store.write_text(json.dumps({"position_ids": sorted(self.managed_position_ids)}, indent=2))

    def market_snapshot(self, *, timestamp: datetime) -> MarketSnapshot:
        if not self.api_key or not self.secret:
            raise ValueError("Gate.io credentials are required for market data")
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{self.base_url}/futures/usdt/v1/spot/price",
                auth=(self.api_key, self.secret),
                params={"symbol": "BTC/USDT"},
            )
            response.raise_for_status()
            data = response.json()
        price = float(data["price"])
        return MarketSnapshot(
            timestamp=timestamp,
            source="gateio",
            instrument="BTC/USDT:USDT",
            price=price,
        )

    def get_snapshot(self) -> ExchangeSnapshot:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{self.base_url}/futures/usdt/v1/spot/accounts", auth=(self.api_key, self.secret))
            response.raise_for_status()
            data = response.json()
        return ExchangeSnapshot(
            leverage=float(data.get("leverage", 14.0)), margin_mode=str(data.get("margin_mode", "isolated")),
            position_count=len(data.get("positions", [])), positions=tuple(data.get("positions", [])),
        )

    def place_order(self, *, side: str, quantity: float, price: float, stop_loss_price: float, take_profit_price: float) -> OrderResult:
        payload = {
            "symbol": "BTC/USDT:USDT", "side": side, "amount": quantity,
            "price": price, "type": "MARKET", "stopLossPrice": stop_loss_price,
            "takeProfitPrice": take_profit_price, "closePosition": False,
        }
        with httpx.Client(timeout=15.0) as client:
            response = client.post(f"{self.base_url}/futures/usdt/v1/spot/orders", auth=(self.api_key, self.secret), json=payload)
            response.raise_for_status()
            data = response.json()
        position_id = data.get("positionId") or data.get("position_id")
        if position_id:
            self.managed_position_ids.add(str(position_id))
            self._save_position_ids()
        return OrderResult(
            order_id=str(data["id"]), side=side, quantity=float(data.get("amount", quantity)),
            price=float(data.get("price", price)), status=str(data.get("status", "UNKNOWN")),
            fee=float(data.get("fee", 0.0)), stop_loss_order_id=data.get("stopLossOrderId"),
            take_profit_order_id=data.get("takeProfitOrderId"),
        )

    def close_positions(self) -> list[OrderResult]:
        results: list[OrderResult] = []
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{self.base_url}/futures/usdt/v1/spot/positions", auth=(self.api_key, self.secret), params={"symbol": "BTC/USDT:USDT"})
            response.raise_for_status()
            positions = response.json()
        for position in positions:
            position_id = str(position.get("positionId") or position.get("position_id") or "")
            if position_id not in self.managed_position_ids or float(position.get("size", 0.0)) == 0:
                continue
            response = client.post(f"{self.base_url}/futures/usdt/v1/spot/orders", auth=(self.api_key, self.secret), json={
                "symbol": "BTC/USDT:USDT", "side": "SELL" if position.get("side") == "LONG" else "BUY",
                "amount": float(position["size"]), "type": "MARKET", "closePosition": True,
            })
            response.raise_for_status()
            data = response.json()
            results.append(OrderResult(str(data["id"]), "CLOSE", float(position["size"]), 0.0, str(data.get("status", "UNKNOWN"))))
            self.managed_position_ids.discard(position_id)
            self._save_position_ids()
        return results

    def set_leverage(self, leverage: float) -> bool:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(f"{self.base_url}/futures/usdt/v1/spot/leverage", auth=(self.api_key, self.secret), json={"leverage": leverage})
            response.raise_for_status()
            return response.json().get("success", True)


def market_price_from_decision(decision: Decision, price: float) -> tuple[float, float]:
    if decision.action == "LONG":
        stop = price * (1.0 - decision.stop_loss_pct)
        target = price * (1.0 + decision.take_profit_pct)
    elif decision.action == "SHORT":
        stop = price * (1.0 + decision.stop_loss_pct)
        target = price * (1.0 - decision.take_profit_pct)
    else:
        raise ValueError("HOLD cannot create an order")
    return stop, target


def decision_to_order(decision: Decision, price: float, quantity: float) -> dict[str, Any]:
    if decision.action == "HOLD":
        raise ValueError("HOLD cannot create an order")
    stop, target = market_price_from_decision(decision, price)
    return {
        "side": "BUY" if decision.action == "LONG" else "SELL",
        "quantity": quantity,
        "price": price,
        "stop_loss_price": stop,
        "take_profit_price": target,
    }
