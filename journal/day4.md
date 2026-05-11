## Day 4 — 2026-05-10

### Numbers
- Starting capital: ~$113.80 and ~0.00097431 ETH
- Ending capital: ~$113.65 (CHIP price drift + Day-4 incident) and ~0,0009707949858 ETH
- PnL: -$0.13 realized (Bug #8 incident manual rebalance + smoke test costs)
- Trades: 2 attempted, 0 fully completed
  - 12:22: signal generated, CEX submission timed out 42s (network), no order placed
  - 21:47: CEX leg filled, DEX leg reverted (Bug #8) → manual recovery
- Win rate: 0% (no clean round-trip)
- Best trade: N/A (none completed)+$0.006 (Bug #8 incident: bought 200 CHIP \$0.06441 / sold 200 CHIP \$0.0644)
- Worst trade: N/A (none completed)
- Fees paid: 0,00003784 BNB (CEX) + 0.000003515014188 ETH

### What Happened
- Switched to Day-4 settings (max_trade_usd: 10 → 15, max_loss_per_trade: 3 → 4, max_daily_loss: 12 → 15, trade_size: 130 → 200 CHIP)
- 12:22:44 — signal at spread 86.1 bps (huge opportunity, expected_pnl +$0.56). CEX order request to Binance timed out after 42 seconds. Verified via `fetch_my_trades` that no order was actually placed. Bug #9: CCXT default timeout too long for our 5s signal TTL
- 21:29:47 — V3 quoter returned anomalous quote (`dex_sell=$0.0677, dex_buy=$0.0647`, internal spread 467 bps within one response). PreTradeValidator caught it with "spread 552 bps > 500 bps (likely bad data)". Defensive validator earned its keep
- 21:47:21 — second naked-position incident (Bug #8):
  - Spread 47.2 bps, BUY_CEX_SELL_DEX direction
  - CEX leg DONE: bought 200 CHIP on Binance for $12.95 USDC
  - DEX leg REVERTED: "Too little received" (output below `amount_out_min` set with 50 bps slippage)
  - Caught as "Unexpected error" by outer try/except (Bug #7 fix didn't cover this path because the exception came from `build_dex_swap` not from `send_raw_transaction`)
  - Manual recovery: sold 200 CHIP back on Binance market at $0.0644 → +$0.10 net
- During Bug #8 audit found Bug #10: `_unwind_cex` and `_unwind_dex` raised `NotImplementedError` for live mode. This was the SILENT KILLER — every prior naked-position incident actually could have auto-recovered if unwind worked, but it was disabled in code from Week 4
- Built `scripts/smoke_dex_round_trip.py` to verify entire DEX path against real chain without waiting for arb signal. Real swap sold 5 CHIP for $0.322 USDC:
  - Gas used 175,698 at 20 gwei = **$0.012** (8× cheaper than my $0.10 estimate)
  - Slippage vs CEX mid: **+9.5 bps** (10× within 100 bps tolerance)
  - Effective fill: $0.06445/CHIP
  - tx: `0x1f4376535d4602d83fd0407237cfc677eca21cbfa13f5bf6906506bacb413b03`

### Problems Encountered
- Bug #8: 50 bps slippage tolerance too tight for CHIP V3 pool — 99% LP concentration means each swap moves price 5-15 bps, plus 1-2s gap between V3 quote observation and broadcast often shifts price further
- Bug #7's safety net wrapped `web3.send_raw_transaction` but NOT `tx_builder.build_dex_swap()`. web3.py runs `eth_call` simulation during `build_transaction` for gas estimation; if the swap would revert, it raises THERE — bypassing my safety net
- Bug #9: CCXT default timeout for create_order is 30-60s. With our 5s signal TTL, a 42s wait wastes the entire opportunity window
- Bug #10: discovered during defensive audit (`grep NotImplementedError src/executor/`). Both `_unwind_cex` and `_unwind_dex` raised exceptions in live mode, meaning every previous "leg2 failure → unwind" attempt would have actually thrown and ended in `UNWIND_FAILED` state. We just got lucky none of the prior incidents ran the unwind path

### Changes Made
- `src/executor/engine.py _execute_dex_leg`: wrapped `tx_builder.build_dex_swap()` in try/except returning `success=False` so unwind logic catches the simulation revert
- `configs/chip_observe.yaml`: `slippage_bps: 50 → 100` (10× safety margin per smoke test data); `gas_cost_usd_default: 0.10 → 0.03` (matches real measured gas); `trade_size: 130 → 200`; Day-4 risk limits
- `src/exchange/client.py`: explicit `'timeout': 8000` ms for ccxt.binance constructor
- `src/executor/unwind.py`: implemented real broadcast for both `_unwind_cex` (`exchange.create_market_order`) and `_unwind_dex` (`web3.send_raw_transaction` + receipt poll). Both wrapped in try/except returning `UnwindResult(success=False, ...)`
- New: `scripts/smoke_dex_round_trip.py` (end-to-end DEX swap verification with `--dry` flag for build-only mode)

### Lessons Learned
- Safety net needs to wrap EVERY level of the call stack. Bug #8 slipped past Bug #7's fix because `build_transaction` runs an `eth_call` BEFORE the explicit broadcast. For any single-point-of-failure, audit every call in that critical section
- Real measurements correct estimates by 5-10× (gas 8× cheaper, slippage 10× cushion). Conservative defaults were rejecting profitable signals as "too negative". One $0.012 smoke test invalidated weeks of cost assumptions
- Defensive code review (`grep NotImplementedError` in hot paths) takes 5 minutes and prevented at least one more naked-position incident. Highest leverage activity of the week
- The 500 bps cap on PreTradeValidator looked paranoid in code review; in production, it caught a phantom $0.56 profit signal that would have actually lost ~$0.10 on real fills

### Tomorrow's Plan
- Day 5 trading + demo
- Bot continues with corrected gas_cost ($0.03), slippage 100 bps, all 10 fixes active
- Target: 1+ successful arb round-trip (DoD)
- Manual rebalance any inventory skew before demo
- Draft `reports/final_report.md`
- Demo prep: kill-switch demo, walkthrough of trade flow, 10-bug timeline as evidence of disciplined live debugging
