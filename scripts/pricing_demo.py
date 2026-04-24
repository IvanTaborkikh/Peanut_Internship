
"""
Pricing module demo — no network required.

Demonstrates:
  1. AMM math  (UniswapV2Pair)
  2. Price impact table  (PriceImpactAnalyzer)
  3. Multi-hop routing  (RouteFinder)
  4. Mempool tx parsing  (MempoolMonitor)

Usage:
  make pricing-demo
  # or directly:
  .venv/bin/python3 scripts/pricing_demo.py
"""

from decimal import Decimal

from src.core.types import Address, Token
from src.pricing.mempool_monitor import MempoolMonitor
from src.pricing.price_impact_analyzer import PriceImpactAnalyzer
from src.pricing.route_finder import RouteFinder
from src.pricing.uniswap_v2_pair import UniswapV2Pair

# ── Tokens ────────────────────────────────────────────────────────────────────

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)
DAI  = Token(address=Address("0x6B175474E89094C44Da98b954EedeAC495271d0F"), symbol="DAI",  decimals=18)

# ── Pools (approximate mainnet reserves) ─────────────────────────────────────

PAIR_ETH_USDC = UniswapV2Pair(
    address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
    token0=WETH, token1=USDC,
    reserve0=1_000 * 10**18,
    reserve1=2_000_000 * 10**6,
)

PAIR_ETH_DAI = UniswapV2Pair(
    address=Address("0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"),
    token0=WETH, token1=DAI,
    reserve0=500 * 10**18,
    reserve1=1_000_000 * 10**18,
)

PAIR_DAI_USDC = UniswapV2Pair(
    address=Address("0xAE461cA67B15dc8dc81CE7615e0320dA1A9aB8D5"),
    token0=DAI, token1=USDC,
    reserve0=5_000_000 * 10**18,
    reserve1=5_000_000 * 10**6,
)


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


# ── 1. AMM math ───────────────────────────────────────────────────────────────

section("1. AMM Math — UniswapV2Pair")

amount_in_eth = 1 * 10**18
usdc_out = PAIR_ETH_USDC.get_amount_out(amount_in_eth, WETH)
spot      = PAIR_ETH_USDC.get_spot_price(WETH)
exec_p    = PAIR_ETH_USDC.get_execution_price(amount_in_eth, WETH)
impact    = PAIR_ETH_USDC.get_price_impact(amount_in_eth, WETH)

print("Input:            1 WETH")
print(f"Output:           {usdc_out / 10**6:.2f} USDC")
print(f"Spot price:       {float(spot):.6f} WETH/USDC")
print(f"Execution price:  {float(exec_p):.6f} WETH/USDC")
print(f"Price impact:     {float(impact * 100):.4f}%")

print("\n--- simulate_swap (reserves after trade) ---")
new_pair = PAIR_ETH_USDC.simulate_swap(amount_in_eth, WETH)
print(f"Old reserves:  {PAIR_ETH_USDC.reserve0 / 10**18:.1f} WETH / {PAIR_ETH_USDC.reserve1 / 10**6:,.0f} USDC")
print(f"New reserves:  {new_pair.reserve0 / 10**18:.2f} WETH / {new_pair.reserve1 / 10**6:,.0f} USDC")

print("\n--- get_amount_in (reverse calc) ---")
want_usdc = 2000 * 10**6
eth_needed = PAIR_ETH_USDC.get_amount_in(want_usdc, USDC)
print(f"To get 2000 USDC need: {eth_needed / 10**18:.6f} WETH")


# ── 2. Price impact table ─────────────────────────────────────────────────────

section("2. Price Impact Analysis")

analyzer = PriceImpactAnalyzer(PAIR_ETH_USDC)
sizes_eth = [
    int(0.1  * 10**18),
    int(1    * 10**18),
    int(5    * 10**18),
    int(10   * 10**18),
    int(50   * 10**18),
    int(100  * 10**18),
]

table = analyzer.generate_impact_table(WETH, sizes_eth)
print(f"{'Amount In (ETH)':>16}  {'Output (USDC)':>14}  {'Impact':>8}")
print("-" * 44)
for row in table:
    eth  = row["amount_in"] / 10**18
    usdc = row["amount_out"] / 10**6
    pct  = float(row["price_impact_pct"])
    print(f"{eth:>16.1f}  {usdc:>14,.2f}  {pct:>7.4f}%")

max_size = analyzer.find_max_size_for_impact(WETH, Decimal("0.01"))
print(f"\nMax trade for <1% impact: {max_size / 10**18:.4f} WETH")

cost = analyzer.estimate_true_cost(
    amount_in=1 * 10**18,
    token_in=WETH,
    gas_price_gwei=20,
)
print("\nTrue cost breakdown (1 ETH, 20 gwei):")
print(f"  Gross output:   {cost['gross_output'] / 10**6:.2f} USDC")
print(f"  Gas cost (ETH): {cost['gas_cost_eth'] / 10**18:.6f} ETH")
print(f"  Net output:     {cost['net_output'] / 10**6:.2f} USDC")


# ── 3. Routing ────────────────────────────────────────────────────────────────

section("3. Route Finding — WETH → USDC")

finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
all_routes = finder.find_all_routes(WETH, USDC, max_hops=2)
print(f"Found {len(all_routes)} route(s):")
for i, r in enumerate(all_routes):
    path_str = " → ".join(t.symbol for t in r.path)
    out = r.get_output(1 * 10**18)
    print(f"  [{i+1}] {path_str}  →  {out / 10**6:.4f} USDC  (gas est: {r.estimate_gas():,})")

print()
comparison = finder.compare_routes(WETH, USDC, 1 * 10**18, gas_price_gwei=20)
print(f"{'Route':<22}  {'Gross':>12}  {'Gas est':>10}  {'Net':>12}")
print("-" * 62)
for item in comparison:
    path_str = " → ".join(t.symbol for t in item["route"].path)
    gross = item["gross_output"] / 10**6
    gas   = item["gas_estimate"]
    net   = item["net_output"] / 10**6
    print(f"{path_str:<22}  {gross:>12.4f}  {gas:>10,}  {net:>12.4f}")

best_route, net_out = finder.find_best_route(WETH, USDC, 1 * 10**18, gas_price_gwei=20)
best_path = " → ".join(t.symbol for t in best_route.path)
print(f"\nBest route: {best_path}  (net: {net_out / 10**6:.4f} USDC)")

print()
amounts = best_route.get_intermediate_amounts(1 * 10**18)
for i, (token, amount) in enumerate(zip(best_route.path, amounts)):
    div = 10**token.decimals
    print(f"  Step {i}: {amount / div:.6f} {token.symbol}")


# ── 4. Mempool tx parsing ─────────────────────────────────────────────────────

section("4. Mempool — Parse Swap Transaction")

monitor = MempoolMonitor(ws_url="wss://fake", callback=lambda _: None)

# Real-looking swapExactTokensForTokens calldata (selector + ABI-encoded params)
# swapExactTokensForTokens(1000 USDC, 950 USDC min, [USDC, WETH], to, deadline)
selector = "38ed1739"
# amount_in  = 1000 * 10^6
# amount_out = 950 * 10^6
# path offset = 0x80 (128)
# to offset   = 0xc0 (but we encode inline)
# deadline    = 9999999999
def pad32(val: int) -> str:
    return val.to_bytes(32, "big").hex()

def pad32addr(addr: str) -> str:
    return "000000000000000000000000" + addr[2:]

amount_in_raw  = 1000 * 10**6
amount_out_raw = 950  * 10**6
# ABI layout for swapExactTokensForTokens(uint, uint, address[], address, uint):
# payload slot 0 (offset   0): amountIn
# payload slot 1 (offset  32): amountOutMin
# payload slot 2 (offset  64): path array offset = 160 (5 slots * 32 bytes)
# payload slot 3 (offset  96): to address
# payload slot 4 (offset 128): deadline
# payload slot 5 (offset 160): path array length
# payload slot 6 (offset 192): path[0]
# payload slot 7 (offset 224): path[1]
path_offset = 160  # 5 static slots * 32 bytes
deadline    = 9_999_999_999
usdc_addr = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
weth_addr = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
to_addr   = "0xdead000000000000000000000000000000000000"

calldata = (
    "0x" + selector
    + pad32(amount_in_raw)
    + pad32(amount_out_raw)
    + pad32(path_offset)
    + pad32addr(to_addr)
    + pad32(deadline)
    + pad32(2)           # path length
    + pad32addr(usdc_addr)
    + pad32addr(weth_addr)
)

tx = {
    "hash": "0xabc123",
    "from": "0xab5801a7d398351b8be11c439e05c5b3259aec9b",
    "to":   "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "input": calldata,
    "gasPrice": hex(20 * 10**9),
    "value": "0x0",
}

swap = monitor.parse_transaction(tx)
if swap:
    print(f"DEX:              {swap.dex}")
    print(f"Method:           {swap.method}")
    print(f"Token in:         {swap.token_in}")
    print(f"Token out:        {swap.token_out}")
    print(f"Amount in:        {swap.amount_in / 10**6:.2f} USDC")
    print(f"Min amount out:   {swap.min_amount_out / 10**6:.2f} USDC")
    print(f"Slippage:         {float(swap.slippage_tolerance * 100):.1f}%")
    print(f"Gas price:        {swap.gas_price / 10**9:.0f} gwei")
else:
    print("Not detected as swap")

print()
