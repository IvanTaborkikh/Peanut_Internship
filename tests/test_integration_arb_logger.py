"""
Tests for ArbLogger.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from src.integration.arb_logger import ArbLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(
    pair='ETH/USDT',
    gap_bps='50',
    direction='buy_dex_sell_cex',
    net_pnl_bps='5',
    executable=True,
    fallback=False,
):
    return {
        'pair':                   pair,
        'timestamp':              datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        'dex_price':              Decimal('2000'),
        'cex_bid':                Decimal('2010'),
        'cex_ask':                Decimal('2011'),
        'gap_bps':                Decimal(gap_bps),
        'direction':              direction,
        'estimated_costs_bps':    Decimal('45'),
        'estimated_net_pnl_bps':  Decimal(net_pnl_bps),
        'inventory_ok':           executable,
        'executable':             executable,
        'dex_price_is_fallback':  fallback,
    }


@pytest.fixture
def log_file(tmp_path):
    return str(tmp_path / 'test_arb.csv')


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

def test_creates_file_on_init(log_file):
    ArbLogger(log_file)
    assert Path(log_file).exists()


def test_creates_header_on_init(log_file):
    ArbLogger(log_file)
    content = Path(log_file).read_text()
    assert 'timestamp' in content
    assert 'gap_bps' in content
    assert 'executable' in content


def test_does_not_overwrite_existing_file(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(), size=1.0)
    # Re-open — should not wipe the existing row
    ArbLogger(log_file)
    assert len(ArbLogger(log_file).read_all()) == 1


# ---------------------------------------------------------------------------
# log()
# ---------------------------------------------------------------------------

def test_log_appends_row(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(), size=1.0)
    assert len(logger.read_all()) == 1


def test_log_multiple_rows(log_file):
    logger = ArbLogger(log_file)
    for _ in range(5):
        logger.log(make_result(), size=1.0)
    assert len(logger.read_all()) == 5


def test_log_stores_correct_values(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(gap_bps='123', direction='buy_cex_sell_dex'), size=2.0)

    row = logger.read_all()[0]
    assert row['pair']      == 'ETH/USDT'
    assert row['size']      == '2.0'
    assert row['gap_bps']   == '123'
    assert row['direction'] == 'buy_cex_sell_dex'


def test_log_none_direction_stored_as_empty(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(direction=None, gap_bps='0', net_pnl_bps='-45', executable=False))
    row = logger.read_all()[0]
    assert row['direction'] == ''


def test_log_executable_true_stored_correctly(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(executable=True))
    assert logger.read_all()[0]['executable'] == 'True'


def test_log_executable_false_stored_correctly(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(executable=False, net_pnl_bps='-10'))
    assert logger.read_all()[0]['executable'] == 'False'


# ---------------------------------------------------------------------------
# tail()
# ---------------------------------------------------------------------------

def test_tail_returns_last_n(log_file):
    logger = ArbLogger(log_file)
    for i in range(10):
        logger.log(make_result(gap_bps=str(i)), size=1.0)

    rows = logger.tail(3)
    assert len(rows) == 3
    # newest first: gap_bps 9, 8, 7
    assert rows[0]['gap_bps'] == '9'


def test_tail_returns_all_if_fewer_than_n(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(), size=1.0)
    assert len(logger.tail(100)) == 1


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------

def test_summary_empty_log(log_file):
    s = ArbLogger(log_file).summary()
    assert s['total_checks'] == 0
    assert s['executable']   == 0


def test_summary_counts_correctly(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(executable=True,  net_pnl_bps='5'))
    logger.log(make_result(executable=True,  net_pnl_bps='3'))
    logger.log(make_result(executable=False, net_pnl_bps='-10'))

    s = logger.summary()
    assert s['total_checks'] == 3
    assert s['executable']   == 2
    assert s['with_direction'] == 3


def test_summary_best_gap(log_file):
    logger = ArbLogger(log_file)
    logger.log(make_result(gap_bps='10'))
    logger.log(make_result(gap_bps='50'))
    logger.log(make_result(gap_bps='25'))

    assert logger.summary()['best_gap_bps'] == Decimal('50')
