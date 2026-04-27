# Unwind Strategy

## Why we need unwind

A two-leg arb has three terminal states:

1. Both legs filled → realised PnL ≈ expected
2. Neither leg started → no position, no cost (rejected pre-flight)
3. **Leg 1 filled, leg 2 didn't** → we are now exposed to a directional position
   that we never wanted

Case 3 is what `unwind` solves. We *must* flatten or our P&L is now market-direction
risk, not arb risk.

## When unwind triggers

| Trigger | Source state | What we hold |
|---|---|---|
| `LEG2_PENDING` timeout | `_execute_*_first` | leg-1 fill on leg-1 venue |
| `leg2['success'] == False` | `_execute_*_first` | leg-1 fill on leg-1 venue |
| Partial fill below `min_fill_ratio` | leg-1 stage | partial fill on leg-1 venue |

DEX-first using Flashbots Protect is special: a failed DEX leg-1 costs nothing,
so we don't unwind — we only unwind if leg-2 (CEX) fails *after* DEX confirmed.

## Strategy: MARKET (only one currently implemented)

**Plan:** flatten on the same venue as leg-1, opposite side, market order
(or aggressive marketable limit, for DEX).

```
direction = BUY_CEX_SELL_DEX, leg1 = CEX → unwind = CEX market sell
direction = BUY_CEX_SELL_DEX, leg1 = DEX → unwind = DEX swap reverse path
direction = BUY_DEX_SELL_CEX, leg1 = CEX → unwind = CEX market buy
direction = BUY_DEX_SELL_CEX, leg1 = DEX → unwind = DEX swap reverse path
```

Why market and not limit:
- **Time-critical:** every second of exposure adds variance. We accept some
  slippage to lock in a known loss now.
- **Reliable fill:** market orders fill against the top-of-book. Limit chase can
  miss and leave us stuck longer.
- **Simple:** less state machinery. We commit to the loss and move on.

## Strategy: LIMIT_CHASE (placeholder — not implemented)

Future option. Posts an aggressive limit at best-bid/ask, repegs every N seconds
toward the mid until filled or timeout. Smaller realised loss in calm markets,
but adds complexity and can fail to fill.

## What unwind doesn't do

- It does **not** retry the failed leg. We treat the original arb as dead.
- It does **not** try to "improve" by waiting. Variance kills us — flat now.
- It does **not** rebalance across venues. That's the rebalancer's job (week 3).

## Reporting

Every unwind result is folded back into `ExecutionContext.actual_net_pnl`:

```
actual_net_pnl = leg1_pnl + unwind_pnl - all_fees - gas
```

If unwind itself fails (e.g. CEX rate-limit, DEX revert), we move to
`UNWIND_FAILED` and surface this via the Telegram notifier as a *manual action
required* alert — operator must flatten by hand.

## Dry-run behaviour

In `dry_run=True` (current default for both TEST and PROD modes), unwind goes
through the same builder code paths and produces a `PreparedCexOrder` /
`PreparedDexTx`, then logs them. **No order is submitted, no tx is broadcast.**
This lets us observe what the bot *would* do without taking position risk.
