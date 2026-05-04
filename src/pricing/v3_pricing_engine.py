"""Drop-in replacement for `PricingEngine` (V2) that uses Uniswap V3 QuoterV2.

The interface mirrors `src.pricing.pricing_engine.PricingEngine` so
`SignalGenerator._fetch_prices` can use either without branching.

Differences vs V2 engine:
- No pool dict — Uniswap routing is delegated to the Quoter contract.
- `load_pools()` and `refresh_pool()` are no-ops (V3 quotes are always fresh
  per call; no cached state to refresh).
- `start_monitoring()` is a no-op for now (V3 mempool decoding is out of scope
  for the first iteration; if needed later, decode V3 swap event signatures
  rather than V2 transfers).
- `simulated_output == expected_output` since QuoterV2 itself runs the
  simulation; no separate ForkSimulator round-trip needed.
"""
import logging
import time
from typing import Optional

from src.chain.client import ChainClient
from src.core.types import Token
from src.pricing.pricing_engine import Quote, QuoteError
from src.pricing.uniswap_v3_quoter import (
    UniswapV3Quoter,
    UniswapV3QuoterError,
)

logger = logging.getLogger(__name__)


class UniswapV3PricingEngine:
    """V3 equivalent of PricingEngine. Same `get_quote()` shape so callers
    don't need to know which Uniswap version is wired up."""

    def __init__(
        self,
        chain_client: ChainClient,
        chain_id: int = 42161,
        fee_tier: int = 3000,
    ):
        self.client = chain_client
        self.chain_id = chain_id
        self.fee_tier = fee_tier
        self.quoter = UniswapV3Quoter(chain_client, chain_id)
        # Empty `pools` dict for surface-compatibility with code that iterates
        # `pricing_engine.pools.keys()` (e.g. `_pool_refresh_loop`).
        self.pools: dict = {}
        self.router = None

    def load_pools(self, pool_addresses: list) -> None:
        """No-op for V3. Quoter handles routing internally."""
        if pool_addresses:
            logger.debug(
                "V3 engine ignoring %d pool address(es) — Quoter handles routing",
                len(pool_addresses),
            )

    def refresh_pool(self, address) -> None:
        """No-op for V3 — every quote is a fresh chain call."""

    def get_quote(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        sender: Optional = None,
    ) -> Quote:
        """Return a `Quote` for an exact-input swap via Uniswap V3."""
        try:
            amount_out = self.quoter.quote_exact_input_single(
                token_in, token_out, amount_in, fee_tier=self.fee_tier,
            )
        except UniswapV3QuoterError as e:
            raise QuoteError(str(e)) from e

        return Quote(
            route=None,                         # V3 routing is internal to Quoter
            amount_in=amount_in,
            expected_output=amount_out,
            simulated_output=amount_out,        # Quoter IS the simulation
            gas_estimate=120_000,               # static, V3 single-hop swap
            timestamp=time.time(),
        )

    async def start_monitoring(self) -> None:
        """V3 mempool monitor not implemented — quotes are pull-based per tick."""
        logger.info("V3 engine: mempool monitor is a no-op (quotes are per-tick).")
