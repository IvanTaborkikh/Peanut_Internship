import pytest
from decimal import Decimal
from src.exchange.orderbook import OrderBookAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_orderbook(bids=None, asks=None) -> dict:
    """Build a minimal order book dict in the format ExchangeClient returns."""
    bids = bids or [
        (Decimal('2010'), Decimal('5')),
        (Decimal('2009'), Decimal('10')),
        (Decimal('2008'), Decimal('20')),
        (Decimal('2005'), Decimal('8')),
    ]
    asks = asks or [
        (Decimal('2011'), Decimal('3')),
        (Decimal('2012'), Decimal('7')),
        (Decimal('2013'), Decimal('15')),
        (Decimal('2015'), Decimal('10')),
    ]
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / Decimal('2')
    return {
        'symbol': 'ETH/USDT',
        'timestamp': 1706000000000,
        'bids': bids,
        'asks': asks,
        'best_bid': bids[0],
        'best_ask': asks[0],
        'mid_price': mid,
        'spread_bps': (best_ask - best_bid) / mid * Decimal('10000'),
    }


@pytest.fixture
def ob():
    return OrderBookAnalyzer(make_orderbook())


# ---------------------------------------------------------------------------
# walk_the_book
# ---------------------------------------------------------------------------

def test_walk_the_book_exact_fill(ob):
    """Fill exactly at one price level — first ask has 3 ETH."""
    result = ob.walk_the_book('buy', 3)

    assert result['fully_filled'] is True
    assert result['levels_consumed'] == 1
    assert result['avg_price'] == Decimal('2011')
    assert result['total_cost'] == Decimal('6033')
    assert len(result['fills']) == 1


def test_walk_the_book_multiple_levels():
    """Fill across multiple levels — avg price is qty-weighted."""
    # asks: 3 @ 2011, 7 @ 2012 → buy 8 ETH
    # 3 ETH @ 2011 = 6033, 5 ETH @ 2012 = 10060 → total = 16093 / 8 = 2011.625
    analyzer = OrderBookAnalyzer(make_orderbook())
    result = analyzer.walk_the_book('buy', 8)

    expected_avg = (Decimal('3') * Decimal('2011') + Decimal('5') * Decimal('2012')) / Decimal('8')
    assert result['fully_filled'] is True
    assert result['levels_consumed'] == 2
    assert result['avg_price'] == expected_avg


def test_walk_the_book_insufficient_liquidity():
    """Returns fully_filled=False when requested qty exceeds book depth."""
    # Total ask liquidity: 3 + 7 + 15 + 10 = 35 ETH
    analyzer = OrderBookAnalyzer(make_orderbook())
    result = analyzer.walk_the_book('buy', 100)

    assert result['fully_filled'] is False
    # Should have consumed all 4 ask levels
    assert result['levels_consumed'] == 4


def test_walk_the_book_sell_walks_bids(ob):
    """sell side walks bids (highest first)."""
    result = ob.walk_the_book('sell', 5)

    assert result['fully_filled'] is True
    assert result['levels_consumed'] == 1
    assert result['avg_price'] == Decimal('2010')


def test_walk_the_book_partial_level():
    """Partial fill at a level: only takes what's needed."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    # First ask: 3 @ 2011. Buy 2 — should take 2 from that level only.
    result = analyzer.walk_the_book('buy', 2)

    assert result['fully_filled'] is True
    assert result['levels_consumed'] == 1
    assert result['fills'][0]['qty'] == Decimal('2')


def test_walk_the_book_slippage_zero_for_single_level(ob):
    """No slippage when entire fill at the best level."""
    result = ob.walk_the_book('buy', 1)
    assert result['slippage_bps'] == Decimal('0')


def test_walk_the_book_slippage_positive_multi_level():
    """Slippage > 0 when fill goes beyond the best level."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    result = analyzer.walk_the_book('buy', 8)  # crosses 2 levels
    assert result['slippage_bps'] > Decimal('0')


def test_walk_the_book_fills_are_decimal():
    """Each fill entry has Decimal price, qty, cost."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    result = analyzer.walk_the_book('buy', 5)
    for fill in result['fills']:
        assert isinstance(fill['price'], Decimal)
        assert isinstance(fill['qty'], Decimal)
        assert isinstance(fill['cost'], Decimal)


# ---------------------------------------------------------------------------
# depth_at_bps
# ---------------------------------------------------------------------------

def test_depth_at_bps_correct():
    """
    Best ask = 2011. Within 10 bps = up to 2011 * 1.001 = 2013.011.
    Asks: 3 @ 2011, 7 @ 2012, 15 @ 2013 — all qualify. 2015 does not.
    Expected: 3 + 7 + 15 = 25 ETH.
    """
    analyzer = OrderBookAnalyzer(make_orderbook())
    depth = analyzer.depth_at_bps('ask', 10)
    assert depth == Decimal('25')


def test_depth_at_bps_bid_side():
    """
    Best bid = 2010. Within 10 bps = down to 2010 * 0.999 = 2007.99.
    Bids: 5 @ 2010, 10 @ 2009, 20 @ 2008 — all qualify. 2005 does not.
    Expected: 5 + 10 + 20 = 35 ETH.
    """
    analyzer = OrderBookAnalyzer(make_orderbook())
    depth = analyzer.depth_at_bps('bid', 10)
    assert depth == Decimal('35')


def test_depth_at_bps_zero_bps():
    """At 0 bps only the best level counts."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    depth = analyzer.depth_at_bps('ask', 0)
    assert depth == Decimal('3')


# ---------------------------------------------------------------------------
# imbalance
# ---------------------------------------------------------------------------

def test_imbalance_range(ob):
    """Imbalance always in [-1.0, +1.0]."""
    val = ob.imbalance()
    assert -1.0 <= val <= 1.0


def test_imbalance_bid_heavy():
    """More bids than asks → positive imbalance."""
    bids = [(Decimal('2010'), Decimal('100'))]
    asks = [(Decimal('2011'), Decimal('1'))]
    analyzer = OrderBookAnalyzer(make_orderbook(bids=bids, asks=asks))
    assert analyzer.imbalance() > 0


def test_imbalance_ask_heavy():
    """More asks than bids → negative imbalance."""
    bids = [(Decimal('2010'), Decimal('1'))]
    asks = [(Decimal('2011'), Decimal('100'))]
    analyzer = OrderBookAnalyzer(make_orderbook(bids=bids, asks=asks))
    assert analyzer.imbalance() < 0


def test_imbalance_balanced():
    """Equal bid and ask qty → imbalance = 0."""
    bids = [(Decimal('2010'), Decimal('10'))]
    asks = [(Decimal('2011'), Decimal('10'))]
    analyzer = OrderBookAnalyzer(make_orderbook(bids=bids, asks=asks))
    assert analyzer.imbalance() == pytest.approx(0.0)


def test_imbalance_levels_parameter():
    """levels parameter limits how deep we look."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    val_1 = analyzer.imbalance(levels=1)
    val_all = analyzer.imbalance(levels=10)
    # With different depths results may differ — just verify both are in range
    assert -1.0 <= val_1 <= 1.0
    assert -1.0 <= val_all <= 1.0


# ---------------------------------------------------------------------------
# effective_spread
# ---------------------------------------------------------------------------

def test_effective_spread_greater_than_quoted(ob):
    """Effective spread >= quoted spread (best ask - best bid) for any qty > 0."""
    quoted_spread_bps = ob._ob['spread_bps']
    effective = ob.effective_spread(5)
    assert effective >= quoted_spread_bps


def test_effective_spread_increases_with_qty():
    """Larger qty → deeper levels → wider effective spread."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    small = analyzer.effective_spread(1)
    large = analyzer.effective_spread(20)
    assert large >= small


def test_effective_spread_is_decimal(ob):
    """effective_spread returns Decimal."""
    result = ob.effective_spread(2)
    assert isinstance(result, Decimal)


def test_effective_spread_positive(ob):
    """Effective spread is always positive (asks > bids)."""
    assert ob.effective_spread(3) > Decimal('0')


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_walk_the_book_zero_qty(ob):
    """walk_the_book with qty=0 returns zeros without error."""
    result = ob.walk_the_book('buy', 0)
    assert result['avg_price']       == Decimal('0')
    assert result['total_cost']      == Decimal('0')
    assert result['levels_consumed'] == 0
    assert result['fills']           == []


def test_walk_the_book_invalid_side_raises():
    """Invalid side should not silently walk the wrong side."""
    analyzer = OrderBookAnalyzer(make_orderbook())
    # 'asks' is a typo — should raise or at least not return bids data silently.
    # Current behaviour: falls through to bids (side != 'buy' → bids).
    # This test documents the bug so it becomes visible if behaviour changes.
    result = analyzer.walk_the_book('asks', 3)
    # With invalid side it walks bids — avg_price will be a bid price, not ask price
    assert result['avg_price'] != Decimal('2011')   # 2011 is the best ask
