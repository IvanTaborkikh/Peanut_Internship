# Setup Guide

## Requirements

- Python 3.12+
- A free [Alchemy](https://alchemy.com) account for RPC endpoints
- Sepolia testnet ETH (for running the integration test)

> **Windows users:** The `Makefile` uses Unix commands (`find`, `test`, `python3`) that do not work natively on Windows CMD or PowerShell. Use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) (Windows Subsystem for Linux) to run all `make` commands. Alternatively, run the underlying Python commands directly without `make`.

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/IvanTaborkikh/trade
cd trade

# 2. Create virtual environment (required before make install)
python3 -m venv .venv        # macOS/Linux
python  -m venv .venv        # Windows

# 3. Install dependencies
make install

# 4. Install git hooks (ruff linter + detect-secrets)
make pre-commit-install
```

> All `make` commands use `.venv` automatically — no need to activate it manually.

---

## Environment Variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

`.env` contents:

```env
# Your Ethereum private key — NEVER commit this file!
PRIVATE_KEY=0x...

# Sepolia testnet RPC (get free key at alchemy.com)
RPC_URL=https://eth-sepolia.g.alchemy.com/v2/your_key

# Mainnet RPC (for the transaction analyzer)
MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/your_key

# Chain ID: 11155111 = Sepolia, 1 = Mainnet
CHAIN_ID=11155111
```

> Get Sepolia ETH at [Google Web3 Faucet](https://cloud.google.com/application/web3/faucet/ethereum/sepolia)

---

## Verify Setup

Run the unit tests to make sure everything is installed correctly:

```bash
make test
```

Expected output: `153 passed`.

---

## Run the Integration Test

Sends a real transaction on Sepolia testnet:

```bash
# Default: sends 0.000001 ETH to 0x...dEaD
make integration-test

# Custom amount
make integration-test AMOUNT=0.00005

# Custom recipient
make integration-test TO=0xYourAddress

# Both
make integration-test AMOUNT=0.00005 TO=0xYourAddress
```

Requires `PRIVATE_KEY` and `RPC_URL` to be set in `.env` and a small amount of Sepolia ETH in the wallet.

---

## Available Make Commands

Run `make help` to see all commands with descriptions.

| Command | What it does |
|---------|--------------|
| `make install` | Install dependencies |
| `make pre-commit-install` | Wire up git hooks |
| `make test` | Run all 153 unit tests |
| `make lint` | Check code with ruff |
| `make lint-fix` | Auto-fix lint errors |
| `make format` | Auto-format code |
| `make analyze TX=0x...` | Analyze a transaction |
| `make integration-test` | Run integration test on Sepolia |
| `make clean` | Remove cache files |

---

## Security Notes

- `.env` is listed in `.gitignore` — it will never be committed
- `.claudeignore` prevents AI tools from auto-reading secret files
- `detect-secrets` pre-commit hook blocks accidental commits of secrets
- Private key is never logged, printed, or exposed in `repr()`