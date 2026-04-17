"""
Smoke test for InventoryTracker — uses real Binance testnet data + simulated wallet.

Run: PYTHONPATH=. .venv/bin/python scripts/smoke_tracker.py
"""
import logging
logging.basicConfig(level=logging.WARNING)

from decimal import Decimal  # noqa: E402
from src.config import BINANCE_CONFIG  # noqa: E402
from src.exchange.client import ExchangeClient  # noqa: E402
from src.inventory.tracker import InventoryTracker, Venue  # noqa: E402

# ── Setup ──────────────────────────────────────────────────────────────────

client  = ExchangeClient(BINANCE_CONFIG)
tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])

# 1. Load real Binance testnet balances
print("Loading Binance balances...")
cex_balance = client.fetch_balance()
tracker.update_from_cex(Venue.BINANCE, cex_balance)

# 2. Simulate a wallet with some ETH and USDC
tracker.update_from_wallet(Venue.WALLET, {
    'ETH':  Decimal('5'),
    'USDC': Decimal('3000'),
})

# ── Snapshot ───────────────────────────────────────────────────────────────

print("\n=== PORTFOLIO SNAPSHOT ===")
snap = tracker.snapshot()
print(f"  Timestamp: {snap['timestamp']}")

print("\n  Per venue:")
for venue_name, assets in snap['venues'].items():
    if assets:
        print(f"    [{venue_name}]")
        for asset, data in assets.items():
            print(f"      {asset}: free={data['free']}  locked={data['locked']}")

print("\n  Totals across all venues:")
for asset, total in snap['totals'].items():
    print(f"    {asset}: {total}")

# ── can_execute check ──────────────────────────────────────────────────────

print("\n=== CAN EXECUTE (buy 1 ETH on Binance, sell 1 ETH from Wallet) ===")
check = tracker.can_execute(
    buy_venue=Venue.BINANCE,  buy_asset='USDT', buy_amount=Decimal('2500'),
    sell_venue=Venue.WALLET,  sell_asset='ETH',  sell_amount=Decimal('1'),
)
status = "YES" if check['can_execute'] else "NO"
print(f"  can_execute: {status}")
print(f"  Binance USDT available: {check['buy_venue_available']}  needed: {check['buy_venue_needed']}")
print(f"  Wallet ETH  available: {check['sell_venue_available']}  needed: {check['sell_venue_needed']}")
if check['reason']:
    print(f"  Reason: {check['reason']}")

# ── Simulate a trade ───────────────────────────────────────────────────────

print("\n=== SIMULATING TRADE (buy 2 ETH on Binance @ 2300 USDT each) ===")
eth_before  = tracker.get_available(Venue.BINANCE, 'ETH')
usdt_before = tracker.get_available(Venue.BINANCE, 'USDT')
print(f"  Before: ETH={eth_before}  USDT={usdt_before}")

tracker.record_trade(
    venue=Venue.BINANCE,
    side='buy',
    base_asset='ETH',
    quote_asset='USDT',
    base_amount=Decimal('2'),
    quote_amount=Decimal('4600'),
    fee=Decimal('4.6'),
    fee_asset='USDT',
)

eth_after  = tracker.get_available(Venue.BINANCE, 'ETH')
usdt_after = tracker.get_available(Venue.BINANCE, 'USDT')
print(f"  After:  ETH={eth_after}  USDT={usdt_after}")
print(f"  Delta:  ETH={eth_after - eth_before:+}  USDT={usdt_after - usdt_before:+}")

# ── Skew ───────────────────────────────────────────────────────────────────

print("\n=== SKEW ANALYSIS (assets present on both venues) ===")
for s in tracker.get_skews():
    # Only meaningful for assets that exist on both venues
    on_both = all(info['amount'] > 0 for info in s['venues'].values())
    if not on_both:
        continue
    flag = " *** REBALANCE NEEDED ***" if s['needs_rebalance'] else ""
    print(f"  {s['asset']}: total={s['total']}  max_deviation={s['max_deviation_pct']:.1f}%{flag}")
    for venue_name, info in s['venues'].items():
        print(f"    {venue_name}: {info['amount']} ({info['pct']:.1f}%,  dev={info['deviation_pct']:.1f}%)")
