# Week 6 Final Report — Live Arbitrage Trading

**Author**: Ivan Taborskikh
**Period**: 2026-05-07 to 2026-05-11
**Pair**: CHIP/USDC (Binance Spot ↔ Uniswap V3 Arbitrum)
**Capital**: $100 instructor-funded → ~\$105.45 ending
**DoD**: 1+ successful real arbitrage round-trip — **achieved 2026-05-11 07:44**

---

## 1. Executive Summary

Took a 5-week-old arbitrage bot from "passes dry-run, never broadcasts" to
"executes live trades and auto-recovers from leg failures" over 5 days of
live operation with $100 real capital.

**Headline numbers**:
- 10 distinct bugs found and fixed (all but 4 invisible in dry-run)
- 1 fully completed real arbitrage cycles end-to-end
- 1 leg-2 failure recovered automatically by auto-unwind
- 2 leg-2 failures recovered manually (before auto-unwind was implemented)
- Realized trading PnL: **−$0.27 over 3 genuine arb attempts** (Day 3 +$0.80 excluded — Bug #6 naked-short with lucky CHIP drop, not strategy PnL; well within `max_daily_loss=$15`)
- Capital ended at ~\$105.45 (+$5.45 from start, mostly from CHIP price appreciation, partially from realized losses)
- 0 production incidents that bypassed risk limits
- 0 funds lost to bugs (manual recovery available in all cases)

**Honest framing**: 5 unrealized USD of profit is largely luck (CHIP pumped
20% mid-week). The arbitrage system itself broke even-to-slightly-down in
realized terms. The value delivered isn't dollars — it's a hardened code
path, a thorough understanding of why each piece can fail in production, and
a documented playbook for live-debugging financial software.

---

## 2. System Architecture & Setup

### Venues
- **CEX**: Binance Spot (`CHIP/USDC` market, 10 bps taker fee, Spot+Margin permission, no withdraw)
- **DEX**: Uniswap V3 on Arbitrum One (`CHIP/USDC` 0.01% pool, $1.1M TVL, single dominant LP at 99%)

### Capital allocation (after funding)
| Venue | USDC   | CHIP   | ETH           | USD value |
|---|--------|--------|---------------|-----------|
| Binance Spot | 30.438 | 350    | —             | $51.93    |
| Arbitrum wallet | 30,362    | 390.77 | 0.00096  | 54.32     |
| Trust Wallet (cold) | —      | —      | residual BNB  | <$1       |

### Wallet design choice
Generated a fresh Arbitrum wallet (`0x84b7346c...`) specifically for the bot
rather than exporting the user's main Trust Wallet seed. Rationale: blast
radius — if `.env` leaks, only the bot's $50 is at risk, not personal
holdings. Bot wallet was imported into Trust Wallet UI as a separate entry
for visibility.

### Code wiring (bot side)
- `scripts/arb_bot.py` instantiates `Web3 + LocalAccount + TxBuilder` from `PRIVATE_KEY` env var
- `TxBuilder` auto-selects V3 SwapRouter02 or V2 router based on `dex.version` config
- `Executor` receives `tx_builder` and `chain_id`, routes through builder paths instead of legacy `simulation_mode`
- `RiskManager + safety_check + AutoKillSwitch` run BEFORE every CEX/DEX broadcast
- Telegram notifier subscribes to both `SignalGeneratedEvent` and `ExecutionDoneEvent`

---

## 3. Trading Results

### Real trades executed across the week

| Time (UTC) | Direction | Spread | Size | Outcome | Net PnL |
|---|---|---|---|---|---|
| Day 3, 20:29 | BUY_DEX_SELL_CEX | 30.8 bps | 130 CHIP | CEX leg filled, DEX revert (Bug #6), manual recovery | **+$0.80** (lucky CHIP drop — bug, not arb) |
| Day 4, 21:47 | BUY_CEX_SELL_DEX | 47.2 bps | 200 CHIP | CEX leg filled, DEX revert (Bug #8), manual recovery | **−$0.10** |
| Day 5, 07:44 | BUY_DEX_SELL_CEX | 34.3 bps | 200 CHIP | **Full success — round-trip complete** | **−$0.04** |
| Day 5, 08:50 | BUY_DEX_SELL_CEX | 57.4 bps | 200 CHIP | CEX leg filled, DEX revert, **auto-unwind succeeded** | **−$0.13** |

**Total realized arb PnL: −$0.27 across 3 genuine arb attempts** (Day 4: −$0.10, Day 5: −$0.04 + −$0.13). Day 3's +$0.80 is excluded — it came from Bug #6 creating an accidental naked-short that happened to profit from a 7% CHIP drop, not from the arbitrage strategy.
Every arb attempt generated a small loss because CHIP pool's small size means slippage during the 1-2s broadcast lag often exceeds the captured spread.

### Capital trajectory
- Day 1 close: ~$99.8 and ~0.00099431 ETH
- Day 2 close: ~\$113 (CHIP price +20%) and 0.00097431 ETH
- Day 3 close: ~\$113.80 (CHIP price stable; +$0.80 from accidental naked-short window) and ~0.00097431 ETH
- Day 4 close:  ~$113.65 (CHIP price drift + Day-4 incident) and ~0,0009707949858 ETH
- Day 5 close: ~$105.45 (CHIP price correction + Day-5 trading + manual rebalance fees) and ~0,00096 ETH

The variance is dominated by CHIP price (a volatile small-cap), not by trading
performance. Holding the same composition without ever trading would have
produced essentially the same outcome.

### Why we didn't hit "3+ trades" stretch
Live arbitrage on CHIP/USDC exposed a structural limit: real spread
opportunities ≥30 bps appear ~3-5 times per day, but our DEX-side reverts
on volatile spreads (50+ bps) because pool depth is shallow. Of the 4
attempts, only the smallest spread (Day 5 07:44, 34 bps) executed without
DEX revert. Counter-intuitively, **smaller spreads are MORE likely to
complete** because price moves less during the broadcast window.

---

## 4. Risk Management in Practice

### Risk controls that fired

| Control | Trigger                                                    | Outcome |
|---|------------------------------------------------------------|---|
| `RiskManager.check_pre_trade` (max_trade_usd) | Day 2 21:01: \$10.60 trade > $10 limit                     | Blocked. Reduced trade_size next iteration. |
| `Signal.is_valid` (expiry, inventory, score) | Day 3 17:14: inventory check returned `inventory_ok=False` | Caught Bug #4 (wallet balances stub). |
| `PreTradeValidator` (max-spread cap 500 bps) | Day 4 21:29: V3 quoter glitched to 552 bps                 | Blocked bad-data trade (would have lost ~$0.10). |
| `slippage_bps` constraint in DEX swap | Multiple times                                             | Forced revert when amount_out_min not met (DEX only). |
| Auto-unwind on leg2 failure | Day 5 08:50                                                | Recovered naked CEX position in 1.8 seconds. |
| `max_daily_loss` ($15 limit) | Never approached                                           | Realized loss across 4 days = $0.30 (50× headroom). |
| `ABSOLUTE_MIN_CAPITAL` ($50 floor) | Never approached                                           | Capital lowest = ~$105.45 (well above floor). |

### Incidents

**Three production incidents** where bot held an unintended directional
position. All recovered with capital intact:

1. **Day 3 20:29 — Bug #6 (amount_in calc error)**: Bot sold 130 CHIP on
   Binance, DEX leg STF'd because tried to spend 130 USDC instead of 8.71.
   Naked-short for ~14 hours during which CHIP dropped 7%, netting +$0.80.
   Manual recovery: bought 130 CHIP back on Binance. **Lucky outcome — would
   normally lose \$0.10.**

2. **Day 4 21:47 — Bug #8 (slippage too tight)**: Bot bought 200 CHIP on
   Binance, DEX revert with "Too little received" because real pool drift
   (1s) exceeded 50 bps tolerance. Naked-long. Manual recovery cost $0.10.

3. **Day 5 08:50 — Bug #10 fix demo (DEX revert)**: Bot sold 200 CHIP on
   Binance, DEX leg reverted, **auto-unwind triggered automatically** and
   bought CHIP back via Binance market order in 1.8 seconds. Net cost $0.13.
   No manual intervention.

The pattern: **same class of failure (DEX leg revert) but progressively
better recovery as fixes accumulated**. By Day 5 the failure mode was
internalized into the system as a routine recoverable event.

### Kill switch

Implemented two layers:
- **Manual file-based**: `touch /tmp/arb_bot_kill` → bot exits next tick (≤5s)
- **Auto programmatic**: capital < 50% initial OR >50 errors/hour → auto-trip

Tested kill switch interactively during the week multiple times (e.g., before
each manual rebalance). Worked reliably; bot logged "kill switch active"
and exited cleanly within 5 seconds.

### What I would do differently

- **Run forked-mainnet integration tests in Week 5** (before live). Would
  have caught Bugs #6 and #10 without losing real money. Was deferred as
  out-of-scope; in retrospect, ~3 hours of fork-test setup is cheaper than
  $0.20 in incident losses + 6 hours of live debugging.
- **Wider safety nets at every layer from the start**. The Bug #7 (web3
  exception) and Bug #10 (NotImplementedError in unwind) were both
  "deferred concerns" from earlier weeks that became blocking on Day 3-4.
- **Single canonical filter chain** for signal acceptance. The duplicate
  filter logic in signal generator (`min_profit_usd`) and executor
  (`is_valid`) caused Bug #3 — different thresholds in two places, the
  stricter one silently won. Should be refactored to one filter.

---

## 5. Bug Hunt Timeline

10 distinct bugs found and fixed across 5 days. **6 of 10 were invisible in
dry-run** — they only manifested when real broadcast was attempted.

| # | Day | Bug | How it manifested | Fix |
|---|---|---|---|---|
| 1 | Pre-launch | V2 router used for V3 pool | Code review (`grep router`) | Added V3 SwapRouter02 + `exactInputSingle` ABI |
| 2 | Pre-launch | `simulation: True` hardcoded in `from_config` | Code review | Auto-disable when TxBuilder wired |
| 3 | Day 2 evening | `Signal.is_valid` had hardcoded `pnl > 0` | Live: signal at -$0.10 rejected | Parameterized with `min_pnl` from config |
| 4 | Day 3 17:14 | `_fetch_wallet_balances` returned `{}` | Live: inventory check always False | Wired to `web3.balanceOf` for ERC20s |
| 5 | Day 3 18:11 | Binance API key missing Spot trading permission | Live: -2015 error | Enabled Spot Trading on the API key |
| 6 | Day 3 20:29 | Wrong `amount_in_wei` for BUY_DEX direction | Live: STF revert + naked short | Direction-symmetric calc + regression tests |
| 7 | Day 3 (audit) | `web3.send_raw_transaction` exception bypassed unwind | Code review during Bug #6 post-mortem | try/except returning `success=False` |
| 8 | Day 4 21:47 | `slippage_bps=50` too tight for CHIP pool + `build_dex_swap` not wrapped | Live: "Too little received" + naked long | Bumped to 100 bps + wrapped build call |
| 9 | Day 4 12:22 | CCXT default 42s timeout for orders | Live: missed 86 bps signal during slow API | Set explicit `timeout: 8000` ms |
| 10 | Day 4 (audit) | `_unwind_cex` and `_unwind_dex` raised NotImplementedError in live mode | Code review (`grep NotImplementedError`) | Implemented real broadcast for both |

### Patterns observed

1. **Onion of dormant bugs**: Bugs #3, #4, #5, #6, #8 each only became
   visible AFTER the previous one was fixed. There was no way to see them
   in dry-run because dry-run short-circuited the code path before reaching
   them. Live testing at small notional was the only way.

2. **Code-review beats live-debugging when targeted**: Bugs #1, #2, #7, #10
   were found by deliberate code reviews (grep for known anti-patterns).
   Cost: ~30 minutes total. Saved: probably 2-4 hours of incident-driven
   debugging (Bug #10 alone would have caused another naked-position
   incident on Day 5).

3. **Defensive validators pay off**: `PreTradeValidator`'s 500 bps cap
   caught a bad V3 quote on Day 4 that would have bought into a phantom
   profit and lost money. The validator looked "paranoid" in code review;
   it justified itself in production.

4. **Lucky outcomes are noise**: Bug #6 incident netted +$0.80 by random
   timing of a CHIP price drop. This is **statistically meaningless** —
   the system had a 0% success rate at the time, only luck saved capital.
   Don't optimize for the accident; fix the system.

---

## 6. Lessons Learned

### Strategic
- **Live testing at minimum size is irreplaceable.** Two weeks of dry-run
  testing produced 0 incidents; one evening of live trading produced 4. The
  asymmetry isn't surprising — dry-run can only test code paths it's
  configured to take, and the live broadcast paths were specifically
  short-circuited in `simulation_mode`. Run ALL paths live, even if for
  $1 trades.
- **Real measurements beat conservative estimates by an order of magnitude.**
  Gas estimate $0.50 → reality $0.012. Slippage tolerance 50 bps → real
  slippage 9.5 bps. The conservative defaults were rejecting profitable
  signals because the math said they'd lose money. **One $0.012 smoke test
  invalidated weeks of cost assumptions.**
- **CHIP/USDC arbitrage was the hard mode.** A CEX-DEX pair with thin pool
  liquidity, 99% LP concentration, and high price volatility is exactly
  the worst possible target for a beginner's first live arbitrage. ETH/USDC
  would have been operationally easier (deeper pool, less revert risk) but
  more competitive (sub-5-bps spreads dominated by professional bots).
  Both have valid arguments — for a learning week, CHIP forced engagement
  with edge cases (Bug #4, #6, #8, validator) that ETH wouldn't have.
- **Onion-of-bugs phenomenon is the dominant cost of live operations.**
  Each fix unlocked the next bug. Future projects should structure
  pre-launch testing to walk every code path explicitly (not just the happy
  path), to surface as much of the onion as possible before real money is
  at stake.

### Operational
- **Auto-unwind > manual unwind, always.** Three incidents — first two
  required 5-15 minutes of manual recovery; third recovered in 1.8 seconds
  autonomously. The Bug #10 fix's value isn't measured in dollars saved
  per incident; it's measured in preventing entire classes of "stuck
  position" scenarios.
- **Kill switch and observability matter more than individual features.**
  At any moment during the week I could `touch /tmp/arb_bot_kill` to stop
  the bot, `python check_balances.py` to see real state, `tail -f` the
  log, or `/status` from Telegram. This made aggressive iteration safe.
- **Inventory rebalancing is a real operational cost.** After two
  same-direction trades, the bot would have run out of one asset on one
  side. Cross-venue Binance withdrawals cost $1 + 5-15 min, vs trade-based
  rebalancing at $0.04. For Day 5 chose transfer-based (more visible audit
  trail despite higher cost) but trade-based would scale better in
  production.

### Technical
- **Single canonical filter chain.** Bug #3 was duplicate filter logic
  with different thresholds (`min_profit_usd` in signal generator vs.
  hardcoded `> 0` in executor). Future arch: one source of truth.
- **Direction-symmetric code needs symmetric tests.** Bug #6's naive
  `size * 10^token_in.decimals` worked for one direction, broke for the
  other. Two regression tests (one per direction) added in
  `tests/test_engine_dex_amounts.py`.
- **Safety nets need to wrap every level of the call stack.** Bug #7
  caught web3 exceptions in `send_raw_transaction`; Bug #8 still slipped
  through because `build_transaction` (called earlier) wasn't wrapped. The
  fix wrapped both. The lesson: **for any "single point of failure", check
  every call in that critical section, not just the most obvious one**.
- **Reading legacy code for `NotImplementedError` paid off.** Bug #10
  audit took 5 minutes (`grep NotImplementedError src/executor/`). Found
  two production-blocker stubs from Week 4. This kind of targeted review
  is high-leverage.

---

## Appendix: Files

- `journal/day{1,2,3,4,5}_*.md` — daily trading + bug-fix journals
- `reports/advanced_strategy_analysis.md` — Day 3 lecture deliverable on
  perpetual funding rate arbitrage
- `configs/chip_observe.yaml` — final tuned config (slippage 100 bps, gas
  $0.03, trade_size 200, min_spread 30, min_profit_usd −0.15, max_trade $15)
- `scripts/grant_v3_allowances.py` — one-shot ERC20 approve for SwapRouter02
- `scripts/check_balances.py` — pre-flight inventory dump
- `scripts/dry_simulate_signal.py` — sanity-check both directions before bot start
- `scripts/smoke_dex_round_trip.py` — end-to-end DEX swap smoke test
  (verified Day 4: tx `0x1f4376...3b03`)
- `tests/test_engine_dex_amounts.py` — Bug #6 regression tests
- `tests/test_tx_builder_v3.py` — V3 calldata unit tests
- `preflight_checklist.md` — Week 5 deliverable, signed
- `logs/bot_20260507*.log` ... `logs/bot_20260511*.log` — every live
  session of the week, with full SPREAD/SIGNAL/EXECUTED/FAILED traces

### Live evidence
- First successful arb: https://arbiscan.io/tx/0x905dbbb60323ab54f9e22fb6c96fb900b53b7dfe3ca6d8aac5fbdc33d8df890a
- Smoke test (Day 4): https://arbiscan.io/tx/0x1f4376535d4602d83fd0407237cfc677eca21cbfa13f5bf6906506bacb413b03
- ArbBot wallet: https://arbiscan.io/address/0x84b7346c733Daa8c189e1f31d574087971e5cD7a
- Approve transactions:
  - USDC → SwapRouter02: https://arbiscan.io/tx/0x400d1c0bbd4e2b3b160b70c9d69b1489e51b72fe5801a25d8c2a2720719c056f
  - CHIP → SwapRouter02: https://arbiscan.io/tx/0x98a631b4a86ce458b2127c0bf17a1b3295735ae2afe4c1221e88207d87b1c3d6
