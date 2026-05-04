"""Time-bounded trading pause with auto-resume.

Distinct from `CircuitBreaker` (executor-failure burst) and the bot-level
`paused` boolean. This one carries a deadline + reason and lets the bot keep
running (reading prices, generating signals) without executing trades.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradingPauseManager:
    """Pause trade execution for a bounded duration; auto-resume on deadline."""
    paused_until: Optional[datetime] = None
    pause_reason: Optional[str] = None

    def pause(self, duration_minutes: int, reason: str) -> None:
        """Pause for N minutes. Repeated calls overwrite the previous deadline."""
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be > 0")
        self.paused_until = datetime.now() + timedelta(minutes=duration_minutes)
        self.pause_reason = reason
        logger.warning(
            "TRADING_PAUSED | reason=%s | duration=%dmin | until=%s",
            reason, duration_minutes, self.paused_until.isoformat(timespec='seconds'),
        )

    def is_paused(self) -> bool:
        """True iff currently paused. Auto-clears state on deadline."""
        if self.paused_until is None:
            return False
        if datetime.now() >= self.paused_until:
            logger.info("TRADING_RESUMED | prior_reason=%s", self.pause_reason)
            self.paused_until = None
            self.pause_reason = None
            return False
        return True

    def time_remaining(self) -> float:
        """Seconds until auto-resume. Zero if not paused."""
        if not self.is_paused():
            return 0.0
        return (self.paused_until - datetime.now()).total_seconds()

    def cancel(self) -> None:
        """Manual resume — clear pause state immediately."""
        if self.paused_until is None:
            return
        logger.info("TRADING_RESUMED | manual cancel | prior_reason=%s", self.pause_reason)
        self.paused_until = None
        self.pause_reason = None
