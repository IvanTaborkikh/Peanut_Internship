import time
from decimal import Decimal
from unittest.mock import MagicMock

from src.strategy.signal import Signal, Direction
from src.strategy.fees import FeeStructure
from src.strategy.generator import SignalGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(
    spread_bps: Decimal = Decimal('80'),
    score: Decimal = Decimal('70'),
    net_pnl: Decimal = Decimal('10'),
    inventory_ok: bool = True,
    within_limits: bool = True,
    ttl: float = 5.0,
) -> Signal:
    now = time.time()
    return Signal(
        signal_id='ETHUSDT_test0001',
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'),
        dex_price=Decimal('2016'),
        spread_bps=spread_bps,
        size=Decimal('0.1'),
        expected_gross_pnl=Decimal('16'),
        expected_fees=Decimal('6'),
        expected_net_pnl=net_pnl,
        score=score,
        timestamp=now,
        expiry=now + ttl,
        inventory_ok=inventory_ok,
        within_limits=within_limits,
    )


def make_exchange(bid: float = 2000.0, ask: float = 2001.0) -> MagicMock:
    ex = MagicMock()
    ex.fetch_order_book.return_value = {
        'bids': [(bid, 10.0)],
        'asks': [(ask, 10.0)],
    }
    return ex


def make_inventory(usdt: float = 100_000.0, eth: float = 10.0) -> MagicMock:
    inv = MagicMock()
    inv.get_available.side_effect = lambda venue, asset: Decimal(str(
        {'USDT': usdt, 'ETH': eth}.get(asset, 0)
    ))
    return inv


def make_generator(bid=2000.0, ask=2001.0, config=None) -> SignalGenerator:
    # gas_cost_usd=0.5 simulates Arbitrum; min_profit_usd=0.1 allows small test trades
    base_config = {'min_profit_usd': 0.1}
    base_config.update(config or {})
    return SignalGenerator(
        exchange_client=make_exchange(bid, ask),
        pricing_module=None,
        inventory_tracker=make_inventory(),
        fee_structure=FeeStructure(gas_cost_usd=Decimal('0.5')),
        config=base_config,
    )


# ---------------------------------------------------------------------------
# Signal dataclass tests
# ---------------------------------------------------------------------------

def test_signal_create_assigns_id_and_timestamp():
    before = time.time()
    sig = Signal.create(
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('70'),
        expiry=time.time() + 5,
        inventory_ok=True, within_limits=True,
    )
    assert sig.signal_id.startswith('ETHUSDT_')
    assert sig.timestamp >= before


def test_signal_is_valid_all_ok():
    sig = make_signal()
    assert sig.is_valid() is True


def test_signal_is_valid_expired():
    sig = make_signal(ttl=-1)
    assert sig.is_valid() is False


def test_signal_is_valid_no_inventory():
    sig = make_signal(inventory_ok=False)
    assert sig.is_valid() is False


def test_signal_is_valid_exceeds_limits():
    sig = make_signal(within_limits=False)
    assert sig.is_valid() is False


def test_signal_is_valid_negative_pnl():
    sig = make_signal(net_pnl=Decimal('-1'))
    assert sig.is_valid() is False


def test_signal_is_valid_zero_score():
    sig = make_signal(score=Decimal('0'))
    assert sig.is_valid() is False


def test_signal_age_seconds():
    sig = make_signal()
    time.sleep(0.05)
    assert sig.age_seconds() >= 0.05


# ---------------------------------------------------------------------------
# SignalGenerator tests
# ---------------------------------------------------------------------------

def test_generate_signal_profitable():
    """Generates signal when spread exceeds breakeven."""
    gen = make_generator()
    signal = gen.generate('ETH/USDT', Decimal('0.1'))
    assert signal is not None
    assert signal.expected_net_pnl > 0
    assert signal.spread_bps > 0


def test_generate_signal_no_opportunity():
    """Returns None when min_spread_bps threshold is too high."""
    gen = make_generator(config={'min_spread_bps': 500})
    assert gen.generate('ETH/USDT', Decimal('0.1')) is None


def test_generate_signal_no_opportunity_min_profit():
    """Returns None when min_profit_usd threshold is too high."""
    gen = make_generator(config={'min_profit_usd': 10_000.0})
    assert gen.generate('ETH/USDT', Decimal('0.1')) is None


def test_cooldown_prevents_rapid_signals():
    """Second call within cooldown returns None."""
    gen = make_generator(config={'cooldown_seconds': 10})
    first = gen.generate('ETH/USDT', Decimal('0.1'))
    assert first is not None
    second = gen.generate('ETH/USDT', Decimal('0.1'))
    assert second is None


def test_direction_selection_buy_cex_sell_dex():
    """With stub DEX prices, BUY_CEX_SELL_DEX direction is picked."""
    gen = make_generator()
    signal = gen.generate('ETH/USDT', Decimal('0.1'))
    assert signal is not None
    assert signal.direction == Direction.BUY_CEX_SELL_DEX


def test_generate_sets_expiry():
    """Signal expiry is set in the future."""
    gen = make_generator()
    signal = gen.generate('ETH/USDT', Decimal('0.1'))
    assert signal is not None
    assert signal.expiry > time.time()


def test_generate_respects_position_limit():
    """Signal is rejected when trade value exceeds max_position_usd."""
    gen = make_generator(config={'max_position_usd': 1.0})  # $1 limit
    signal = gen.generate('ETH/USDT', Decimal('0.1'))
    assert signal is None or signal.within_limits is False


def test_exchange_error_returns_none():
    """Exception in fetch_order_book returns None gracefully."""
    gen = make_generator()
    gen.exchange.fetch_order_book.side_effect = Exception("network error")
    assert gen.generate('ETH/USDT', Decimal('0.1')) is None
