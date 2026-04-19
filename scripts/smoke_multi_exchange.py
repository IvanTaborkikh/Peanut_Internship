"""
Multi-exchange arb check: Binance testnet vs Bybit testnet.

Treats Bybit mid price as the reference price and compares it against
Binance bid/ask to detect cross-exchange price divergence.

Usage:
    PYTHONPATH=. python scripts/smoke_multi_exchange.py [PAIR] [SIZE]

Examples:
    PYTHONPATH=. python scripts/smoke_multi_exchange.py
    PYTHONPATH=. python scripts/smoke_multi_exchange.py BTC/USDT 0.1
"""
import logging
import sys
from decimal import Decimal

logging.basicConfig(level=logging.WARNING)

from src.config import BINANCE_CONFIG, BYBIT_CONFIG  # noqa: E402
from src.exchange.client import ExchangeClient  # noqa: E402
from src.exchange.cex_pricer_adapter import CexPricingAdapter  # noqa: E402
from src.integration.arb_checker import ArbChecker  # noqa: E402
from src.integration.arb_logger import ArbLogger  # noqa: E402
from src.inventory.tracker import InventoryTracker, Venue  # noqa: E402
from src.inventory.pnl import PnLEngine  # noqa: E402

pair = sys.argv[1] if len(sys.argv) > 1 else 'ETH/USDT'
size = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

print('Connecting to Binance testnet...')
binance = ExchangeClient(BINANCE_CONFIG)

print('Connecting to Bybit testnet...')
try:
    bybit   = ExchangeClient(BYBIT_CONFIG)
    adapter = CexPricingAdapter(bybit, name='Bybit')
except Exception as e:
    print(f'  Bybit connection failed ({e})')
    print('  Falling back to CEX mid as reference price.\n')
    adapter = None

base_asset, quote_asset = pair.split('/')

tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])
tracker.update_from_cex(Venue.BINANCE, binance.fetch_balance())
tracker.update_from_wallet(Venue.WALLET, {
    base_asset:  Decimal('5'),
    quote_asset: Decimal('15000'),
})

logger  = ArbLogger('arb_log.csv')
checker = ArbChecker(
    pricing_engine=adapter,
    exchange_client=binance,
    inventory_tracker=tracker,
    pnl_engine=PnLEngine(),
)

logging.disable(logging.ERROR)   # suppress exchange-level error noise for bad symbols
try:
    result = checker.check(pair, size=size)
except Exception as e:
    logging.disable(logging.NOTSET)
    if 'does not have market symbol' in str(e) or 'BadSymbol' in type(e).__name__:
        print(f'\n  ❌ Unknown pair: {pair}')
        print('  Check the symbol name — Binance may use a different ticker.')
        print('  Example: MATIC was renamed to POL/USDT\n')
    else:
        print(f'\n  ❌ Error: {e}\n')
    sys.exit(1)
logging.disable(logging.NOTSET)
logger.log(result, size=size)

W          = 50
src_label  = adapter.name if adapter else 'CEX mid (fallback)'
verdict    = '✅ PROFITABLE' if result['executable'] else '❌ NOT PROFITABLE'
reason     = 'Execute!' if result['executable'] else (
    'costs exceed gap' if result['estimated_net_pnl_bps'] <= 0 else 'inventory insufficient'
)

print(f'\n{"═" * W}')
print(f'  MULTI-EXCHANGE ARB: {pair}  (size: {size} {base_asset})')
print(f'  {src_label} vs Binance')
print(f'{"═" * W}\n')

print(f'  {src_label:12} mid:  ${result["dex_price"]:>10,.2f}')
print(f'  Binance bid:        ${result["cex_bid"]:>10,.2f}')
print(f'  Binance ask:        ${result["cex_ask"]:>10,.2f}')
print(f'\n  Gap:     {result["gap_bps"]:.2f} bps')
print(f'  Costs:   {result["estimated_costs_bps"]:.2f} bps')
print(f'  Net PnL: {result["estimated_net_pnl_bps"]:.2f} bps  {verdict}')
print(f'\n  Direction: {result["direction"] or "none — prices within spread"}')
print(f'  Verdict:   {"EXECUTE" if result["executable"] else "SKIP"} — {reason}')
print('\n  Logged to arb_log.csv')
print(f'{"═" * W}')
