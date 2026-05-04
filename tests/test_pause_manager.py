"""Unit tests for TradingPauseManager."""
from datetime import datetime, timedelta

import pytest

from src.safety import TradingPauseManager


def test_default_not_paused():
    pm = TradingPauseManager()
    assert pm.is_paused() is False
    assert pm.time_remaining() == 0.0
    assert pm.pause_reason is None


def test_pause_then_active():
    pm = TradingPauseManager()
    pm.pause(5, "test")
    assert pm.is_paused() is True
    assert pm.pause_reason == "test"
    assert pm.time_remaining() > 0


def test_auto_resume_on_deadline():
    pm = TradingPauseManager()
    pm.pause(1, "test")
    # Force deadline into the past.
    pm.paused_until = datetime.now() - timedelta(seconds=1)
    assert pm.is_paused() is False
    assert pm.pause_reason is None
    assert pm.paused_until is None


def test_cancel_clears_state():
    pm = TradingPauseManager()
    pm.pause(30, "test")
    pm.cancel()
    assert pm.is_paused() is False
    assert pm.pause_reason is None


def test_cancel_when_not_paused_is_noop():
    pm = TradingPauseManager()
    pm.cancel()
    assert pm.is_paused() is False


def test_repeated_pause_overrides():
    pm = TradingPauseManager()
    pm.pause(1, "first")
    first_until = pm.paused_until
    pm.pause(60, "second")
    assert pm.pause_reason == "second"
    assert pm.paused_until > first_until


def test_pause_invalid_duration_raises():
    pm = TradingPauseManager()
    with pytest.raises(ValueError):
        pm.pause(0, "x")
    with pytest.raises(ValueError):
        pm.pause(-5, "x")
