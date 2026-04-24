"""
Quick end-to-end smoke test — no Binance keys needed.
Run from project root: python scripts/smoke_test.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
from decimal import Decimal
from unittest.mock import MagicMock
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.executor.engine import Executor, ExecutorConfig, ExecutorState
from src.inventory.tracker import InventoryTracker, Venue
from src.core.events import EventBus, PriceTickEvent, SignalGeneratedEvent, SignalScoredEvent, ExecutionDoneEvent

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')



mock_exchange = MagicMock()
mock_exchange.fetch_order_book.return_value = {
    'bids': [(2000.0, 10.0)],
    'asks': [(2001.0, 10.0)],
}
mock_exchange.fetch_balance.return_value = {
    'ETH':  {'free': Decimal('10'), 'locked': Decimal('0'), 'total': Decimal('10')},
    'USDT': {'free': Decimal('50000'), 'locked': Decimal('0'), 'total': Decimal('50000')},
}

inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
inventory.update_from_cex(Venue.BINANCE, mock_exchange.fetch_balance())
inventory.update_from_wallet(Venue.WALLET, {'ETH': Decimal('10'), 'USDT': Decimal('50000')})

generator = SignalGenerator(
    mock_exchange, None, inventory,
    FeeStructure(gas_cost_usd=Decimal('0.5')),
    {'min_profit_usd': 0.1, 'cooldown_seconds': 0},
)
scorer   = SignalScorer()
executor = Executor(mock_exchange, None, inventory,
                    ExecutorConfig(simulation_mode=True, use_flashbots=False))
bus = EventBus()

signals_generated = 0
executions_done = 0


async def on_price_tick(e: PriceTickEvent):
    if executor.circuit_breaker.is_open():
        return
    signal = generator.generate(e.pair, e.size)
    if signal:
        await bus.publish(SignalGeneratedEvent(signal))


async def on_signal_generated(e: SignalGeneratedEvent):
    global signals_generated
    signals_generated += 1
    e.signal.score = scorer.score(e.signal, inventory.get_skews())
    logging.info(f"  Signal: spread={e.signal.spread_bps:.1f}bps score={e.signal.score}")
    await bus.publish(SignalScoredEvent(e.signal))


async def on_signal_scored(e: SignalScoredEvent):
    if e.signal.score < 60:
        logging.info(f"  Skipped: score={e.signal.score} < 60")
        return
    ctx = await executor.execute(e.signal)
    await bus.publish(ExecutionDoneEvent(ctx))


async def on_execution_done(e: ExecutionDoneEvent):
    global executions_done
    ctx = e.ctx
    scorer.record_result(ctx.signal.pair, ctx.state == ExecutorState.DONE)
    if ctx.state == ExecutorState.DONE:
        executions_done += 1
        logging.info(f"  SUCCESS pnl=${ctx.actual_net_pnl:.4f}")
    else:
        logging.warning(f"  FAILED: {ctx.error}")


bus.subscribe(PriceTickEvent,       on_price_tick)
bus.subscribe(SignalGeneratedEvent, on_signal_generated)
bus.subscribe(SignalScoredEvent,    on_signal_scored)
bus.subscribe(ExecutionDoneEvent,   on_execution_done)


async def main():
    logging.info("=== Smoke test: 5 ticks ===")
    for i in range(5):
        logging.info(f"Tick {i + 1}")
        await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))
        await asyncio.sleep(0.1)

    logging.info("\n=== Results ===")
    logging.info(f"Signals generated : {signals_generated}")
    logging.info(f"Executions done   : {executions_done}")

    assert signals_generated >= 1, "No signals generated!"
    assert executions_done >= 1,   "No executions completed!"
    logging.info("SMOKE TEST PASSED ✓")


asyncio.run(main())
