"""
Tests for CexPricingAdapter.
"""
from decimal import Decimal
from unittest.mock import MagicMock

from src.exchange.cex_pricer_adapter import CexPricingAdapter


def make_client(mid='2010.5'):
    mock = MagicMock()
    mock.fetch_order_book.return_value = {
        'mid_price': Decimal(mid),
        'best_bid':  (Decimal('2010'), Decimal('5')),
        'best_ask':  (Decimal('2011'), Decimal('5')),
    }
    return mock


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def test_get_dex_price_returns_required_keys():
    adapter = CexPricingAdapter(make_client())
    result  = adapter.get_dex_price('ETH/USDT', size=1.0)
    assert 'price'            in result
    assert 'price_impact_bps' in result


def test_get_dex_price_returns_mid_price():
    adapter = CexPricingAdapter(make_client(mid='2010.5'))
    result  = adapter.get_dex_price('ETH/USDT')
    assert result['price'] == Decimal('2010.5')


def test_price_impact_is_always_zero():
    """CEX orders hit the book directly — no price impact curve."""
    adapter = CexPricingAdapter(make_client())
    assert adapter.get_dex_price('ETH/USDT')['price_impact_bps'] == Decimal('0')


def test_calls_fetch_order_book_with_correct_pair():
    client  = make_client()
    adapter = CexPricingAdapter(client)
    adapter.get_dex_price('BTC/USDT', size=0.5)
    client.fetch_order_book.assert_called_once_with('BTC/USDT')


# ---------------------------------------------------------------------------
# Name label
# ---------------------------------------------------------------------------

def test_default_name():
    adapter = CexPricingAdapter(make_client())
    assert adapter.name == 'CEX2'


def test_custom_name():
    adapter = CexPricingAdapter(make_client(), name='Bybit')
    assert adapter.name == 'Bybit'


# ---------------------------------------------------------------------------
# Integration with ArbChecker
# ---------------------------------------------------------------------------

def test_adapter_works_as_arb_checker_pricing_engine():
    """ArbChecker accepts CexPricingAdapter without modification."""
    from src.integration.arb_checker import ArbChecker
    from src.inventory.tracker import InventoryTracker, Venue
    from unittest.mock import MagicMock

    def make_ob(bid, ask):
        bid_d, ask_d = Decimal(bid), Decimal(ask)
        mid = (bid_d + ask_d) / 2
        return {
            'symbol': 'ETH/USDT', 'timestamp': 0,
            'bids': [(bid_d, Decimal('10'))], 'asks': [(ask_d, Decimal('10'))],
            'best_bid': (bid_d, Decimal('10')), 'best_ask': (ask_d, Decimal('10')),
            'mid_price': mid,
            'spread_bps': (ask_d - bid_d) / mid * Decimal('10000'),
        }

    bybit_client   = make_client(mid='2000')
    binance_client = MagicMock()
    binance_client.fetch_order_book.return_value = make_ob('2010', '2011')
    binance_client.get_trading_fees.return_value = {
        'maker': Decimal('0.001'), 'taker': Decimal('0.001'),
    }

    tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])
    tracker.update_from_cex(Venue.BINANCE, {
        'ETH':  {'free': Decimal('5'), 'locked': Decimal('0'), 'total': Decimal('5')},
        'USDT': {'free': Decimal('20000'), 'locked': Decimal('0'), 'total': Decimal('20000')},
    })
    tracker.update_from_wallet(Venue.WALLET, {
        'ETH':  Decimal('5'),
        'USDT': Decimal('20000'),
    })

    adapter = CexPricingAdapter(bybit_client, name='Bybit')
    checker = ArbChecker(
        pricing_engine=adapter,
        exchange_client=binance_client,
        inventory_tracker=tracker,
        pnl_engine=MagicMock(),
    )

    result = checker.check('ETH/USDT', size=1.0)
    assert result['direction'] == 'buy_dex_sell_cex'   # Bybit cheaper → buy there
    assert result['dex_price'] == Decimal('2000')
    assert result['dex_price_is_fallback'] is False
