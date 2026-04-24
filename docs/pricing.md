# Pricing Module

The `pricing` module provides the full pipeline from raw pool reserves to a verified swap quote.
It is the core of the arbitrage system — responsible for finding the best route, estimating price impact, monitoring the mempool, and validating quotes against a local fork.

---

## Architecture

```
UniswapV2Pair          — AMM math for a single pool
Route                  — ordered list of pools (one swap path)
RouteFinder            — graph-based DFS to find and rank all routes
PriceImpactAnalyzer    — impact table, max safe trade size, true cost
MempoolMonitor         — WebSocket listener for pending Uniswap swaps
ForkSimulator          — verifies output via real contract on Anvil fork
PricingEngine          — unified interface that wires everything together
```

Data flows top to bottom: raw reserves → AMM math → route selection → fork verification → Quote.

---

## UniswapV2Pair

Represents a single Uniswap V2 liquidity pool. All math uses integer arithmetic only — no floats — to match the Solidity contract exactly.

```python
from src.pricing.uniswap_v2_pair import UniswapV2Pair
from src.core.types import Address, Token

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)

pair = UniswapV2Pair(
    address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
    token0=WETH,
    token1=USDC,
    reserve0=1_000 * 10 ** 18,  # 1000 ETH
    reserve1=2_000_000 * 10 ** 6,  # 2M USDC
    fee_bps=30,  # 0.30% fee (default)
)
```

### Methods

**`get_amount_out(amount_in, token_in) → int`**

How much `token_out` you receive for `amount_in` of `token_in`.
Matches Uniswap V2 Solidity formula exactly — verified against real mainnet transaction.

```python
usdc_out = pair.get_amount_out(1 * 10**18, WETH)
# → 1_992_013_962 (≈ 1992.01 USDC)
```

**`get_amount_in(amount_out, token_out) → int`**

How much `token_in` you need to receive exactly `amount_out` of `token_out`.
Raises `ValueError` if `amount_out` exceeds pool reserve.

```python
eth_needed = pair.get_amount_in(2000 * 10**6, USDC)
# → slightly more than 1 ETH
```

**`get_spot_price(token_in) → Decimal`**

Current pool price as `reserve_in / reserve_out`. Does not account for fee or trade size.
Raises `ValueError` if `reserve_out` is zero.

**`get_execution_price(amount_in, token_in) → Decimal`**

Actual price for a trade of `amount_in`. Always worse than spot price due to price impact.

**`get_price_impact(amount_in, token_in) → Decimal`**

Price impact as a fraction (0.01 = 1%).
`impact = (execution_price - spot_price) / spot_price`

**`simulate_swap(amount_in, token_in) → UniswapV2Pair`**

Returns a **new** pair with updated reserves after the swap. Never mutates the original.

**`from_chain(address, client) → UniswapV2Pair`** (classmethod)

Loads real reserves and token metadata from the blockchain.

```python
pair = UniswapV2Pair.from_chain(Address("0xB4e16..."), chain_client)
```

---

## Route

Represents a specific swap path through one or more pools.

```python
from src.pricing.route import Route

# Direct: WETH → USDC
route = Route(pools=[pair], path=[WETH, USDC])

# Multi-hop: WETH → DAI → USDC
route = Route(pools=[pair_eth_dai, pair_dai_usdc], path=[WETH, DAI, USDC])

# Custom gas params for expensive tokens
route = Route(pools=[pair], path=[WETH, USDC], base_gas=200_000, gas_per_hop=120_000)
```

Validation: constructor raises `ValueError` if pool count ≠ path length - 1, or if any pool does not contain the expected tokens from the path.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_output(amount_in)` | `int` | Final output amount after all hops |
| `get_intermediate_amounts(amount_in)` | `list[int]` | Amount at each step: `[input, after_hop1, ...]` |
| `estimate_gas()` | `int` | `base_gas + (num_hops - 1) * gas_per_hop` |
| `num_hops` | `int` | Number of pools in the route |

---

## RouteFinder

Finds all routes between two tokens using DFS on a token graph, then selects the best one by net output after gas cost.

```python
from src.pricing.route_finder import RouteFinder

finder = RouteFinder(pools=[pair_eth_usdc, pair_eth_dai, pair_dai_usdc])

# Find all routes
routes = finder.find_all_routes(WETH, USDC, max_hops=3)

# Best route by net output after gas
route, net_output = finder.find_best_route(WETH, USDC, 1 * 10 ** 18, gas_price_gwei=20)

# Comparison table
table = finder.compare_routes(WETH, USDC, 1 * 10 ** 18, gas_price_gwei=20)
# → list of RouteComparison(route, gross_output, gas_estimate, gas_cost, net_output)
```

### Gas cost conversion

Gas is paid in ETH (wei), but `gross_output` is in output token units. `RouteFinder` converts gas cost to output token units using the spot price from a direct pool. If no direct pool exists, gas cost is returned in wei as a best-effort fallback.

### With ForkSimulator (real gas estimates)

```python
finder = RouteFinder(pools, simulator=fork_simulator)
```

When a simulator is provided, `find_best_route` uses actual gas from `getAmountsOut` instead of the static formula.

### Incremental pool update

```python
finder.update_pool(new_pair)  # updates one pool and rebuilds graph — no full rebuild
```

---

## PriceImpactAnalyzer

Analyzes how trade size affects price for a single pool.

```python
from src.pricing.price_impact_analyzer import PriceImpactAnalyzer
from decimal import Decimal

analyzer = PriceImpactAnalyzer(pair)
```

### `generate_impact_table(token_in, sizes) → list[dict]`

Returns a row for each trade size with: `amount_in`, `amount_out`, `spot_price`, `execution_price`, `price_impact_pct`.

```python
sizes = [1 * 10**18, 5 * 10**18, 10 * 10**18]
table = analyzer.generate_impact_table(WETH, sizes)
```

### `find_max_size_for_impact(token_in, max_impact_pct) → int`

Binary search for the largest trade where `price_impact <= max_impact_pct`.

```python
max_size = analyzer.find_max_size_for_impact(WETH, Decimal("0.01"))  # 1% limit
```

### `estimate_true_cost(amount_in, token_in, gas_price_gwei, gas_estimate) → dict`

Returns `gross_output`, `gas_cost_eth`, `gas_cost_in_output_token`, `net_output`, `effective_price`.

```python
cost = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20)
print(cost["net_output"])       # gross minus gas
print(cost["effective_price"])  # Decimal: token_in per token_out
```

---

## MempoolMonitor

Listens to Ethereum mempool via WebSocket and calls a callback for every recognized Uniswap swap.

```python
from src.pricing.mempool_monitor import MempoolMonitor


def on_swap(swap):
    print(swap.dex, swap.method, swap.amount_in)


monitor = MempoolMonitor(ws_url="wss://eth-mainnet.g.alchemy.com/v2/...", callback=on_swap)
await monitor.start()  # runs indefinitely
```

### ParsedSwap fields

| Field | Type | Description |
|-------|------|-------------|
| `tx_hash` | `str` | Transaction hash |
| `dex` | `str` | `"UniswapV2"` or `"UniswapV3"` |
| `method` | `str` | Function name |
| `token_in` | `Address \| None` | Input token address |
| `token_out` | `Address \| None` | Output token address |
| `amount_in` | `int` | Input amount in raw units (wei for ETH swaps) |
| `min_amount_out` | `int` | Minimum output (slippage bound) |
| `gas_price` | `int` | Gas price in wei |
| `slippage_tolerance` | `Decimal` | `(amount_in - min_amount_out) / amount_in` |

### Recognized selectors

| Selector | Method |
|----------|--------|
| `0x38ed1739` | `swapExactTokensForTokens` |
| `0x7ff36ab5` | `swapExactETHForTokens` |
| `0x18cbafe5` | `swapExactTokensForETH` |
| `0x5ae401dc` | `multicall` (V3, basic detection only) |

ABI decoding uses `eth_abi.decode` — no manual byte parsing.

---

## ForkSimulator

Simulates swaps on a local [Anvil](https://book.getfoundry.sh/anvil/) fork using `getAmountsOut` — a view call that requires no token approvals and costs no gas.

```python
from src.pricing.fork_simulator import ForkSimulator

simulator = ForkSimulator("http://localhost:8545")
```

Start Anvil first:
```bash
make fork
```

### `simulate_route(route, amount_in, sender) → SimulationResult`

```python
result = simulator.simulate_route(route, 1 * 10**18, sender)
print(result.success)     # bool
print(result.amount_out)  # int
print(result.gas_used)    # int
print(result.error)       # str | None
```

### `compare_simulation_vs_calculation(pair, amount_in, token_in) → dict`

Compares local AMM math against the real contract result.

```python
comparison = simulator.compare_simulation_vs_calculation(pair, 1 * 10**18, WETH)
# → {"calculated": int, "simulated": int, "difference": int, "match": bool}
```

---

## PricingEngine

The main interface. Wires together pools, routing, simulation, and mempool monitoring.

```python
from src.pricing.pricing_engine import PricingEngine

engine = PricingEngine(
    chain_client=client,
    fork_url="http://localhost:8545",
    ws_url="wss://eth-mainnet.g.alchemy.com/v2/...",
)

engine.load_pools([Address("0xB4e16...")])
```

### `get_quote(token_in, token_out, amount_in, gas_price_gwei, sender) → Quote`

Finds the best route, runs fork simulation, and returns a validated quote.
Raises `QuoteError` if no route exists or simulation fails.

```python
quote = engine.get_quote(WETH, USDC, 1 * 10**18, gas_price_gwei=20)
print(quote.expected_output)   # net output from RouteFinder
print(quote.simulated_output)  # output from Anvil simulation
print(quote.gas_estimate)      # gas used
print(quote.is_valid)          # True if diff < 0.1%
```

### `Quote.is_valid`

Returns `True` if the difference between `expected_output` and `simulated_output` is less than 0.1%. A quote with `is_valid=False` means the fork result diverged from local math — indicates stale reserves or an unusual pool.

### `start_monitoring()`

Async method that starts the mempool WebSocket listener. When a pending swap touches a loaded pool, reserves are refreshed automatically.

```python
import asyncio
asyncio.create_task(engine.start_monitoring())
```

### `refresh_pool(address)`

Re-fetches reserves for a single pool from chain. Uses `RouteFinder.update_pool()` internally — no full graph rebuild.

---

## CLI: impact_analyzer

```bash
# Offline demo with hardcoded pair
make impact-analyzer

# Custom token and sizes (amounts in human units, multiplied by decimals automatically)
make impact-analyzer TOKEN_IN=WETH SIZES=1,5,10,50

# Real pair from chain
make impact-analyzer PAIR=0xB4e16... TOKEN_IN=WETH RPC=https://eth-mainnet.g.alchemy.com/v2/key
```

---

## Notes

- All reserve amounts are in raw token units (wei for 18-decimal tokens)
- Gas cost conversion to output token units requires a direct pool — for multi-hop routes without a direct pool, gas is expressed in wei (acknowledged limitation)
- `ForkSimulator` requires a running Anvil fork (`make fork`)
- `MempoolMonitor` requires a WebSocket RPC endpoint (`WS_RPC_URL` in `.env`)
