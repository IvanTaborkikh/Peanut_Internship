import csv
import os
import pytest
import tempfile
from datetime import datetime
from decimal import Decimal

from src.inventory.tracker import Venue
from src.inventory.pnl import TradeLeg, ArbRecord, PnLEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_leg(
    side: str,
    venue: Venue,
    price: str,
    amount: str = '1',
    fee: str = '1',
    symbol: str = 'ETH/USDT',
    ts: datetime = None,
) -> TradeLeg:
    return TradeLeg(
        id=f'{side}-leg',
        timestamp=ts or datetime(2025, 1, 1, 14, 30),
        venue=venue,
        symbol=symbol,
        side=side,
        amount=Decimal(amount),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_asset='USDT',
    )


def make_trade(
    buy_price: str,
    sell_price: str,
    amount: str = '1',
    buy_fee: str = '1',
    sell_fee: str = '1',
    gas: str = '0',
    ts: datetime = None,
) -> ArbRecord:
    ts = ts or datetime(2025, 1, 1, 14, 30)
    return ArbRecord(
        id='trade-1',
        timestamp=ts,
        buy_leg=make_leg('buy',  Venue.WALLET,  buy_price,  amount, buy_fee,  ts=ts),
        sell_leg=make_leg('sell', Venue.BINANCE, sell_price, amount, sell_fee, ts=ts),
        gas_cost_usd=Decimal(gas),
    )


# ---------------------------------------------------------------------------
# ArbRecord properties
# ---------------------------------------------------------------------------

def test_gross_pnl_calculation():
    """Gross PnL = sell revenue - buy cost."""
    trade = make_trade(buy_price='2000', sell_price='2005', amount='2', buy_fee='0', sell_fee='0')
    # sell: 2 * 2005 = 4010, buy: 2 * 2000 = 4000 → gross = 10
    assert trade.gross_pnl == Decimal('10')


def test_gross_pnl_negative_when_losing():
    """Gross PnL is negative when buy price > sell price."""
    trade = make_trade(buy_price='2005', sell_price='2000', amount='1', buy_fee='0', sell_fee='0')
    assert trade.gross_pnl == Decimal('-5')


def test_net_pnl_includes_all_fees():
    """Net PnL = gross - buy_fee - sell_fee - gas."""
    trade = make_trade(
        buy_price='2000', sell_price='2010', amount='1',
        buy_fee='2', sell_fee='2', gas='1',
    )
    # gross = 10, fees = 2+2+1 = 5 → net = 5
    assert trade.net_pnl == Decimal('5')


def test_total_fees_sums_all_three():
    """total_fees = buy_fee + sell_fee + gas."""
    trade = make_trade(buy_price='2000', sell_price='2010', buy_fee='2', sell_fee='3', gas='1')
    assert trade.total_fees == Decimal('6')


def test_pnl_bps_calculation():
    """net_pnl_bps = net_pnl / notional * 10000."""
    trade = make_trade(
        buy_price='2000', sell_price='2010', amount='1',
        buy_fee='2', sell_fee='2', gas='0',
    )
    # gross=10, fees=4, net=6, notional=2000 → bps = 6/2000*10000 = 30
    assert trade.net_pnl_bps == Decimal('30')


def test_notional_is_buy_cost():
    """Notional = buy amount * buy price."""
    trade = make_trade(buy_price='2000', sell_price='2010', amount='3')
    assert trade.notional == Decimal('6000')


def test_net_pnl_zero_notional():
    """net_pnl_bps returns 0 when notional is 0 (no division error)."""
    trade = make_trade(buy_price='0', sell_price='0', amount='1')
    assert trade.net_pnl_bps == Decimal('0')


# ---------------------------------------------------------------------------
# PnLEngine.record + summary
# ---------------------------------------------------------------------------

def test_summary_with_no_trades():
    """Summary returns zeros when no trades recorded — no division errors."""
    engine = PnLEngine()
    s = engine.summary()

    assert s['total_trades']      == 0
    assert s['total_pnl_usd']     == Decimal('0')
    assert s['win_rate']          == 0.0
    assert s['sharpe_estimate']   == 0.0
    assert s['pnl_by_hour']       == {}


def test_summary_win_rate():
    """Win rate = profitable trades / total * 100."""
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))   # net = +8 ✅
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))   # net = +8 ✅
    engine.record(make_trade('2010', '2000', buy_fee='1', sell_fee='1'))   # net = -12 ❌

    s = engine.summary()
    assert s['total_trades'] == 3
    assert s['win_rate'] == pytest.approx(66.67, abs=0.01)


def test_summary_total_pnl():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))  # net = 8
    engine.record(make_trade('2000', '2005', buy_fee='1', sell_fee='1'))  # net = 3

    s = engine.summary()
    assert s['total_pnl_usd'] == Decimal('11')


def test_summary_best_and_worst_trade():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))   # net = 8
    engine.record(make_trade('2000', '2003', buy_fee='1', sell_fee='1'))   # net = 1
    engine.record(make_trade('2010', '2000', buy_fee='1', sell_fee='1'))   # net = -12

    s = engine.summary()
    assert s['best_trade_pnl']  == Decimal('8')
    assert s['worst_trade_pnl'] == Decimal('-12')


def test_summary_total_notional():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', amount='2'))  # notional = 4000
    engine.record(make_trade('2000', '2010', amount='1'))  # notional = 2000

    s = engine.summary()
    assert s['total_notional'] == Decimal('6000')


def test_summary_pnl_by_hour():
    """pnl_by_hour groups trades by hour of timestamp."""
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1',
                             ts=datetime(2025, 1, 1, 14, 0)))   # net=8 at hour 14
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1',
                             ts=datetime(2025, 1, 1, 14, 30)))  # net=8 at hour 14
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1',
                             ts=datetime(2025, 1, 1, 15, 0)))   # net=8 at hour 15

    s = engine.summary()
    assert s['pnl_by_hour'][14] == Decimal('16')
    assert s['pnl_by_hour'][15] == Decimal('8')


def test_summary_avg_pnl_per_trade():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))  # net=8
    engine.record(make_trade('2000', '2006', buy_fee='1', sell_fee='1'))  # net=4

    s = engine.summary()
    assert s['avg_pnl_per_trade'] == Decimal('6')


# ---------------------------------------------------------------------------
# PnLEngine.recent
# ---------------------------------------------------------------------------

def test_recent_returns_newest_first():
    engine = PnLEngine()
    for i in range(5):
        t = make_trade('2000', '2010', ts=datetime(2025, 1, 1, 10 + i, 0))
        t.id = f'trade-{i}'
        engine.record(t)

    recent = engine.recent(3)
    assert len(recent) == 3
    # Newest first → ids should be 4, 3, 2
    assert recent[0]['id'] == 'trade-4'
    assert recent[1]['id'] == 'trade-3'


def test_recent_has_required_fields():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010'))

    entry = engine.recent(1)[0]
    for key in ('id', 'timestamp', 'symbol', 'buy_venue', 'sell_venue',
                'net_pnl_usd', 'net_pnl_bps', 'profitable'):
        assert key in entry


def test_recent_profitable_flag():
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', buy_fee='1', sell_fee='1'))  # net=8 → profitable
    engine.record(make_trade('2010', '2000', buy_fee='1', sell_fee='1'))  # net=-12 → not

    recent = engine.recent(2)
    # newest first
    assert recent[0]['profitable'] is False
    assert recent[1]['profitable'] is True


# ---------------------------------------------------------------------------
# PnLEngine.export_csv
# ---------------------------------------------------------------------------

def test_export_csv_format():
    """CSV has expected columns and correct values."""
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010', amount='1', buy_fee='1', sell_fee='1', gas='0.5'))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        path = f.name

    try:
        engine.export_csv(path)

        with open(path, newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]

        assert row['symbol']     == 'ETH/USDT'
        assert row['buy_price']  == '2000'
        assert row['sell_price'] == '2010'
        assert Decimal(row['net_pnl']) == Decimal('7.5')    # 10 - 1 - 1 - 0.5
        assert Decimal(row['gross_pnl']) == Decimal('10')
    finally:
        os.unlink(path)


def test_export_csv_all_columns_present():
    """CSV header contains all required columns."""
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010'))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        path = f.name

    try:
        engine.export_csv(path)
        with open(path, newline='') as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames

        for col in ('id', 'timestamp', 'symbol', 'buy_venue', 'sell_venue',
                    'net_pnl', 'net_pnl_bps', 'gross_pnl', 'total_fees', 'notional'):
            assert col in cols
    finally:
        os.unlink(path)


def test_export_csv_multiple_trades():
    """CSV has one row per recorded trade."""
    engine = PnLEngine()
    engine.record(make_trade('2000', '2010'))
    engine.record(make_trade('2000', '2008'))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        path = f.name

    try:
        engine.export_csv(path)
        with open(path, newline='') as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
    finally:
        os.unlink(path)
