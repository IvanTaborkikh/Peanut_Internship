# Arbitrage Trading System

Ethereum arbitrage trading system built during internship at **Peanut Trade**.
Designed for MEV, HFT, and on-chain arbitrage strategies.

---

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/setup.md](docs/setup.md) | Installation, environment setup, make commands |
| [docs/core.md](docs/core.md) | `Address`, `TokenAmount`, `WalletManager`, `CanonicalSerializer` |
| [docs/chain.md](docs/chain.md) | `ChainClient`, `TransactionBuilder`, `Analyzer`, error handling |
| [docs/pricing.md](docs/pricing.md) | `UniswapV2Pair`, `RouteFinder`, `PriceImpactAnalyzer`, `ForkSimulator`, `PricingEngine` |
| [docs/exchange.md](docs/exchange.md) | `ExchangeClient`, `OrderBookAnalyzer` |
| [docs/inventory.md](docs/inventory.md) | `InventoryTracker`, `RebalancePlanner`, `PnLEngine` |

---

## What it can do now

- Load Ethereum wallet from environment, sign messages and transactions
- Connect to any EVM-compatible RPC endpoint with automatic retry
- Build, sign, and send transactions to testnet or mainnet
- Analyze any Ethereum transaction: block info, gas analysis, token transfers, revert reasons
- Calculate Uniswap V2 swap output with Solidity-exact integer arithmetic
- Find optimal multi-hop swap routes using DFS graph traversal
- Analyze price impact across trade sizes and find max safe trade size
- Monitor Ethereum mempool for pending Uniswap swaps via WebSocket
- Simulate swaps on a local Anvil fork and verify against local math
- Full end-to-end integration test on Sepolia testnet
- Connect to Binance testnet, fetch live order books, and analyze market depth
- Simulate order fills and slippage across multiple price levels
- Track inventory across CEX (Binance) and DEX (wallet) venues
- Detect inventory skew and generate rebalancing plans with fee accounting
- Record per-trade PnL with gross/net breakdown and CSV export
- Detect CEX/DEX arbitrage opportunities and validate against real inventory
- Compare prices across two CEX exchanges (Binance vs Bybit) for cross-exchange arb
- Log every arb opportunity check to CSV with gap, costs, direction and executability

---

## Quick Start

**Requirements:** Python 3.12+, [Foundry](https://book.getfoundry.sh/getting-started/installation) (for fork simulation)

```bash
# 1. Clone
git clone https://github.com/IvanTaborkikh/trade
cd trade

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows

# 3. Install dependencies
make install

# 4. Set up git hooks
make pre-commit-install

# 5. Run tests
make test
```

### Environment setup
```bash
cp .env.example .env
```

Fill in `.env`:
```env
# Your Ethereum private key — NEVER commit this file!
PRIVATE_KEY=0x...

# Sepolia testnet RPC
RPC_URL=https://eth-sepolia.g.alchemy.com/v2/your_key

# Mainnet HTTP RPC (for fork simulation and transaction analyzer)
MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/your_key

# Mainnet WebSocket RPC (for mempool monitoring)
WS_RPC_URL=wss://eth-mainnet.g.alchemy.com/v2/your_key

# Chain ID: 11155111 = Sepolia, 1 = Mainnet
CHAIN_ID=11155111

# Binance testnet (Week 3)
BINANCE_TESTNET_API_KEY=your_binance_testnet_key
BINANCE_TESTNET_SECRET=your_binance_testnet_secret

# Bybit testnet — optional, for multi-exchange arb (Week 3)
BYBIT_TESTNET_API_KEY=your_bybit_testnet_key
BYBIT_TESTNET_SECRET=your_bybit_testnet_secret
```

> Get a free RPC endpoint at [alchemy.com](https://alchemy.com)
> Get Sepolia ETH at [https://cloud.google.com/application/web3/faucet/ethereum/sepolia](https://cloud.google.com/application/web3/faucet/ethereum/sepolia)

---

## Project Structure
```
src/
  core/                   # Week 1 — wallet, types, serialization
    wallet.py             # WalletManager
    serializer.py         # CanonicalSerializer
    types.py              # Address, TokenAmount, Token, TransactionRequest, TransactionReceipt
  chain/                  # Week 1 — blockchain interaction
    client.py             # ChainClient
    builder.py            # TransactionBuilder
    analyzer.py           # CLI transaction analyzer
    errors.py             # ChainError, RPCError, TransactionFailed, etc.
  pricing/                # Week 2 — AMM math, routing, simulation
    UniswapV2Pair.py      # AMM math (Solidity-exact integer arithmetic)
    Route.py              # Single swap route with gas estimation
    RouteFinder.py        # DFS multi-hop route discovery
    PriceImpactAnalyzer.py# Impact table, max trade size, true cost
    MempoolMonitor.py     # WebSocket pending tx parser (eth_abi)
    ForkSimulator.py      # Swap simulation via Anvil fork
    PricingEngine.py      # Unified pricing interface + Quote validation
    impact_analyzer.py    # CLI for price impact analysis
  exchange/               # Week 3 — CEX connectivity
    client.py             # ExchangeClient (ccxt/Binance, rate limiter, normalized responses)
    orderbook.py          # OrderBookAnalyzer (walk-the-book, depth, imbalance, effective spread)
    cex_pricer_adapter.py # CexPricingAdapter (wraps ExchangeClient as pricing_engine for CEX-vs-CEX arb)
  inventory/              # Week 3 — portfolio management
    tracker.py            # InventoryTracker (multi-venue balances, can_execute, skew)
    rebalancer.py         # RebalancePlanner (transfer plans, fee accounting, min balances)
    pnl.py                # PnLEngine (per-trade PnL, summary stats, CSV export)
  integration/            # Week 3 — end-to-end arb detection
    arb_checker.py        # ArbChecker (DEX price + CEX order book → opportunity assessment)
    arb_logger.py         # ArbLogger (append every check() result to CSV for analysis)
  strategy/               # Week 4 — coming soon
  executor/               # Week 4 — coming soon
  safety/                 # Week 5 — coming soon
scripts/
  integration_test.py     # Week 1 — End-to-end Sepolia test
  pricing_demo.py         # Week 2 — Pricing module demo (no network needed)
  test_fork_simulator.py  # Week 2 — Fork simulation verification
  test_mempool.py         # Week 2 — Live mempool monitoring
  start_fork.sh           # Start Anvil mainnet fork
  smoke_exchange.py       # Week 3 — Binance testnet: order book, balance, fees
  smoke_orderbook.py      # Week 3 — Formatted order book report with depth analysis
  smoke_tracker.py        # Week 3 — Inventory snapshot + skew analysis
  smoke_multi_exchange.py # Week 3 — Multi-exchange arb check (Bybit vs Binance)
tests/                    # 392 unit tests
.env                      # Secret config — never commit!
.env.example              # Safe template
.pre-commit-config.yaml   # ruff + detect-secrets hooks
Makefile
requirements.txt
```

---

## Week 1 — Core Infrastructure

### What was added

| Module | Description |
|--------|-------------|
| `core/wallet.py` | Secure key management, EIP-191 and EIP-712 signing |
| `core/serializer.py` | Deterministic JSON serialization with keccak256 hashing |
| `core/types.py` | Base types: `Address`, `TokenAmount`, `Token`, `TransactionRequest`, `TransactionReceipt` |
| `chain/client.py` | RPC client with retry logic and exponential backoff |
| `chain/builder.py` | Fluent transaction builder |
| `chain/analyzer.py` | CLI tool for analyzing any Ethereum transaction |
| `chain/errors.py` | Typed error hierarchy |
| `scripts/integration_test.py` | End-to-end test on Sepolia |

### Transaction Analyzer CLI
```bash
make analyze TX=0xTxHash...
make analyze TX=0xTxHash... RPC=https://eth-mainnet.g.alchemy.com/v2/your_key
```
```
Transaction Analysis
==================================================
Hash:           0xaf6e8e35...
Block:          20,012,198
Timestamp:      2024-06-03 15:29:59 UTC
Status:         SUCCESS

From:           0xb84c1F96...
To:             0x7a250d56...
Value:          0.004200 ETH

Gas Analysis
----------------------------------------
Gas Limit:      144,654
Gas Used:       119,074 (82.32%)
Effective Price:18.244923 gwei
Transaction Fee:0.002172 ETH

Function Called
----------------------------------------
Selector:       0x7ff36ab5
Function:       swapExactETHForTokens(uint256,address[],address,uint256)
Arguments:
  - amountOutMin: 15742253361321223426
  - path: ['0xC02aaA3...', '0x6B17547...']

Token Transfers
----------------------------------------
1. WETH: 0x7a250d... → 0xA478c2...  0.004200 WETH
2. DAI:  0xA478c2... → 0xb84c1F...  15.820965 DAI

Swap Summary
----------------------------------------
Sold:           0.004200 WETH
Received:       15.820965 DAI
Execution Price:3766.9 DAI/WETH
```

### Integration Test
```bash
make integration-test
make integration-test AMOUNT=0.00005 TO=0xAddress
```
```
==================================================
Integration Test — Sepolia Testnet
==================================================

1. Loading wallet
----------------------------------------
  Wallet: 0xB865196D16922b08c53e966019067d98e9D4A465
  ✓ Private key not exposed in repr

2. Connecting to Sepolia
----------------------------------------
  RPC: https://eth-sepolia.g.alchemy.com/v2/***
  Chain ID: 11155111

3. Checking balance
----------------------------------------
  Balance: 0.047000 ETH
  ✓ Balance > 0

4. Building transaction
----------------------------------------
  To:            0x000000000000000000000000000000000000dEaD
  Value:         0.000001 ETH
  Estimated Gas: 25200
  Gas cost:      0.00000055 ETH
  Total needed:  0.00000155 ETH
  ✓ Gas limit set
  ✓ Sufficient balance (need 0.00000155 ETH)

5. Signing transaction
----------------------------------------
  Signature: v=1, r=0x871fac4a...
  ✓ Signature valid
  ✓ Recovered address matches

6. Sending transaction
----------------------------------------
  TX Hash: 0xd6f6bff5...
  ✓ TX hash received

7. Waiting for confirmation
----------------------------------------
  Block:    10580879
  Status:   SUCCESS
  Gas Used: 21000
  Fee:      0.0000004603 ETH
  ✓ Transaction confirmed

==================================================
Integration test PASSED ✓
==================================================
```

---

## Week 2 — Pricing Module

### What was added

| Module | Description |
|--------|-------------|
| `pricing/UniswapV2Pair.py` | AMM math with Solidity-exact integer arithmetic, verified against real on-chain tx |
| `pricing/Route.py` | Swap route: ordered list of pools and tokens, configurable gas params |
| `pricing/RouteFinder.py` | Graph-based DFS route discovery, selects best route by net output after gas |
| `pricing/PriceImpactAnalyzer.py` | Impact table, binary search for max trade size, true cost with gas |
| `pricing/MempoolMonitor.py` | WebSocket subscription to pending txs, ABI decoding via `eth_abi` |
| `pricing/ForkSimulator.py` | Local Anvil fork simulation using `getAmountsOut` view call |
| `pricing/PricingEngine.py` | Unified interface: loads pools, gets quotes, reacts to mempool events |

### Pricing Demo (no network needed)
```bash
make pricing-demo
```
```
────────────────────────────────────────────────────────────
  1. AMM Math — UniswapV2Pair
────────────────────────────────────────────────────────────
Input:            1 WETH
Output:           1992.01 USDC
Spot price:       500000000.000000 WETH/USDC
Execution price:  502004513.560734 WETH/USDC
Price impact:     0.4009%

--- simulate_swap (reserves after trade) ---
Old reserves:  1000.0 WETH / 2,000,000 USDC
New reserves:  1001.00 WETH / 1,998,008 USDC

--- get_amount_in (reverse calc) ---
To get 2000 USDC need: 1.004013 WETH

────────────────────────────────────────────────────────────
  2. Price Impact Analysis
────────────────────────────────────────────────────────────
 Amount In (ETH)   Output (USDC)    Impact
--------------------------------------------
             0.1          199.38   0.3109%
             1.0        1,992.01   0.4009%
             5.0        9,920.55   0.8009%
            10.0       19,743.16   1.3009%
            50.0       94,965.95   5.3009%
           100.0      181,322.18  10.3009%

Max trade for <1% impact: 6.9910 WETH

True cost breakdown (1 ETH, 20 gwei):
  Gross output:   1992.01 USDC
  Gas cost (ETH): 0.003000 ETH
  Net output:     1986.01 USDC

────────────────────────────────────────────────────────────
  3. Route Finding — WETH → USDC
────────────────────────────────────────────────────────────
Found 2 route(s):
  [1] WETH → USDC           →  1992.0140 USDC  (gas est: 150,000)
  [2] WETH → DAI → USDC     →  1983.2748 USDC  (gas est: 250,000)

Route                          Gross     Gas est           Net
--------------------------------------------------------------
WETH → USDC                1992.0140     150,000     1986.0140
WETH → DAI → USDC          1983.2748     250,000     1973.2748

Best route: WETH → USDC  (net: 1986.0140 USDC)

────────────────────────────────────────────────────────────
  4. Mempool — Parse Swap Transaction
────────────────────────────────────────────────────────────
DEX:              UniswapV2
Method:           swapExactTokensForTokens
Token in:         0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
Token out:        0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
Amount in:        1000.00 USDC
Min amount out:   950.00 USDC
Slippage:         5.0%
Gas price:        20 gwei
```

### Price Impact CLI
```bash
make impact-analyzer
make impact-analyzer TOKEN_IN=WETH SIZES=1,5,10,50
```
```
Price Impact Analysis: WETH → USDC
Pair:        0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc
Reserves:    1,000.00 WETH / 2,000,000.00 USDC
Spot price:  2,000.0000 USDC/WETH

┌────────────────┬────────────────┬────────────────┬───────────┐
│        WETH In │       USDC Out │      USDC/WETH │    Impact │
├────────────────┼────────────────┼────────────────┼───────────┤
│         0.1000 │       199.3801 │       1,993.80 │     0.31% │
│         1.0000 │       1,992.01 │       1,992.01 │     0.40% │
│         5.0000 │       9,920.55 │       1,984.11 │     0.80% │
│        10.0000 │      19,743.16 │       1,974.32 │     1.30% │
│        50.0000 │      94,965.95 │       1,899.32 │     5.30% │
│       100.0000 │     181,322.18 │       1,813.22 │    10.30% │
└────────────────┴────────────────┴────────────────┴───────────┘

Max trade for 1% impact: 6.9910 WETH
```

### Fork Simulation
```bash
# Terminal 1 — start Anvil fork
make fork

# Terminal 2 — verify our math against real contracts
make test-fork
```
```
Fork Simulator Test
========================================

1. Connecting to fork...
   Connected to http://localhost:8545

2. Loading WETH/USDC pair from fork...
   Pair: 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc
   token0: USDC  reserve0: 131,447,219.188192
   token1: WETH  reserve1: 74491.511240

3. Fork simulation: 1.0000 WETH → 1753.3258 USDC

4. Compare
   Calculated:  1753325821
   Simulated:   1753325821
   Difference:  0
   Match:       YES ✓
```

### Live Mempool Monitoring
```bash
make test-mempool   # requires WS_RPC_URL in .env
```
```
Connecting to wss://eth-mainnet.g.alchemy.com/v2/...
Watching mempool for Uniswap V2/V3 swaps. Press Ctrl+C to stop.

[#1] UniswapV2 — swapExactETHForTokens
  tx:        0x47037c377c70c30ce8...
  sender:    0x225E8e1679...
  token_in:  0xC02aaA39...
  token_out: 0xe7cF04f4...
  amount_in: 450000000000000000
  slippage:  2.1%
  gas price: 18 gwei

[#2] UniswapV2 — swapExactTokensForTokens
  tx:        0x2fb7933c4f03d02697...
  sender:    0xAbbA7BeF6d...
  token_in:  0xA0b86991...
  token_out: 0xC02aaA39...
  amount_in: 5000000000
  slippage:  0.5%
  gas price: 20 gwei
```

---

## Make Commands

Run `make help` to see all available commands.

**Setup**

| Command | Description |
|---------|-------------|
| `make install` | Install all dependencies |
| `make pre-commit-install` | Wire up git hooks (ruff + detect-secrets) |

**Development**

| Command | Description |
|---------|-------------|
| `make test` | Run all 392 unit tests |
| `make lint` | Check code with ruff |
| `make lint-fix` | Auto-fix lint errors |
| `make format` | Auto-format code |
| `make clean` | Remove cache files |

**Blockchain**

| Command | Description |
|---------|-------------|
| `make analyze TX=0x...` | Analyze a transaction |
| `make integration-test` | End-to-end test on Sepolia |

**Exchange / Inventory (Week 3)**

| Command | Description |
|---------|-------------|
| `make smoke-exchange` | Fetch order book, balance and fees from Binance testnet |
| `make smoke-orderbook` | Formatted order book report `[PAIR=ETH/USDT DEPTH=20]` |
| `make smoke-tracker` | Inventory snapshot + skew analysis |
| `make arb-check` | Arb opportunity check `[PAIR=ETH/USDT SIZE=1]` |
| `make smoke-multi` | Multi-exchange arb: Bybit vs Binance `[PAIR=ETH/USDT SIZE=1]` |
| `make arb-log` | Show arb opportunity log `[N=20 FILE=arb_log.csv]` |
| `make rebalance-check` | Show inventory skew across venues |
| `make rebalance-plan` | Generate transfer plan `[ASSET=ETH]` |
| `make pnl-summary` | PnL summary (simulated trades) |
| `make pnl-recent` | Last N trades `[N=5]` |

**Pricing**

| Command | Description |
|---------|-------------|
| `make pricing-demo` | Run pricing module demo (no network needed) |
| `make impact-analyzer` | Show price impact table |
| `make fork` | Start Anvil mainnet fork on port 8545 |
| `make stop-fork` | Stop running Anvil process |
| `make test-fork` | Verify AMM math against fork (needs Anvil running) |
| `make test-mempool` | Watch live mempool for Uniswap swaps (needs `WS_RPC_URL`) |

---

## Security

**Private key protection**
- Key loaded only from environment variable — never hardcoded
- `WalletManager.__repr__` never exposes the private key
- Exceptions sanitized — key never appears in logs or error messages
- `detect-secrets` pre-commit hook blocks accidental commits of secrets

**Financial precision**
- All amounts use integer arithmetic or `Decimal` — never `float`
- `float`: `1.5 * 10**18 = 1499999999999999873` ❌
- `Decimal`: `Decimal("1.5") * 10**18 = 1500000000000000000` ✅

**Rules — never break these**
- Never commit `.env` to git
- Never log or print a private key
- Never use `float` for token amounts
- Never use a raw address string — always wrap in `Address()`

---

## Changelog

### Week 3 — Exchange, Inventory & Arb Detection
- `exchange/client.py`: ExchangeClient with rolling-window rate limiter, dynamic limit loading from `/exchangeInfo`, server-side weight sync via `X-MBX-USED-WEIGHT-1M` header
- `exchange/orderbook.py`: OrderBookAnalyzer — walk-the-book, depth, imbalance, effective spread
- `exchange/cex_pricer_adapter.py`: CexPricingAdapter — wraps any ExchangeClient as a pricing_engine for CEX-vs-CEX arb
- `inventory/tracker.py`: InventoryTracker — multi-venue balance tracking, `can_execute`, skew analysis
- `inventory/rebalancer.py`: RebalancePlanner — transfer plans with fee accounting and minimum operating balance guards
- `inventory/pnl.py`: PnLEngine — per-trade gross/net PnL, win rate, Sharpe estimate, CSV export
- `integration/arb_checker.py`: ArbChecker — full arb pipeline: DEX price → gap → costs → inventory → verdict
- `integration/arb_logger.py`: ArbLogger — appends every check() result to CSV for analysis
- Multi-exchange support: Bybit testnet via CexPricingAdapter + ArbChecker
- 392 unit tests passing

### Week 2 — Pricing Module
- `pricing/` module: UniswapV2Pair, Route, RouteFinder, PriceImpactAnalyzer, MempoolMonitor, ForkSimulator, PricingEngine
- AMM math verified against real on-chain transaction (block 12,000,001)
- Multi-hop routing via DFS with gas-aware route selection
- ABI decoding via `eth_abi` (replaces manual byte parsing)
- Gas cost properly converted to output token units via spot price
- Route gas parameters configurable via constructor
- 234 unit tests passing

### Week 1 — Core Infrastructure
- Project setup: pre-commit, ruff, detect-secrets, Makefile
- `core/` module: WalletManager, CanonicalSerializer, Address, TokenAmount, Token
- `chain/` module: ChainClient, TransactionBuilder, Transaction Analyzer CLI
- Integration test passing on Sepolia
- 153 unit tests passing
