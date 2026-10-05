from __future__ import annotations

from math import isfinite

from .models import Decision, MarketSnapshot, RiskBudget, RiskDecision


class RiskGate:
    def __init__(self, budget: RiskBudget, *, max_wave_pct: float = 0.10) -> None:
        self.budget = budget
        self.max_wave_pct = max_wave_pct

    def evaluate(self, snapshot: MarketSnapshot, decision: Decision) -> RiskDecision:
        reasons: list[str] = []
        if snapshot.quality_flags:
            reasons.append("DATA_QUALITY_INVALID")
        if not 0.0 <= decision.confidence <= 1.0:
            reasons.append("INVALID_CONFIDENCE")
        if decision.confidence < 0.70:
            reasons.append("CONFIDENCE_BELOW_THRESHOLD")
        if not 0.008 <= decision.stop_loss_pct <= 0.020:
            reasons.append("INVALID_STOP_LOSS")
        if not 0.020 <= decision.take_profit_pct <= 0.040:
            reasons.append("INVALID_TAKE_PROFIT")
        if decision.take_profit_pct <= decision.stop_loss_pct:
            reasons.append("TAKE_PROFIT_NOT_ABOVE_STOP_LOSS")
        if snapshot.orderbook_imbalance is not None and abs(snapshot.orderbook_imbalance) > self.max_wave_pct:
            reasons.append("SINGLE_ROOT_WAVE_EXCESSIVE")
        if not all(isfinite(v) for v in (decision.confidence, decision.stop_loss_pct, decision.take_profit_pct)):
            reasons.append("NON_FINITE_DECISION_VALUE")
        if decision.action == "HOLD" and not reasons:
            return RiskDecision(True, (), "HOLD is a safe state")
        if reasons:
            return RiskDecision(False, tuple(reasons), "; ".join(reasons))
        return RiskDecision(True, (), "All risk gates passed")

    def calculate_quantity(
        self, equity: float, price: float, stop_loss_pct: float, risk_budget_pct: float = 0.01
    ) -> float:
        if equity <= 0 or price <= 0 or stop_loss_pct <= 0:
            raise ValueError("Equity, price, and stop-loss must be positive")
        risk_amount = equity * risk_budget_pct
        risk_based_quantity = risk_amount / (price * stop_loss_pct)
        position_based_quantity = equity * self.budget.max_position_pct / price
        return min(risk_based_quantity, position_based_quantity)

    def position_limit(self, equity: float, quantity: float, price: float) -> bool:
        if quantity <= 0 or price <= 0:
            return False
        notional = quantity * price
        return notional <= equity * self.budget.max_position_pct
