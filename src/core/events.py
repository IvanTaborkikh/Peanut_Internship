import inspect
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from src.strategy.signal import Signal
from src.executor.engine import ExecutionContext


@dataclass
class PriceTickEvent:
    pair: str
    size: Decimal


@dataclass
class SignalGeneratedEvent:
    signal: Signal


@dataclass
class SignalScoredEvent:
    signal: Signal


@dataclass
class ExecutionDoneEvent:
    ctx: ExecutionContext


class EventBus:
    def __init__(self):
        self._handlers: dict[type, list[Callable]] = {}

    def subscribe(self, event_type: type, handler: Callable):
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Any):
        for handler in self._handlers.get(type(event), []):
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                name = getattr(handler, '__name__', repr(handler))
                logging.warning("EventBus: handler %s raised %s: %s", name, type(e).__name__, e, exc_info=True)
