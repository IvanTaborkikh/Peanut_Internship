## Day 1 — 2026-05-07

### Numbers
- Starting capital: $100 (100 USDT instructor-funded) + 0,00100431 ETH
- Ending capital: ~$99.8 and ~0.00099431 ETH
- PnL: -$0.20 (funding-flow fees only, no trading)
- Trades: 0 (0 wins, 0 losses)
- Win rate: N/A
- Best trade: N/A
- Worst trade: N/A
- Fees paid: $0.2 (CEX) + 0.00001ETH (DEX gas)

### What Happened
- Pre-launch code audit: found 3 critical blockers preventing live trading
  - Bug #1: `tx_builder.py` built swaps via Uniswap V2 router; CHIP/USDC only exists on V3 → first real trade would revert
  - Bug #2: `arb_bot.py:202` hardcoded `'simulation': True` in `from_config()` → TxBuilder never wired to Executor
  - Bug #3 (initial form): `engine.py` raised `NotImplementedError` for both legs when `dry_run=False`
- Funding flow executed manually:
  - Trust Wallet 100 USDT (BSC) → Binance via BEP20
  - Binance Convert USDT → USDC, then market-bought $25 worth CHIP (~452 CHIP at $0.055)
  - Generated NEW Arbitrum wallet via `eth_account.Account.create()` (address `0x84b7346c...`), imported as separate "ArbBot" entry in Trust Wallet
  - Withdrew 50 USDC + 0.0008 ETH from Binance to ArbBot via Arbitrum One
  - Swapped 25 USDC → 444 CHIP on Uniswap V3 UI (used MetaMask because Trust Wallet's DApp browser failed with WalletConnect)
  - Ran `scripts/grant_v3_allowances.py` to approve USDC + CHIP for SwapRouter02 (gas $0.005)
- First live launch at 23:50 via `make dry-run-chip`, ran 30 min until SIGTERM
- Observed: 775 SPREAD samples, max real spread ±10 bps — 0 signals at min_spread=130 threshold

### Problems Encountered
- Trust Wallet DApp browser threw "No accounts available" on Uniswap connect → switched to MetaMask desktop
- WalletConnect session glitches when toggling between imported wallets — confirmed Trust Wallet UX bug, not our code
- Realized that 30-min dry-run produced 0 trades because real CHIP/USDC market is ~10× more efficient between Binance and Uniswap than the 130 bps threshold assumed

### Changes Made
- `src/configs/tokens.py`: added `UNISWAP_V3_ROUTERS` mapping + `get_v3_router()` helper
- `src/executor/tx_builder.py`: added `UNISWAP_V3_ROUTER_ABI` (`exactInputSingle`), parameterized constructor by `dex_version` + `fee_tier`, branched `build_dex_swap` on version
- `src/executor/engine.py`: replaced `NotImplementedError` with real broadcast paths for both CEX (`create_limit_ioc_order`) and DEX (`web3.send_raw_transaction` + receipt poll)
- `scripts/arb_bot.py`: instantiated `Web3 + LocalAccount + TxBuilder` from `PRIVATE_KEY` env, passed `tx_builder` and `chain_id` to Executor, removed hardcoded `simulation=True`
- `configs/chip_observe.yaml`: corrected misleading USDT→USDC comments, set `dry_run: false`, `trade_size: 88` (~$4.93 under Day 1 $5 limit)
- New: `scripts/grant_v3_allowances.py` (one-shot ERC20 approve)
- New: `scripts/check_balances.py` (pre-flight inventory dump)
- New: `tests/test_tx_builder_v3.py` (3 unit tests for V3 calldata)

### Lessons Learned
- Dry-run mode was hiding 3 production-only code paths — should have run a forked-mainnet test in Week 4-5 to surface these
- Generating a fresh Arbitrum wallet for the bot (rather than exporting Trust Wallet seed) limits blast radius if `.env` leaks — only $50 at risk, not personal holdings

### Tomorrow's Plan
- Scale `trade_size` to 175 CHIP (~$10) per Day 2 rule
- Lower `min_spread_bps` to 80 (gas-share drops to 50 bps at $10)
- Run unbounded for 6-10 hours, target 1+ real trade
- Add manual inventory rebalance if CHIP price moves significantly
