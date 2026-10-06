from __future__ import annotations

import base64
import json
import math
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Protocol

import httpx

from .models import Decision, ExchangeSnapshot, MarketSnapshot, OrderResult


class MarketDataSource(Protocol):
    def snapshot(self, *, timestamp: datetime) -> MarketSnapshot: ...


class MarketDataPipeline:
    def __init__(self, *, bitcoin: MarketDataSource, vix: MarketDataSource | None = None,
                 dxy: MarketDataSource | None = None, nq_es: MarketDataSource | None = None,
                 news: MarketDataSource | None = None) -> None:
        self.bitcoin = bitcoin
        self.vix = vix
        self.dxy = dxy
        self.nq_es = nq_es
        self.news = news

    def collect(self, *, timestamp: datetime) -> MarketSnapshot:
        snapshots = [self.bitcoin.snapshot(timestamp=timestamp)]
        for name, source in (("vix", self.vix), ("dxy", self.dxy),
                              ("nq-es", self.nq_es), ("news", self.news)):
            if source is not None:
                snapshots.append(source.snapshot(timestamp=timestamp))

        bitcoin = snapshots[0]
        if bitcoin.price is None or bitcoin.price <= 0:
            raise ValueError("Bitcoin market data must include a positive price")
        if not bitcoin.kline_base64:
            raise ValueError("Bitcoin market data must include K-line data")

        merged = {
            "timestamp": timestamp,
            "source": bitcoin.source,
            "instrument": bitcoin.instrument,
            "price": bitcoin.price,
            "kline_base64": bitcoin.kline_base64,
            "orderbook_imbalance": bitcoin.orderbook_imbalance,
            "cvd": bitcoin.cvd,
            "macro_summary": bitcoin.macro_summary,
            "news": tuple(bitcoin.news + tuple(
                item_item for item in snapshots[1:] for item_item in item.news
            )),
            "vix": next((item.price for item in snapshots[1:] if item.instrument == "VIX"), None),
            "dxy": next((item.price for item in snapshots[1:] if item.instrument == "DXY"), None),
            "nq_es_spread": next((item.price for item in snapshots[1:] if item.instrument == "NQ/ES"), None),
            "quality_flags": tuple(dict.fromkeys(bitcoin.quality_flags + tuple(
                flag for item in snapshots[1:] for flag in item.quality_flags
            ))),
            "data_sources": tuple(dict.fromkeys(
                (bitcoin.source,) + tuple(item.source for item in snapshots[1:])
            )),
        }
        return MarketSnapshot(**merged)


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
    def __init__(
        self,
        api_key: str | None = None,
        secret: str | None = None,
        *,
        base_url: str = "https://api.gateio.ws/api/v4",
        position_store: Path | None = None,
        max_close_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.getenv("GATEIO_API_KEY")
        self.secret = secret or os.getenv("GATEIO_SECRET")
        self.base_url = base_url.rstrip("/")
        self.live = bool(os.getenv("ALLOW_LIVE_TRADING", "").lower() in {"1", "true", "yes"})
        self.position_store = position_store
        self.max_close_attempts = max_close_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.close_failures: list[dict[str, Any]] = []
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

    def _get(self, path: str, **kwargs: Any) -> Any:
        if not self.api_key or not self.secret:
            raise ValueError("Gate.io credentials are required for market data")
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{self.base_url}/futures/usdt/v1/spot{path}",
                auth=(self.api_key, self.secret),
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    def market_snapshot(self, *, timestamp: datetime) -> MarketSnapshot:
        price_data = self._get("/price", params={"symbol": "BTC/USDT"})
        candle_data = self._get("/candles", params={"symbol": "BTC/USDT", "interval": "1h", "limit": 24})
        book_data = self._get("/order_book", params={"symbol": "BTC/USDT", "limit": 100})

        price = float(price_data["price"])
        candles = [
            {
                "timestamp": int(candle.get("timestamp", 0)),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0.0)),
            }
            for candle in candle_data
        ]
        kline_base64 = base64.b64encode(json.dumps(candles, separators=(",", ":")).encode()).decode()

        bids = [float(level["price"]) for level in book_data if float(level.get("size", 0.0)) > 0]
        asks = [float(level["price"]) for level in book_data if float(level.get("size", 0.0)) > 0]
        if bids and asks:
            midpoint = (min(bids) + max(asks)) / 2.0
            orderbook_imbalance = sum(1.0 if price < midpoint else -1.0 for price in [min(bids), max(asks)]) / 2.0
        else:
            orderbook_imbalance = None

        closes = [candle["close"] for candle in candles]
        if len(closes) >= 2:
            returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:])]
            mean = sum(returns) / len(returns)
            cvd = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns)) / abs(mean) if mean else None
        else:
            cvd = None

        return MarketSnapshot(
            timestamp=timestamp,
            source="gateio",
            instrument="BTC/USDT:USDT",
            price=price,
            kline_base64=kline_base64,
            orderbook_imbalance=orderbook_imbalance,
            cvd=cvd,
            macro_summary="Gate.io BTC market snapshot; macro and news sources are supplied separately.",
            quality_flags=tuple(flag for flag, present in (
                ("KLINE_DATA_PRESENT", bool(candles)),
                ("ORDER_BOOK_DATA_PRESENT", bool(book_data)),
            ) if present),
            data_sources=("gateio",),
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
            request_payload=payload, exchange_response=dict(data),
        )

    def close_positions(self) -> list[OrderResult]:
        results: list[OrderResult] = []
        self.close_failures.clear()
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{self.base_url}/futures/usdt/v1/spot/positions", auth=(self.api_key, self.secret), params={"symbol": "BTC/USDT:USDT"})
            response.raise_for_status()
            positions = response.json()
            for position in positions:
                position_id = str(position.get("positionId") or position.get("position_id") or "")
                if position_id not in self.managed_position_ids or float(position.get("size", 0.0)) == 0:
                    continue
                close_payload = {
                    "symbol": "BTC/USDT:USDT", "side": "SELL" if position.get("side") == "LONG" else "BUY",
                    "amount": float(position["size"]), "type": "MARKET", "reduceOnly": True, "closePosition": True,
                }
                try:
                    data = self._post_close_order(client, close_payload)
                except httpx.HTTPStatusError as exc:
                    error = self._error_message(exc.response)
                    self.close_failures.append({
                        "position_id": position_id,
                        "error": error,
                        "status_code": exc.response.status_code,
                        "attempts": self.max_close_attempts,
                    })
                    continue
                results.append(OrderResult(
                    str(data["id"]), "CLOSE", float(position["size"]), 0.0,
                    str(data.get("status", "UNKNOWN")), request_payload=close_payload,
                    exchange_response=dict(data),
                ))
                self.managed_position_ids.discard(position_id)
                self._save_position_ids()
        return results

    def _post_close_order(self, client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.max_close_attempts + 1):
            response = client.post(
                f"{self.base_url}/futures/usdt/v1/spot/orders",
                auth=(self.api_key, self.secret),
                json=payload,
            )
            if response.is_success:
                return response.json()
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == self.max_close_attempts:
                response.raise_for_status()
            retry_after = response.headers.get("retry-after")
            delay = self.retry_delay_seconds
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            time.sleep(delay)
        raise RuntimeError("Close order retry loop exhausted")

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("error") or payload.get("message") or payload.get("msg")
            if message:
                return str(message)
        except (ValueError, TypeError):
            pass
        return f"Gate.io returned HTTP {response.status_code}"

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
