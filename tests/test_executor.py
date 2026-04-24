import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock

from src.strategy.signal import Signal, Direction
from src.executor.engine import Executor, ExecutorConfig, ExecutorState
from src.executor.recovery import CircuitBreaker, CircuitBreakerConfig
from src.core.events import EventBus, PriceTickEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(score: Decimal = Decimal('70')) -> Signal:
    now = time.time()
    return Signal(
        signal_id='ETHUSDT_test0001',
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'),
        dex_price=Decimal('2016'),
        spread_bps=Decimal('80'),
        size=Decimal('0.1'),
        expected_gross_pnl=Decimal('16'),
        expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'),
        score=score,
        timestamp=now,
        expiry=now + 10,
        inventory_ok=True,
        within_limits=True,
    )


def make_executor(config: ExecutorConfig = None) -> Executor:
    return Executor(
        exchange_client=MagicMock(),
        pricing_module=None,
        inventory_tracker=MagicMock(),
        config=config or ExecutorConfig(simulation_mode=True, use_flashbots=False),
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_execute_success_cex_first():
    """Both legs fill in simulation → state DONE."""
    executor = make_executor(ExecutorConfig(simulation_mode=True, use_flashbots=False))
    signal = make_signal()
    ctx = asyncio.run(executor.execute(signal))
    assert ctx.state == ExecutorState.DONE
    assert ctx.actual_net_pnl is not None
    assert ctx.finished_at is not None


def test_execute_success_dex_first():
    """DEX-first path (Flashbots mode) → state DONE."""
    executor = make_executor(ExecutorConfig(simulation_mode=True, use_flashbots=True))
    signal = make_signal()
    ctx = asyncio.run(executor.execute(signal))
    assert ctx.state == ExecutorState.DONE


def test_execute_records_fill_prices():
    executor = make_executor()
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.leg1_fill_price is not None
    assert ctx.leg2_fill_price is not None
    assert ctx.leg1_fill_size == Decimal('0.1')


# ---------------------------------------------------------------------------
# PnL correctness (#10, #11)
# ---------------------------------------------------------------------------

def test_execute_pnl_value_correct():
    """Verify _calculate_pnl produces a positive, numerically correct result."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=True, use_flashbots=False,
        gas_cost_usd=Decimal('0.5'),
    ))
    ctx = asyncio.run(executor.execute(make_signal()))
    # Simulation: cex fill = 2000*1.0001 = 2000.2, dex fill = 2016*0.9998 = 2015.5968
    # gross = (2015.5968 - 2000.2) * 0.1 = 1.53968
    # cex_fee = 0.1 * 2000.2 * 0.001 = 0.20002
    # dex_fee = 0.1 * 2015.5968 * 0.003 = 0.60467904
    # net = 1.53968 - 0.20002 - 0.60467904 - 0.5 ≈ 0.235
    assert ctx.state == ExecutorState.DONE
    assert ctx.actual_net_pnl > 0
    assert Decimal('0.15') < ctx.actual_net_pnl < Decimal('0.40')


def test_execute_sets_execution_quality():
    """execution_quality is set after a successful trade."""
    executor = make_executor()
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.DONE
    assert ctx.execution_quality is not None
    assert ctx.execution_quality > 0


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

def test_execute_invalid_signal_rejected():
    """Expired signal is REJECTED before any trade."""
    executor = make_executor()
    now = time.time()
    expired = Signal(
        signal_id='ETHUSDT_expired',
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('70'),
        timestamp=now - 10,
        expiry=now - 1,
        inventory_ok=True, within_limits=True,
    )
    ctx = asyncio.run(executor.execute(expired))
    assert ctx.state == ExecutorState.REJECTED
    assert ctx.error == 'Signal invalid'


def test_execute_zero_score_signal_rejected():
    """Signal with score=0 is REJECTED before any trade."""
    executor = make_executor()
    ctx = asyncio.run(executor.execute(make_signal(score=Decimal('0'))))
    assert ctx.state == ExecutorState.REJECTED


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------

def test_execute_cex_timeout():
    """Slow CEX leg exceeding timeout → FAILED."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
        leg1_timeout=0.01,
    ))

    async def slow_cex(signal, size=None):
        await asyncio.sleep(1)
        return {'success': True, 'price': Decimal('2000'), 'filled': Decimal('0.1')}

    executor._execute_cex_leg = slow_cex
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.FAILED
    assert 'timeout' in ctx.error.lower()


def test_execute_dex_timeout_triggers_unwind():
    """DEX timeout after CEX fill → unwind → FAILED."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
        leg1_timeout=5.0,
        leg2_timeout=0.01,
    ))

    async def fast_cex(signal, size=None):
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size}

    async def slow_dex(signal, size):
        await asyncio.sleep(1)
        return {'success': True, 'price': Decimal('2016'), 'filled': size}

    async def ok_unwind(ctx):
        return True

    executor._execute_cex_leg = fast_cex
    executor._execute_dex_leg = slow_dex
    executor._unwind = ok_unwind

    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.FAILED
    assert 'timeout' in ctx.error.lower()


# ---------------------------------------------------------------------------
# Partial fill
# ---------------------------------------------------------------------------

def test_partial_fill_below_threshold_rejected():
    """Fill < min_fill_ratio → unwind → PARTIAL state."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
        min_fill_ratio=Decimal('0.8'),
    ))

    async def partial_cex(signal, size=None):
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size * Decimal('0.5')}

    async def ok_unwind(ctx):
        return True

    executor._execute_cex_leg = partial_cex
    executor._unwind = ok_unwind
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.PARTIAL
    assert 'fill' in ctx.error.lower()


def test_partial_fill_above_threshold_continues():
    """Fill ≥ min_fill_ratio → proceeds to leg 2."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
        min_fill_ratio=Decimal('0.8'),
        leg2_timeout=5.0,
    ))

    async def almost_full_cex(signal, size=None):
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size * Decimal('0.9')}

    async def ok_dex(signal, size):
        return {'success': True, 'price': Decimal('2016'), 'filled': size}

    executor._execute_cex_leg = almost_full_cex
    executor._execute_dex_leg = ok_dex
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.DONE


def test_partial_fill_unwind_fails():
    """Fill < min_fill_ratio AND unwind fails → UNWIND_FAILED state."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
        min_fill_ratio=Decimal('0.8'),
    ))

    async def partial_cex(signal, size=None):
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size * Decimal('0.5')}

    async def failing_unwind(ctx):
        return False

    executor._execute_cex_leg = partial_cex
    executor._unwind = failing_unwind
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.UNWIND_FAILED
    assert 'UNWIND FAILED' in ctx.error


# ---------------------------------------------------------------------------
# DEX-first (Flashbots) unwind failure (#12)
# ---------------------------------------------------------------------------

def test_dex_first_cex_timeout_unwind_fails():
    """CEX timeout after DEX fill, unwind fails → UNWIND_FAILED."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=True,
        leg2_timeout=5.0,
        leg1_timeout=0.01,
    ))

    async def fast_dex(signal, size):
        return {'success': True, 'price': Decimal('2016'), 'filled': size}

    async def slow_cex(signal, size=None):
        await asyncio.sleep(1)
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size}

    async def failing_unwind(ctx):
        return False

    executor._execute_dex_leg = fast_dex
    executor._execute_cex_leg = slow_cex
    executor._unwind = failing_unwind
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.UNWIND_FAILED


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_blocks_execution():
    """Open circuit breaker → REJECTED (nothing was traded)."""
    executor = make_executor()
    executor.circuit_breaker.tripped_at = time.time()
    ctx = asyncio.run(executor.execute(make_signal()))
    assert ctx.state == ExecutorState.REJECTED
    assert 'circuit breaker' in ctx.error.lower()


def test_circuit_breaker_trips_after_failures():
    """3 failed executions trip the circuit breaker."""
    executor = make_executor(ExecutorConfig(
        simulation_mode=False,
        use_flashbots=False,
    ))
    executor.circuit_breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=3, window_seconds=60)
    )

    async def failing_cex(signal, size=None):
        return {'success': False, 'error': 'rejected'}

    executor._execute_cex_leg = failing_cex

    for _ in range(3):
        sig = Signal.create(
            pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
            cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
            size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
            expected_net_pnl=Decimal('10'), score=Decimal('70'),
            expiry=time.time() + 10,
            inventory_ok=True, within_limits=True,
        )
        asyncio.run(executor.execute(sig))

    assert executor.circuit_breaker.is_open() is True


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

def test_replay_protection_blocks_duplicate_signal():
    """Same signal_id → second attempt is REJECTED (nothing traded)."""
    executor = make_executor()
    signal = make_signal()

    ctx1 = asyncio.run(executor.execute(signal))
    assert ctx1.state == ExecutorState.DONE

    ctx2 = asyncio.run(executor.execute(signal))
    assert ctx2.state == ExecutorState.REJECTED
    assert 'duplicate' in ctx2.error.lower()


def test_replay_protection_allows_different_signals():
    """Different signal_ids execute independently."""
    executor = make_executor()
    sig_a = Signal.create(
        pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('70'),
        expiry=time.time() + 10,
        inventory_ok=True, within_limits=True,
    )
    sig_b = Signal.create(
        pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('70'),
        expiry=time.time() + 10,
        inventory_ok=True, within_limits=True,
    )
    ctx_a = asyncio.run(executor.execute(sig_a))
    ctx_b = asyncio.run(executor.execute(sig_b))
    assert ctx_a.state == ExecutorState.DONE
    assert ctx_b.state == ExecutorState.DONE


# ---------------------------------------------------------------------------
# Unexpected exception safety (#2 — try/finally in execute)
# ---------------------------------------------------------------------------

def test_unexpected_exception_still_records_replay_and_cb():
    """If execution raises unexpectedly, replay_protection and circuit_breaker are still updated."""
    executor = make_executor(ExecutorConfig(simulation_mode=False, use_flashbots=False))

    async def exploding_cex(signal, size=None):
        raise RuntimeError("something blew up")

    executor._execute_cex_leg = exploding_cex
    signal = make_signal()
    ctx = asyncio.run(executor.execute(signal))

    assert ctx.state == ExecutorState.FAILED
    assert executor.replay_protection.is_duplicate(signal)
    assert executor.circuit_breaker.failures  # failure was recorded


# ---------------------------------------------------------------------------
# EventBus error isolation (#5, #13)
# ---------------------------------------------------------------------------

def test_eventbus_handler_error_does_not_skip_remaining():
    """If one handler raises, remaining handlers for the same event still fire."""
    bus = EventBus()
    results = []

    async def bad_handler(e):
        raise RuntimeError("intentional test error")

    async def good_handler(e):
        results.append("fired")

    bus.subscribe(PriceTickEvent, bad_handler)
    bus.subscribe(PriceTickEvent, good_handler)

    asyncio.run(bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1'))))
    assert results == ["fired"]
