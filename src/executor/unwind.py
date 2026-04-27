"""Unwind logic — flatten a stuck position from a half-filled arb.

See docs/unwind_strategy.md for the design rationale. Strategy: market order
on the same venue as leg-1, opposite side. Limit-chase is a placeholder.
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from src.configs.tokens import get_token
from src.executor.tx_builder import PreparedCexOrder, PreparedDexTx, TxBuilder
from src.strategy.signal import Direction


logger = logging.getLogger(__name__)


class UnwindStrategy(Enum):
    MARKET = "market"
    LIMIT_CHASE = "limit_chase"   # not implemented


@dataclass
class UnwindPlan:
    venue: str          # 'cex' | 'dex'
    side: str           # 'buy' | 'sell'
    size: Decimal
    pair: str
    strategy: UnwindStrategy = UnwindStrategy.MARKET


@dataclass
class UnwindResult:
    success: bool
    prepared_order: Optional[PreparedCexOrder] = None
    prepared_tx: Optional[PreparedDexTx] = None
    error: Optional[str] = None


def plan_unwind(ctx) -> Optional[UnwindPlan]:
    """Decide which trade flattens the leg-1 fill.

    Returns None when there is nothing to flatten (e.g. leg-1 never filled).
    """
    if not ctx.leg1_fill_size or ctx.leg1_fill_size == 0:
        return None

    direction = ctx.signal.direction
    leg1_venue = ctx.leg1_venue

    # leg-1 was the BUY side of the arb iff:
    #   - direction = BUY_CEX_SELL_DEX and leg1 is on CEX
    #   - direction = BUY_DEX_SELL_CEX and leg1 is on DEX
    leg1_was_buy = (
        (direction == Direction.BUY_CEX_SELL_DEX and leg1_venue == 'cex') or
        (direction == Direction.BUY_DEX_SELL_CEX and leg1_venue == 'dex')
    )
    unwind_side = 'sell' if leg1_was_buy else 'buy'

    return UnwindPlan(
        venue=leg1_venue,
        side=unwind_side,
        size=ctx.leg1_fill_size,
        pair=ctx.signal.pair,
    )


async def execute_unwind(
    plan: UnwindPlan,
    tx_builder: Optional[TxBuilder],
    exchange_client,
    chain_id=None,
    dry_run: bool = True,
) -> UnwindResult:
    """Build (and in dry_run, never broadcast) the unwind transaction."""
    try:
        if plan.venue == 'cex':
            return await _unwind_cex(plan, tx_builder, exchange_client, dry_run)
        return await _unwind_dex(plan, tx_builder, chain_id, dry_run)
    except Exception as e:
        logger.exception("unwind failed")
        return UnwindResult(success=False, error=str(e))


async def _unwind_cex(
    plan: UnwindPlan,
    tx_builder: Optional[TxBuilder],
    exchange_client,
    dry_run: bool,
) -> UnwindResult:
    if tx_builder is not None:
        order = tx_builder.build_cex_market(plan.pair, plan.side, plan.size)
    else:
        order = PreparedCexOrder(
            exchange=getattr(exchange_client, 'id', 'binance'),
            symbol=plan.pair,
            side=plan.side,
            type='market',
            amount=plan.size,
            price=None,
        )

    if dry_run:
        logger.warning(
            "DRY-RUN UNWIND CEX: %s %s %s (would not broadcast)",
            order.side, order.amount, order.symbol,
        )
        return UnwindResult(success=True, prepared_order=order)

    raise NotImplementedError("CEX unwind broadcast disabled — set dry_run=True")


async def _unwind_dex(
    plan: UnwindPlan,
    tx_builder: Optional[TxBuilder],
    chain_id,
    dry_run: bool,
) -> UnwindResult:
    if tx_builder is None:
        return UnwindResult(success=False, error="DEX unwind requires tx_builder")
    if chain_id is None:
        return UnwindResult(success=False, error="DEX unwind requires chain_id")

    base_sym, quote_sym = plan.pair.split('/')
    token_base = get_token(chain_id, base_sym)
    token_quote = get_token(chain_id, quote_sym)

    # 'sell' means we sell base for quote → swap base→quote
    # 'buy'  means we buy back base with quote → swap quote→base
    if plan.side == 'sell':
        token_in, token_out = token_base, token_quote
        amount_in_wei = int(plan.size * (10 ** token_in.decimals))
    else:
        token_in, token_out = token_quote, token_base
        # we don't know exact quote amount needed — caller should pass plan.size in quote terms
        # for the simple case we treat plan.size as already-in-token_in terms
        amount_in_wei = int(plan.size * (10 ** token_in.decimals))

    # No min-out protection on unwind: we want fills, not blocks. 0 = "any output".
    # In production, replace with a stale-quote-aware floor (e.g. 95% of fresh quote).
    amount_out_min = 0
    prepared = tx_builder.build_dex_swap(token_in, token_out, amount_in_wei, amount_out_min)

    if dry_run:
        logger.warning(
            "DRY-RUN UNWIND DEX: swap %s %s -> %s on chain %s (would not broadcast tx %s)",
            amount_in_wei, token_in.symbol, token_out.symbol, chain_id, prepared.tx_hash,
        )
        return UnwindResult(success=True, prepared_tx=prepared)

    raise NotImplementedError("DEX unwind broadcast disabled — set dry_run=True")
