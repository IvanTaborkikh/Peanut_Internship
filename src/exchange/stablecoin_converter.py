"""Live USDC↔USDT exchange-rate cache via Binance USDC/USDT spot.

Used when the CEX side of an arb pair is denominated in USDT but the DEX
side is in USDC (or vice versa). Treating them as 1:1 introduces a 5–10 bps
phantom spread that masks real signals — this converter pulls the live
USDC/USDT mid price from Binance and applies it.

TTL-cached so the per-tick pipeline doesn't fetch on every iteration.
"""
import logging
import time
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


class StablecoinConverter:
    """Live USDC/USDT rate via Binance spot orderbook (TTL-cached)."""

    PAIR = 'USDC/USDT'

    def __init__(self, exchange_client, ttl_sec: float = 5.0):
        self._cex = exchange_client
        self._ttl = ttl_sec
        self._cache: Optional[tuple[float, Decimal]] = None  # (timestamp, ratio)

    def current_ratio(self) -> Decimal:
        """USDC value in USDT (e.g. 0.9998 means 1 USDC = 0.9998 USDT)."""
        return self._get_ratio()

    def usdc_to_usdt(self, amount_usdc: Decimal) -> Decimal:
        return amount_usdc * self._get_ratio()

    def usdt_to_usdc(self, amount_usdt: Decimal) -> Decimal:
        ratio = self._get_ratio()
        if ratio == 0:
            raise ValueError("USDC/USDT ratio is zero — cannot divide")
        return amount_usdt / ratio

    def _get_ratio(self) -> Decimal:
        now = time.time()
        if self._cache is not None and now - self._cache[0] < self._ttl:
            return self._cache[1]
        ob = self._cex.fetch_order_book(self.PAIR, limit=5)
        bid = Decimal(str(ob['bids'][0][0]))
        ask = Decimal(str(ob['asks'][0][0]))
        mid = (bid + ask) / 2
        self._cache = (now, mid)
        logger.debug("STABLE_CONV | %s mid=%s", self.PAIR, mid)
        return mid
