"""
Realistic market model for simulation.

Price model:
  - CEX price follows a Gaussian random walk (configurable volatility)
  - DEX price: 85% of ticks track CEX within ±noise_bps (no arb)
               15% of ticks diverge by arb_min_bps..arb_max_bps (real opportunity)
  - DEX prices are already net of the 30 bps DEX swap fee

Execution model:
  - CEX: 3% rejection, 5% partial fill (80-95%), 0-5 bps slippage
  - DEX: 5% failure, 5-30 bps slippage on top of provided price
"""
import random
from decimal import Decimal


class RealisticMarket:
    def __init__(
        self,
        base_price: Decimal = Decimal('2000'),
        volatility_per_tick: float = 0.001,   # 0.1% std-dev per tick
        arb_probability: float = 0.15,         # fraction of ticks with genuine arb
        arb_min_bps: float = 60,
        arb_max_bps: float = 150,
        noise_bps: float = 15,                 # std-dev of DEX noise (bps)
        cex_reject_rate: float = 0.03,
        cex_partial_fill_rate: float = 0.05,
        dex_fail_rate: float = 0.05,
        seed: int = None,
    ):
        if seed is not None:
            random.seed(seed)

        self.price = base_price
        self.volatility_per_tick = volatility_per_tick
        self.arb_probability = arb_probability
        self.arb_min_bps = arb_min_bps
        self.arb_max_bps = arb_max_bps
        self.noise_bps = noise_bps
        self.cex_reject_rate = cex_reject_rate
        self.cex_partial_fill_rate = cex_partial_fill_rate
        self.dex_fail_rate = dex_fail_rate

        self._dex_deviation: Decimal = Decimal('0')
        self._dex_stale = True

    # ── Price movement ────────────────────────────────────────────────────

    def advance(self):
        """One time step: Gaussian random walk on the CEX price."""
        change = Decimal(str(random.gauss(0, self.volatility_per_tick)))
        self.price = max(Decimal('1'), self.price * (1 + change))
        self._dex_stale = True  # force new DEX deviation on next get_dex_prices()

    # ── Market data ───────────────────────────────────────────────────────

    def fetch_order_book(self, pair: str) -> dict:
        """CEX order book with a realistic 5 bps bid/ask spread."""
        half = self.price * Decimal('0.00025')
        bid = round(self.price - half, 4)
        ask = round(self.price + half, 4)
        return {
            'bids': [(float(bid), 10.0)],
            'asks': [(float(ask), 10.0)],
        }

    def get_dex_prices(self) -> tuple[Decimal, Decimal]:
        """
        Returns (dex_sell, dex_buy) — net of the 30 bps DEX swap fee.

        dex_sell = price you receive per ETH when selling ETH on DEX.
        dex_buy  = price you pay per ETH when buying ETH on DEX.

        Both already account for the DEX LP fee so that:
          spread_a = (dex_sell - cex_ask) / cex_ask * 10_000  (arb: buy CEX, sell DEX)
          spread_b = (cex_bid  - dex_buy)  / dex_buy  * 10_000 (arb: buy DEX, sell CEX)
        """
        if self._dex_stale:
            if random.random() < self.arb_probability:
                bps = Decimal(str(random.uniform(self.arb_min_bps, self.arb_max_bps))) / 10000
                self._dex_deviation = bps * random.choice([1, -1])
            else:
                self._dex_deviation = Decimal(str(
                    random.gauss(0, self.noise_bps / 10000)
                ))
            self._dex_stale = False

        fee = Decimal('0.003')
        dex_sell = self.price * (1 + self._dex_deviation - fee)
        dex_buy  = self.price * (1 + self._dex_deviation + fee)
        return dex_sell, dex_buy

    def fetch_balance(self) -> dict:
        return {
            'ETH':  {'free': Decimal('10'),    'locked': Decimal('0'), 'total': Decimal('10')},
            'USDT': {'free': Decimal('50000'), 'locked': Decimal('0'), 'total': Decimal('50000')},
        }

    # ── Order execution ───────────────────────────────────────────────────

    def execute_cex_order(self, side: str, price: Decimal, size: Decimal) -> dict:
        """
        Simulate a CEX IOC order.
        - 3% rejection (no position, no cost)
        - 5% partial fill at 80-95% of requested size
        - 0-5 bps slippage adverse to direction
        """
        if random.random() < self.cex_reject_rate:
            return {'success': False, 'error': 'insufficient_liquidity'}

        fill_ratio = (
            random.uniform(0.50, 0.90)   # range crosses the 0.8 min_fill_ratio threshold
            if random.random() < self.cex_partial_fill_rate
            else 1.0
        )
        filled = size * Decimal(str(fill_ratio))

        slippage = Decimal(str(random.uniform(0, 0.0005)))
        fill_price = price * (1 + slippage) if side == 'buy' else price * (1 - slippage)

        return {'success': True, 'price': fill_price, 'filled': filled}

    def execute_dex_swap(self, price: Decimal, size: Decimal, sell_base: bool) -> dict:
        """
        Simulate a DEX swap at `price` (caller already applies market drift).
        - 5% failure (slippage guard triggered, no position change)
        - 5-30 bps additional slippage on top of price
        sell_base=True  → selling ETH (fill_price lower is worse)
        sell_base=False → buying ETH  (fill_price higher is worse)
        """
        if random.random() < self.dex_fail_rate:
            return {'success': False, 'error': 'execution_reverted'}

        slippage = Decimal(str(random.uniform(0.0005, 0.003)))
        fill_price = price * (1 - slippage) if sell_base else price * (1 + slippage)

        return {'success': True, 'price': fill_price, 'filled': size}
