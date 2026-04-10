from decimal import Decimal

from src.core.types import Token
from src.pricing.UniswapV2Pair import UniswapV2Pair


class PriceImpactAnalyzer:
    """
    Analyzes price impact across different trade sizes.
    """

    def __init__(self, pair: UniswapV2Pair):
        self.pair = pair

    def generate_impact_table(
        self,
        token_in: Token,
        sizes: list[int],
    ) -> list[dict]:
        """
        Returns list of dicts for each trade size:
        {
            'amount_in': int,
            'amount_out': int,
            'spot_price': Decimal,
            'execution_price': Decimal,
            'price_impact_pct': Decimal,
        }
        """
        spot_price = self.pair.get_spot_price(token_in)
        rows = []
        for size in sizes:
            amount_out = self.pair.get_amount_out(size, token_in)
            exec_price = self.pair.get_execution_price(size, token_in)
            impact = self.pair.get_price_impact(size, token_in)
            rows.append({
                "amount_in": size,
                "amount_out": amount_out,
                "spot_price": spot_price,
                "execution_price": exec_price,
                "price_impact_pct": impact * 100,
            })
        return rows

    def find_max_size_for_impact(
        self,
        token_in: Token,
        max_impact_pct: Decimal,
    ) -> int:
        """
        Binary search to find the largest trade size
        with price impact <= max_impact_pct.
        """
        reserve_in, _ = self.pair._reserves_for(token_in)

        # Cap at 50% of reserve_in — impact grows unbounded beyond that
        lo = 0
        hi = reserve_in // 2

        # Quick check: even 1 unit might exceed limit
        if self.pair.get_price_impact(1, token_in) > max_impact_pct:
            return 0

        for _ in range(64):
            mid = (lo + hi) // 2
            if mid == 0:
                break
            try:
                impact = self.pair.get_price_impact(mid, token_in)
            except (AssertionError, ZeroDivisionError):
                hi = mid
                continue
            if impact <= max_impact_pct:
                lo = mid
            else:
                hi = mid

        return lo

    def estimate_true_cost(
        self,
        amount_in: int,
        token_in: Token,
        gas_price_gwei: int,
        gas_estimate: int = 150_000,
    ) -> dict:
        """
        Returns total cost including gas:
        {
            'gross_output': int,
            'gas_cost_eth': int,
            'gas_cost_in_output_token': int,
            'net_output': int,
            'effective_price': Decimal,
        }
        """
        gross_output = self.pair.get_amount_out(amount_in, token_in)
        gas_cost_eth = gas_estimate * gas_price_gwei * 10**9
        spot = self.pair.get_spot_price(token_in)
        if spot > 0:
            gas_cost_in_output_token = int(Decimal(gas_cost_eth) / spot)
        else:
            gas_cost_in_output_token = 0

        net_output = gross_output - gas_cost_in_output_token
        if amount_in > 0:
            effective_price = Decimal(amount_in) / Decimal(gross_output) if gross_output else Decimal(0)
        else:
            effective_price = Decimal(0)

        return {
            "gross_output": gross_output,
            "gas_cost_eth": gas_cost_eth,
            "gas_cost_in_output_token": gas_cost_in_output_token,
            "net_output": net_output,
            "effective_price": effective_price,
        }