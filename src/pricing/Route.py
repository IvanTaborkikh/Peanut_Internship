from src.core.types import Token
from src.pricing.UniswapV2Pair import UniswapV2Pair


class Route:
    """Represents a swap route through one or more pools."""

    def __init__(
        self,
        pools: list[UniswapV2Pair],
        path: list[Token],
        base_gas: int = 150_000,
        gas_per_hop: int = 100_000,
    ):
        if len(pools) != len(path) - 1:
            raise ValueError(
                f"pools length ({len(pools)}) must equal path length - 1 ({len(path) - 1})"
            )
        for i, pool in enumerate(pools):
            expected = {path[i].address, path[i + 1].address}
            actual = {pool.token0.address, pool.token1.address}
            if expected != actual:
                raise ValueError(
                    f"Pool {i} tokens {actual} do not match path tokens {expected}"
                )
        self.pools = list(pools)
        self.path = list(path)  # [token_in, intermediate..., token_out]
        self.base_gas = base_gas
        self.gas_per_hop = gas_per_hop

    @property
    def num_hops(self) -> int:
        return len(self.pools)

    def get_output(self, amount_in: int) -> int:
        """
        Simulate the full route and return the final output amount.
        """
        amount = amount_in
        for i, pool in enumerate(self.pools):
            amount = pool.get_amount_out(amount, self.path[i])
        return amount

    def get_intermediate_amounts(self, amount_in: int) -> list[int]:
        """
        Return amount at each step:
        [input, after_hop1, after_hop2, ...]
        """
        amounts = [amount_in]
        amount = amount_in
        for i, pool in enumerate(self.pools):
            amount = pool.get_amount_out(amount, self.path[i])
            amounts.append(amount)
        return amounts

    def estimate_gas(self) -> int:
        """
        Estimate gas cost: base_gas + gas_per_hop * (num_hops - 1).
        Defaults: 150k base + 100k per additional hop.
        Override via constructor: Route(pools, path, base_gas=200_000, gas_per_hop=120_000)
        """
        return self.base_gas + (self.num_hops - 1) * self.gas_per_hop