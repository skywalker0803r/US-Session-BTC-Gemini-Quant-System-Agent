from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


DecisionAction = Literal["LONG", "SHORT", "HOLD"]


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: datetime
    source: str
    instrument: str
    kline_base64: str | None = None
    orderbook_imbalance: float | None = None
    cvd: float | None = None
    macro_summary: str | None = None
    news: tuple[dict[str, Any], ...] = ()
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    confidence: float
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str
    model: str
    prompt_version: str
    generated_at: datetime
    input_snapshot_id: str
    risk_reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "reasoning": self.reasoning,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "generated_at": self.generated_at.isoformat(),
            "input_snapshot_id": self.input_snapshot_id,
            "risk_reason_codes": list(self.risk_reason_codes),
        }


@dataclass(frozen=True)
class RiskBudget:
    initial_equity: float
    max_loss_pct: float = 0.28
    max_position_pct: float = 0.02
    max_daily_loss_pct: float = 0.08
    max_single_trade_loss_pct: float = 0.02

    @property
    def max_loss_amount(self) -> float:
        return self.initial_equity * self.max_loss_pct


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    side: str
    quantity: float
    price: float
    status: str
    fee: float = 0.0
    stop_loss_order_id: str | None = None
    take_profit_order_id: str | None = None
    error: str | None = None


@dataclass
class Event:
    event_id: str
    timestamp: datetime
    type: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "type": self.type,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class RiskDecision:
    allow: bool
    reason_codes: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ExchangeSnapshot:
    leverage: float
    margin_mode: str
    position_count: int
    positions: tuple[dict[str, Any], ...]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
