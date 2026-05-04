"""Risk limits and runtime risk manager.

`RiskLimits` is the configurable surface (loaded from BotConfig).
`RiskManager` enforces those limits before each trade and tracks running
state — daily PnL, drawdown, consecutive losses, hourly frequency.

Absolute hardcoded ceilings live in `safety.killswitch` and run *after* this.
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Deque, Optional
import time

logger = logging.getLogger(__name__)

# Subset of RiskLimits fields that may be mutated at runtime via update_limit().
# Anything not listed here is immutable after config load.
_UPDATEABLE_LIMITS = frozenset({
    'max_trade_usd',
    'max_trade_pct',
    'max_loss_per_trade',
    'max_daily_loss',
    'max_drawdown_pct',
    'max_trades_per_hour',
    'consecutive_loss_limit',
})

_INT_FIELDS = frozenset({'max_trades_per_hour', 'consecutive_loss_limit'})


@dataclass
class RiskLimits:
    """Configurable risk thresholds — values typically come from BotConfig.risk."""
    # Per-trade limits
    max_trade_usd:           Decimal = Decimal('20')
    max_trade_pct:           Decimal = Decimal('0.20')   # of current capital

    # Position limits
    max_position_per_token:  Decimal = Decimal('30')
    max_open_positions:      int     = 1

    # Loss limits
    max_loss_per_trade:      Decimal = Decimal('5')
    max_daily_loss:          Decimal = Decimal('15')      # absolute USD value of daily loss
    max_drawdown_pct:        Decimal = Decimal('0.20')

    # Frequency limits
    max_trades_per_hour:     int     = 20
    consecutive_loss_limit:  int     = 3


@dataclass
class RiskManager:
    """Stateful enforcement of RiskLimits across a session."""
    limits:           RiskLimits
    initial_capital:  Decimal
    peak_capital:     Decimal           = field(init=False)
    current_capital:  Decimal           = field(init=False)
    daily_pnl:        Decimal           = field(default_factory=lambda: Decimal('0'))
    consecutive_losses: int             = 0
    _trade_timestamps: Deque[float]     = field(default_factory=deque)
    _last_daily_reset: float            = field(default_factory=time.time)

    def __post_init__(self):
        self.peak_capital = self.initial_capital
        self.current_capital = self.initial_capital

    @property
    def trades_this_hour(self) -> int:
        cutoff = time.time() - 3600
        while self._trade_timestamps and self._trade_timestamps[0] < cutoff:
            self._trade_timestamps.popleft()
        return len(self._trade_timestamps)

    @property
    def drawdown_pct(self) -> Decimal:
        if self.peak_capital <= 0:
            return Decimal('0')
        return (self.peak_capital - self.current_capital) / self.peak_capital

    def check_pre_trade(self, signal) -> tuple[bool, str]:
        """Run all limits; returns (allowed, reason)."""
        trade_value = signal.size * signal.cex_price

        if trade_value > self.limits.max_trade_usd:
            return False, f"Trade ${trade_value:.2f} > max_trade_usd ${self.limits.max_trade_usd}"

        if self.current_capital > 0:
            cap_limit = self.current_capital * self.limits.max_trade_pct
            if trade_value > cap_limit:
                return False, f"Trade ${trade_value:.2f} > {self.limits.max_trade_pct:.0%} of capital ${self.current_capital:.2f}"

        if self.daily_pnl <= -self.limits.max_daily_loss:
            return False, f"Daily loss ${self.daily_pnl:.2f} hit limit ${self.limits.max_daily_loss}"

        if self.drawdown_pct >= self.limits.max_drawdown_pct:
            return False, f"Drawdown {self.drawdown_pct:.1%} >= limit {self.limits.max_drawdown_pct:.0%}"

        if self.consecutive_losses >= self.limits.consecutive_loss_limit:
            return False, f"{self.consecutive_losses} consecutive losses (limit {self.limits.consecutive_loss_limit})"

        if self.trades_this_hour >= self.limits.max_trades_per_hour:
            return False, f"Hourly trade count {self.trades_this_hour} hit limit {self.limits.max_trades_per_hour}"

        return True, "OK"

    def record_trade(self, pnl: Decimal) -> None:
        """Update state with the realised PnL of a finished trade."""
        self.daily_pnl += pnl
        self.current_capital += pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital
        self._trade_timestamps.append(time.time())
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def reset_daily(self) -> None:
        """Manual call at start of a new trading day. Doesn't touch capital/peak."""
        self.daily_pnl = Decimal('0')
        self.consecutive_losses = 0
        self._last_daily_reset = time.time()

    def maybe_reset_daily(self, now: Optional[float] = None) -> bool:
        """Auto-reset if 24h passed since last reset. Returns True if a reset happened."""
        now = now or time.time()
        if now - self._last_daily_reset >= 86_400:
            self.reset_daily()
            return True
        return False

    def snapshot(self) -> dict:
        """Cheap dict for logging/Telegram summaries — no secrets."""
        return {
            'capital':     str(self.current_capital),
            'peak':        str(self.peak_capital),
            'daily_pnl':   str(self.daily_pnl),
            'drawdown':    f"{self.drawdown_pct:.1%}",
            'consec_loss': self.consecutive_losses,
            'tph':         self.trades_this_hour,
        }

    def update_limit(self, name: str, value) -> tuple[bool, str]:
        """Mutate a single RiskLimits field at runtime.

        Validates membership in `_UPDATEABLE_LIMITS`, coerces type, requires
        positive value, and rejects anything that would breach an ABSOLUTE_*
        ceiling. On success: setattr + logs `LIMIT_UPDATE | name=value`.
        """
        # Local import to avoid circular dependency at module import time.
        from src.safety.killswitch import (
            ABSOLUTE_MAX_DAILY_LOSS,
            ABSOLUTE_MAX_TRADE_USD,
            ABSOLUTE_MAX_TRADES_PER_HOUR,
        )

        if name not in _UPDATEABLE_LIMITS:
            return False, f"Unknown or immutable limit: {name}"

        try:
            if name in _INT_FIELDS:
                coerced = int(value)
            else:
                coerced = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return False, f"Invalid value for {name}: {value!r}"

        if coerced <= 0:
            return False, f"{name} must be > 0"

        if name == 'max_trade_usd' and coerced > ABSOLUTE_MAX_TRADE_USD:
            return False, (
                f"max_trade_usd {coerced} exceeds ABSOLUTE_MAX_TRADE_USD "
                f"{ABSOLUTE_MAX_TRADE_USD}"
            )
        if name == 'max_daily_loss' and coerced > ABSOLUTE_MAX_DAILY_LOSS:
            return False, (
                f"max_daily_loss {coerced} exceeds ABSOLUTE_MAX_DAILY_LOSS "
                f"{ABSOLUTE_MAX_DAILY_LOSS}"
            )
        if name == 'max_trades_per_hour' and coerced > ABSOLUTE_MAX_TRADES_PER_HOUR:
            return False, (
                f"max_trades_per_hour {coerced} exceeds ABSOLUTE_MAX_TRADES_PER_HOUR "
                f"{ABSOLUTE_MAX_TRADES_PER_HOUR}"
            )
        if name in ('max_trade_pct', 'max_drawdown_pct') and coerced > Decimal('1'):
            return False, f"{name} must be <= 1.0 (got {coerced})"

        setattr(self.limits, name, coerced)
        logger.info("LIMIT_UPDATE | %s=%s", name, coerced)
        return True, f"{name} updated to {coerced}"
