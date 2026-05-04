from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional, TypedDict

from src.core.types import Address, Token
from src.pricing.route import Route
from src.pricing.uniswap_v2_pair import UniswapV2Pair

if TYPE_CHECKING:
    from src.pricing.fork_simulator import ForkSimulator

ZERO_ADDRESS = Address("0x0000000000000000000000000000000000000000")


class RouteComparison(TypedDict):
    route: Route
    gross_output: int
    gas_estimate: int
    gas_cost: int
    net_output: int


class RouteFinder:
    """
    Finds optimal routes between tokens across multiple pools.
    """

    def __init__(
        self,
        pools: list[UniswapV2Pair],
        simulator: Optional[ForkSimulator] = None,
        sender: Optional[Address] = None,
    ):
        self.pools = pools
        self.graph = self._build_graph()
        self.simulator = simulator
        self.sender = sender or ZERO_ADDRESS

    def _build_graph(self) -> dict[Address, list[tuple[UniswapV2Pair, Token]]]:
        """
        Build adjacency graph:
        token_address → [(pool, other_token), ...]
        """
        graph: dict[Address, list[tuple[UniswapV2Pair, Token]]] = {}
        for pool in self.pools:
            t0 = pool.token0
            t1 = pool.token1
            graph.setdefault(t0.address, []).append((pool, t1))
            graph.setdefault(t1.address, []).append((pool, t0))
        return graph

    def update_pool(self, updated_pool: UniswapV2Pair) -> None:
        """
        Replace a single pool in-place and rebuild the graph.
        More efficient than creating a new RouteFinder on every refresh.
        """
        for i, pool in enumerate(self.pools):
            if pool.address == updated_pool.address:
                self.pools[i] = updated_pool
                break
        self.graph = self._build_graph()

    def find_all_routes(
        self,
        token_in: Token,
        token_out: Token,
        max_hops: int = 3,
    ) -> list[Route]:
        """
        Find all possible routes up to max_hops using DFS.
        """
        routes: list[Route] = []

        def dfs(
            current_token: Token,
            pools_used: list[UniswapV2Pair],
            path: list[Token],
            visited_pools: set[Address],
        ) -> None:
            if len(pools_used) > max_hops:
                return
            if current_token.address == token_out.address and pools_used:
                routes.append(Route(list(pools_used), list(path)))
                return
            for pool, next_token in self.graph.get(current_token.address, []):
                if pool.address in visited_pools:
                    continue
                visited_pools.add(pool.address)
                pools_used.append(pool)
                path.append(next_token)
                dfs(next_token, pools_used, path, visited_pools)
                path.pop()
                pools_used.pop()
                visited_pools.discard(pool.address)

        dfs(token_in, [], [token_in], set())
        return routes

    def _get_gas_estimate(self, route: Route) -> int:
        """
        Return real gas estimate from fork simulator if available,
        otherwise fall back to static estimate from route.estimate_gas().
        """
        if self.simulator is not None:
            result = self.simulator.simulate_route(route, 0, self.sender)
            if result.success and result.gas_used > 0:
                return result.gas_used
        return route.estimate_gas()

    def _gas_cost_in_output_token(
        self,
        route: Route,
        gas_price_gwei: int,
    ) -> int:
        """
        Convert gas cost to output token units.

        Gas is always paid in ETH (wei). Three cases:
          1) Output IS ETH/WETH — gas_cost is already in output units, return as-is.
          2) Input IS ETH/WETH and a direct pool exists — `gas_cost_wei / spot`
             converts ETH wei → output wei via the pool's reserve ratio
             (spot = reserve_in / reserve_out = ETH_wei per output_wei).
          3) Neither side is ETH or no pool — best-effort fallback: return
             gas_cost_wei (under-corrects when output ≠ ETH, but errs safe).
        """
        gas_cost_wei = self._get_gas_estimate(route) * gas_price_gwei * 10**9
        token_in = route.path[0]
        token_out = route.path[-1]

        eth_like = {'ETH', 'WETH'}
        if token_out.symbol.upper() in eth_like:
            return gas_cost_wei

        # Direct ETH leg: spot is reserve_ETH / reserve_out, so dividing
        # ETH-wei gas cost by spot yields output-wei.
        if token_in.symbol.upper() in eth_like:
            spot = self._find_spot(token_in, token_out)
            if spot is None or spot == 0:
                return gas_cost_wei
            return int(Decimal(gas_cost_wei) / spot)

        # Multi-hop with ETH pivot (e.g. WBTC→WETH→USDT). Use the WETH↔output
        # pool's spot to convert ETH-wei gas cost to output-wei.
        weth_pivot = next(
            (t for t in route.path if t.symbol.upper() in eth_like),
            None,
        )
        if weth_pivot is not None and weth_pivot != token_out:
            spot = self._find_spot(weth_pivot, token_out)
            if spot is not None and spot != 0:
                return int(Decimal(gas_cost_wei) / spot)

        return gas_cost_wei

    def _find_spot(self, token_in: Token, token_out: Token) -> Optional[Decimal]:
        """
        Find spot price (token_in units per token_out unit) from a direct pool.
        Returns None if no direct pool exists.
        """
        for pool in self.pools:
            addrs = {pool.token0.address, pool.token1.address}
            if token_in.address in addrs and token_out.address in addrs:
                return pool.get_spot_price(token_in)
        return None

    def find_best_route(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
        max_hops: int = 3,
    ) -> tuple[Route, int]:
        """
        Find route that maximizes NET output (after gas).
        Returns (best_route, net_output).
        Raises ValueError if no route exists.
        """
        routes = self.find_all_routes(token_in, token_out, max_hops)
        if not routes:
            raise ValueError(f"No route found from {token_in.symbol} to {token_out.symbol}")

        best_route = routes[0]
        best_net = routes[0].get_output(amount_in) - self._gas_cost_in_output_token(routes[0], gas_price_gwei)

        for route in routes[1:]:
            gross = route.get_output(amount_in)
            gas_cost = self._gas_cost_in_output_token(route, gas_price_gwei)
            net = gross - gas_cost
            if net > best_net:
                best_net = net
                best_route = route

        return best_route, best_net

    def compare_routes(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
    ) -> list[RouteComparison]:
        """
        Compare all routes with detailed breakdown, sorted by net_output descending.
        """
        routes = self.find_all_routes(token_in, token_out)
        results: list[RouteComparison] = []
        for route in routes:
            gross = route.get_output(amount_in)
            gas_est = route.estimate_gas()
            gas_cost = self._gas_cost_in_output_token(route, gas_price_gwei)
            results.append(RouteComparison(
                route=route,
                gross_output=gross,
                gas_estimate=gas_est,
                gas_cost=gas_cost,
                net_output=gross - gas_cost,
            ))
        results.sort(key=lambda r: r["net_output"], reverse=True)
        return results
