# Arbitrage Trading System

Ethereum arbitrage trading system built during internship at **Peanut Trade**.
Designed for MEV, HFT, and on-chain arbitrage strategies.

---

## What it can do now

- Load Ethereum wallet from environment, sign messages and transactions
- Connect to any EVM-compatible RPC endpoint with automatic retry
- Build, sign, and send transactions to testnet or mainnet
- Analyze any Ethereum transaction: block info, gas analysis,
  token transfers, revert reasons; decodes common DeFi functions
  (ERC-20, Uniswap V2/V3) — unknown functions show raw selector
- Full end-to-end integration test on Sepolia testnet

---

## Quick Start

**Requirements:** Python 3.12+
```bash
# 1. Clone
git clone https://github.com/IvanTaborkikh/trade
cd trade

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux

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

# Sepolia testnet RPC (get free key at alchemy.com)
RPC_URL=https://eth-sepolia.g.alchemy.com/v2/your_key

# Mainnet RPC (for transaction analyzer)
MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/your_key

# Chain ID: 11155111 = Sepolia, 1 = Mainnet
CHAIN_ID=11155111
```

> Get a free RPC endpoint at [alchemy.com](https://alchemy.com)
> Get Sepolia ETH at [https://cloud.google.com/application/web3/faucet/ethereum/sepolia](https://cloud.google.com/application/web3/faucet/ethereum/sepolia)

---

## Project Structure
```
trade/
  core/                   # Week 1 — wallet, types, serialization
    wallet.py             # WalletManager
    serializer.py         # CanonicalSerializer
    types.py              # Address, TokenAmount, Token, TransactionRequest, TransactionReceipt
  chain/                  # Week 1 — blockchain interaction
    client.py             # ChainClient
    builder.py            # TransactionBuilder
    analyzer.py           # CLI transaction analyzer
    errors.py             # ChainError, RPCError, TransactionFailed, etc.
  pricing/                # Week 2 — coming soon
  exchange/               # Week 3 — coming soon
  inventory/              # Week 3 — coming soon
  strategy/               # Week 4 — coming soon
  executor/               # Week 4 — coming soon
  safety/                 # Week 5 — coming soon
  config/                 # Week 5 — coming soon
  scripts/
    integration_test.py   # Week 1 — End-to-end Sepolia test
  tests/                  # 110 unit tests
  .env                    # Secret config — never commit!
  .env.example            # Safe template
  .pre-commit-config.yaml # ruff + detect-secrets hooks
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
# Analyze any Sepolia transaction
python -m chain.analyzer 0xTxHash...

# Analyze any Mainnet transaction
python -m chain.analyzer 0xTxHash... --rpc https://eth-mainnet.g.alchemy.com/v2/your_key
```

Example output:
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
python scripts/integration_test.py
```

Output:
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
  RPC: https://eth-sepolia.g.alchemy.com/v2/...                                                                                                                           
                                                                                                                                                                          
3. Checking balance
----------------------------------------
  Balance: 0.047000 ETH
  ✓ Balance > 0
  ✓ Sufficient balance for test (> 0.002 ETH)

4. Building transaction
----------------------------------------
  To:            0x000000000000000000000000000000000000dEaD
  Value:         0.000001 ETH
  Estimated Gas: 25200
  Max Fee:       26005723 wei
  Max Priority:  1500000 wei
  ✓ Gas limit set
  ✓ Max fee set
  ✓ Recipient is correct

5. Signing transaction
----------------------------------------
  Signature: v=1, r=0x871fac4a...
  Recovered address: 0xB865196D16922b08c53e966019067d98e9D4A465
  ✓ Signature valid
  ✓ Recovered address matches

6. Sending transaction
----------------------------------------
  TX Hash: 0xd6f6bff5ade9afe41054ac6e4b060f290f4a584bb06c5661a15df6f39910cd7f
  ✓ TX hash received

7. Waiting for confirmation
----------------------------------------
  Waiting... (up to 120 seconds)
  Block:    10580879
  Status:   SUCCESS
  Gas Used: 21000
  Fee:      0.0000004603 ETH
  ✓ Transaction confirmed
  ✓ Gas used > 0

8. Analyzing transaction
----------------------------------------

Transaction Analysis
==================================================
Hash:           0xd6f6bff5ade9afe41054ac6e4b060f290f4a584bb06c5661a15df6f39910cd7f
Block:          10,580,879
Timestamp:      2026-04-03 10:17:36 UTC
Status:         SUCCESS

From:           0xB865196D16922b08c53e966019067d98e9D4A465
To:             0x000000000000000000000000000000000000dEaD
Value:          0.000001 ETH

Gas Analysis
----------------------------------------
Gas Limit:      25,200
Gas Used:       21,000 (83.33%)
Base Fee:       0.026006 gwei
Priority Fee:   0.001500 gwei
Effective Price:0.021918 gwei
Transaction Fee:0.000000460282641000 ETH

Function Called
----------------------------------------
Selector:       0x
Function:       ETH Transfer (no calldata)

==================================================
Integration test PASSED ✓
==================================================
```

---

## Make Commands

| Command | What it does |
|---------|--------------|
| `make install` | Install all dependencies |
| `make test` | Run all 110 tests |
| `make lint` | Lint with ruff |
| `make lint-fix` | Auto-fix lint errors |
| `make format` | Auto-format with ruff |
| `make pre-commit-install` | Wire up git hooks |
| `make clean` | Remove cache files |

---

## Security

**Private key protection**
- Key loaded only from environment variable — never hardcoded
- `WalletManager.__repr__` never exposes the private key
- Exceptions sanitized — key never appears in logs or error messages
- `detect-secrets` pre-commit hook blocks accidental commits of secrets

**Financial precision**
- All amounts use `Decimal` — never `float`
- `float`: `1.5 * 10**18 = 1499999999999999872` ❌
- `Decimal`: `Decimal("1.5") * 10**18 = 1500000000000000000` ✅

**Rules — never break these**
- Never commit `.env` to git
- Never log or print a private key
- Never use `float` for token amounts
- Never use a raw address string — always wrap in `Address()`

---


## Changelog

### Week 1 — Core Infrastructure
- Project setup: pre-commit, ruff, detect-secrets, Makefile
- `core/` module: WalletManager, CanonicalSerializer, Address, TokenAmount, Token
- `chain/` module: ChainClient, TransactionBuilder, Transaction Analyzer CLI
- Integration test passing on Sepolia
- 110 unit tests passing