"""Tests for safety.killswitch — file kill switch, AutoKillSwitch, safety_check."""
from decimal import Decimal

from src.safety.killswitch import (
    ABSOLUTE_MAX_DAILY_LOSS,
    ABSOLUTE_MAX_TRADE_USD,
    ABSOLUTE_MAX_TRADES_PER_HOUR,
    ABSOLUTE_MIN_CAPITAL,
    AutoKillSwitch,
    is_kill_switch_active,
    safety_check,
    write_heartbeat,
)
from src.safety.limits import RiskLimits, RiskManager


# --- file-based kill switch ---

def test_kill_switch_inactive_when_file_absent(tmp_path):
    path = tmp_path / "kill"
    assert not is_kill_switch_active(str(path))


def test_kill_switch_active_when_file_present(tmp_path):
    path = tmp_path / "kill"
    path.touch()
    assert is_kill_switch_active(str(path))


# --- AutoKillSwitch ---

def test_auto_killswitch_trips_below_capital_floor():
    rm = RiskManager(RiskLimits(), Decimal('100'))
    rm.record_trade(Decimal('-60'))            # capital = 40, < 50% of 100
    aks = AutoKillSwitch()
    assert aks.check(rm) is True
    assert aks.triggered
    assert 'Capital' in aks.reason


def test_auto_killswitch_does_not_trip_above_floor():
    rm = RiskManager(RiskLimits(), Decimal('100'))
    rm.record_trade(Decimal('-30'))            # capital = 70, > 50%
    aks = AutoKillSwitch()
    assert aks.check(rm) is False
    assert not aks.triggered


def test_auto_killswitch_trips_on_too_many_errors():
    rm = RiskManager(RiskLimits(), Decimal('100'))
    aks = AutoKillSwitch(max_errors_1h=10)
    for _ in range(11):
        aks.record_error()
    assert aks.check(rm) is True
    assert 'Errors' in aks.reason


def test_auto_killswitch_reset_clears_state():
    rm = RiskManager(RiskLimits(), Decimal('100'))
    rm.record_trade(Decimal('-60'))
    aks = AutoKillSwitch()
    aks.check(rm)
    assert aks.triggered
    aks.reset()
    assert not aks.triggered
    assert aks.error_count_1h == 0


# --- safety_check (final gate, ABSOLUTE_*) ---

def test_safety_check_passes_normal_trade():
    ok, _ = safety_check(Decimal('5'), Decimal('-2'), Decimal('100'), 1)
    assert ok


def test_safety_check_blocks_above_absolute_max_trade():
    ok, reason = safety_check(ABSOLUTE_MAX_TRADE_USD + Decimal('1'),
                              Decimal('0'), Decimal('100'), 1)
    assert not ok
    assert 'ABSOLUTE_MAX_TRADE_USD' in reason


def test_safety_check_blocks_at_absolute_daily_loss():
    ok, reason = safety_check(Decimal('1'), -ABSOLUTE_MAX_DAILY_LOSS,
                              Decimal('100'), 1)
    assert not ok
    assert 'ABSOLUTE_MAX_DAILY_LOSS' in reason


def test_safety_check_blocks_below_absolute_min_capital():
    ok, reason = safety_check(Decimal('1'), Decimal('0'),
                              ABSOLUTE_MIN_CAPITAL - Decimal('1'), 1)
    assert not ok
    assert 'ABSOLUTE_MIN_CAPITAL' in reason


def test_safety_check_blocks_at_absolute_hourly_limit():
    ok, reason = safety_check(Decimal('1'), Decimal('0'),
                              Decimal('100'), ABSOLUTE_MAX_TRADES_PER_HOUR)
    assert not ok
    assert 'ABSOLUTE_MAX_TRADES_PER_HOUR' in reason


# --- heartbeat ---

def test_write_heartbeat_creates_file_with_recent_timestamp(tmp_path):
    import time
    path = tmp_path / 'hb'
    write_heartbeat(str(path))
    content = path.read_text()
    written = float(content)
    assert abs(time.time() - written) < 5
