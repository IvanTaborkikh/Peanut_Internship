## Day 5 — 2026-05-11

### Numbers
- Starting capital: ~$113.65 and ~0.0009707949858 ETH
- Ending capital: ~$105.45 (CHIP price correction + Day-5 trading + manual rebalance fees) and ~0,00096 ETH
- PnL: -\$0.17 realized trading (-\$0.04 on first arb + -\$0.13 on auto-unwound trade) | -$0.286 cross-venue rebalance fees)
- Trades: 2 attempted, 1 fully completed, 1 auto-unwound
- Win rate: 0% (both completed trades had small negative PnL — but DoD "1+ real arb" achieved)
- Best trade: -$0.04 (first successful arb at 07:44, BUY_DEX_SELL_CEX, spread 34.3 bps)
- Worst trade: -$0.13 (08:50 trade where DEX leg reverted, auto-unwind succeeded)
- Fees paid: 0.00002649 BNB + 0.2 USDC (CEX) + 0.0000037 ETH (DEX)

### What Happened
- 07:44:09 — **FIRST SUCCESSFUL ARBITRAGE ROUND-TRIP. DoD ACHIEVED.**
  - Spread 34.3 bps, BUY_DEX_SELL_CEX, size 200 CHIP
  - CEX leg DONE in 257 ms: sold 200 CHIP \$0,06098 = +\$12,196 USDC (Binance order id 29813752)
  - DEX leg CONFIRMED at block 461607657: bought 199.24 CHIP for \$12.16 USDC (effective price $0.06104)
  - Net PnL -$0.04 (matched bot's prediction exactly — `gas_cost_usd_default=0.03` calibration confirmed)
  - tx: `0x905dbbb60323ab54f9e22fb6c96fb900b53b7dfe3ca6d8aac5fbdc33d8df890a`
- 08:50:41 — **Bug #10 fix demonstrated in production**:
  - Spread 57.4 bps, BUY_DEX_SELL_CEX
  - CEX leg DONE: sold 200 CHIP  \$0.0599 = +12,10691 USDC
  - DEX leg REVERTED on-chain (tx broadcast then state changed before mining)
  - **AUTO-UNWIND triggered automatically**: bot bought 200 CHIP back via Binance market order in 1.8 seconds
  - Without yesterday's Bug #10 fix → would have left naked-short 200 CHIP requiring manual recovery (as on Days 3 and 4)
  - Net cost \$0.13 — slightly more than typical manual unwind ($0.10) but ZERO operator time

### Problems Encountered
- DEX leg revert recurred at 08:50 even with 100 bps slippage tolerance (Bug #8 fix). Same root cause: 1-2s broadcast lag on a thin pool with 99% LP concentration → price drift exceeds slippage cap. Could mitigate further with flashbot-style atomic execution but out of scope

### Changes Made
- No code changes today — Day-4 fixes held up under real load

### Lessons Learned
- Smaller spreads execute MORE reliably than bigger ones on thin pools — bigger spreads imply higher volatility, which means bigger price drift during 1-2s broadcast window → DEX leg more likely to revert with "Too little received"
- The auto-unwind code path (Bug #10 fix) is the most structurally important defense in the system. Saved 5+ minutes of manual recovery and eliminated the human-in-the-loop risk window. Worth more than any individual bug fix
- PnL prediction accuracy after gas correction (Day 4) was perfect: -\$0.04 expected, -$0.04 realized. Conservative defaults DO matter — wrong gas estimate doesn't just affect single trades, it skews which signals get accepted at all

### Tomorrow's Plan
- Project ends today — no "tomorrow" trading session
- Final deliverables to confirm:
  - All 5 day journals (this format)
  - `reports/final_report.md` — comprehensive 6-section writeup
  - `reports/advanced_strategy_analysis.md` — Day 3 perpetual funding rate arb analysis
  - `preflight_checklist.md` — Week 5 deliverable, signed
  - All `logs/bot_*.log` from Days 1-5 attached as evidence
- Demo afternoon: kill-switch demo, live bot walkthrough, 10-bug timeline, arbiscan tx as proof of successful arb
