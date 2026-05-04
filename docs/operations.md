# Operations Runbook

How to operate the bot day-to-day. **Read before each session.**

## STOP Protocol

When something feels wrong:

```
S — STOP the bot
    touch /tmp/arb_bot_kill        # next tick will halt within 5s
    Don't try to "fix it" while running.

T — TAKE STOCK of positions
    PYTHONPATH=. python scripts/emergency_flatten.py     # dry-run plan
    Or check Binance app: which assets, where, how much?

O — OBSERVE before acting
    What does the log say?  tail -200 logs/bot_*.log
    Is Binance up?  https://www.binance.com/en/support/announcement
    Is RPC alive?   curl $MAINNET_RPC_URL -H "Content-Type: application/json" \
                     -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
    Is this a bot bug or a market event?

P — PLAN before reacting
    If positions safe → debug at leisure
    If exposed       → emergency_flatten --confirm  (or close manually in Binance)
    Don't rush. Don't add positions to "fix" something.
```

## Decision Framework — Pre-Committed Rules

Decide once. Do not relitigate under stress.

```
IF daily_pnl <= -$10:
    → STOP for the day, no exceptions
    → Review logs tomorrow, do not trade
    → Reduce limits in configs/prod.yaml before resuming

IF 3 consecutive losses (consecutive_loss_limit hit):
    → CB will pause; this is correct behaviour
    → Read the last 3 trade logs before unpausing
    → Resume only if cause is understood

IF error_rate > 10% in any hour:
    → STOP, debug
    → Common causes: rate limit, expired API key, RPC down

IF circuit breaker trips:
    → Telegram alert fires
    → Wait full cooldown (10 min)
    → If trips again same day → done for the day

IF unexpected balance drift (verify_balances logs CRITICAL):
    → STOP immediately
    → Open Binance, list positions manually
    → Cross-check with last 10 lines of bot log
    → Resume only after reconciling

IF unsure:
    → STOP
    → Better to miss trades than lose money
```

## Daily Ramp-Up

Limits live in `configs/prod.yaml`. Move to next tier only with instructor sign-off.

| Day | max_trade_usd | max_daily_loss | Operator cadence |
|---|---|---|---|
| 1   | 5  | 10 | Watch every trade; supervise actively |
| 2-3 | 10 | 15 | Spot-check hourly |
| 4-5 | 20 | 20 | Trust monitoring; focus on analysis |

## Reading Logs

```bash
# Live tail
tail -f logs/bot_*.log

# Count signals processed
grep -c "Signal:" logs/bot_*.log

# All would-be trades (dry-run mode)
grep "DRY RUN | Would trade" logs/bot_*.log

# All real successes
grep "SUCCESS:" logs/bot_*.log

# All errors
grep "ERROR \|" logs/bot_*.log

# Risk gate rejections (by reason)
grep "Risk check rejected" logs/bot_*.log | sed 's/.*: //' | sort | uniq -c | sort -rn

# Sum PnL across all sessions today
grep "PnL=\$" logs/bot_$(date +%Y%m%d)*.log | grep -oE 'PnL=\$-?[0-9.]+' | cut -d'$' -f2 | paste -sd+ | bc
```

### Structured signal log (`SIGNAL | <STATUS>`)

Every signal that enters the pipeline emits one structured line per stage:

```
SIGNAL | GENERATED | pair=ETH/USDT | direction=BUY_CEX_SELL_DEX | size=0.0050 | spread=60.0bps | expected_pnl=$0.90
SIGNAL | REJECTED_VALIDATOR | ... | reason=signal too old (...)
SIGNAL | REJECTED_RISK      | ... | reason=Trade $... > max_trade_usd ...
SIGNAL | REJECTED_SCORE     | ... | reason=score=40 < 60
SIGNAL | REJECTED_PAUSED    | ... | reason=3 consecutive losses | resume_in=1450s
SIGNAL | DRY_RUN_EXECUTED   | ...
SIGNAL | EXECUTED           | ...
```

```bash
# Pipeline funnel
grep "SIGNAL | GENERATED"   logs/bot_*.log | wc -l
grep "SIGNAL | EXECUTED"    logs/bot_*.log | wc -l
grep "SIGNAL | DRY_RUN_EXECUTED" logs/bot_*.log | wc -l

# Why are signals dropping out?
grep "SIGNAL | REJECTED_" logs/bot_*.log | awk -F'|' '{print $2}' | sort | uniq -c | sort -rn

# Hourly summary (one line per hour)
grep "HOURLY_SUMMARY" logs/bot_*.log
```

## Telegram control plane

Inbound commands (configured via `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`):

| Command | What it does |
|---|---|
| `/status` | bot state, pause status, circuit breaker, win rate |
| `/limits` | current `RiskLimits` |
| `/metrics` | session counters: signals → trades, execution rate |
| `/errors` | recent (last 60 min) failures grouped by type |
| `/signals` | last 5 signals with score + outcome |
| `/pnl` | session PnL |
| `/winrate` | win rate over last 10 / 20 / 50 trades |
| `/inventory` | balances for configured pairs |
| `/pause [min]` | pause N minutes; no arg = until `/resume` |
| `/resume` | clear all pauses |
| `/set_max_trade <usd>` | live update (validated against `ABSOLUTE_MAX_TRADE_USD`) |
| `/set_max_daily_loss <usd>` | live update (validated against `ABSOLUTE_MAX_DAILY_LOSS`) |
| `/set_consecutive_limit <n>` | tighten / loosen pause-trigger threshold |
| `/enable_real_trading` | start DRY→REAL switch (returns "confirm with /confirm_real_trading") |
| `/confirm_real_trading` | confirm within 60 s — flips both `bot.dry_run` **and** `executor.config.simulation_mode` |

A confirmed switch logs `CRITICAL: 🚨 REAL TRADING ENABLED 🚨` and pushes a Telegram alert.

## DRY → REAL transition (Day 1 procedure)

1. Start with `dry_run: true` in `configs/prod.yaml`. Bot prints "PRODUCTION MODE — REAL MONEY" warning **only** if both `mode: prod` and `dry_run: false` (so you stay safe in dry-run).
2. Watch logs for at least 10 min in dry-run. Confirm:
   - `SIGNAL | GENERATED` lines appear (≥ a few per minute on a normal market)
   - `SIGNAL | DRY_RUN_EXECUTED` lines appear when a signal would have traded
   - `HOURLY_SUMMARY` schedule confirmed; no unexpected errors in `/errors`
3. Send `/enable_real_trading` from your Telegram. Bot replies with confirmation prompt.
4. Send `/confirm_real_trading` within 60 s. Bot flips both layers, logs CRITICAL.
5. From this point real money is at risk. First few `EXECUTED` lines deserve a manual log read each.
6. Abort path: `touch /tmp/arb_bot_kill`. Bot halts within ≤ 5 s.

## Trading Journal

Write **every day** in `logs/journal_YYYY-MM-DD.md`:

```markdown
## Day X — YYYY-MM-DD

### Numbers
- Starting capital:  $XX
- Ending capital:    $XX
- PnL:               $XX
- Trades:            X (X wins, X losses)
- Win rate:          X%
- Best trade:        $XX
- Worst trade:       $XX
- Max drawdown:      X%

### What Happened
- [Notable events: spreads seen, market regime, any manual actions]

### Problems Encountered
- [Errors, surprises, anything that triggered STOP]

### Changes Made
- [Config changes, code changes — link commits]

### Lessons Learned
- [What would I do differently?]

### Tomorrow's Plan
- [Any limit changes? Approval to ramp up?]
```

## Manual Override Procedures

### Reset Circuit Breaker
The CB self-resets after `cooldown_seconds`. To force-clear early, restart the bot.

### Disarm Kill Switch
```bash
rm /tmp/arb_bot_kill
```
The bot does **not** auto-restart on disarm — you must start it manually.

### Manual Position Close (no bot)
```bash
# Dry-run preview:
PYTHONPATH=. python scripts/emergency_flatten.py

# Actually flatten (irreversible):
PYTHONPATH=. python scripts/emergency_flatten.py --confirm
```

Or use the Binance UI / mobile app. When in doubt, the UI is the safest path.

### Rotate API Keys
If a key is exposed:
1. Binance UI → API Management → Delete the key (immediate)
2. Generate new key, mirror old permissions (Spot only, no withdrawals, IP whitelist)
3. Update `.env` (`BINANCE_API_KEY`, `BINANCE_SECRET`)
4. Restart bot
5. Investigate the leak (`git log -p`, `pre-commit run`)

## API Key Expiration (90-day rule)

Binance "Enable Spot & Margin Trading" permission **expires 90 days after activation
unless the key has an IP whitelist**. After expiration the key still authenticates
read endpoints (orderbook, balances) but every order submission is silently rejected.
Without monitoring, this means:
- Bot looks healthy: no auth errors on startup, no warnings.
- Every trade attempt fails. AutoKillSwitch only trips after 50 errors/hour — by which
  time you may have dozens of failed orders and possibly half-filled positions.

The bot now has three layers of protection:

1. **Pre-flight check** in `ArbBot.run()` — calls `fetch_balance()` and
   `sapi_get_account_apirestrictions()`. If the key is invalid → bot refuses to start.
   If `tradingAuthorityExpirationTime` shows expiration ≤ 0 days → also refuses.
   If expiration ≤ 7 days → starts but logs `WARNING` + Telegram alert.

2. **Periodic re-check** every 15 min via `_api_key_health_loop`. If permission is
   revoked mid-flight → triggers `AutoKillSwitch`, sends Telegram critical alert,
   stops bot. If expiration enters the 7-day window → one-shot warning.

3. **`/key_status`** Telegram command — on-demand probe. Returns either:
   - `✅ Valid · Expires: <ISO date> · Days remaining: <N>`
   - `✅ Valid (IP-whitelisted, no expiration)`
   - `❌ Invalid: <error>`

### Recommended setup
**Whitelist your IP** in Binance UI → API Management — this disables the 90-day expiry
entirely. The bot will log `API key is IP-whitelisted — no expiration.` on startup.

If you cannot use IP whitelist (dynamic IP), set a calendar reminder for **day 80**
of each key's lifetime and rotate proactively. The bot will still warn at day 7 but
that gives you very little operational margin.

### Rotation procedure
1. Generate a new key in Binance UI **before** the old one expires.
2. Update `.env` (`BINANCE_API_KEY`, `BINANCE_SECRET`) with the new values.
3. Restart bot. Pre-flight will confirm fresh expiration date.
4. Disable / delete the old key in the UI.

## Pre-Session Checklist (every time you start the bot)

- [ ] `git status` clean (no uncommitted experimental code)
- [ ] `make test` passes
- [ ] `.env` has the right keys for the chosen mode
- [ ] `logs/` directory exists
- [ ] Binance app open in another window
- [ ] Telegram notifier on
- [ ] Today's intended limits match `configs/prod.yaml`
- [ ] Heart rate normal — if anxious, don't start

## Why these rules

- **Survival > profit.** Losing $100 ends learning; preserving capital extends it.
- **Decisions under stress are bad.** Pre-commit them when calm.
- **A working bot can become a money-destroying machine instantly.** Knight Capital lost $440M in 45 minutes. Our protections (`safety/` module + CB + kill switch + ABSOLUTE_*) exist because of stories like that.
