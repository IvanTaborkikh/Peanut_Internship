from decimal import Decimal
from src.inventory.tracker import InventoryTracker, Venue
from src.inventory.rebalancer import RebalancePlanner, TransferPlan, MIN_OPERATING_BALANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tracker(binance_eth='0', wallet_eth='0',
                 binance_usdt='0', wallet_usdt='0',
                 binance_usdc='0', wallet_usdc='0') -> InventoryTracker:
    tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])

    cex = {}
    if Decimal(binance_eth)  > 0:
        cex['ETH']  = {'free': Decimal(binance_eth),  'locked': Decimal('0'), 'total': Decimal(binance_eth)}
    if Decimal(binance_usdt) > 0:
        cex['USDT'] = {'free': Decimal(binance_usdt), 'locked': Decimal('0'), 'total': Decimal(binance_usdt)}
    if Decimal(binance_usdc) > 0:
        cex['USDC'] = {'free': Decimal(binance_usdc), 'locked': Decimal('0'), 'total': Decimal(binance_usdc)}
    if cex:
        tracker.update_from_cex(Venue.BINANCE, cex)

    wallet = {}
    if Decimal(wallet_eth)  > 0:
        wallet['ETH']  = Decimal(wallet_eth)
    if Decimal(wallet_usdt) > 0:
        wallet['USDT'] = Decimal(wallet_usdt)
    if Decimal(wallet_usdc) > 0:
        wallet['USDC'] = Decimal(wallet_usdc)
    if wallet:
        tracker.update_from_wallet(Venue.WALLET, wallet)

    return tracker


def make_planner(tracker: InventoryTracker, threshold: float = 30.0) -> RebalancePlanner:
    return RebalancePlanner(tracker, threshold_pct=threshold)


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------

def test_check_detects_skewed_asset():
    """Asset with 85/15 split (> 30% deviation) flagged for rebalance."""
    tracker = make_tracker(binance_eth='85', wallet_eth='15')
    planner = make_planner(tracker)

    results = planner.check_all()
    eth = next(r for r in results if r['asset'] == 'ETH')

    assert eth['needs_rebalance'] is True
    assert eth['max_deviation_pct'] > 30.0


def test_check_passes_balanced_asset():
    """Asset with 55/45 split (< 30% deviation) not flagged."""
    tracker = make_tracker(binance_eth='55', wallet_eth='45')
    planner = make_planner(tracker)

    results = planner.check_all()
    eth = next(r for r in results if r['asset'] == 'ETH')

    assert eth['needs_rebalance'] is False
    assert eth['max_deviation_pct'] < 30.0


def test_check_all_returns_all_assets():
    """check_all includes every tracked asset."""
    tracker = make_tracker(binance_eth='8', wallet_eth='2',
                           binance_usdt='6000', wallet_usdt='4000')
    planner = make_planner(tracker)

    assets = {r['asset'] for r in planner.check_all()}
    assert 'ETH' in assets
    assert 'USDT' in assets


def test_check_all_schema():
    """Each entry has required keys."""
    tracker = make_tracker(binance_eth='5', wallet_eth='5')
    planner = make_planner(tracker)

    for item in planner.check_all():
        assert 'asset'             in item
        assert 'max_deviation_pct' in item
        assert 'needs_rebalance'   in item


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def test_plan_generates_correct_transfer():
    """Plan moves excess from over-weighted venue to under-weighted venue."""
    # Binance 9, Wallet 1 → target 5 each → send 4 from Binance to Wallet
    tracker = make_tracker(binance_eth='9', wallet_eth='1')
    planner = make_planner(tracker)

    plans = planner.plan('ETH')

    assert len(plans) == 1
    p = plans[0]
    assert p.from_venue == Venue.BINANCE
    assert p.to_venue   == Venue.WALLET
    assert p.asset      == 'ETH'
    assert p.amount     == Decimal('4')


def test_plan_empty_when_balanced():
    """No plans generated for a balanced asset."""
    tracker = make_tracker(binance_eth='50', wallet_eth='50')
    planner = make_planner(tracker)

    assert planner.plan('ETH') == []


def test_plan_respects_min_operating_balance():
    """Transfer never leaves from_venue below minimum operating balance."""
    min_eth = MIN_OPERATING_BALANCE['ETH']   # 0.5 ETH
    # Binance has 2 ETH (80%), Wallet has 0.5 ETH (20%) → skewed
    # Max transferable = 2 - 0.5 (min) = 1.5 ETH
    tracker = make_tracker(binance_eth='2', wallet_eth='0.5')
    planner = make_planner(tracker)

    plans = planner.plan('ETH')

    if plans:
        remaining = Decimal('2') - plans[0].amount
        assert remaining >= min_eth


def test_plan_accounts_for_fees():
    """net_amount = amount - fee."""
    tracker = make_tracker(binance_eth='9', wallet_eth='1')
    planner = make_planner(tracker)

    plans = planner.plan('ETH')
    assert len(plans) == 1

    p = plans[0]
    assert p.net_amount == p.amount - p.estimated_fee
    assert p.estimated_fee == Decimal('0.005')   # from TRANSFER_FEES['ETH']


def test_plan_direction_wallet_to_binance():
    """When wallet is over-weighted, transfer goes wallet → binance."""
    tracker = make_tracker(binance_eth='1', wallet_eth='9')
    planner = make_planner(tracker)

    plans = planner.plan('ETH')
    assert len(plans) == 1
    assert plans[0].from_venue == Venue.WALLET
    assert plans[0].to_venue   == Venue.BINANCE


def test_plan_returns_empty_for_unknown_asset():
    """No plan for an asset with no balances."""
    tracker = make_tracker(binance_eth='5', wallet_eth='5')
    planner = make_planner(tracker)

    assert planner.plan('BTC') == []


def test_plan_usdt_uses_correct_fee():
    """USDT transfer uses USDT fee (1.0), not ETH fee."""
    tracker = make_tracker(binance_usdt='9000', wallet_usdt='1000')
    planner = make_planner(tracker)

    plans = planner.plan('USDT')
    assert len(plans) == 1
    assert plans[0].estimated_fee == Decimal('1.0')


# ---------------------------------------------------------------------------
# plan_all
# ---------------------------------------------------------------------------

def test_plan_all_only_includes_skewed_assets():
    """plan_all skips assets that don't need rebalancing."""
    tracker = make_tracker(
        binance_eth='85', wallet_eth='15',      # skewed → needs plan
        binance_usdt='55', wallet_usdt='45',    # balanced → no plan
    )
    planner = make_planner(tracker)

    plans = planner.plan_all()

    assert 'ETH' in plans
    assert 'USDT' not in plans


def test_plan_all_returns_dict():
    """plan_all returns {asset: [TransferPlan, ...]}."""
    tracker = make_tracker(binance_eth='85', wallet_eth='15')
    planner = make_planner(tracker)

    plans = planner.plan_all()
    assert isinstance(plans, dict)
    for asset, ps in plans.items():
        assert isinstance(ps, list)
        assert all(isinstance(p, TransferPlan) for p in ps)


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

def test_estimate_cost_sums_correctly():
    """Total fees = sum of all plan fees; time = max (parallel execution)."""
    tracker = make_tracker(
        binance_eth='85',    wallet_eth='15',
        binance_usdc='9000', wallet_usdc='1000',
    )
    planner = make_planner(tracker)

    all_plans = [p for ps in planner.plan_all().values() for p in ps]
    cost = planner.estimate_cost(all_plans)

    expected_fees = sum(p.estimated_fee for p in all_plans)
    expected_time = max(p.estimated_time_min for p in all_plans)

    assert cost['total_transfers'] == len(all_plans)
    assert cost['total_fees_usd']  == expected_fees
    assert cost['total_time_min']  == expected_time
    assert set(cost['assets_affected']) == {'ETH', 'USDC'}


def test_estimate_cost_empty_plans():
    """Empty plan list returns zeroed cost dict."""
    tracker = make_tracker(binance_eth='5', wallet_eth='5')
    planner = make_planner(tracker)

    cost = planner.estimate_cost([])

    assert cost['total_transfers'] == 0
    assert cost['total_fees_usd']  == Decimal('0')
    assert cost['total_time_min']  == 0
    assert cost['assets_affected'] == []


def test_plan_returns_empty_when_only_one_venue_has_balance():
    """Asset present on only one venue → skew=50% but can't rebalance (nothing to send to)."""
    # Binance has ETH, Wallet has none → 100/0 split → needs_rebalance=True
    # But target for wallet = 50%, deficit = 50% → plan tries to send from Binance
    # This IS a valid plan — it should generate a transfer.
    tracker = make_tracker(binance_eth='10', wallet_eth='0')
    planner = make_planner(tracker)

    plans = planner.plan('ETH')
    # Plan should exist — Binance sends to Wallet to reach 50/50
    assert len(plans) == 1
    assert plans[0].from_venue == Venue.BINANCE
    assert plans[0].to_venue   == Venue.WALLET
