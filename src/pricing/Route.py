from src.core.types import Token
from src.pricing.UniswapV2Pair import UniswapV2Pair


class Route:
    """Represents a swap route through one or more pools."""

    def __init__(self, pools: list[UniswapV2Pair], path: list[Token]):
        if len(pools) != len(path) - 1:
            raise ValueError(
                f"pools length ({len(pools)}) must equal path length - 1 ({len(path) - 1})"
            )
        self.pools = pools
        self.path = path  # [token_in, intermediate..., token_out]

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
        Estimate gas cost: ~150k base + ~100k per additional hop.
        """
        return 150_000 + (self.num_hops - 1) * 100_000