import pytest
from decimal import Decimal
from src.inventory.tracker import InventoryTracker, Venue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tracker():
    return InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])


def cex_balances(eth='10', usdt='20000'):
    return {
        'ETH':  {'free': Decimal(eth),   'locked': Decimal('0'), 'total': Decimal(eth)},
        'USDT': {'free': Decimal(usdt), 'locked': Decimal('0'), 'total': Decimal(usdt)},
    }


def wallet_balances(eth='10'):
    return {'ETH': Decimal(eth)}


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def test_snapshot_aggregates_across_venues(tracker):
    """Total ETH = Binance ETH + Wallet ETH."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='10'))
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='5'))

    snap = tracker.snapshot()
    assert snap['totals']['ETH'] == Decimal('15')


def test_snapshot_has_required_keys(tracker):
    tracker.update_from_cex(Venue.BINANCE, cex_balances())
    snap = tracker.snapshot()
    assert 'timestamp' in snap
    assert 'venues' in snap
    assert 'totals' in snap


def test_snapshot_venues_structure(tracker):
    """Each venue has per-asset free/locked/total breakdown."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances())
    snap = tracker.snapshot()
    eth = snap['venues']['binance']['ETH']
    assert 'free' in eth and 'locked' in eth and 'total' in eth


def test_snapshot_totals_are_decimal(tracker):
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='7'))
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='3'))
    snap = tracker.snapshot()
    assert isinstance(snap['totals']['ETH'], Decimal)


# ---------------------------------------------------------------------------
# get_available
# ---------------------------------------------------------------------------

def test_get_available_returns_free_only(tracker):
    """get_available returns free balance, not total."""
    tracker.update_from_cex(Venue.BINANCE, {
        'ETH': {'free': Decimal('5'), 'locked': Decimal('3'), 'total': Decimal('8')},
    })
    assert tracker.get_available(Venue.BINANCE, 'ETH') == Decimal('5')


def test_get_available_missing_asset_returns_zero(tracker):
    assert tracker.get_available(Venue.BINANCE, 'BTC') == Decimal('0')


def test_get_available_missing_venue_returns_zero(tracker):
    assert tracker.get_available(Venue.WALLET, 'ETH') == Decimal('0')


# ---------------------------------------------------------------------------
# can_execute
# ---------------------------------------------------------------------------

def test_can_execute_passes_when_sufficient(tracker):
    """Returns can_execute=True with enough balance on both sides."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='5', usdt='20000'))
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='5'))

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE, buy_asset='USDT',  buy_amount=Decimal('5000'),
        sell_venue=Venue.WALLET, sell_asset='ETH',  sell_amount=Decimal('2'),
    )
    assert result['can_execute'] is True
    assert result['reason'] is None


def test_can_execute_fails_insufficient_buy(tracker):
    """Returns can_execute=False when buy venue lacks funds."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(usdt='100'))   # only 100 USDT
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='10'))

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE, buy_asset='USDT',  buy_amount=Decimal('5000'),
        sell_venue=Venue.WALLET, sell_asset='ETH',  sell_amount=Decimal('2'),
    )
    assert result['can_execute'] is False
    assert 'USDT' in result['reason']


def test_can_execute_fails_insufficient_sell(tracker):
    """Returns can_execute=False when sell venue lacks asset."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(usdt='20000'))
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='0.5'))  # only 0.5 ETH

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE, buy_asset='USDT',  buy_amount=Decimal('5000'),
        sell_venue=Venue.WALLET, sell_asset='ETH',  sell_amount=Decimal('2'),
    )
    assert result['can_execute'] is False
    assert 'ETH' in result['reason']


def test_can_execute_fails_both_legs(tracker):
    """reason mentions both failures when both legs are short."""
    result = tracker.can_execute(
        buy_venue=Venue.BINANCE, buy_asset='USDT',  buy_amount=Decimal('1000'),
        sell_venue=Venue.WALLET, sell_asset='ETH',  sell_amount=Decimal('1'),
    )
    assert result['can_execute'] is False
    assert 'USDT' in result['reason'] and 'ETH' in result['reason']


def test_can_execute_returns_available_amounts(tracker):
    """Result includes available and needed quantities for both legs."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(usdt='15000'))
    tracker.update_from_wallet(Venue.WALLET, wallet_balances(eth='3'))

    result = tracker.can_execute(
        buy_venue=Venue.BINANCE, buy_asset='USDT',  buy_amount=Decimal('5000'),
        sell_venue=Venue.WALLET, sell_asset='ETH',  sell_amount=Decimal('2'),
    )
    assert result['buy_venue_available']  == Decimal('15000')
    assert result['buy_venue_needed']     == Decimal('5000')
    assert result['sell_venue_available'] == Decimal('3')
    assert result['sell_venue_needed']    == Decimal('2')


# ---------------------------------------------------------------------------
# record_trade
# ---------------------------------------------------------------------------

def test_record_trade_updates_balances(tracker):
    """After buy trade: base increases, quote decreases, fee deducted."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='10', usdt='20000'))

    tracker.record_trade(
        venue=Venue.BINANCE,
        side='buy',
        base_asset='ETH',
        quote_asset='USDT',
        base_amount=Decimal('2'),
        quote_amount=Decimal('4000'),
        fee=Decimal('4'),
        fee_asset='USDT',
    )

    assert tracker.get_available(Venue.BINANCE, 'ETH')  == Decimal('12')
    assert tracker.get_available(Venue.BINANCE, 'USDT') == Decimal('15996')  # 20000 - 4000 - 4


def test_record_trade_sell_updates_balances(tracker):
    """After sell trade: base decreases, quote increases, fee deducted."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='10', usdt='20000'))

    tracker.record_trade(
        venue=Venue.BINANCE,
        side='sell',
        base_asset='ETH',
        quote_asset='USDT',
        base_amount=Decimal('2'),
        quote_amount=Decimal('4000'),
        fee=Decimal('4'),
        fee_asset='USDT',
    )

    assert tracker.get_available(Venue.BINANCE, 'ETH')  == Decimal('8')
    assert tracker.get_available(Venue.BINANCE, 'USDT') == Decimal('23996')  # 20000 + 4000 - 4


def test_record_trade_fee_in_base_asset(tracker):
    """Fee can be in base asset (e.g., BNB discounted to ETH fee)."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='10', usdt='20000'))

    tracker.record_trade(
        venue=Venue.BINANCE,
        side='buy',
        base_asset='ETH',
        quote_asset='USDT',
        base_amount=Decimal('2'),
        quote_amount=Decimal('4000'),
        fee=Decimal('0.002'),
        fee_asset='ETH',
    )

    assert tracker.get_available(Venue.BINANCE, 'ETH') == Decimal('11.998')  # 10 + 2 - 0.002


# ---------------------------------------------------------------------------
# skew
# ---------------------------------------------------------------------------

def test_skew_detects_imbalance(tracker):
    """85/15 split shows >30% deviation and triggers rebalance."""
    tracker.update_from_cex(Venue.BINANCE, {'ETH': {'free': Decimal('85'), 'locked': Decimal('0'), 'total': Decimal('85')}})
    tracker.update_from_wallet(Venue.WALLET, {'ETH': Decimal('15')})

    result = tracker.skew('ETH')
    assert result['max_deviation_pct'] > 30.0
    assert result['needs_rebalance'] is True


def test_skew_balanced(tracker):
    """50/50 split shows ~0% deviation."""
    tracker.update_from_cex(Venue.BINANCE, {'ETH': {'free': Decimal('5'), 'locked': Decimal('0'), 'total': Decimal('5')}})
    tracker.update_from_wallet(Venue.WALLET, {'ETH': Decimal('5')})

    result = tracker.skew('ETH')
    assert result['max_deviation_pct'] == pytest.approx(0.0, abs=0.01)
    assert result['needs_rebalance'] is False


def test_skew_total_correct(tracker):
    tracker.update_from_cex(Venue.BINANCE, {'ETH': {'free': Decimal('6'), 'locked': Decimal('0'), 'total': Decimal('6')}})
    tracker.update_from_wallet(Venue.WALLET, {'ETH': Decimal('4')})

    result = tracker.skew('ETH')
    assert result['total'] == Decimal('10')


def test_skew_missing_asset_on_one_venue(tracker):
    """Asset present only on one venue → 100/0 split → max_deviation = 50%."""
    tracker.update_from_cex(Venue.BINANCE, {'ETH': {'free': Decimal('10'), 'locked': Decimal('0'), 'total': Decimal('10')}})
    # WALLET has no ETH

    result = tracker.skew('ETH')
    assert result['max_deviation_pct'] == pytest.approx(50.0, abs=0.01)
    assert result['needs_rebalance'] is True


# ---------------------------------------------------------------------------
# get_skews
# ---------------------------------------------------------------------------

def test_get_skews_returns_all_assets(tracker):
    """get_skews() returns one entry per tracked asset."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='10', usdt='20000'))
    tracker.update_from_wallet(Venue.WALLET, {'ETH': Decimal('5'), 'WETH': Decimal('3')})

    skews = tracker.get_skews()
    assets = {s['asset'] for s in skews}

    assert 'ETH'  in assets
    assert 'USDT' in assets
    assert 'WETH' in assets


def test_get_skews_each_entry_has_schema(tracker):
    """Every entry from get_skews() has the full skew schema."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances())
    skews = tracker.get_skews()

    for s in skews:
        assert 'asset'            in s
        assert 'total'            in s
        assert 'venues'           in s
        assert 'max_deviation_pct' in s
        assert 'needs_rebalance'  in s


# ---------------------------------------------------------------------------
# record_trade — edge cases
# ---------------------------------------------------------------------------

def test_record_trade_oversell_clamps_to_zero(tracker):
    """Selling more than available clamps free balance to 0 (does not go negative)."""
    tracker.update_from_cex(Venue.BINANCE, cex_balances(eth='1', usdt='20000'))

    tracker.record_trade(
        venue=Venue.BINANCE,
        side='sell',
        base_asset='ETH',
        quote_asset='USDT',
        base_amount=Decimal('5'),   # selling 5 ETH but only have 1
        quote_amount=Decimal('10000'),
        fee=Decimal('10'),
        fee_asset='USDT',
    )

    # Should clamp to 0, not go negative
    assert tracker.get_available(Venue.BINANCE, 'ETH') == Decimal('0')
