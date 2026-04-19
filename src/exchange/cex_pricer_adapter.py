"""
CexPricingAdapter: wraps an ExchangeClient to act as a pricing_engine
for ArbChecker, enabling CEX-vs-CEX arbitrage detection.

ArbChecker expects:
    pricing_engine.get_dex_price(pair, size) -> {'price': Decimal, 'price_impact_bps': Decimal}

By wrapping a second ExchangeClient with this adapter, ArbChecker treats
that exchange's mid price as the "DEX price" and detects price divergence
between two centralised exchanges.

Example:
    bybit   = ExchangeClient(BYBIT_CONFIG)
    adapter = CexPricingAdapter(bybit, name='Bybit')

    checker = ArbChecker(
        pricing_engine=adapter,
        exchange_client=binance_client,   # the "CEX" side
        ...
    )
"""
from decimal import Decimal


class CexPricingAdapter:
    """
    Presents a second ExchangeClient as a pricing_engine.
    Returns the exchange's mid price as the reference price.
    Price impact is always 0 — CEX orders hit the book directly.
    """

    def __init__(self, exchange_client, name: str = 'CEX2'):
        """
        Args:
            exchange_client: ExchangeClient instance for the second exchange.
            name:            Human-readable label shown in CLI output.
        """
        self._client = exchange_client
        self.name    = name

    def get_dex_price(self, pair: str, size: float = 1.0) -> dict:
        """
        Fetch mid price from the wrapped exchange.

        Returns the same shape as PricingEngine.get_dex_price() so that
        ArbChecker can use it without modification.
        """
        ob = self._client.fetch_order_book(pair)
        return {
            'price':            ob['mid_price'],
            'price_impact_bps': Decimal('0'),
        }
