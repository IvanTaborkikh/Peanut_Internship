"""
Live mempool monitor test.

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/test_mempool.py

Requires WS_RPC_URL in .env (wss://eth-mainnet.g.alchemy.com/v2/...)
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from src.pricing.mempool_monitor import MempoolMonitor, ParsedSwap  # noqa: E402

WS_URL = os.getenv("WS_RPC_URL") or os.getenv("MAINNET_WS_URL")

if not WS_URL:
    print("Error: set WS_RPC_URL in .env")
    print("  WS_RPC_URL=wss://eth-mainnet.g.alchemy.com/v2/YOUR_KEY")
    sys.exit(1)

swap_count = 0


def on_swap(swap: ParsedSwap) -> None:
    global swap_count
    swap_count += 1

    token_in  = swap.token_in.checksum[:10] + "..."  if swap.token_in  else "ETH"
    token_out = swap.token_out.checksum[:10] + "..." if swap.token_out else "ETH"

    print(f"\n[#{swap_count}] {swap.dex} — {swap.method}")
    print(f"  tx:        {swap.tx_hash[:20]}...")
    print(f"  sender:    {swap.sender.checksum[:12]}...")
    print(f"  token_in:  {token_in}")
    print(f"  token_out: {token_out}")
    print(f"  amount_in: {swap.amount_in}")
    print(f"  slippage:  {float(swap.slippage_tolerance * 100):.1f}%")
    print(f"  gas price: {swap.gas_price // 10**9} gwei")


async def main() -> None:
    print(f"Connecting to {WS_URL[:50]}...")
    print("Watching mempool for Uniswap V2/V3 swaps. Press Ctrl+C to stop.\n")

    monitor = MempoolMonitor(ws_url=WS_URL, callback=on_swap)
    try:
        await monitor.start()
    except KeyboardInterrupt:
        print(f"\nStopped. Detected {swap_count} swap(s).")


if __name__ == "__main__":
    asyncio.run(main())