"""
Tests for ArbChecker.
All external dependencies (CEX client, DEX pricer, tracker) are mocked.
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from src.integration.arb_checker import ArbChecker, _DEX_FEE_BPS
from src.inventory.tracker import InventoryTracker, Venue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ob(bid='2010', ask='2011'):
    """Minimal order book dict matching ExchangeClient.fetch_order_book() format."""
    bid_d, ask_d = Decimal(bid), Decimal(ask)
    mid = (bid_d + ask_d) / 2
    return {
        'symbol': 'ETH/USDT',
        'timestamp': 1706000000000,
        'bids': [(bid_d, Decimal('10')), (bid_d - 1, Decimal('20'))],
        'asks': [(ask_d, Decimal('10')), (ask_d + 1, Decimal('20'))],
        'best_bid': (bid_d, Decimal('10')),
        'best_ask': (ask_d, Decimal('10')),
        'mid_price': mid,
        'spread_bps': (ask_d - bid_d) / mid * Decimal('10000'),
    }


def make_cex_client(bid='2010', ask='2011', taker_fee='0.001'):
    mock = MagicMock()
    mock.fetch_order_book.return_value = make_ob(bid, ask)
    mock.get_trading_fees.return_value = {
        'maker': Decimal(taker_fee),
        'taker': Decimal(taker_fee),
    }
    return mock


def make_tracker(wallet_usdt='20000', wallet_eth='5',
                 binance_usdt='20000', binance_eth='5'):
    tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])
    tracker.update_from_cex(Venue.BINANCE, {
        'USDT': {'free': Decimal(binance_usdt), 'locked': Decimal('0'), 'total': Decimal(binance_usdt)},
        'ETH':  {'free': Decimal(binance_eth),  'locked': Decimal('0'), 'total': Decimal(binance_eth)},
    })
    tracker.update_from_wallet(Venue.WALLET, {
        'USDT': Decimal(wallet_usdt),
        'ETH':  Decimal(wallet_eth),
    })
    return tracker


def make_dex_pricer(price: str, impact_bps: str = '1'):
    """Mock pricing_engine with get_dex_price()."""
    mock = MagicMock()
    mock.get_dex_price.return_value = {
        'price':            Decimal(price),
        'price_impact_bps': Decimal(impact_bps),
    }
    return mock


def make_checker(dex_price='2000', cex_bid='2010', cex_ask='2011',
                 wallet_usdt='20000', wallet_eth='5',
                 binance_usdt='20000', binance_eth='5',
                 gas_usd='5', taker_fee='0.001'):
    return ArbChecker(
        pricing_engine=make_dex_pricer(dex_price),
        exchange_client=make_cex_client(cex_bid, cex_ask, taker_fee),
        inventory_tracker=make_tracker(wallet_usdt, wallet_eth, binance_usdt, binance_eth),
        pnl_engine=MagicMock(),
        gas_cost_usd=Decimal(gas_usd),
    )


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

def test_check_returns_required_keys():
    checker = make_checker()
    result  = checker.check('ETH/USDT', size=1.0)

    for key in ('pair', 'timestamp', 'dex_price', 'cex_bid', 'cex_ask',
                'gap_bps', 'direction', 'estimated_costs_bps',
                'estimated_net_pnl_bps', 'inventory_ok', 'executable', 'details'):
        assert key in result


def test_check_details_has_all_cost_fields():
    checker = make_checker()
    details = checker.check('ETH/USDT')['details']

    for field in ('dex_price_impact_bps', 'cex_slippage_bps',
                  'cex_fee_bps', 'dex_fee_bps', 'gas_cost_usd'):
        assert field in details


def test_check_dex_fee_bps_is_30():
    """DEX fee is always 30 bps (Uniswap V2)."""
    checker = make_checker()
    assert checker.check('ETH/USDT')['details']['dex_fee_bps'] == _DEX_FEE_BPS == Decimal('30')


# ---------------------------------------------------------------------------
# Direction detection
# ---------------------------------------------------------------------------

def test_direction_buy_dex_sell_cex():
    """dex_price < cex_bid → buy on DEX, sell on CEX."""
    checker = make_checker(dex_price='2000', cex_bid='2015', cex_ask='2016')
    result  = checker.check('ETH/USDT')
    assert result['direction'] == 'buy_dex_sell_cex'


def test_direction_buy_cex_sell_dex():
    """cex_ask < dex_price → buy on CEX, sell on DEX."""
    checker = make_checker(dex_price='2020', cex_bid='2010', cex_ask='2011')
    result  = checker.check('ETH/USDT')
    assert result['direction'] == 'buy_cex_sell_dex'


def test_direction_none_within_spread():
    """Prices within spread → no direction."""
    # cex mid ≈ dex_price, no clear arb
    checker = make_checker(dex_price='2010', cex_bid='2009', cex_ask='2011')
    result  = checker.check('ETH/USDT')
    assert result['direction'] is None
    assert result['gap_bps']   == Decimal('0')


# ---------------------------------------------------------------------------
# Gap calculation
# ---------------------------------------------------------------------------

def test_gap_bps_buy_dex_sell_cex():
    """gap = (cex_bid - dex_price) / dex_price * 10000."""
    checker = make_checker(dex_price='2000', cex_bid='2010', cex_ask='2011')
    result  = checker.check('ETH/USDT')
    expected = (Decimal('2010') - Decimal('2000')) / Decimal('2000') * Decimal('10000')
    assert result['gap_bps'] == pytest.approx(expected, rel=Decimal('1e-6'))


def test_gap_bps_buy_cex_sell_dex():
    """gap = (dex_price - cex_ask) / cex_ask * 10000."""
    checker = make_checker(dex_price='2025', cex_bid='2010', cex_ask='2012')
    result  = checker.check('ETH/USDT')
    expected = (Decimal('2025') - Decimal('2012')) / Decimal('2012') * Decimal('10000')
    assert result['gap_bps'] == pytest.approx(expected, rel=Decimal('1e-6'))


# ---------------------------------------------------------------------------
# Cost components
# ---------------------------------------------------------------------------

def test_costs_include_gas():
    """Gas is included in estimated_costs_bps."""
    checker   = make_checker(dex_price='2000', cex_bid='2100', gas_usd='10')
    result    = checker.check('ETH/USDT', size=1.0)
    # gas_bps = 10 / (1 * 2000) * 10000 = 50 bps
    assert result['details']['gas_cost_usd'] == Decimal('10')
    assert result['estimated_costs_bps'] >= Decimal('50')


def test_costs_include_cex_fee():
    """CEX taker fee is included in costs."""
    checker = make_checker(dex_price='2000', cex_bid='2100', taker_fee='0.002')
    result  = checker.check('ETH/USDT')
    # 0.002 * 10000 = 20 bps
    assert result['details']['cex_fee_bps'] == Decimal('20')


def test_net_pnl_equals_gap_minus_costs():
    """estimated_net_pnl_bps == gap_bps - estimated_costs_bps."""
    checker = make_checker(dex_price='2000', cex_bid='2100')
    result  = checker.check('ETH/USDT')
    assert result['estimated_net_pnl_bps'] == result['gap_bps'] - result['estimated_costs_bps']


# ---------------------------------------------------------------------------
# Inventory checks
# ---------------------------------------------------------------------------

def test_inventory_ok_when_sufficient():
    """inventory_ok=True when both venues have enough funds."""
    checker = make_checker(
        dex_price='2000', cex_bid='2100',
        wallet_usdt='10000', binance_eth='5',
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['inventory_ok'] is True


def test_inventory_fails_insufficient_wallet_usdt():
    """inventory_ok=False when wallet lacks USDT for DEX buy."""
    checker = make_checker(
        dex_price='2000', cex_bid='2100',
        wallet_usdt='100',    # need ~2000, only have 100
        binance_eth='5',
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['inventory_ok'] is False


def test_inventory_fails_insufficient_binance_eth():
    """inventory_ok=False when Binance lacks ETH to sell."""
    checker = make_checker(
        dex_price='2000', cex_bid='2100',
        wallet_usdt='10000',
        binance_eth='0',   # need 2 ETH, have 0
    )
    result = checker.check('ETH/USDT', size=2.0)
    assert result['inventory_ok'] is False


# ---------------------------------------------------------------------------
# Executable flag
# ---------------------------------------------------------------------------

def test_executable_requires_positive_pnl_and_inventory():
    """executable=True only if net PnL > 0 AND inventory OK."""
    checker = make_checker(
        dex_price='2000', cex_bid='2100',   # large gap → positive pnl
        wallet_usdt='10000', binance_eth='5',
    )
    result = checker.check('ETH/USDT', size=1.0)
    # Check consistency: executable == (net_pnl > 0 and inventory_ok)
    expected = result['estimated_net_pnl_bps'] > 0 and result['inventory_ok']
    assert result['executable'] == expected


def test_not_executable_when_costs_exceed_gap():
    """executable=False when costs > gap even with good inventory."""
    # Tiny gap (0.5 bps), costs will exceed it
    checker = make_checker(
        dex_price='2010', cex_bid='2010.1', cex_ask='2010.2',
        wallet_usdt='20000', binance_eth='10',
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['executable'] is False


# ---------------------------------------------------------------------------
# DEX pricer fallback
# ---------------------------------------------------------------------------

def test_fallback_to_mid_when_pricing_engine_is_none():
    """When pricing_engine=None, dex_price falls back to CEX mid."""
    checker = ArbChecker(
        pricing_engine=None,
        exchange_client=make_cex_client('2010', '2012'),
        inventory_tracker=make_tracker(),
        pnl_engine=MagicMock(),
    )
    result = checker.check('ETH/USDT')
    # mid = (2010 + 2012) / 2 = 2011
    assert result['dex_price'] == Decimal('2011')


def test_fallback_sets_dex_price_is_fallback_flag():
    """dex_price_is_fallback=True when pricing_engine is None or raises."""
    checker = ArbChecker(
        pricing_engine=None,
        exchange_client=make_cex_client('2010', '2012'),
        inventory_tracker=make_tracker(),
        pnl_engine=MagicMock(),
    )
    assert checker.check('ETH/USDT')['dex_price_is_fallback'] is True


def test_real_dex_price_sets_fallback_false():
    """dex_price_is_fallback=False when pricing_engine provides a price."""
    checker = make_checker(dex_price='2000', cex_bid='2010', cex_ask='2011')
    assert checker.check('ETH/USDT')['dex_price_is_fallback'] is False


def test_fallback_to_mid_when_pricing_engine_raises():
    """When pricing_engine throws, dex_price falls back to CEX mid."""
    bad_pricer = MagicMock()
    bad_pricer.get_dex_price.side_effect = Exception('connection lost')

    checker = ArbChecker(
        pricing_engine=bad_pricer,
        exchange_client=make_cex_client('2010', '2012'),
        inventory_tracker=make_tracker(),
        pnl_engine=MagicMock(),
    )
    result = checker.check('ETH/USDT')
    assert result['dex_price'] == Decimal('2011')
    assert result['dex_price_is_fallback'] is True


# ---------------------------------------------------------------------------
# buy_cex_sell_dex — inventory check
# ---------------------------------------------------------------------------

def test_inventory_buy_cex_sell_dex_needs_binance_usdt_and_wallet_eth():
    """buy_cex_sell_dex checks Binance USDT (buy) and Wallet ETH (sell)."""
    # dex > cex_ask → buy on CEX, sell on DEX
    checker = make_checker(
        dex_price='2025', cex_bid='2010', cex_ask='2012',
        binance_usdt='20000', wallet_eth='5',   # sufficient
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['direction']    == 'buy_cex_sell_dex'
    assert result['inventory_ok'] is True


def test_inventory_buy_cex_sell_dex_fails_no_binance_usdt():
    """buy_cex_sell_dex: inventory fails when Binance lacks USDT."""
    checker = make_checker(
        dex_price='2025', cex_bid='2010', cex_ask='2012',
        binance_usdt='100',   # need ~2012, only have 100
        wallet_eth='5',
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['direction']    == 'buy_cex_sell_dex'
    assert result['inventory_ok'] is False


def test_inventory_buy_cex_sell_dex_fails_no_wallet_eth():
    """buy_cex_sell_dex: inventory fails when Wallet lacks ETH."""
    checker = make_checker(
        dex_price='2025', cex_bid='2010', cex_ask='2012',
        binance_usdt='20000',
        wallet_eth='0',   # need 1 ETH to sell on DEX, have 0
    )
    result = checker.check('ETH/USDT', size=1.0)
    assert result['direction']    == 'buy_cex_sell_dex'
    assert result['inventory_ok'] is False


def test_execution_price_is_dex_price_for_buy_dex():
    """execution_price == dex_price when direction is buy_dex_sell_cex."""
    checker = make_checker(dex_price='2000', cex_bid='2100', cex_ask='2101')
    result  = checker.check('ETH/USDT')
    assert result['direction']       == 'buy_dex_sell_cex'
    assert result['execution_price'] == Decimal('2000')


def test_execution_price_is_cex_ask_for_buy_cex():
    """execution_price == cex_ask when direction is buy_cex_sell_dex."""
    checker = make_checker(dex_price='2100', cex_bid='2010', cex_ask='2011')
    result  = checker.check('ETH/USDT')
    assert result['direction']       == 'buy_cex_sell_dex'
    assert result['execution_price'] == Decimal('2011')


# ---------------------------------------------------------------------------
# Pair validation
# ---------------------------------------------------------------------------

def test_invalid_pair_no_slash_raises():
    """Pair without '/' raises ValueError."""
    checker = make_checker()
    with pytest.raises(ValueError, match='ETHUSDT'):
        checker.check('ETHUSDT')


def test_invalid_pair_too_many_parts_raises():
    """Pair with two slashes raises ValueError."""
    checker = make_checker()
    with pytest.raises(ValueError):
        checker.check('ETH/USDT/BUSD')


def test_invalid_pair_empty_part_raises():
    """Pair with empty base or quote raises ValueError."""
    checker = make_checker()
    with pytest.raises(ValueError):
        checker.check('/USDT')


# ---------------------------------------------------------------------------
# Pricing engine fallback logging
# ---------------------------------------------------------------------------

def test_warning_logged_when_pricing_engine_raises(caplog):
    """Warning is logged when pricing_engine raises, before falling back to CEX mid."""
    import logging
    bad_pricer = MagicMock()
    bad_pricer.get_dex_price.side_effect = RuntimeError('timeout')

    checker = ArbChecker(
        pricing_engine=bad_pricer,
        exchange_client=make_cex_client('2010', '2012'),
        inventory_tracker=make_tracker(),
        pnl_engine=MagicMock(),
    )

    with caplog.at_level(logging.WARNING, logger='src.integration.arb_checker'):
        result = checker.check('ETH/USDT')

    assert result['dex_price_is_fallback'] is True
    assert any('timeout' in r.message for r in caplog.records)
