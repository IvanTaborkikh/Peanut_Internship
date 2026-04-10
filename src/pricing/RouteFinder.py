from src.core.types import Token
from src.pricing.Route import Route
from src.pricing.UniswapV2Pair import UniswapV2Pair


class RouteFinder:
    """
    Finds optimal routes between tokens across multiple pools.
    """

    def __init__(self, pools: list[UniswapV2Pair]):
        self.pools = pools
        self.graph = self._build_graph()

    def _build_graph(self) -> dict:
        """
        Build adjacency graph:
        token_address → [(pool, other_token), ...]
        """
        graph: dict = {}
        for pool in self.pools:
            t0 = pool.token0
            t1 = pool.token1
            graph.setdefault(t0.address, []).append((pool, t1))
            graph.setdefault(t1.address, []).append((pool, t0))
        return graph

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
            visited_pools: set,
        ) -> None:
            if len(pools_used) > max_hops:
                return
            if current_token.address == token_out.address and pools_used:
                routes.append(Route(list(pools_used), list(path)))
                return
            for pool, next_token in self.graph.get(current_token.address, []):
                if id(pool) in visited_pools:
                    continue
                visited_pools.add(id(pool))
                pools_used.append(pool)
                path.append(next_token)
                dfs(next_token, pools_used, path, visited_pools)
                path.pop()
                pools_used.pop()
                visited_pools.discard(id(pool))

        dfs(token_in, [], [token_in], set())
        return routes

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
        """
        routes = self.find_all_routes(token_in, token_out, max_hops)
        if not routes:
            raise ValueError(f"No route found from {token_in.symbol} to {token_out.symbol}")

        best_route = None
        best_net = None

        for route in routes:
            gross = route.get_output(amount_in)
            gas_cost = route.estimate_gas() * gas_price_gwei
            net = gross - gas_cost
            if best_net is None or net > best_net:
                best_net = net
                best_route = route

        return best_route, best_net  # type: ignore[return-value]

    def compare_routes(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        gas_price_gwei: int,
    ) -> list[dict]:
        """
        Compare all routes with detailed breakdown:
        {
            'route': Route,
            'gross_output': int,
            'gas_estimate': int,
            'gas_cost': int,
            'net_output': int,
        }
        """
        routes = self.find_all_routes(token_in, token_out)
        results = []
        for route in routes:
            gross = route.get_output(amount_in)
            gas_est = route.estimate_gas()
            gas_cost = gas_est * gas_price_gwei
            results.append({
                "route": route,
                "gross_output": gross,
                "gas_estimate": gas_est,
                "gas_cost": gas_cost,
                "net_output": gross - gas_cost,
            })
        results.sort(key=lambda r: r["net_output"], reverse=True)
        return results