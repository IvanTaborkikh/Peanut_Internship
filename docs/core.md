# Core Module

`src/core/` contains the foundational types and utilities used across the entire system.
No blockchain connection required — pure Python logic.

---

## `types.py` — Base Types

### `Address`

Ethereum address with automatic validation and checksumming.

```python
from src.core import Address

addr = Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b")

addr.checksum   # "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"  (EIP-55)
addr.lower      # "0xab5801a7d398351b8be11c439e05c5b3259aec9b"

# Comparison is case-insensitive
Address("0xABC...") == Address("0xabc...")  # True

# Invalid address raises immediately
Address("not_an_address")  # ValueError
```

---

### `TokenAmount`

Stores token amounts as raw integers (wei-equivalent) to avoid float precision errors.

```python
from src.core import TokenAmount
from decimal import Decimal

# From human-readable string
amount = TokenAmount.from_human("1.5", decimals=18, symbol="ETH")
amount.raw     # 1500000000000000000
amount.human   # Decimal("1.5")

# Float is explicitly forbidden
TokenAmount.from_human(1.5, 18)   # ValueError — use "1.5" or Decimal("1.5")

# Arithmetic
a = TokenAmount(raw=1000, decimals=18)
b = TokenAmount(raw=500, decimals=18)
(a + b).raw    # 1500
(a * 2).raw    # 2000
(a * Decimal("1.5")).raw  # 1500

# Adding different decimals raises
TokenAmount(raw=1000, decimals=18) + TokenAmount(raw=1000, decimals=6)  # ValueError
```

> **Why not float?**
> `1.5 * 10**18` with float = `1499999999999999872` ❌
> `Decimal("1.5") * 10**18` = `1500000000000000000` ✅

---

### `Token`

Represents an ERC-20 token. Identity is by address only — symbol and decimals are metadata.

```python
from src.core import Token, Address

usdc = Token(
    address=Address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
    symbol="USDC",
    decimals=6,
)

# Two tokens at the same address are equal regardless of symbol/decimals
t1 = Token(address=usdc.address, symbol="USDC", decimals=6)
t2 = Token(address=usdc.address, symbol="FAKE", decimals=18)
t1 == t2   # True — same address
```

---

### `TransactionRequest`

A transaction ready to be signed. Built by `TransactionBuilder` — rarely constructed directly.

```python
from src.core import TransactionRequest, Address, TokenAmount

tx = TransactionRequest(
    to=Address("0xRecipient..."),
    value=TokenAmount.from_human("0.1", 18, "ETH"),
    data=b"",
    nonce=5,
    gas_limit=21000,
    max_fee_per_gas=20_000_000_000,
    max_priority_fee=1_500_000_000,
    chain_id=11155111,
)

tx.to_dict()  # web3-compatible dict for signing
```

---

### `TransactionReceipt`

Parsed result after a transaction is confirmed.

```python
receipt.tx_hash           # "0xabc..."
receipt.block_number      # 1234567
receipt.status            # True = success, False = reverted
receipt.gas_used          # 21000
receipt.effective_gas_price  # wei
receipt.tx_fee            # TokenAmount — gas_used × effective_gas_price
receipt.logs              # list of raw log dicts
```

---

## `wallet.py` — WalletManager

Secure key management. Private key is **never** exposed in logs, repr, or exceptions.

```python
from src.core import WalletManager

# Load from environment variable
wallet = WalletManager.from_env("PRIVATE_KEY")

# Or directly (for testing only)
wallet = WalletManager("0xprivatekey...")

wallet.address   # "0xAb5801..." — checksummed
repr(wallet)     # "WalletManager(address=0xAb5801...)" — no key exposed
```

### Signing a message (EIP-191)

```python
signed = wallet.sign_message("hello world")
signed.signature   # bytes

# Verify
from eth_account import Account
from eth_account.messages import encode_defunct

msg = encode_defunct(text="hello world")
recovered = Account.recover_message(msg, signature=signed.signature)
recovered == wallet.address   # True
```

### Signing a transaction

```python
signed = wallet.sign_transaction(tx.to_dict())
signed.raw_transaction   # bytes — ready to broadcast
```

### Signing typed data (EIP-712)

Used by DeFi protocols (Uniswap Permit2, etc.):

```python
signed = wallet.sign_typed_data(domain, types, value)
```

### Generating a new wallet

```python
wallet, private_key = WalletManager.generate()
# private_key is returned to the caller — store it securely
# it is NOT printed to stdout
```

---

## `serializer.py` — CanonicalSerializer

Deterministic JSON serialization for hashing and signing structured data.

```python
from src.core import CanonicalSerializer

data = {"b": 2, "a": 1}

# Serialize — keys sorted, no whitespace
CanonicalSerializer.serialize(data)
# b'{"a":1,"b":2}'

# Hash — keccak256 of serialized bytes
CanonicalSerializer.hash(data)
# bytes (32)

# Verify determinism over 1000 iterations
CanonicalSerializer.verify_determinism(data, iterations=1000)
# True
```

**Rules:**
- Keys are sorted alphabetically at every nesting level
- No whitespace between keys/values
- `float` values raise `ValueError` — use `int` or `str`
- Unicode is preserved as-is (not escaped)