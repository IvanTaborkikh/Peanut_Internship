"""Unit tests for RiskManager.update_limit() runtime mutation."""
from decimal import Decimal

from src.safety import RiskLimits, RiskManager
from src.safety.killswitch import (
    ABSOLUTE_MAX_DAILY_LOSS,
    ABSOLUTE_MAX_TRADE_USD,
    ABSOLUTE_MAX_TRADES_PER_HOUR,
)


def _rm() -> RiskManager:
    return RiskManager(RiskLimits(), Decimal('100'))


def test_update_max_trade_usd_ok():
    rm = _rm()
    ok, msg = rm.update_limit('max_trade_usd', '12')
    assert ok and 'updated' in msg
    assert rm.limits.max_trade_usd == Decimal('12')


def test_update_consecutive_loss_limit_int():
    rm = _rm()
    ok, _ = rm.update_limit('consecutive_loss_limit', 5)
    assert ok
    assert rm.limits.consecutive_loss_limit == 5


def test_update_unknown_field_rejected():
    rm = _rm()
    ok, msg = rm.update_limit('totally_made_up', 1)
    assert not ok and 'Unknown' in msg


def test_immutable_field_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_position_per_token', 5)
    assert not ok


def test_negative_value_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_trade_usd', '-1')
    assert not ok


def test_zero_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_trade_usd', 0)
    assert not ok


def test_above_absolute_max_trade_rejected():
    rm = _rm()
    over = ABSOLUTE_MAX_TRADE_USD + Decimal('1')
    ok, msg = rm.update_limit('max_trade_usd', str(over))
    assert not ok
    assert 'ABSOLUTE_MAX_TRADE_USD' in msg


def test_above_absolute_max_daily_loss_rejected():
    rm = _rm()
    over = ABSOLUTE_MAX_DAILY_LOSS + Decimal('1')
    ok, _ = rm.update_limit('max_daily_loss', str(over))
    assert not ok


def test_above_absolute_max_trades_per_hour_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_trades_per_hour', ABSOLUTE_MAX_TRADES_PER_HOUR + 1)
    assert not ok


def test_pct_above_one_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_trade_pct', '1.5')
    assert not ok


def test_invalid_value_rejected():
    rm = _rm()
    ok, _ = rm.update_limit('max_trade_usd', 'not-a-number')
    assert not ok
