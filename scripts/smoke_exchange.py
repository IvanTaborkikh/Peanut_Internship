"""
Smoke test for ExchangeClient against Binance testnet.
Run: .venv/bin/python scripts/smoke_exchange.py
"""
import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

from src.config import BINANCE_CONFIG  # noqa: E402
from src.exchange.client import ExchangeClient  # noqa: E402

client = ExchangeClient(BINANCE_CONFIG)

# --- Order book ---
print("\n=== ORDER BOOK ===")
ob = client.fetch_order_book('ETH/USDT')
print(f"  mid_price : {ob['mid_price']}")
print(f"  spread_bps: {ob['spread_bps']:.4f}")
print(f"  best_bid  : {ob['best_bid']}")
print(f"  best_ask  : {ob['best_ask']}")

# --- Balance ---
print("\n=== BALANCE ===")
balance = client.fetch_balance()
if balance:
    for asset, data in balance.items():
        print(f"  {asset}: free={data['free']}  locked={data['locked']}")
else:
    print("  (empty — no funds on testnet account)")

# --- Fees ---
print("\n=== FEES ===")
fees = client.get_trading_fees('ETH/USDT')
print(f"  maker={fees['maker']}  taker={fees['taker']}")
