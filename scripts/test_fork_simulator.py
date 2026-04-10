"""
Fork Simulator test — requires running Anvil fork.

Step 1: Start fork in a separate terminal:
    ./scripts/start_fork.sh

Step 2: Run this script:
    PYTHONPATH=. .venv/bin/python3 scripts/test_fork_simulator.py

What it tests:
  - Loads WETH/USDC pair from forked mainnet
  - Calculates expected output with our AMM math
  - Simulates swap on fork via getAmountsOut
  - Compares calculated vs simulated
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

FORK_URL = "http://localhost:8545"

from src.core.types import Address, Token          # noqa: E402
from src.pricing.ForkSimulator import ForkSimulator  # noqa: E402
from src.pricing.UniswapV2Pair import UniswapV2Pair  # noqa: E402
from src.chain.client import ChainClient             # noqa: E402

WETH = Token(Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), "WETH", 18)
USDC = Token(Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), "USDC", 6)
PAIR_ADDR = Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc")
SENDER    = Address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")  # Anvil account #0


def check_fork_running() -> bool:
    import requests
    try:
        r = requests.post(FORK_URL, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def main() -> None:
    print("── Fork Simulator Test ──────────────────────────────")

    if not check_fork_running():
        print(f"\nError: Anvil not running at {FORK_URL}")
        print("Start it first:")
        print("  ./scripts/start_fork.sh")
        sys.exit(1)

    rpc_url = os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL")
    if not rpc_url:
        print("Error: set MAINNET_RPC_URL or ETH_RPC_URL in .env")
        sys.exit(1)

    print(f"Fork running at {FORK_URL} ✓")
    print()

    # ── 1. Load pair from fork ────────────────────────────────────────────────
    print("Loading WETH/USDC pair from fork...")
    fork_client = ChainClient([FORK_URL])
    pair = UniswapV2Pair.from_chain(PAIR_ADDR, fork_client)
    print(f"  token0: {pair.token0.symbol}  reserve0: {pair.reserve0}")
    print(f"  token1: {pair.token1.symbol}  reserve1: {pair.reserve1}")
    print()

    # ── 2. Calculate expected output ─────────────────────────────────────────
    amount_in = 1 * 10**18  # 1 WETH


    calculated = pair.get_amount_out(amount_in, WETH)
    print(f"AMM calculation:  {amount_in / 10**18:.4f} WETH → {calculated / 10**6:.4f} USDC")

    # ── 3. Simulate on fork ───────────────────────────────────────────────────
    simulator = ForkSimulator(FORK_URL)
    comparison = simulator.compare_simulation_vs_calculation(pair, amount_in, WETH)
    print(f"Fork simulation:  {amount_in / 10**18:.4f} WETH → {comparison['simulated'] / 10**6:.4f} USDC")
    print()

    # ── 4. Compare ────────────────────────────────────────────────────────────
    print(f"Calculated:  {comparison['calculated']}")
    print(f"Simulated:   {comparison['simulated']}")
    print(f"Difference:  {comparison['difference']}")
    print(f"Match:       {'✅ YES' if comparison['match'] else '❌ NO'}")


if __name__ == "__main__":
    main()
