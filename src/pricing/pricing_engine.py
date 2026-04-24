import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.chain.client import ChainClient
from src.core.types import Address, Token
from src.pricing.fork_simulator import ForkSimulator
from src.pricing.mempool_monitor import MempoolMonitor, ParsedSwap
from src.pricing.route import Route
from src.pricing.route_finder import RouteFinder
from src.pricing.uniswap_v2_pair import UniswapV2Pair

logger = logging.getLogger(__name__)


class QuoteError(Exception):
    """Raised when a price quote cannot be produced."""
    pass


@dataclass
class Quote:
    """Result of a best-route price quote."""

    route: Route
    amount_in: int
    expected_output: int
    simulated_output: int
    gas_estimate: int
    timestamp: float

    @property
    def is_valid(self) -> bool:
        """
        Quote is valid if simulation matches expectation within 0.1% tolerance.
        """
        if self.expected_output == 0:
            return False
        tolerance = Decimal("0.001")
        diff = abs(self.expected_output - self.simulated_output)
        relative = Decimal(diff) / Decimal(self.expected_output)
        return relative < tolerance


class PricingEngine:
    """
    Main interface for the pricing module.
    Integrates AMM math, routing, fork simulation, and mempool monitoring.
    """

    def __init__(
        self,
        chain_client: ChainClient,
        fork_url: str,
        ws_url: str,
    ):
        self.client = chain_client
        self.simulator = ForkSimulator(fork_url)
        self.monitor = MempoolMonitor(ws_url, self._on_mempool_swap)
        self.pools: dict[Address, UniswapV2Pair] = {}
        self.router: Optional[RouteFinder] = None

    async def start_monitoring(self) -> None:
        """
        Start listening to mempool events via WebSocket.
        Runs indefinitely — call from an async task or event loop.

        Example:
            asyncio.create_task(engine.start_monitoring())
        """
        await self.monitor.start()

    def load_pools(self, pool_addresses: list[Address]) -> None:
        """
        Load pool data from chain for each address.
        Builds the RouteFinder after loading.
        """
        for addr in pool_addresses:
            self.pools[addr] = UniswapV2Pair.from_chain(addr, self.client)
        self.router = RouteFinder(list(self.pools.values()), simulator=self.simulator)

    def refresh_pool(self, address: Address) -> None:
        """
        Re-fetch reserves for a single pool from chain.
        Updates the pool in-place via RouteFinder.update_pool() — no full rebuild.
        """
        if address not in self.pools:
            raise KeyError(f"Pool {address} not loaded — call load_pools() first.")
        updated = UniswapV2Pair.from_chain(address, self.client)
        self.pools[address] = updated
        if self.router is not None:
            self.router.update_pool(updated)

    def get_quote(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        sender: Optional[Address] = None,
    ) -> Quote:
        """
        Find the best route and verify it with fork simulation.
        Raises QuoteError if no route exists or simulation fails.
        """
        if self.router is None:
            raise QuoteError("No pools loaded — call load_pools() first.")

        try:
            route, net_output = self.router.find_best_route(
                token_in, token_out, amount_in, gas_price_gwei
            )
        except ValueError as e:
            raise QuoteError(str(e)) from e

        # Use zero address as sender if not provided (view-only simulation)
        sim_sender = sender or Address("0x0000000000000000000000000000000000000000")
        sim_result = self.simulator.simulate_route(route, amount_in, sim_sender)

        if not sim_result.success:
            raise QuoteError(f"Simulation failed: {sim_result.error}")

        return Quote(
            route=route,
            amount_in=amount_in,
            expected_output=net_output,
            simulated_output=sim_result.amount_out,
            gas_estimate=sim_result.gas_used,
            timestamp=time.time(),
        )

    def _on_mempool_swap(self, swap: ParsedSwap) -> None:
        """
        Handle a detected mempool swap.
        If the swap touches one of our loaded pools, refresh its reserves.
        """
        if not swap.token_in or not swap.token_out:
            return

        for addr, pair in self.pools.items():
            token_addrs = {pair.token0.address, pair.token1.address}
            if swap.token_in in token_addrs or swap.token_out in token_addrs:
                try:
                    self.refresh_pool(addr)
                except Exception as e:
                    logger.warning("Failed to refresh pool %s after mempool swap: %s", addr, e)
