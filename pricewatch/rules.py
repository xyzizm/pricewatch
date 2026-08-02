"""
Alert rules.

A rule answers one question: given the current price and what we saw last
time, should we fire an alert?

Rules are pure functions of their inputs — no network, no files, no clock.
That is what makes them trivial to test and safe to change.

Adding a rule means writing one class with `evaluate()` and registering it
in RULES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Protocol


@dataclass(frozen=True)
class Alert:
    """A fired alert. Immutable so it cannot be mutated after the fact."""

    symbol: str
    rule: str
    message: str
    price: float


class Rule(Protocol):
    name: str

    def evaluate(
        self, symbol: str, price: float, previous: Optional[float]
    ) -> Optional[Alert]:
        """Return an Alert to fire, or None to stay silent."""
        ...


class AboveRule:
    """Fire when price crosses above a threshold.

    Only fires on the crossing, not on every check while the price stays high.
    Without that, a wallet sitting above the threshold would spam you forever.
    """

    name = "above"

    def __init__(self, threshold: float):
        self.threshold = float(threshold)

    def evaluate(
        self, symbol: str, price: float, previous: Optional[float]
    ) -> Optional[Alert]:
        if price <= self.threshold:
            return None
        # First observation: fire, so the user learns the current state once.
        if previous is not None and previous > self.threshold:
            return None  # already above last time — this is not a new crossing
        return Alert(
            symbol=symbol,
            rule=self.name,
            price=price,
            message=f"{symbol} crossed above {self.threshold:g} — now {price:g}",
        )


class BelowRule:
    """Fire when price crosses below a threshold."""

    name = "below"

    def __init__(self, threshold: float):
        self.threshold = float(threshold)

    def evaluate(
        self, symbol: str, price: float, previous: Optional[float]
    ) -> Optional[Alert]:
        if price >= self.threshold:
            return None
        if previous is not None and previous < self.threshold:
            return None
        return Alert(
            symbol=symbol,
            rule=self.name,
            price=price,
            message=f"{symbol} dropped below {self.threshold:g} — now {price:g}",
        )


class PercentMoveRule:
    """Fire when price moves by at least N percent since the last check."""

    name = "percent_move"

    def __init__(self, percent: float):
        self.percent = abs(float(percent))

    def evaluate(
        self, symbol: str, price: float, previous: Optional[float]
    ) -> Optional[Alert]:
        # Nothing to compare against on the first run, and division by zero
        # is not a useful alert.
        if previous is None or previous == 0:
            return None

        change = (price - previous) / previous * 100
        if abs(change) < self.percent:
            return None

        direction = "up" if change > 0 else "down"
        return Alert(
            symbol=symbol,
            rule=self.name,
            price=price,
            message=(
                f"{symbol} moved {direction} {abs(change):.2f}% "
                f"({previous:g} -> {price:g})"
            ),
        )


RULES: Dict[str, type] = {
    AboveRule.name: AboveRule,
    BelowRule.name: BelowRule,
    PercentMoveRule.name: PercentMoveRule,
}


def build_rule(spec: dict) -> Rule:
    """
    Build a rule from a config fragment such as:
        {"type": "above", "threshold": 100000}
        {"type": "percent_move", "percent": 5}
    """
    spec = dict(spec)
    rule_type = spec.pop("type", None)
    if rule_type is None:
        raise ValueError("Rule is missing required field 'type'")

    try:
        rule_class = RULES[rule_type]
    except KeyError:
        known = ", ".join(sorted(RULES))
        raise ValueError(f"Unknown rule '{rule_type}'. Available: {known}") from None

    try:
        return rule_class(**spec)
    except TypeError as exc:
        raise ValueError(f"Bad parameters for rule '{rule_type}': {exc}") from None
