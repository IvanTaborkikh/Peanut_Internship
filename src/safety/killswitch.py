"""Kill switches and absolute hard limits.

Three layers of stopping power:

1. **File-based manual kill switch** — `touch /tmp/arb_bot_kill` and the bot
   notices on the next tick. `rm` to allow restart. Survives restarts.
2. **AutoKillSwitch** — programmatic conditions (capital fell <50%, error rate).
3. **ABSOLUTE_* constants + safety_check()** — non-configurable ceilings that
   run *after* RiskManager. If RiskLimits is mis-configured, this still saves you.
"""
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


# ------------------------------------------------------------------------
# ABSOLUTE LIMITS — DO NOT make these configurable. They are the last line
# of defence when RiskLimits is mis-configured or someone "just bumps it
# for one trade". If you want to change these, do it in source review.
# ------------------------------------------------------------------------
ABSOLUTE_MAX_TRADE_USD       = Decimal('25')
ABSOLUTE_MAX_DAILY_LOSS      = Decimal('20')
ABSOLUTE_MIN_CAPITAL         = Decimal('50')
ABSOLUTE_MAX_TRADES_PER_HOUR = 30


KILL_SWITCH_FILE = "/tmp/arb_bot_kill"

logger = logging.getLogger(__name__)


def is_kill_switch_active(path: str = KILL_SWITCH_FILE) -> bool:
    """True iff the kill-switch file exists. Operators activate via `touch`."""
    return os.path.exists(path)


def safety_check(
    trade_usd: Decimal,
    daily_pnl: Decimal,
    total_capital: Decimal,
    trades_this_hour: int,
) -> tuple[bool, str]:
    """Final non-negotiable gate. Runs *after* RiskManager.check_pre_trade.

    Returns (allowed, reason). Anything that breaches an ABSOLUTE_* constant
    is rejected here regardless of config.
    """
    if trade_usd > ABSOLUTE_MAX_TRADE_USD:
        return False, f"Trade ${trade_usd:.2f} exceeds ABSOLUTE_MAX_TRADE_USD ${ABSOLUTE_MAX_TRADE_USD}"
    if daily_pnl <= -ABSOLUTE_MAX_DAILY_LOSS:
        return False, f"Daily loss ${daily_pnl:.2f} hit ABSOLUTE_MAX_DAILY_LOSS ${ABSOLUTE_MAX_DAILY_LOSS}"
    if total_capital < ABSOLUTE_MIN_CAPITAL:
        return False, f"Capital ${total_capital:.2f} < ABSOLUTE_MIN_CAPITAL ${ABSOLUTE_MIN_CAPITAL}"
    if trades_this_hour >= ABSOLUTE_MAX_TRADES_PER_HOUR:
        return False, f"{trades_this_hour} trades/hr hit ABSOLUTE_MAX_TRADES_PER_HOUR {ABSOLUTE_MAX_TRADES_PER_HOUR}"
    return True, "OK"


@dataclass
class AutoKillSwitch:
    """Trips the bot programmatically. Caller checks `triggered` each tick."""
    triggered:         bool            = False
    reason:            Optional[str]   = None
    triggered_at:      Optional[float] = None
    capital_floor_pct: Decimal         = Decimal('0.5')
    max_errors_1h:     int             = 50
    _error_timestamps: deque           = field(default_factory=deque, init=False, repr=False)

    @property
    def error_count_1h(self) -> int:
        self._prune_old_errors()
        return len(self._error_timestamps)

    def _prune_old_errors(self) -> None:
        cutoff = time.time() - 3600
        while self._error_timestamps and self._error_timestamps[0] < cutoff:
            self._error_timestamps.popleft()

    def check(self, risk_manager) -> bool:
        """Re-evaluate against the live RiskManager. Returns triggered state."""
        if self.triggered:
            return True

        floor = risk_manager.initial_capital * self.capital_floor_pct
        if risk_manager.current_capital < floor:
            self.trigger(f"Capital ${risk_manager.current_capital:.2f} below "
                         f"{self.capital_floor_pct:.0%} floor ${floor:.2f}")
            return True

        if self.error_count_1h > self.max_errors_1h:
            self.trigger(f"Errors in last hour: {self.error_count_1h} > {self.max_errors_1h}")
            return True

        return False

    def record_error(self) -> None:
        self._error_timestamps.append(time.time())

    def trigger(self, reason: str) -> None:
        self.triggered = True
        self.reason = reason
        self.triggered_at = time.time()
        logger.critical("AUTO KILL SWITCH: %s", reason)

    def reset(self) -> None:
        """Manual reset after operator inspection."""
        self.triggered = False
        self.reason = None
        self.triggered_at = None
        self._error_timestamps.clear()


def write_heartbeat(path: str = "/tmp/arb_bot_heartbeat") -> None:
    """Write current timestamp to a heartbeat file. External watchdog reads this."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(str(time.time()))
    except OSError as e:
        logger.warning("Failed to write heartbeat: %s", e)
