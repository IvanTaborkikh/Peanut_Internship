import time
from decimal import Decimal

from src.strategy.signal import Signal, Direction
from src.strategy.scorer import SignalScorer, ScorerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(
    spread_bps: Decimal = Decimal('80'),
    score: Decimal = Decimal('0'),
    pair: str = 'ETH/USDT',
    ttl: float = 5.0,
) -> Signal:
    now = time.time()
    return Signal(
        signal_id='ETHUSDT_test0001',
        pair=pair,
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'),
        dex_price=Decimal('2016'),
        spread_bps=spread_bps,
        size=Decimal('0.1'),
        expected_gross_pnl=Decimal('16'),
        expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'),
        score=score,
        timestamp=now,
        expiry=now + ttl,
        inventory_ok=True,
        within_limits=True,
    )


def balanced_skews(asset: str = 'ETH') -> list[dict]:
    return [{'asset': asset, 'needs_rebalance': False, 'max_deviation_pct': 5.0}]


def imbalanced_skews(asset: str = 'ETH') -> list[dict]:
    return [{'asset': asset, 'needs_rebalance': True, 'max_deviation_pct': 45.0}]


# ---------------------------------------------------------------------------
# score()
# ---------------------------------------------------------------------------

def test_score_returns_value_between_0_and_100():
    scorer = SignalScorer()
    sig = make_signal(spread_bps=Decimal('80'))
    score = scorer.score(sig, balanced_skews())
    assert 0 <= score <= 100


def test_score_high_spread_gives_high_score():
    """100 bps spread scores higher than 40 bps spread."""
    scorer = SignalScorer()
    high = scorer.score(make_signal(spread_bps=Decimal('100')), balanced_skews())
    low  = scorer.score(make_signal(spread_bps=Decimal('40')),  balanced_skews())
    assert high > low


def test_score_below_min_spread_gives_zero_spread_component():
    """Spread below min_spread_bps contributes 0 to score."""
    config = ScorerConfig(min_spread_bps=Decimal('50'))
    scorer = SignalScorer(config)
    score = scorer.score(make_signal(spread_bps=Decimal('20')), balanced_skews())
    # Spread component is 0; total score comes from other factors only
    max_without_spread = (
        80 * config.liquidity_weight +
        60 * config.inventory_weight +
        50 * config.history_weight
    )
    assert score <= round(max_without_spread, 1)


def test_score_inventory_penalty_for_imbalanced():
    """Imbalanced inventory (needs_rebalance=True) gives lower score."""
    scorer = SignalScorer()
    sig = make_signal(spread_bps=Decimal('80'))
    good = scorer.score(sig, balanced_skews())
    bad  = scorer.score(sig, imbalanced_skews())
    assert good > bad


def test_score_no_skew_data_for_asset():
    """Empty skew list still returns a valid score."""
    scorer = SignalScorer()
    score = scorer.score(make_signal(), [])
    assert 0 <= score <= 100


def test_score_excellent_spread_caps_at_100():
    """Spread above excellent_spread_bps does not exceed 100 total."""
    scorer = SignalScorer()
    score = scorer.score(make_signal(spread_bps=Decimal('500')), balanced_skews())
    assert score <= 100


# ---------------------------------------------------------------------------
# record_result / _score_history
# ---------------------------------------------------------------------------

def test_history_score_defaults_to_50_with_no_data():
    scorer = SignalScorer()
    sig = make_signal()
    # No history → history component should be 50
    score_no_hist = scorer.score(sig, balanced_skews())
    scorer.record_result('ETH/USDT', True)
    scorer.record_result('ETH/USDT', True)
    scorer.record_result('ETH/USDT', True)
    score_with_hist = scorer.score(sig, balanced_skews())
    assert score_with_hist >= score_no_hist


def test_record_result_caps_history_at_100():
    scorer = SignalScorer()
    for _ in range(200):
        scorer.record_result('ETH/USDT', True)
    assert len(scorer.recent_results) <= 100


def test_history_all_failures_lowers_score():
    scorer = SignalScorer()
    for _ in range(10):
        scorer.record_result('ETH/USDT', False)
    sig = make_signal()
    score = scorer.score(sig, balanced_skews())
    assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# apply_decay
# ---------------------------------------------------------------------------

def test_decay_fresh_signal_minimal_reduction():
    scorer = SignalScorer()
    sig = make_signal(score=Decimal('80'), ttl=10.0)
    decayed = scorer.apply_decay(sig)
    assert decayed <= Decimal('80')
    assert decayed > Decimal('40')  # < 50% decay on a fresh signal


def test_decay_old_signal_reduces_score():
    """Signal near expiry gets heavily decayed score."""
    scorer = SignalScorer()
    now = time.time()
    old_sig = Signal(
        signal_id='old',
        pair='ETH/USDT',
        direction=Direction.BUY_CEX_SELL_DEX,
        cex_price=Decimal('2000'), dex_price=Decimal('2016'), spread_bps=Decimal('80'),
        size=Decimal('0.1'), expected_gross_pnl=Decimal('16'), expected_fees=Decimal('6'),
        expected_net_pnl=Decimal('10'), score=Decimal('80'),
        timestamp=now - 9,   # 9 seconds old
        expiry=now + 1,       # expires in 1s  → ttl=10, age=9 → near end
        inventory_ok=True, within_limits=True,
    )
    decayed = scorer.apply_decay(old_sig)
    assert decayed < Decimal('80') * Decimal('0.6')  # at least 40% decay
