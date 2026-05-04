# Strategy Review — Where the Edge Is, and Where It Isn't

A honest look at how this bot is supposed to make money and where it's
likely to lose.

## 1. Edge thesis

**Latency arbitrage between CEX (Binance) and DEX (Uniswap V2 on Ethereum
mainnet / Arbitrum).**

Mid-tick price moves on Binance show up in DEX pool ratios only after the
next block (and only if someone trades through the pool). That gap — usually
50–200 ms on Arbitrum, 1–12 s on Ethereum L1 — is the window we trade.

```
t=0.0s   Binance ETH ticks 2010 → 2015
t=0.0s   DEX pool still implies 2010
t=0.2s   We see the gap → emit Signal (75 bps)
t=0.5s   Submit CEX leg @ 2015 (taker)
t=1.0s   CEX fills, submit DEX swap
t=12s    DEX swap confirmed @ effective 2014.20
         net = (2014.20 - 2015) - fees ≈ -$0.80 ❌
```

The directional sign of the gap is what we capture. We are *not* trading on
view; we are trading on a **time difference between two oracles**.

## 2. Economics

Costs per round-trip arb (using `configs/test.yaml` defaults):

| Component | Where | Bps on $1k notional |
|---|---|---|
| CEX taker fee | `fees.cex_taker_bps` | 10 |
| DEX swap fee (Uniswap V2) | `fees.dex_swap_bps` | 30 |
| Gas (Arbitrum, ~$0.5) | `fees.gas_cost_usd_default` | 5 |
| Slippage allowance | `executor.slippage_bps` | 30 |
| **Total breakeven** | | **75 bps** |

So `signal.min_spread_bps = 50` in the default config is *too tight* — it
emits signals that lose money on slippage alone. Tuning to 80–100 bps is
more honest. (A signal with a 50 bps spread *might* still execute at 60 bps
realised, but the variance is high.)

For Ethereum mainnet at \$5 gas + 12 s settlement, breakeven climbs to
~110 bps on the same \$1k. Mainnet is not viable for \$1k trades; the floor
is closer to $5–10k notional, where gas amortises.

| Trade size | Gas as bps | Breakeven (Arb) | Breakeven (Mainnet, $5 gas) |
|---|---|---|---|
| $500   | 100 / 1000 | 170 / 1070 bps | only if you really hate money |
| $1,000 | 50 / 500 | 120 / 570 bps | dont |
| $5,000 | 10 / 100 | 80 / 170 bps | tight but possible |
| $10,000 | 5 / 50 | 75 / 120 bps | reasonable |

## 3. Why MEV bots beat us on top pairs

Specialised MEV searchers running on ETH/USDC, ETH/USDT, WBTC/ETH:

- They have **co-located CEX feeds** (sub-ms) — we run on a laptop, ~30–80 ms.
- They submit DEX legs through **Flashbots Protect / Builder bundles** with
  a competitive bribe — public-mempool swaps from us get back-run.
- They run **flashloan + atomic swap** for risk-free arb — we hold inventory
  on both venues.
- They're playing for fractions of a basis point at $100k+ size; we need 75+
  bps to break even.

On ETH/USDC mainnet the realistic clearing spread for retail latency is
**under 10 bps** for >99% of the day. Our bot will rarely emit a signal there
that wins.

## 4. Where the edge actually lives

Three regions where the playing field is closer to level:

1. **Less popular pairs** with thinner CEX–DEX coverage. Examples:
   exotic L2 tokens, mid-cap altcoins where MEV searchers haven't built
   custom infrastructure. Spreads of 50–300 bps appear several times a day.
2. **Volatile windows** — major news, liquidations cascading, gas spikes
   that price MEV searchers out of small trades. Our \$1–5k sizes still pay
   for themselves when others can't justify ~$50 of priority fee.
3. **Stablecoin depegs / wrapped vs native** (USDC ↔ USDC.e, WBTC ↔ tBTC).
   These move slowly enough that 12 s confirmation isn't instant death.

Realistic monthly targets at our scale (single account, no flashloans):
- Frequency: 5–30 successful arbs/day on a tuned set of pairs
- PnL/arb: $0.50 – $5
- Win rate: 60–75% (the rest unwind at small losses)
- Net monthly: 1–4% on $5k working capital, *before* downtime / outages

This is **not** the kind of bot that compounds unlimited. It's a learning
vehicle that pays for its hosting. Anyone telling you otherwise is selling
a course.

## 5. Risk inventory

| Risk | Mitigation in code | Residual exposure |
|---|---|---|
| Front-running on public mempool | Flashbots flag (`executor.use_flashbots`) — not yet wired to a real relay | High on mainnet, low on Arbitrum |
| Price moves between legs | `signal.signal_ttl_seconds=5`, slippage cushion in `amount_out_min` | Moderate; partial fills exist |
| Stuck on one venue (leg-2 fails) | `unwind` flattens via market order on leg-1 venue, see [unwind_strategy.md](unwind_strategy.md) | Low; we accept slippage but get out |
| Repeated failures hammering CEX rate limits | `CircuitBreaker` (3 failures / 5 min → 10 min cooldown) | Low |
| Duplicate execution from network glitch | `ReplayProtection` keyed on `signal_id` | Very low |
| Inventory drift across venues | `RebalancePlanner` (Week 3) — manual trigger, not autoroute | Manual; needs operator |
| RPC failure mid-swap | Tx is signed before submission; on revert/timeout we unwind | Low if `dry_run=False` is gated |
| Key compromise | `SecretStr` in config; `.env` git-ignored; `WalletManager.__repr__` masks | Standard hot-wallet risk; mitigate with small balance |
| Pre-existing approval missing | Not handled — first DEX swap on any pair will revert without `WETH.approve(router)` | Manual one-shot, document for ops |

## 6. What changes when `dry_run` flips to False

Today `dry_run: true` is the safety default in both `configs/test.yaml`
and `configs/prod.yaml`. The exact code path that activates on `dry_run: false`:

- `Executor._execute_dex_leg` would call
  `web3.eth.send_raw_transaction(prepared.raw_tx)` instead of just logging
- `Executor._execute_cex_leg` would call
  `exchange.create_order(**prepared)` instead of returning the prepared dict
- `_unwind` likewise

Before flipping the switch, the gates we want in place:

1. **Approval transactions** sent for every traded base/quote on every chain
   the bot uses. One-shot, idempotent, but blocks first run otherwise.
2. **Inventory floor checked against pre-trade balance** — not just config
   limits. Right now `_check_inventory` trusts the cached snapshot; reality
   may differ if a transfer happened.
3. **Circuit breaker** *also* trips on PnL drawdown (e.g. `-$50` realised
   in 30 min), not just count of failed executions. Currently only counts.
4. **Size ramp** — start at 10% of `pairs[*].trade_size`, double every N
   successful arbs up to the configured size. Avoids paying tuition twice.
5. **Operator alerts** — Telegram already wired for `UNWIND_FAILED`;
   add success-rate-below-threshold and PnL-drawdown alerts.

Until those are in place, the bot stays in `dry_run` and we observe the
prepared transactions to confirm signals would have been profitable.

## 7. Honest summary

- The bot does correctly *detect* latency-arb opportunities and *would*
  execute the right trades.
- On top pairs, we're outclassed by MEV infrastructure. Dont fight there.
- The realistic edge is on second-tier pairs and during regime changes.
- The infrastructure (FSM, circuit breaker, replay protection, unwind)
  is solid for a learning system; turning it into production money requires
  Flashbots integration, drawdown-aware risk gating, and a size ramp.
- `dry_run` is doing its job: we can run today, observe what the bot would
  do, and tune thresholds without lighting capital on fire.
