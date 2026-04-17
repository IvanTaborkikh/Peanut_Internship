from decimal import Decimal


class OrderBookAnalyzer:
    """
    Analyze order book snapshots for trading decisions.
    """

    def __init__(self, orderbook: dict):
        """
        Initialize with order book from ExchangeClient.fetch_order_book().
        """
        self._ob = orderbook
        self._bids: list[tuple[Decimal, Decimal]] = orderbook['bids']
        self._asks: list[tuple[Decimal, Decimal]] = orderbook['asks']
        self._mid: Decimal = orderbook['mid_price']

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def walk_the_book(self, side: str, qty: float) -> dict:
        """
        Simulate filling `qty` against the order book.

        side="buy"  → walk asks (we pay ask prices)
        side="sell" → walk bids (we receive bid prices)

        Returns:
        {
            'avg_price': Decimal,
            'total_cost': Decimal,     # In quote currency
            'slippage_bps': Decimal,   # vs best price
            'levels_consumed': int,
            'fully_filled': bool,
            'fills': [{'price': Decimal, 'qty': Decimal, 'cost': Decimal}, ...]
        }
        """
        levels = self._asks if side == 'buy' else self._bids
        remaining = Decimal(str(qty))
        fills = []

        for price, level_qty in levels:
            if remaining <= 0:
                break
            filled_here = min(remaining, level_qty)
            cost = filled_here * price
            fills.append({'price': price, 'qty': filled_here, 'cost': cost})
            remaining -= filled_here

        total_qty = sum(f['qty'] for f in fills)
        total_cost = sum(f['cost'] for f in fills)
        avg_price = total_cost / total_qty if total_qty else Decimal('0')

        best_price = levels[0][0] if levels else Decimal('0')
        slippage_bps = (
            abs(avg_price - best_price) / best_price * Decimal('10000')
            if best_price
            else Decimal('0')
        )

        return {
            'avg_price': avg_price,
            'total_cost': total_cost,
            'slippage_bps': slippage_bps,
            'levels_consumed': len(fills),
            'fully_filled': remaining <= 0,
            'fills': fills,
        }

    def depth_at_bps(self, side: str, bps: float) -> Decimal:
        """
        Total quantity available within `bps` basis points of best price.

        side="bid" → scan bids downward from best_bid
        side="ask" → scan asks upward from best_ask
        """
        if side == 'bid':
            levels = self._bids
            best = levels[0][0] if levels else Decimal('0')
            threshold = best * (1 - Decimal(str(bps)) / Decimal('10000'))
            return sum(qty for price, qty in levels if price >= threshold)
        else:
            levels = self._asks
            best = levels[0][0] if levels else Decimal('0')
            threshold = best * (1 + Decimal(str(bps)) / Decimal('10000'))
            return sum(qty for price, qty in levels if price <= threshold)

    def imbalance(self, levels: int = 10) -> float:
        """
        Order book imbalance ratio in [-1.0, +1.0].
          +1.0 = all bids (buy pressure)
          -1.0 = all asks (sell pressure)
        """
        bid_qty = sum(qty for _, qty in self._bids[:levels])
        ask_qty = sum(qty for _, qty in self._asks[:levels])
        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        return float((bid_qty - ask_qty) / total)

    def effective_spread(self, qty: float) -> Decimal:
        """
        Effective spread for a round-trip of size `qty` in bps.
        = (avg_ask_fill - avg_bid_fill) / mid_price * 10000

        This is the TRUE cost of immediacy for your trade size.
        """
        buy_result = self.walk_the_book('buy', qty)
        sell_result = self.walk_the_book('sell', qty)

        avg_ask = buy_result['avg_price']
        avg_bid = sell_result['avg_price']

        if not self._mid:
            return Decimal('0')
        return (avg_ask - avg_bid) / self._mid * Decimal('10000')
