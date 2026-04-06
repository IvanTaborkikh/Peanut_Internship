# Chain Module

`src/chain/` contains everything related to Ethereum blockchain interaction:
connecting to RPC, building and sending transactions, and analyzing them.

---

## `client.py` — ChainClient

RPC client with automatic retry, exponential backoff, and fallback to multiple endpoints.

```python
from src.chain import ChainClient

client = ChainClient(
    rpc_urls=["https://eth-sepolia.g.alchemy.com/v2/your_key"],
    timeout=30,       # seconds per request
    max_retries=3,    # attempts before raising
)
```

### Multiple RPC endpoints (fallback)

If the first RPC fails, the client automatically switches to the next one:

```python
client = ChainClient(rpc_urls=[
    "https://primary-rpc.com/...",
    "https://backup-rpc.com/...",
])
```

### Methods

```python
from src.core import Address, TokenAmount

# Get ETH balance
balance = client.get_balance(Address("0xYour..."))
balance.human   # Decimal("0.047")
balance.raw     # 47000000000000000

# Get nonce
nonce = client.get_nonce(Address("0xYour..."))  # pending nonce

# Get current gas prices
gas = client.get_gas_price()
gas.base_fee             # wei
gas.priority_fee_low     # wei
gas.priority_fee_medium  # wei
gas.priority_fee_high    # wei
gas.get_max_fee("medium", buffer=1.2)  # maxFeePerGas with 20% buffer

# Estimate gas for a transaction
gas_limit = client.estimate_gas(tx_request)

# Send signed transaction — returns tx hash immediately
tx_hash = client.send_transaction(signed.raw_transaction)

# Wait for confirmation (raises TimeoutError after `timeout` seconds)
receipt = client.wait_for_receipt(tx_hash, timeout=120)

# Get transaction data
tx_data = client.get_transaction(tx_hash)

# Get receipt if available (None if pending)
receipt = client.get_receipt(tx_hash)

# Simulate transaction without sending (eth_call)
result = client.call(tx_request)
```

### Error handling

Non-retryable errors are raised immediately without wasting retry attempts:

| Exception | When raised |
|-----------|-------------|
| `InsufficientFunds` | Not enough ETH for value + gas |
| `NonceTooLow` | Nonce already used |
| `ReplacementUnderpriced` | Replacement tx gas too low |
| `RPCError` | General RPC failure after all retries |
| `TimeoutError` | Transaction not confirmed within timeout |

---

## `builder.py` — TransactionBuilder

Fluent builder for constructing, signing, and sending transactions.

```python
from src.chain import TransactionBuilder

tx_hash = (
    TransactionBuilder(client, wallet)
    .to(Address("0xRecipient..."))
    .value(TokenAmount.from_human("0.1", 18, "ETH"))
    .data(b"")                    # optional calldata (default: empty)
    .chain_id(11155111)           # Sepolia; default is 1 (mainnet)
    .with_gas_estimate(buffer=1.2)  # estimate + 20% buffer
    .with_gas_price("medium")     # "low" | "medium" | "high"
    .send()                       # returns tx hash
)
```

### Contract calls (value = 0)

For contract interactions where no ETH is sent, `value` is optional (defaults to 0):

```python
tx_hash = (
    TransactionBuilder(client, wallet)
    .to(Address("0xContract..."))
    .data(calldata)
    .chain_id(1)
    .with_gas_estimate()
    .with_gas_price("high")
    .send()
)
```

### Build stages

| Method | Description |
|--------|-------------|
| `.to(address)` | Set recipient |
| `.value(amount)` | Set ETH value (default: 0) |
| `.data(bytes)` | Set calldata (default: `b""`) |
| `.nonce(n)` | Override nonce (default: fetched from RPC) |
| `.chain_id(n)` | Set chain ID (default: 1) |
| `.gas_limit(n)` | Set gas limit manually |
| `.with_gas_estimate(buffer)` | Estimate gas via RPC + buffer multiplier |
| `.with_gas_price(priority)` | Set EIP-1559 fees from current network |
| `.build()` | Validate and return `TransactionRequest` |
| `.build_and_sign()` | Build + sign, return `SignedTransaction` |
| `.send()` | Build + sign + send, return tx hash |
| `.send_and_wait(timeout)` | Build + sign + send + wait for receipt |

### Balance check

`build()` automatically checks that the wallet has enough ETH to cover `value + gas`:

```
InsufficientFunds: Balance 0.04697 ETH < required 10.00000 ETH
(value=10.00000000 ETH, gas=0.00000005 ETH)
```

### `send_and_wait` raises on revert

```python
from src.chain.errors import TransactionFailed

try:
    receipt = builder.send_and_wait(timeout=120)
except TransactionFailed as e:
    print(e.tx_hash)    # "0xabc..."
    print(e.receipt)    # TransactionReceipt with status=False
```

---

## `errors.py` — Error Hierarchy

All errors inherit from `ChainError`, so you can catch them all at once:

```python
from src.chain.errors import ChainError

try:
    receipt = builder.send_and_wait()
except ChainError as e:
    print(f"Chain error: {e}")
```

Or catch specific ones:

```python
from src.chain.errors import InsufficientFunds, NonceTooLow, TransactionFailed

try:
    builder.send_and_wait()
except InsufficientFunds:
    print("Not enough ETH")
except NonceTooLow:
    print("Nonce conflict — fetch a fresh nonce")
except TransactionFailed as e:
    print(f"Reverted: {e.tx_hash}")
```

---

## `analyzer.py` — Transaction Analyzer CLI

Analyzes any Ethereum transaction: block info, gas breakdown, token transfers,
decoded function calls, and revert reasons.

### Via Make

```bash
# Uses RPC_URL from .env
make analyze TX=0xabc123...

# With explicit RPC (e.g. mainnet)
make analyze TX=0xabc123... RPC=https://eth-mainnet.g.alchemy.com/v2/your_key
```

### Via Python

```bash
python -m src.chain.analyzer 0xabc123...
python -m src.chain.analyzer 0xabc123... --rpc https://eth-mainnet.g.alchemy.com/v2/your_key
```

### Example output

```
Transaction Analysis
==================================================
Hash:           0xd6f6bff5...
Block:          10,580,879
Timestamp:      2026-04-03 10:17:36 UTC
Status:         SUCCESS

From:           0xB865196D...
To:             0x000000000000000000000000000000000000dEaD
Value:          0.000001 ETH

Gas Analysis
----------------------------------------
Gas Limit:      25,200
Gas Used:       21,000 (83.33%)
Base Fee:       0.026006 gwei
Priority Fee:   0.001500 gwei
Effective Price:0.021918 gwei
Transaction Fee:0.000000460282641000 ETH (0.001234 USD)

Function Called
----------------------------------------
Selector:       0x
Function:       ETH Transfer (no calldata)
```

For DeFi transactions it also shows:

```
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

For reverted transactions it shows the revert reason:

```
Revert Info
----------------------------------------
Revert reason:  Return amount is not enough
```

### Supported function decoding

| Selector | Function |
|----------|----------|
| `0xa9059cbb` | ERC-20 `transfer` |
| `0x095ea7b3` | ERC-20 `approve` |
| `0x23b872dd` | ERC-20 `transferFrom` |
| `0x38ed1739` | Uniswap V2 `swapExactTokensForTokens` |
| `0x7ff36ab5` | Uniswap V2 `swapExactETHForTokens` |
| `0x18cbafe5` | Uniswap V2 `swapExactTokensForETH` |
| `0x04e45aaf` | Uniswap V3 `exactInputSingle` |
| `0xb858183f` | Uniswap V3 `exactInput` |
| `0xac9650d8` | Uniswap V3 `multicall` |

Unknown selectors show the raw hex selector and calldata prefix.