import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from src.strategy.signal import Signal, Direction
from src.executor.recovery import CircuitBreaker, ReplayProtection
from src.executor.tx_builder import TxBuilder
from src.executor.unwind import UnwindResult, execute_unwind, plan_unwind


class ExecutorState(Enum):
    IDLE         = auto()
    VALIDATING   = auto()
    LEG1_PENDING = auto()
    LEG1_FILLED  = auto()
    LEG2_PENDING = auto()
    DONE         = auto()
    PARTIAL      = auto()       # leg1 partially filled, below threshold — unwound successfully
    REJECTED     = auto()       # refused before any trade was placed (circuit breaker / duplicate / stale signal)
    FAILED       = auto()       # execution started but something went wrong — position may have existed but was unwound
    UNWINDING    = auto()
    UNWIND_FAILED = auto()      # unwind itself failed — open position stuck, manual action required


@dataclass
class ExecutionContext:
    signal: Signal
    state: ExecutorState = ExecutorState.IDLE

    leg1_venue: str = ""
    leg1_order_id: Optional[str] = None
    leg1_fill_price: Optional[Decimal] = None
    leg1_fill_size: Optional[Decimal] = None

    leg2_venue: str = ""
    leg2_tx_hash: Optional[str] = None
    leg2_fill_price: Optional[Decimal] = None
    leg2_fill_size: Optional[Decimal] = None

    # Ready transactions, populated when a tx_builder is wired into the Executor.
    prepared_leg1: Optional[object] = None       # PreparedCexOrder | PreparedDexTx
    prepared_leg2: Optional[object] = None
    unwind_result: Optional[UnwindResult] = None

    started_at: float = field(default_factory=time.time)  # Unix timestamp
    finished_at: Optional[float] = None                   # Unix timestamp
    actual_net_pnl: Optional[Decimal] = None
    execution_quality: Optional[Decimal] = None  # actual_pnl / expected_pnl, 1.0 = perfect
    error: Optional[str] = None


@dataclass
class ExecutorConfig:
    leg1_timeout: float = 5.0   # seconds — passed to asyncio.wait_for
    leg2_timeout: float = 60.0  # seconds — passed to asyncio.wait_for
    min_fill_ratio: Decimal = Decimal('0.8')
    use_flashbots: bool = True
    simulation_mode: bool = True   # legacy flag — pure dummy fills, no builder calls
    dry_run: bool = True           # when tx_builder is wired: build & sign, never broadcast
    slippage_bps: Decimal = Decimal('30')
    gas_cost_usd: Decimal = Decimal('0.5')
    # PnL fee rates — defaults match Binance VIP-0 taker (10 bps) and Uniswap V2 swap (30 bps).
    # Override via FeesConfig when wiring from BotConfig.
    cex_taker_bps: Decimal = Decimal('10')
    dex_swap_bps: Decimal = Decimal('30')


class Executor:
    """Execute arbitrage trades across CEX and DEX."""

    def __init__(self, exchange_client, pricing_module, inventory_tracker,
                 config: Optional[ExecutorConfig] = None,
                 tx_builder: Optional[TxBuilder] = None,
                 chain_id=None):
        self.exchange = exchange_client
        self.pricing = pricing_module
        self.inventory = inventory_tracker
        self.config = config or ExecutorConfig()
        self.tx_builder = tx_builder
        self.chain_id = chain_id

        self.circuit_breaker = CircuitBreaker()
        self.replay_protection = ReplayProtection()

    async def execute(self, signal: Signal) -> ExecutionContext:
        ctx = ExecutionContext(signal=signal)

        # Pre-flight checks — nothing traded yet → REJECTED (not FAILED)
        if self.circuit_breaker.is_open():
            ctx.state = ExecutorState.REJECTED
            ctx.error = "Circuit breaker open"
            return ctx

        if self.replay_protection.is_duplicate(signal):
            ctx.state = ExecutorState.REJECTED
            ctx.error = "Duplicate signal"
            return ctx

        ctx.state = ExecutorState.VALIDATING
        if not signal.is_valid():
            ctx.state = ExecutorState.REJECTED
            ctx.error = "Signal invalid"
            return ctx

        # Execute — always record result even if an unexpected exception bubbles up
        try:
            if self.config.use_flashbots:
                ctx = await self._execute_dex_first(ctx)
            else:
                ctx = await self._execute_cex_first(ctx)
        except Exception as e:
            ctx.state = ExecutorState.FAILED
            ctx.error = f"Unexpected error: {e}"
        finally:
            self.replay_protection.mark_executed(signal)
            if ctx.state == ExecutorState.DONE:
                self.circuit_breaker.record_success()
            elif ctx.state not in (ExecutorState.REJECTED,):
                self.circuit_breaker.record_failure()
            ctx.finished_at = time.time()

        return ctx

    async def _execute_cex_first(self, ctx: ExecutionContext) -> ExecutionContext:
        """CEX leg first (default for non-Flashbots)."""
        signal = ctx.signal

        # Leg 1: CEX
        ctx.state = ExecutorState.LEG1_PENDING
        ctx.leg1_venue = 'cex'

        try:
            leg1 = await asyncio.wait_for(
                self._execute_cex_leg(signal),
                timeout=self.config.leg1_timeout
            )
        except asyncio.TimeoutError:
            # IOC order either filled or was cancelled — no position
            ctx.state = ExecutorState.FAILED
            ctx.error = "CEX timeout"
            return ctx

        if not leg1['success']:
            ctx.state = ExecutorState.FAILED
            ctx.error = leg1.get('error', 'CEX rejected')
            return ctx

        # Partial fill below threshold — we have a partial CEX position, must unwind
        if leg1['filled'] / signal.size < self.config.min_fill_ratio:
            ctx.leg1_fill_price = leg1['price']
            ctx.leg1_fill_size = leg1['filled']
            ctx.state = ExecutorState.UNWINDING
            unwind_ok = await self._unwind(ctx)
            if unwind_ok:
                ctx.state = ExecutorState.PARTIAL
                ctx.error = "Partial fill below threshold — unwound"
            else:
                ctx.state = ExecutorState.UNWIND_FAILED
                ctx.error = "Partial fill below threshold — UNWIND FAILED, manual action required"
            return ctx

        ctx.leg1_fill_price = leg1['price']
        ctx.leg1_fill_size = leg1['filled']
        if 'prepared' in leg1:
            ctx.prepared_leg1 = leg1['prepared']
        ctx.state = ExecutorState.LEG1_FILLED

        # Leg 2: DEX
        ctx.state = ExecutorState.LEG2_PENDING
        ctx.leg2_venue = 'dex'

        try:
            leg2 = await asyncio.wait_for(
                self._execute_dex_leg(signal, ctx.leg1_fill_size),
                timeout=self.config.leg2_timeout
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.UNWINDING
            unwind_ok = await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED if unwind_ok else ExecutorState.UNWIND_FAILED
            ctx.error = "DEX timeout - unwound" if unwind_ok else "DEX timeout - UNWIND FAILED, manual action required"
            return ctx

        if not leg2['success']:
            ctx.state = ExecutorState.UNWINDING
            unwind_ok = await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED if unwind_ok else ExecutorState.UNWIND_FAILED
            ctx.error = "DEX failed - unwound" if unwind_ok else "DEX failed - UNWIND FAILED, manual action required"
            return ctx

        ctx.leg2_fill_price = leg2['price']
        ctx.leg2_fill_size = leg2['filled']
        if 'prepared' in leg2:
            ctx.prepared_leg2 = leg2['prepared']
        ctx.actual_net_pnl = self._calculate_pnl(ctx)
        if ctx.signal.expected_net_pnl:
            ctx.execution_quality = ctx.actual_net_pnl / ctx.signal.expected_net_pnl
        ctx.state = ExecutorState.DONE
        return ctx

    async def _execute_dex_first(self, ctx: ExecutionContext) -> ExecutionContext:
        """DEX leg first (when using Flashbots - failed tx = no cost)."""
        signal = ctx.signal

        # Leg 1: DEX
        ctx.state = ExecutorState.LEG1_PENDING
        ctx.leg1_venue = 'dex'

        try:
            leg1 = await asyncio.wait_for(
                self._execute_dex_leg(signal, signal.size),
                timeout=self.config.leg2_timeout
            )
        except asyncio.TimeoutError:
            # Flashbots dropped the tx — no position, no gas cost
            ctx.state = ExecutorState.REJECTED
            ctx.error = "DEX timeout (Flashbots dropped)"
            return ctx

        if not leg1['success']:
            # Flashbots reverted — no position, no gas cost
            ctx.state = ExecutorState.REJECTED
            ctx.error = "DEX failed (no cost via Flashbots)"
            return ctx

        ctx.leg1_fill_price = leg1['price']
        ctx.leg1_fill_size = leg1['filled']
        if 'prepared' in leg1:
            ctx.prepared_leg1 = leg1['prepared']
        ctx.state = ExecutorState.LEG1_FILLED

        # Leg 2: CEX
        ctx.state = ExecutorState.LEG2_PENDING
        ctx.leg2_venue = 'cex'

        try:
            leg2 = await asyncio.wait_for(
                self._execute_cex_leg(signal, ctx.leg1_fill_size),
                timeout=self.config.leg1_timeout
            )
        except asyncio.TimeoutError:
            ctx.state = ExecutorState.UNWINDING
            unwind_ok = await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED if unwind_ok else ExecutorState.UNWIND_FAILED
            ctx.error = "CEX timeout after DEX - unwound" if unwind_ok else "CEX timeout after DEX - UNWIND FAILED, manual action required"
            return ctx

        if not leg2['success']:
            ctx.state = ExecutorState.UNWINDING
            unwind_ok = await self._unwind(ctx)
            ctx.state = ExecutorState.FAILED if unwind_ok else ExecutorState.UNWIND_FAILED
            ctx.error = "CEX failed after DEX - unwound" if unwind_ok else "CEX failed after DEX - UNWIND FAILED, manual action required"
            return ctx

        ctx.leg2_fill_price = leg2['price']
        ctx.leg2_fill_size = leg2['filled']
        if 'prepared' in leg2:
            ctx.prepared_leg2 = leg2['prepared']
        ctx.actual_net_pnl = self._calculate_pnl(ctx)
        if ctx.signal.expected_net_pnl:
            ctx.execution_quality = ctx.actual_net_pnl / ctx.signal.expected_net_pnl
        ctx.state = ExecutorState.DONE
        return ctx

    async def _execute_cex_leg(self, signal: Signal, size: Optional[Decimal] = None) -> dict:
        actual_size = size or signal.size

        # Builder path: prepare a real order; in dry_run, never submit.
        if self.tx_builder is not None:
            prepared = self.tx_builder.build_cex_order(
                signal, self.config.slippage_bps, size=actual_size, order_type='limit',
            )
            if self.config.dry_run:
                logger = __import__('logging').getLogger(__name__)
                logger.info("DRY-RUN CEX LEG: %s %s %s @ %s",
                            prepared.side, prepared.amount, prepared.symbol, prepared.price)
                return {'success': True, 'price': signal.cex_price, 'filled': actual_size,
                        'prepared': prepared}
            raise NotImplementedError("CEX broadcast disabled — set dry_run=True")

        # Legacy paths (kept so existing tests with simulation_mode still work).
        if self.config.simulation_mode:
            await asyncio.sleep(0.1)
            return {'success': True, 'price': signal.cex_price * Decimal('1.0001'), 'filled': actual_size}
        side = 'buy' if signal.direction == Direction.BUY_CEX_SELL_DEX else 'sell'
        result = self.exchange.create_limit_ioc_order(
            symbol=signal.pair, side=side, amount=actual_size,
            price=signal.cex_price * Decimal('1.001'),
        )
        return {'success': result['status'] == 'filled',
                'price': Decimal(str(result['avg_fill_price'])),
                'filled': Decimal(str(result['amount_filled'])),
                'error': result['status']}

    async def _execute_dex_leg(self, signal: Signal, size: Decimal) -> dict:
        # Builder path: build & sign Uniswap V2 swap; in dry_run, never broadcast.
        if self.tx_builder is not None:
            from src.configs.tokens import get_token
            base_sym, quote_sym = signal.pair.split('/')
            token_base = get_token(self.chain_id, base_sym)
            token_quote = get_token(self.chain_id, quote_sym)

            if signal.direction == Direction.BUY_CEX_SELL_DEX:
                token_in, token_out = token_base, token_quote
            else:
                token_in, token_out = token_quote, token_base

            amount_in_wei = int(size * (10 ** token_in.decimals))
            slippage_factor = Decimal('1') - self.config.slippage_bps / Decimal('10000')
            amount_out_min_wei = int(
                size * signal.dex_price * (10 ** token_out.decimals) * slippage_factor
            )

            prepared = self.tx_builder.build_dex_swap(
                token_in, token_out, amount_in_wei, amount_out_min_wei,
            )
            if self.config.dry_run:
                logger = __import__('logging').getLogger(__name__)
                logger.info("DRY-RUN DEX LEG: tx_hash=%s gas=%s amountOutMin=%s",
                            prepared.tx_hash, prepared.gas, prepared.amount_out_min)
                return {'success': True, 'price': signal.dex_price, 'filled': size,
                        'prepared': prepared}
            raise NotImplementedError("DEX broadcast disabled — set dry_run=True")

        # Legacy path.
        if self.config.simulation_mode:
            await asyncio.sleep(0.5)
            return {'success': True, 'price': signal.dex_price * Decimal('0.9998'), 'filled': size}
        raise NotImplementedError("Real DEX execution requires Week 2 integration")

    async def _unwind(self, ctx: ExecutionContext) -> bool:
        """Flatten the stuck leg-1 position. See docs/unwind_strategy.md."""
        # Builder path: real prepared transactions, no broadcast in dry_run.
        if self.tx_builder is not None:
            plan = plan_unwind(ctx)
            if plan is None:
                return True
            result = await execute_unwind(
                plan, self.tx_builder, self.exchange,
                chain_id=self.chain_id, dry_run=self.config.dry_run,
            )
            ctx.unwind_result = result
            return result.success

        # Legacy stub for existing simulation tests.
        if self.config.simulation_mode:
            await asyncio.sleep(0.1)
            return True
        raise NotImplementedError("Real unwind not implemented")

    def _calculate_pnl(self, ctx: ExecutionContext) -> Decimal:
        signal = ctx.signal
        cex_price = ctx.leg1_fill_price if ctx.leg1_venue == 'cex' else ctx.leg2_fill_price
        dex_price = ctx.leg1_fill_price if ctx.leg1_venue == 'dex' else ctx.leg2_fill_price
        cex_size = ctx.leg1_fill_size if ctx.leg1_venue == 'cex' else ctx.leg2_fill_size
        dex_size = ctx.leg1_fill_size if ctx.leg1_venue == 'dex' else ctx.leg2_fill_size
        matched = min(cex_size, dex_size)  # only count the hedged portion
        if signal.direction == Direction.BUY_CEX_SELL_DEX:
            gross = (dex_price - cex_price) * matched
        else:
            gross = (cex_price - dex_price) * matched
        cex_fee = cex_size * cex_price * (self.config.cex_taker_bps / Decimal('10000'))
        dex_fee = dex_size * dex_price * (self.config.dex_swap_bps / Decimal('10000'))
        return gross - cex_fee - dex_fee - self.config.gas_cost_usd
