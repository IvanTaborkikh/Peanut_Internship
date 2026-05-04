"""Execution metrics + windowed error tracking for the bot pipeline."""
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Tuple


@dataclass
class ExecutionMetrics:
    """Counters along the signal → trade pipeline. Hourly summary derives from these."""
    signals_generated:         int = 0
    signals_passed_validator:  int = 0
    signals_passed_risk:       int = 0
    signals_passed_score:      int = 0
    trades_executed:           int = 0
    trades_successful:         int = 0
    trades_failed:             int = 0

    @property
    def execution_rate(self) -> float:
        """Percent of generated signals that became executed trades."""
        if self.signals_generated == 0:
            return 0.0
        return self.trades_executed / self.signals_generated * 100.0

    def snapshot(self) -> dict:
        return {
            'signals_generated':        self.signals_generated,
            'signals_passed_validator': self.signals_passed_validator,
            'signals_passed_risk':      self.signals_passed_risk,
            'signals_passed_score':     self.signals_passed_score,
            'trades_executed':          self.trades_executed,
            'trades_successful':        self.trades_successful,
            'trades_failed':            self.trades_failed,
            'execution_rate_pct':       round(self.execution_rate, 1),
        }


@dataclass
class ErrorTracker:
    """Records errors with timestamps; counts and groups within a time window."""
    window_minutes: int = 60
    _events: Deque[Tuple[float, str]] = field(default_factory=deque)

    def add(self, error_type: str) -> None:
        self._events.append((time.time(), error_type))
        self._evict()

    def _evict(self) -> None:
        cutoff = time.time() - self.window_minutes * 60
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def count(self) -> int:
        self._evict()
        return len(self._events)

    def summary(self) -> dict[str, int]:
        """Counts grouped by error_type within the window."""
        self._evict()
        out: dict[str, int] = {}
        for _, t in self._events:
            out[t] = out.get(t, 0) + 1
        return out
