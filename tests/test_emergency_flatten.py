"""Unit tests for scripts.emergency_flatten — pure-logic plan generation."""
from decimal import Decimal

from scripts.emergency_flatten import FlattenOrder, plan_flatten, render_plan


def test_skips_stable_balances():
    bal = {'USDT': {'free': 100}, 'USDC': {'free': 50}}
    assert plan_flatten(bal) == []


def test_skips_zero_balances():
    bal = {'ETH': {'free': 0}, 'BTC': {'free': '0'}}
    assert plan_flatten(bal) == []


def test_includes_nonzero_nonstable():
    bal = {'ETH': {'free': '0.5'}, 'BTC': {'free': '0.01'}, 'USDT': {'free': 100}}
    plan = plan_flatten(bal)
    symbols = {o.symbol for o in plan if not o.skipped_reason}
    assert symbols == {'ETH/USDT', 'BTC/USDT'}


def test_skips_below_min_amount():
    bal = {'ETH': {'free': '0.0001'}}
    markets = {'ETH/USDT': {'limits': {'amount': {'min': '0.001'}}}}
    plan = plan_flatten(bal, markets)
    assert len(plan) == 1
    assert plan[0].skipped_reason and 'min amount' in plan[0].skipped_reason


def test_keeps_at_or_above_min_amount():
    bal = {'ETH': {'free': '0.5'}}
    markets = {'ETH/USDT': {'limits': {'amount': {'min': '0.001'}}}}
    plan = plan_flatten(bal, markets)
    assert plan[0].skipped_reason is None
    assert plan[0].amount == Decimal('0.5')


def test_skips_when_no_market_for_pair():
    bal = {'EXOTIC': {'free': '5'}}
    markets = {'ETH/USDT': {}}
    plan = plan_flatten(bal, markets)
    assert plan[0].skipped_reason and 'no EXOTIC/USDT market' in plan[0].skipped_reason


def test_render_plan_formats_each_order():
    plan = [
        FlattenOrder(asset='ETH', symbol='ETH/USDT', side='sell', amount=Decimal('0.5')),
        FlattenOrder(asset='BTC', symbol='BTC/USDT', side='sell', amount=Decimal('0.01'),
                     skipped_reason='no BTC/USDT market'),
    ]
    out = render_plan(plan)
    assert 'SELL' in out and '0.5 ETH' in out
    assert 'SKIP' in out and 'BTC' in out


def test_render_empty_plan_says_so():
    assert 'no non-stable balances' in render_plan([])
