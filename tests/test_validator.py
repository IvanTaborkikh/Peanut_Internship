"""Tests for safety.validator — PreTradeValidator."""
from decimal import Decimal
from types import SimpleNamespace

from src.safety.validator import PreTradeValidator


def _signal(spread_bps='50', size='0.01', cex_price='2000', dex_price='2010',
            age=0.5):
    """Stub signal — validator only reads .cex_price/.dex_price/.spread_bps/.size/.age_seconds()."""
    return SimpleNamespace(
        cex_price=Decimal(cex_price),
        dex_price=Decimal(dex_price),
        spread_bps=Decimal(spread_bps),
        size=Decimal(size),
        age_seconds=lambda: age,
    )


def test_valid_signal_accepted():
    ok, reason = PreTradeValidator().validate_signal(_signal())
    assert ok
    assert reason == 'OK'


def test_zero_or_negative_cex_price_rejected():
    ok, reason = PreTradeValidator().validate_signal(_signal(cex_price='0'))
    assert not ok
    assert 'cex_price' in reason


def test_zero_or_negative_dex_price_rejected():
    ok, reason = PreTradeValidator().validate_signal(_signal(dex_price='-1'))
    assert not ok
    assert 'dex_price' in reason


def test_zero_size_rejected():
    ok, reason = PreTradeValidator().validate_signal(_signal(size='0'))
    assert not ok
    assert 'size' in reason


def test_absurd_spread_rejected():
    ok, reason = PreTradeValidator().validate_signal(_signal(spread_bps='600'))
    assert not ok
    assert '600' in reason or 'bps' in reason


def test_stale_signal_rejected():
    ok, reason = PreTradeValidator().validate_signal(_signal(age=10.0))
    assert not ok
    assert 'age' in reason or 'old' in reason.lower() or '10' in reason


def test_validate_price_feed_first_call_seeds_history():
    v = PreTradeValidator()
    ok, _ = v.validate_price_feed(Decimal('2000'), 'ETH/USDT')
    assert ok


def test_validate_price_feed_rejects_large_deviation():
    v = PreTradeValidator(history_size=5)
    for _ in range(5):
        v.validate_price_feed(Decimal('2000'), 'ETH/USDT')
    ok, reason = v.validate_price_feed(Decimal('2200'), 'ETH/USDT')   # +10% > 5%
    assert not ok
    assert 'deviates' in reason or 'avg' in reason


def test_validate_price_feed_accepts_small_deviation():
    v = PreTradeValidator()
    for _ in range(5):
        v.validate_price_feed(Decimal('2000'), 'ETH/USDT')
    ok, _ = v.validate_price_feed(Decimal('2050'), 'ETH/USDT')        # +2.5% < 5%
    assert ok


def test_signal_age_at_boundary():
    """5.0s exactly is the cutoff — must reject anything strictly above."""
    v = PreTradeValidator()
    ok_under, _ = v.validate_signal(_signal(age=4.9))
    ok_at, _ = v.validate_signal(_signal(age=5.0))
    ok_over, _ = v.validate_signal(_signal(age=5.1))
    assert ok_under
    assert ok_at
    assert not ok_over
