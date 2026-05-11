## Day 3 — 2026-05-09

### Numbers
- Starting capital: ~\$113 (CHIP price +20%) and ~0.00097431 ETH
- Ending capital: ~\$113.80 (CHIP price stable; +$0.80 from accidental naked-short window) and ~0.00097431 ETH
- PnL: +$0.80 realized (1 incident — see Worst trade caveat) | unrealized stable
- Trades: 1 attempted, 0 fully completed (CEX leg filled, DEX leg reverted)
- Win rate: N/A (no clean round-trip)
- Best trade: +$0.80 — but this was LUCK from CHIP price drop during 14 hours naked-short window after Bug #6, NOT alpha. Statistically meaningless
- Worst trade: same incident — could have been -$1 if CHIP had moved against us
- Fees paid: 0.00000959 BNB (CEX) + 0 (DEX)

### What Happened
- Lecture: Beyond Spot Arbitrage — perpetual funding rate arb, prediction markets, liquid restaking
- Wrote 1-page advanced strategy analysis on perpetual funding rate arbitrage (`reports/advanced_strategy_analysis.md`) — ~70% of existing arb-bot codebase reusable for that strategy
- Bot continued running with Day-2 evening config (min_spread 30 bps, min_profit_usd -0.20, trade_size 130)
- 17:14:24 — first signal of the day (spread 44.3 bps): rejected with "Signal invalid". Investigation revealed Bug #4 (`_fetch_wallet_balances` always returned empty dict, so inventory check always saw 0 balance on wallet venue)
- 18:11:35 — next signal after Bug #4 fix: reached real Binance broadcast for the first time. Got `-2015 Invalid API-key, IP, or permissions for action` — Bug #5: API key only had Read permission, never enabled Spot trading
- 20:29:41 — first time bot reached actual broadcast on BOTH legs:
  - CEX leg DONE: sold 130 CHIP @ $0.0671 = +$8.72 USDC (order id 29644900)
  - DEX leg REVERTED: `STF` (Safe Transfer From) — Bug #6: bot tried to spend 130 USDC on Arbitrum, only had $30
  - Naked-short 130 CHIP for ~14 hours during which CHIP dropped 7%
  - Manual recovery: bought 130 CHIP back on Binance at $0.061 → kept the $0.80 price differential

### Problems Encountered
- Bug #4: `_fetch_wallet_balances()` was stubbed to return `{}` because `ChainClient` lacked `get_wallet_balances` method. InventoryTracker thus saw 0 USDC and 0 CHIP on wallet venue → `inventory_ok=False` for any direction needing wallet-side balance
- Bug #5: Binance API key created during Week 1 setup had only Read permission. Dry-run never needed write permission, so this stayed silent until first real broadcast attempt
- Bug #6: in `engine.py _execute_dex_leg`, `signal.size` is in BASE units (CHIP). For BUY_DEX_SELL_CEX, `token_in` becomes QUOTE (USDC), but the code naively used `size * 10^token_in.decimals` → 130 USDC instead of 8.71 USDC
- Bug #7 (discovered during Bug #6 post-mortem): `web3.send_raw_transaction` exception bubbled up to outer try/except as "Unexpected error" → bypassed `_unwind()` logic. That's why the naked short happened — the safety net wasn't connected
- The lucky +$0.80 outcome of Bug #6 incident was NOISE, not validation of any strategy. Capital was effectively gambled on a 14-hours unhedged short

### Changes Made
- `scripts/arb_bot.py`: rewrote `_fetch_wallet_balances()` to use `tx_builder.web3` — `eth.get_balance()` for ETH, then ERC20 `balanceOf()` for each token in configured pairs
- API key: enabled Spot Trading on Binance, kept Withdraw disabled. Verified via standalone CCXT test order (`-1013 NOTIONAL` confirmed permission OK)
- `src/executor/engine.py _execute_dex_leg`: direction-symmetric amount calc:
  - `BUY_CEX_SELL_DEX`: `amount_in = size`, `amount_out_min = size × dex_price × slip`
  - `BUY_DEX_SELL_CEX`: `amount_in = size × dex_price`, `amount_out_min = size × slip`
- `src/executor/engine.py _execute_cex_leg`: wrapped `create_limit_ioc_order` in try/except returning `success=False` so executor's existing unwind logic triggers on broker exceptions
- New: `tests/test_engine_dex_amounts.py` — 2 tests (one per direction) verifying correct wei amounts

### Lessons Learned
- Onion of dormant bugs: 4 of 4 today's bugs were invisible until the previous one was fixed. Live testing at small notional is the only way to surface them
- API permission requirements should be POSITIVELY VERIFIED in preflight — `exchange.create_order(...)` with a tiny test, not just claimed
- Direction-symmetric code requires direction-symmetric tests. Forking by direction in calc-heavy code without matched test coverage → Bug #6
- Lucky outcomes are dangerous lessons. The +$0.80 doesn't validate the system; it just delays the next failure

### Tomorrow's Plan
- Day 4 limit jump to $15/trade
- Scale `trade_size` to 200 CHIP (~$13 at current price), `min_profit_usd` to -0.15
- Watch for any unwrapped exception path that could re-trigger Bug #6-class incident
- Consider adding pre-trade balance verification before broadcast (extra paranoia)
