"""
End-to-end bot flow test — no real exchange or blockchain needed.
Runs every scenario through the real EventBus pipeline.

Usage:
  python scripts/bot_e2e_test.py
"""
import asyncio
import logging
import sys
import time
from decimal import Decimal
from unittest.mock import MagicMock

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__file__)))

from src.core.events import EventBus, PriceTickEvent, SignalGeneratedEvent, SignalScoredEvent, ExecutionDoneEvent
from src.executor.engine import Executor, ExecutorConfig, ExecutorState
from src.executor.recovery import CircuitBreaker, CircuitBreakerConfig
from src.inventory.pnl import PnLEngine
from src.inventory.tracker import InventoryTracker, Venue
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.strategy.signal import Signal, Direction
from scripts.arb_bot import execution_to_arb_record

logging.basicConfig(level=logging.WARNING)  # suppress noise during tests

PASS = "✓"
FAIL = "✗"
_failures = []


def check(label: str, condition: bool):
    if condition:
        print(f"  {PASS} {label}")
    else:
        print(f"  {FAIL} {label}  ← FAILED")
        _failures.append(label)


# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

def make_mock_exchange(bid: float = 2000.0, ask: float = 2001.0) -> MagicMock:
    ex = MagicMock()
    ex.fetch_order_book.return_value = {
        'bids': [(bid, 10.0)],
        'asks': [(ask, 10.0)],
    }
    ex.fetch_balance.return_value = {
        'ETH':  {'free': Decimal('10'),    'locked': Decimal('0'), 'total': Decimal('10')},
        'USDT': {'free': Decimal('50000'), 'locked': Decimal('0'), 'total': Decimal('50000')},
    }
    return ex


def make_pipeline(bid=2000.0, ask=2001.0, cb_config=None, flashbots=False, signal_config=None):
    exchange = make_mock_exchange(bid, ask)
    inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
    inventory.update_from_cex(Venue.BINANCE, exchange.fetch_balance())
    inventory.update_from_wallet(Venue.WALLET, {'ETH': Decimal('10'), 'USDT': Decimal('50000')})

    pnl = PnLEngine()
    base_signal_config = {'min_profit_usd': 0.1, 'cooldown_seconds': 0}
    if signal_config:
        base_signal_config.update(signal_config)
    generator = SignalGenerator(
        exchange, None, inventory,
        FeeStructure(gas_cost_usd=Decimal('0.5')),
        base_signal_config,
    )
    scorer = SignalScorer()
    executor = Executor(
        exchange, None, inventory,
        ExecutorConfig(simulation_mode=True, use_flashbots=flashbots),
    )
    if cb_config:
        executor.circuit_breaker = CircuitBreaker(cb_config)

    bus = EventBus()
    executed: list = []

    async def on_price_tick(e: PriceTickEvent):
        if executor.circuit_breaker.is_open():
            return
        signal = generator.generate(e.pair, e.size)
        if signal:
            await bus.publish(SignalGeneratedEvent(signal))

    async def on_signal_generated(e: SignalGeneratedEvent):
        e.signal.score = scorer.score(e.signal, inventory.get_skews())
        await bus.publish(SignalScoredEvent(e.signal))

    async def on_signal_scored(e: SignalScoredEvent):
        if e.signal.score < 60:
            return
        ctx = await executor.execute(e.signal)
        await bus.publish(ExecutionDoneEvent(ctx))

    async def on_execution_done(e: ExecutionDoneEvent):
        ctx = e.ctx
        scorer.record_result(ctx.signal.pair, ctx.state == ExecutorState.DONE)
        if ctx.state == ExecutorState.DONE and ctx.actual_net_pnl is not None:
            pnl.record(execution_to_arb_record(ctx))
        executed.append(ctx)

    bus.subscribe(PriceTickEvent,       on_price_tick)
    bus.subscribe(SignalGeneratedEvent, on_signal_generated)
    bus.subscribe(SignalScoredEvent,    on_signal_scored)
    bus.subscribe(ExecutionDoneEvent,   on_execution_done)

    return bus, executor, scorer, pnl, executed


def make_signal(signal_id='test_sig', score=Decimal('70'), ttl=10) -> Signal:
    now = time.time()
    return Signal(
        signal_id=signal_id,
        pair='ETH/USDT', direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'),
        spread_bps=Decimal('80'), size=Decimal('0.1'),
        expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=score,
        timestamp=now, expiry=now + ttl,
        inventory_ok=True, within_limits=True,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — 10 ticks, all succeed
# ---------------------------------------------------------------------------

async def test_happy_path():
    print("\nScenario 1: Happy path — 10 ticks")
    bus, executor, scorer, pnl, executed = make_pipeline()

    for _ in range(10):
        await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))

    summary = pnl.summary()
    check("10 executions completed",       len(executed) == 10)
    check("All executions DONE",           all(c.state == ExecutorState.DONE for c in executed))
    check("PnL recorded for all trades",   summary['total_trades'] == 10)
    check("Total PnL > 0",                 summary['total_pnl_usd'] > 0)
    check("Win rate 100%",                 summary['win_rate'] == 100.0)
    check("Scorer history populated",      len(scorer.recent_results) == 10)
    check("Circuit breaker still closed",  not executor.circuit_breaker.is_open())
    check("Replay cache has 10 entries",   len(executor.replay_protection.executed) == 10)


# ---------------------------------------------------------------------------
# Scenario 2: Replay protection — same signal_id twice
# ---------------------------------------------------------------------------

async def test_replay_protection():
    print("\nScenario 2: Replay protection — same signal submitted twice")
    executor = Executor(
        MagicMock(), None, MagicMock(),
        ExecutorConfig(simulation_mode=True, use_flashbots=False),
    )
    signal = make_signal('replay_test')

    ctx1 = await executor.execute(signal)
    ctx2 = await executor.execute(signal)

    check("First execution DONE",          ctx1.state == ExecutorState.DONE)
    check("Duplicate REJECTED",            ctx2.state == ExecutorState.REJECTED)
    check("Duplicate error mentions dup",  'duplicate' in (ctx2.error or '').lower())


# ---------------------------------------------------------------------------
# Scenario 3: Stale signal — expiry in the past
# ---------------------------------------------------------------------------

async def test_stale_signal():
    print("\nScenario 3: Stale signal — expiry already passed")
    executor = Executor(
        MagicMock(), None, MagicMock(),
        ExecutorConfig(simulation_mode=True, use_flashbots=False),
    )
    stale = make_signal('stale_test', ttl=-5)
    ctx = await executor.execute(stale)

    check("Stale signal REJECTED",         ctx.state == ExecutorState.REJECTED)
    check("Error mentions signal invalid", 'invalid' in (ctx.error or '').lower())


# ---------------------------------------------------------------------------
# Scenario 4: Score threshold — score < 60 skips execution
# ---------------------------------------------------------------------------

async def test_score_threshold():
    print("\nScenario 4: Score threshold — score < 60 skipped")
    bus, executor, scorer, pnl, executed = make_pipeline()

    low_score_signal = make_signal('low_score', score=Decimal('30'))
    # Inject directly into the scored event to bypass generator
    await bus.publish(SignalScoredEvent(low_score_signal))

    check("No executions for low-score signal", len(executed) == 0)
    check("PnL engine empty",                   pnl.summary()['total_trades'] == 0)


# ---------------------------------------------------------------------------
# Scenario 5: Circuit breaker — trips after failures, blocks ticks, recovers
# ---------------------------------------------------------------------------

async def test_circuit_breaker():
    print("\nScenario 5: Circuit breaker — trip, block, recover")
    cb_config = CircuitBreakerConfig(failure_threshold=2, window_seconds=60, cooldown_seconds=1)
    bus, executor, scorer, pnl, executed = make_pipeline(cb_config=cb_config)

    # Inject 2 failures manually
    executor.circuit_breaker.record_failure()
    executor.circuit_breaker.record_failure()

    check("CB open after 2 failures", executor.circuit_breaker.is_open())

    # Publish a tick — should be blocked
    ticks_before = len(executed)
    await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))
    check("Tick skipped while CB open", len(executed) == ticks_before)

    # Wait for cooldown
    await asyncio.sleep(1.1)
    check("CB closed after cooldown",  not executor.circuit_breaker.is_open())

    # Now ticks should work again
    await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))
    check("Execution resumes after CB reset", len(executed) > ticks_before)


# ---------------------------------------------------------------------------
# Scenario 6: Partial fill — below threshold → PARTIAL state
# ---------------------------------------------------------------------------

async def test_partial_fill():
    print("\nScenario 6: Partial fill — fill < min_fill_ratio → PARTIAL")
    executor = Executor(
        MagicMock(), None, MagicMock(),
        ExecutorConfig(simulation_mode=False, use_flashbots=False, min_fill_ratio=Decimal('0.8')),
    )

    async def partial_cex(signal, size=None):
        return {'success': True, 'price': Decimal('2000'), 'filled': signal.size * Decimal('0.5')}

    async def ok_unwind(ctx):
        return True

    executor._execute_cex_leg = partial_cex
    executor._unwind = ok_unwind

    ctx = await executor.execute(make_signal('partial_test'))

    check("State is PARTIAL",              ctx.state == ExecutorState.PARTIAL)
    check("Error mentions fill",           'fill' in (ctx.error or '').lower())
    check("leg1 fill price recorded",      ctx.leg1_fill_price is not None)


# ---------------------------------------------------------------------------
# Scenario 7: DEX-first (Flashbots) — full happy path
# ---------------------------------------------------------------------------

async def test_dex_first():
    print("\nScenario 7: DEX-first (Flashbots mode) — happy path")
    bus, executor, scorer, pnl, executed = make_pipeline(flashbots=True)

    for _ in range(3):
        await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))

    check("3 DEX-first executions DONE",   len(executed) == 3)
    check("All DONE state",                all(c.state == ExecutorState.DONE for c in executed))
    check("PnL recorded",                  pnl.summary()['total_trades'] == 3)


# ---------------------------------------------------------------------------
# Scenario 8: No opportunity — min_profit_usd threshold filters all signals
# ---------------------------------------------------------------------------

async def test_no_opportunity():
    print("\nScenario 8: No opportunity — min_profit_usd threshold too high")
    bus, executor, scorer, pnl, executed = make_pipeline(
        signal_config={'min_profit_usd': 10_000}
    )

    for _ in range(5):
        await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))

    check("No executions when profit threshold too high", len(executed) == 0)
    check("PnL engine empty",                            pnl.summary()['total_trades'] == 0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 55)
    print("Bot End-to-End Test")
    print("=" * 55)

    await test_happy_path()
    await test_replay_protection()
    await test_stale_signal()
    await test_score_threshold()
    await test_circuit_breaker()
    await test_partial_fill()
    await test_dex_first()
    await test_no_opportunity()

    print("\n" + "=" * 55)
    if _failures:
        print(f"FAILED — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  {FAIL} {f}")
        sys.exit(1)
    else:
        print(f"ALL SCENARIOS PASSED {PASS}")
    print("=" * 55)


if __name__ == '__main__':
    asyncio.run(main())
