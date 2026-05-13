# Pre-Flight Checklist

Complete and co-sign before any real-money trading.
Failure to pass any item = dry-run only.

---

## Code Readiness

- [ ] Bot connects to Binance production (verified: can read order book)
- [ ] Bot connects to Arbitrum (verified: can read pool reserves)
- [ ] Fee calculation uses real fees (CEX 10 bps, DEX 30 bps, gas $0.10–$0.50)
- [ ] Risk limits configured for $100 capital (`max_trade_usd=5`, `max_daily_loss=10` in `configs/prod.yaml`)
- [ ] Kill switch tested: `touch /tmp/arb_bot_kill` → bot stopped within 5 s
- [ ] Auto kill switch tested: capital < 50% of initial → bot stopped automatically
- [ ] Circuit breaker tested: 3 consecutive failures → CB trips, 10-min cooldown holds
- [ ] Safety constants hardcoded (`ABSOLUTE_MAX_TRADE_USD=25`, `ABSOLUTE_MAX_DAILY_LOSS=20`, `ABSOLUTE_MIN_CAPITAL=50`, `ABSOLUTE_MAX_TRADES_PER_HOUR=30`)
- [ ] Dry run completed (30+ minutes of `logs/bot_*.log` attached)

## Security

- [ ] API key: Spot Trading only
- [ ] API key: IP whitelist set
- [ ] API key: NO withdrawal permission
- [ ] `.env` file listed in `.gitignore`
- [ ] No secrets in git history (`git log --all -- .env` empty)

## Operational

- [ ] Logging writes to files (`logs/bot_YYYYMMDD_HHMMSS.log` created on each run)
- [ ] Telegram alerts working (start, stop, errors, kill switch)
- [ ] Know how to read logs (`tail -f logs/bot_*.log`, `grep "SIGNAL |"`)
- [ ] Emergency flatten procedure documented and tested (`scripts/emergency_flatten.py --dry-run`)
- [ ] Binance app/web ready for manual intervention

---

| Role       | Name            | Date       | Signature |
|------------|-----------------|------------|-----------|
| Student    | Ivan Taborskikh | 2026-05-04 | I.T.      |
| Instructor |                 |            |           |

**Evidence:** `logs/bot_20260504_083752.log` (29:28 dry-run, 775 spread observations, 0 real trades)

