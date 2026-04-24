import time
from decimal import Decimal


from src.executor.recovery import CircuitBreaker, CircuitBreakerConfig, ReplayProtection
from src.strategy.signal import Signal, Direction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(signal_id: str = 'ETHUSDT_abc12345') -> Signal:
    now = time.time()
    return Signal(
        signal_id=signal_id,
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('70'),
        timestamp=now, expiry=now + 5,
        inventory_ok=True, within_limits=True,
    )


def fast_config(threshold: int = 3, window: float = 60, cooldown: float = 1) -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        failure_threshold=threshold,
        window_seconds=window,
        cooldown_seconds=cooldown,
    )


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker()
    assert cb.is_open() is False


def test_circuit_breaker_trips_after_threshold():
    """3 failures in window trips breaker."""
    cb = CircuitBreaker(fast_config(threshold=3))
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is True


def test_circuit_breaker_does_not_trip_below_threshold():
    cb = CircuitBreaker(fast_config(threshold=5))
    for _ in range(4):
        cb.record_failure()
    assert cb.is_open() is False


def test_circuit_breaker_resets_after_cooldown():
    """Breaker resets after cooldown_seconds have passed."""
    cb = CircuitBreaker(fast_config(threshold=1, cooldown=0.05))
    cb.record_failure()
    assert cb.is_open() is True
    time.sleep(0.1)
    assert cb.is_open() is False


def test_circuit_breaker_ignores_old_failures():
    """Failures outside the window don't count toward threshold."""
    cb = CircuitBreaker(fast_config(threshold=3, window=0.05))
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.1)          # window expires
    cb.record_failure()      # only 1 fresh failure
    assert cb.is_open() is False


def test_time_until_reset_zero_when_closed():
    cb = CircuitBreaker()
    assert cb.time_until_reset() == 0


def test_time_until_reset_positive_when_open():
    cb = CircuitBreaker(fast_config(threshold=1, cooldown=60))
    cb.record_failure()
    remaining = cb.time_until_reset()
    assert 0 < remaining <= 60


def test_record_success_does_not_trip():
    cb = CircuitBreaker(fast_config(threshold=3))
    cb.record_success()
    cb.record_success()
    assert cb.is_open() is False


# ---------------------------------------------------------------------------
# ReplayProtection
# ---------------------------------------------------------------------------

def test_replay_allows_new_signal():
    """Fresh signal_id is not a duplicate."""
    rp = ReplayProtection()
    sig = make_signal('ETHUSDT_new00001')
    assert rp.is_duplicate(sig) is False


def test_replay_blocks_duplicate_after_mark():
    """Same signal_id blocked after mark_executed."""
    rp = ReplayProtection()
    sig = make_signal('ETHUSDT_dup00001')
    rp.mark_executed(sig)
    assert rp.is_duplicate(sig) is True


def test_replay_allows_different_ids():
    """Different signal_ids are independent."""
    rp = ReplayProtection()
    sig_a = make_signal('ETHUSDT_aaa00001')
    sig_b = make_signal('ETHUSDT_bbb00002')
    rp.mark_executed(sig_a)
    assert rp.is_duplicate(sig_b) is False


def test_replay_expires_after_ttl():
    """Executed signal is forgiven after TTL seconds."""
    rp = ReplayProtection(ttl_seconds=0.05)
    sig = make_signal('ETHUSDT_exp00001')
    rp.mark_executed(sig)
    assert rp.is_duplicate(sig) is True
    time.sleep(0.1)
    assert rp.is_duplicate(sig) is False


def test_replay_cleanup_removes_old_entries():
    rp = ReplayProtection(ttl_seconds=0.05)
    for i in range(10):
        s = make_signal(f'ETHUSDT_{i:08d}')
        rp.mark_executed(s)
    time.sleep(0.1)
    rp._cleanup()
    assert len(rp.executed) == 0
