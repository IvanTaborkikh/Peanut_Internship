"""Tests for safety.limits — RiskLimits + RiskManager."""
from decimal import Decimal
from types import SimpleNamespace

from src.safety.limits import RiskLimits, RiskManager


def _signal(size='0.01', cex_price='2000') -> SimpleNamespace:
    """Lightweight signal stub — RiskManager only reads .size and .cex_price."""
    return SimpleNamespace(size=Decimal(size), cex_price=Decimal(cex_price))


def _rm(limits: RiskLimits = None, capital='100') -> RiskManager:
    return RiskManager(limits or RiskLimits(), Decimal(capital))


# --- pre-trade gates ---

def test_trade_value_above_max_trade_usd_blocked():
    rm = _rm(RiskLimits(max_trade_usd=Decimal('20')))
    ok, reason = rm.check_pre_trade(_signal(size='0.02', cex_price='2000'))  # $40
    assert not ok
    assert 'max_trade_usd' in reason


def test_trade_value_above_capital_pct_blocked():
    rm = _rm(RiskLimits(max_trade_usd=Decimal('1000'),
                        max_trade_pct=Decimal('0.10')), capital='100')
    ok, reason = rm.check_pre_trade(_signal(size='0.01', cex_price='2000'))  # $20 > 10% of 100
    assert not ok
    assert 'capital' in reason


def test_daily_loss_limit_blocks_further_trades():
    rm = _rm(RiskLimits(max_daily_loss=Decimal('15')))
    rm.record_trade(Decimal('-15'))    # exact limit hit
    ok, reason = rm.check_pre_trade(_signal(size='0.005'))   # $10 — under all other limits
    assert not ok
    assert 'Daily loss' in reason


def test_drawdown_limit_blocks_trades():
    rm = _rm(RiskLimits(max_drawdown_pct=Decimal('0.10')), capital='100')
    rm.record_trade(Decimal('20'))     # peak = 120
    rm.record_trade(Decimal('-15'))    # current = 105 → drawdown = 12.5% > 10%
    ok, reason = rm.check_pre_trade(_signal(size='0.005'))
    assert not ok
    assert 'Drawdown' in reason


def test_consecutive_losses_block_trades():
    rm = _rm(RiskLimits(consecutive_loss_limit=3))
    for _ in range(3):
        rm.record_trade(Decimal('-1'))
    ok, reason = rm.check_pre_trade(_signal(size='0.005'))
    assert not ok
    assert 'consecutive' in reason


def test_consecutive_losses_reset_on_win():
    rm = _rm(RiskLimits(consecutive_loss_limit=3))
    rm.record_trade(Decimal('-1'))
    rm.record_trade(Decimal('-1'))
    rm.record_trade(Decimal('+5'))     # win → reset
    rm.record_trade(Decimal('-1'))
    assert rm.consecutive_losses == 1


def test_hourly_trade_limit_blocks():
    rm = _rm(RiskLimits(max_trades_per_hour=2))
    rm.record_trade(Decimal('1'))
    rm.record_trade(Decimal('1'))
    ok, reason = rm.check_pre_trade(_signal())
    assert not ok
    assert 'Hourly' in reason


def test_record_trade_updates_capital_and_peak():
    rm = _rm(capital='100')
    rm.record_trade(Decimal('10'))
    assert rm.current_capital == Decimal('110')
    assert rm.peak_capital == Decimal('110')
    rm.record_trade(Decimal('-30'))
    assert rm.current_capital == Decimal('80')
    assert rm.peak_capital == Decimal('110')   # peak doesn't decrease


def test_reset_daily_zeroes_daily_pnl_and_consec():
    rm = _rm()
    rm.record_trade(Decimal('-1'))
    rm.record_trade(Decimal('-1'))
    assert rm.daily_pnl == Decimal('-2')
    assert rm.consecutive_losses == 2
    rm.reset_daily()
    assert rm.daily_pnl == Decimal('0')
    assert rm.consecutive_losses == 0
    # but capital is preserved
    assert rm.current_capital == Decimal('98')


def test_happy_path_passes_all_gates():
    rm = _rm()
    ok, reason = rm.check_pre_trade(_signal(size='0.005', cex_price='2000'))  # $10
    assert ok
    assert reason == 'OK'


def test_snapshot_contains_expected_keys():
    rm = _rm()
    snap = rm.snapshot()
    assert {'capital', 'peak', 'daily_pnl', 'drawdown', 'consec_loss', 'tph'} == set(snap)
