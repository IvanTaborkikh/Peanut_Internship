"""
Realistic arbitrage bot simulation.

Unlike smoke_test (fixed prices, always profitable), this simulation uses:
  - Random-walk CEX prices
  - DEX-CEX spread that is mostly noise (±15 bps) with occasional genuine
    arb opportunities (60-150 bps, ~15% of ticks)
  - Realistic CEX execution: 3% rejection, 5% partial fill, 0-5 bps slippage
  - Realistic DEX execution: 5% failure, 5-30 bps slippage, price drift
    during the DEX execution delay

Usage:
    PYTHONPATH=. python scripts/realistic_sim.py
    PYTHONPATH=. python scripts/realistic_sim.py --ticks 200 --seed 42 --verbose
"""
import asyncio
import logging
import random
import sys
from argparse import ArgumentParser
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.events import (
    EventBus, PriceTickEvent,
    SignalGeneratedEvent, SignalScoredEvent, ExecutionDoneEvent,
)
from src.executor.engine import Executor, ExecutorConfig, ExecutorState, ExecutionContext
from src.inventory.tracker import InventoryTracker, Venue
from src.simulation.market import RealisticMarket
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator
from src.strategy.scorer import SignalScorer
from src.strategy.signal import Direction, Signal


# ── Realistic signal generator ────────────────────────────────────────────────

class RealisticSignalGenerator(SignalGenerator):
    """Overrides _fetch_prices to pull from the realistic market model."""

    def __init__(self, market: RealisticMarket, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._market = market

    def _fetch_prices(self, pair: str, size: Decimal) -> Optional[dict]:
        try:
            ob = self.exchange.fetch_order_book(pair)
            cex_bid = Decimal(str(ob['bids'][0][0]))
            cex_ask = Decimal(str(ob['asks'][0][0]))
            dex_sell, dex_buy = self._market.get_dex_prices()
            return {
                'cex_bid': cex_bid, 'cex_ask': cex_ask,
                'dex_sell': dex_sell, 'dex_buy': dex_buy,
            }
        except Exception:
            return None


# ── Realistic executor ────────────────────────────────────────────────────────

class RealisticExecutor(Executor):
    """
    Overrides leg methods with realistic execution:
    - CEX: slippage, partial fills, rejections
    - DEX: market drifts during execution delay, slippage, failures
    - Unwind: 90% success rate (10% stuck position)
    """

    def __init__(self, market: RealisticMarket, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._market = market

    async def _execute_cex_leg(self, signal: Signal, size: Optional[Decimal] = None) -> dict:
        actual_size = size or signal.size
        await asyncio.sleep(random.uniform(0.05, 0.25))     # 50-250 ms CEX latency
        side = 'buy' if signal.direction == Direction.BUY_CEX_SELL_DEX else 'sell'
        return self._market.execute_cex_order(side, signal.cex_price, actual_size)

    async def _execute_dex_leg(self, signal: Signal, size: Decimal) -> dict:
        await asyncio.sleep(random.uniform(0.3, 1.2))       # 300 ms-1.2 s DEX latency

        # Price drifts while we wait for the block confirmation
        price_before = self._market.price
        self._market.advance()
        drift = self._market.price / price_before

        # Scale the expected DEX price by the drift, then apply execution slippage
        drifted_price = signal.dex_price * drift
        sell_base = (signal.direction == Direction.BUY_CEX_SELL_DEX)
        return self._market.execute_dex_swap(drifted_price, size, sell_base=sell_base)

    async def _unwind(self, ctx: ExecutionContext) -> bool:
        """90% success rate — 10% chance the position stays stuck."""
        await asyncio.sleep(random.uniform(0.1, 0.4))
        return random.random() > 0.10


# ── Statistics tracker ────────────────────────────────────────────────────────

class SimStats:
    def __init__(self):
        self.ticks = 0
        self.signals_generated = 0
        self.signals_skipped = 0
        self.outcomes: dict[str, int] = {}
        self.pnl_list: list[Decimal] = []
        self.cb_trips = 0
        self.prices: list[Decimal] = []

    def record_tick(self, price: Decimal):
        self.ticks += 1
        self.prices.append(price)

    def record_outcome(self, state: ExecutorState, pnl: Optional[Decimal]):
        key = state.name
        self.outcomes[key] = self.outcomes.get(key, 0) + 1
        if pnl is not None:
            self.pnl_list.append(pnl)

    def print_report(self):
        print()
        print("=" * 52)
        print(f"  Simulation results  ({self.ticks} ticks)")
        print("=" * 52)

        if self.prices:
            lo, hi = min(self.prices), max(self.prices)
            mean = sum(self.prices) / len(self.prices)
            print(f"\n  Price range: ${lo:.2f} — ${hi:.2f}  (mean ${mean:.2f})")

        # exclude REJECTED from "executed" count
        executed = sum(v for k, v in self.outcomes.items() if k != 'REJECTED')
        print(f"\n  Ticks              {self.ticks:>5}")
        print(f"  Signals found      {self.signals_generated:>5}"
              f"  ({self.signals_generated / self.ticks * 100:.0f}%)")
        print(f"  Skipped (score<60) {self.signals_skipped:>5}")
        print(f"  Executed           {executed:>5}")

        if self.outcomes:
            print("\n  Outcomes:")
            order = ['DONE', 'FAILED', 'PARTIAL', 'UNWIND_FAILED', 'REJECTED']
            for key in order + [k for k in self.outcomes if k not in order]:
                count = self.outcomes.get(key, 0)
                if not count:
                    continue
                base = executed if key != 'REJECTED' else (executed + self.outcomes.get('REJECTED', 0))
                pct = count / base * 100 if base else 0
                print(f"    {key:<16} {count:>3}  ({pct:.0f}%)")

        if self.pnl_list:
            total = sum(self.pnl_list)
            avg   = total / len(self.pnl_list)
            best  = max(self.pnl_list)
            worst = min(self.pnl_list)
            wins  = sum(1 for p in self.pnl_list if p > 0)
            print(f"\n  PnL  ({len(self.pnl_list)} completed trades):")
            print(f"    Total:    {'+'if total>=0 else ''}${total:.4f}")
            print(f"    Average:  {'+'if avg  >=0 else ''}${avg:.4f}")
            print(f"    Best:     +${best:.4f}")
            print(f"    Worst:     ${worst:.4f}")
            print(f"    Win rate:  {wins}/{len(self.pnl_list)}"
                  f"  ({wins / len(self.pnl_list) * 100:.0f}%)")
        else:
            print("\n  No completed trades.")

        print(f"\n  Circuit breaker trips: {self.cb_trips}")
        print("=" * 52)
        print()


# ── Main simulation loop ──────────────────────────────────────────────────────

async def run_simulation(n_ticks: int, seed: Optional[int], verbose: bool):
    market = RealisticMarket(
        base_price=Decimal('2000'),
        volatility_per_tick=0.0012,   # 0.12% std-dev per tick (~$2.4 move)
        arb_probability=0.20,         # 20% of ticks have genuine arb opportunity
        arb_min_bps=60,
        arb_max_bps=150,
        noise_bps=15,
        # Rates are higher than production defaults to produce visible
        # failure modes in a short (100-300 tick) demonstration run.
        cex_reject_rate=0.07,         # 7%  → ~1 rejection per 15 executions
        cex_partial_fill_rate=0.10,   # 10% → some fills cross the 0.8 threshold
        dex_fail_rate=0.10,           # 10% → ~1 DEX failure per 10 executions
        seed=seed,
    )

    class ExchangeAdapter:
        def fetch_order_book(self, pair): return market.fetch_order_book(pair)
        def fetch_balance(self):          return market.fetch_balance()

    exchange  = ExchangeAdapter()
    inventory = InventoryTracker([Venue.BINANCE, Venue.WALLET])
    inventory.update_from_cex(Venue.BINANCE, exchange.fetch_balance())
    inventory.update_from_wallet(Venue.WALLET, {'ETH': Decimal('10'), 'USDT': Decimal('50000')})

    fees      = FeeStructure(gas_cost_usd=Decimal('0.5'))
    generator = RealisticSignalGenerator(
        market, exchange, None, inventory, fees,
        {'min_profit_usd': 0.1, 'cooldown_seconds': 0, 'min_spread_bps': 50},
    )
    scorer    = SignalScorer()
    executor  = RealisticExecutor(
        market, exchange, None, inventory,
        ExecutorConfig(
            simulation_mode=False,
            use_flashbots=False,
            gas_cost_usd=Decimal('0.5'),
            leg1_timeout=2.0,
            leg2_timeout=5.0,
            min_fill_ratio=Decimal('0.8'),
        ),
    )

    bus   = EventBus()
    stats = SimStats()
    cb_was_open = False

    # ── Event handlers ────────────────────────────────────────────────────

    async def on_price_tick(e: PriceTickEvent):
        nonlocal cb_was_open
        is_open = executor.circuit_breaker.is_open()
        if is_open and not cb_was_open:
            cb_was_open = True
            stats.cb_trips += 1
            log("  *** CIRCUIT BREAKER OPEN ***")
        elif not is_open and cb_was_open:
            cb_was_open = False
            log("  *** Circuit breaker closed ***")
        if is_open:
            return
        signal = await asyncio.to_thread(generator.generate, e.pair, e.size)
        if signal:
            stats.signals_generated += 1
            await bus.publish(SignalGeneratedEvent(signal))

    async def on_signal_generated(e: SignalGeneratedEvent):
        e.signal.score = scorer.score(e.signal, [])
        await bus.publish(SignalScoredEvent(e.signal))

    async def on_signal_scored(e: SignalScoredEvent):
        sig = e.signal
        if sig.score < 60:
            stats.signals_skipped += 1
            log(f"  skip  spread={sig.spread_bps:.1f}bps score={sig.score:.1f}")
            return
        log(f"  signal spread={sig.spread_bps:.1f}bps score={sig.score:.1f}")
        ctx = await executor.execute(sig)
        await bus.publish(ExecutionDoneEvent(ctx))

    async def on_execution_done(e: ExecutionDoneEvent):
        ctx = e.ctx
        scorer.record_result(ctx.signal.pair, ctx.state == ExecutorState.DONE)
        stats.record_outcome(ctx.state, ctx.actual_net_pnl)
        if ctx.state == ExecutorState.DONE:
            sign = '+' if ctx.actual_net_pnl >= 0 else ''
            log(f"  DONE  pnl={sign}${ctx.actual_net_pnl:.4f}"
                f"  quality={ctx.execution_quality:.2f}" if ctx.execution_quality else "")
        else:
            log(f"  {ctx.state.name}: {ctx.error}")

    bus.subscribe(PriceTickEvent,       on_price_tick)
    bus.subscribe(SignalGeneratedEvent, on_signal_generated)
    bus.subscribe(SignalScoredEvent,    on_signal_scored)
    bus.subscribe(ExecutionDoneEvent,   on_execution_done)

    # ── Tick loop ─────────────────────────────────────────────────────────

    print(f"\nRunning {n_ticks} ticks  (seed={seed})  ...")
    if verbose:
        print()

    for i in range(n_ticks):
        market.advance()
        stats.record_tick(market.price)
        log(f"Tick {i+1:3d}  price=${float(market.price):.2f}")
        await bus.publish(PriceTickEvent('ETH/USDT', Decimal('0.1')))

    stats.print_report()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = ArgumentParser(description='Realistic arb bot simulation')
    parser.add_argument('--ticks',   type=int,  default=100, help='Number of price ticks (default: 100)')
    parser.add_argument('--seed',    type=int,  default=None, help='Random seed for reproducibility')
    parser.add_argument('--verbose', action='store_true',    help='Show per-tick log output')
    args = parser.parse_args()

    global log
    if args.verbose:
        logging.basicConfig(level=logging.WARNING, format='%(message)s')
        log = print
    else:
        def log(*a, **kw):
            pass

    asyncio.run(run_simulation(n_ticks=args.ticks, seed=args.seed, verbose=args.verbose))


if __name__ == '__main__':
    main()
