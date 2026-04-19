"""
Smoke test for OrderBookAnalyzer — fetches real data from Binance testnet
and prints a formatted report matching the spec example.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/smoke_orderbook.py [SYMBOL] [DEPTH] [WALK_SMALL] [WALK_LARGE]

Examples:
  .venv/bin/python scripts/smoke_orderbook.py
  .venv/bin/python scripts/smoke_orderbook.py BTC/USDT 20 0.1 0.5
  .venv/bin/python scripts/smoke_orderbook.py {BASE}/USDT 20 2 10
"""
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal

logging.basicConfig(level=logging.WARNING)   # suppress INFO noise during display

from src.config import BINANCE_CONFIG  # noqa: E402
from src.exchange.client import ExchangeClient  # noqa: E402
from src.exchange.orderbook import OrderBookAnalyzer  # noqa: E402

SYMBOL     = sys.argv[1] if len(sys.argv) > 1 else 'ETH/USDT'
DEPTH      = int(sys.argv[2])   if len(sys.argv) > 2 else 20
WALK_SMALL = float(sys.argv[3]) if len(sys.argv) > 3 else 2
WALK_LARGE = float(sys.argv[4]) if len(sys.argv) > 4 else 10

BASE = SYMBOL.split('/')[0]   # 'ETH/USDT' → 'ETH', 'BTC/USDT' → 'BTC'

client = ExchangeClient(BINANCE_CONFIG)
ob_data = client.fetch_order_book(SYMBOL, limit=DEPTH)
ob = OrderBookAnalyzer(ob_data)

# ── helpers ────────────────────────────────────────────────────────────────

W = 56   # box width

def row(text=''):
    pad = W - 2 - len(text)
    return f'║  {text}{" " * pad}║'

def divider():
    return '╠' + '═' * (W - 2) + '╣'

def fmt_price(d: Decimal) -> str:
    return f'${d:,.2f}'

def fmt_qty(d: Decimal) -> str:
    return f'{d:.1f}'

# ── data ───────────────────────────────────────────────────────────────────

best_bid_p, best_bid_q = ob_data['best_bid']
best_ask_p, best_ask_q = ob_data['best_ask']
mid        = ob_data['mid_price']
spread_abs = best_ask_p - best_bid_p
spread_bps = ob_data['spread_bps']

ts = datetime.fromtimestamp(ob_data['timestamp'] / 1000, tz=timezone.utc)
ts_str = ts.strftime('%Y-%m-%d %H:%M:%S UTC')

depth_bid = ob.depth_at_bps('bid', 10)
depth_ask = ob.depth_at_bps('ask', 10)
imb = ob.imbalance()

if imb > 0.1:
    imb_label = 'buy pressure'
elif imb < -0.1:
    imb_label = 'sell pressure'
else:
    imb_label = 'balanced'
imb_sign = '+' if imb >= 0 else ''

walk_small = ob.walk_the_book('buy', WALK_SMALL)
walk_large = ob.walk_the_book('buy', WALK_LARGE)
eff_spread = ob.effective_spread(WALK_SMALL)

# ── render ─────────────────────────────────────────────────────────────────

lines = [
    '╔' + '═' * (W - 2) + '╗',
    row(f'{SYMBOL} Order Book Analysis  (depth: {DEPTH} levels)'),
    row(f'Timestamp: {ts_str}'),
    divider(),
    row(f'Best Bid:    {fmt_price(best_bid_p)} × {fmt_qty(best_bid_q)} {BASE}'),
    row(f'Best Ask:    {fmt_price(best_ask_p)} × {fmt_qty(best_ask_q)} {BASE}'),
    row(f'Mid Price:   {fmt_price(mid)}'),
    row(f'Spread:      {fmt_price(spread_abs)} ({spread_bps:.2f} bps)'),
    divider(),
    row('Depth (within 10 bps):'),
    row(f'  Bids: {fmt_qty(depth_bid)} {BASE} (${depth_bid * mid:,.0f})'),
    row(f'  Asks: {fmt_qty(depth_ask)} {BASE} (${depth_ask * mid:,.0f})'),
    row(f'Imbalance: {imb_sign}{imb:.2f} ({imb_label})'),
    divider(),
    row(f'Walk-the-book ({WALK_SMALL} {BASE} buy):'),
    row(f'  Avg price:  {fmt_price(walk_small["avg_price"])}'),
    row(f'  Slippage:   {walk_small["slippage_bps"]:.2f} bps'),
    row(f'  Levels:     {walk_small["levels_consumed"]}'),
    row(f'Walk-the-book ({WALK_LARGE} {BASE} buy):'),
    row(f'  Avg price:  {fmt_price(walk_large["avg_price"])}'),
    row(f'  Slippage:   {walk_large["slippage_bps"]:.2f} bps'),
    row(f'  Levels:     {walk_large["levels_consumed"]}'),
    divider(),
    row(f'Effective spread ({WALK_SMALL} {BASE} round-trip): {eff_spread:.2f} bps'),
    '╚' + '═' * (W - 2) + '╝',
]

print('\n'.join(lines))
