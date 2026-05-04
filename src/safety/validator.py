"""Pre-trade signal validation — sanity checks before risk and execution.

These are *correctness* checks (is this signal a valid description of a trade?)
not risk checks (do we want to take it?). Risk lives in `safety.limits`.
"""
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque, Dict


# Signals with implausibly large spreads almost always mean bad data
# (stale price feed, divide-by-zero, mis-decimaled token).
ABSURD_SPREAD_BPS = Decimal('500')

# A signal older than this is stale — market state has moved.
MAX_SIGNAL_AGE_SECONDS = 5.0

# Latest price must not deviate from recent average by more than this fraction —
# cheap protection against price-feed glitches.
PRICE_DEVIATION_LIMIT = Decimal('0.05')   # 5%


@dataclass
class PreTradeValidator:
    """Cheap, deterministic checks. Run before RiskManager."""
    history_size: int = 20
    _price_history: Dict[str, Deque[Decimal]] = field(default_factory=dict)

    def validate_signal(self, signal) -> tuple[bool, str]:
        if signal.cex_price <= 0:
            return False, "cex_price <= 0"
        if signal.dex_price <= 0:
            return False, "dex_price <= 0"
        if signal.size <= 0:
            return False, "size <= 0"

        if signal.spread_bps > ABSURD_SPREAD_BPS:
            return False, f"spread {signal.spread_bps:.0f} bps > {ABSURD_SPREAD_BPS} bps (likely bad data)"

        age = signal.age_seconds()
        if age > MAX_SIGNAL_AGE_SECONDS:
            return False, f"signal age {age:.2f}s > {MAX_SIGNAL_AGE_SECONDS}s"

        return True, "OK"

    def validate_price_feed(self, price: Decimal, pair: str) -> tuple[bool, str]:
        """Optional: feed prices into history; reject prices that jumped too far."""
        history = self._price_history.setdefault(pair, deque(maxlen=self.history_size))
        if history:
            avg = sum(history) / Decimal(len(history))
            if avg > 0:
                deviation = abs(price - avg) / avg
                if deviation > PRICE_DEVIATION_LIMIT:
                    return False, f"price {price} deviates {deviation:.1%} from {pair} recent avg {avg:.2f}"
        history.append(price)
        return True, "OK"
