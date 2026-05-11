## Day 2 — 2026-05-08

### Numbers
- Starting capital: ~$99.8 and 0.00099431 ETH
- Ending capital: ~\$113 (CHIP price +20%) and 0.00097431 ETH
- PnL: \$0 realized (no completed arb trades) | +$13 unrealized from CHIP price move
- Trades: 0 (3 signals generated, all rejected before broadcast)
- Win rate: N/A
- Best trade: N/A (no fills)
- Worst trade: N/A
- Fees paid: 0.00000567 BNB (CEX) + 0.00002 ETH (DEX fees)

### What Happened
- Morning: CHIP pumped 20% overnight ($0.0557 → $0.067). Decided to lock partial profit:
  - Sold 75 CHIP on Binance + 75 CHIP on Arbitrum (Uniswap UI), total ~$10 worth
  - Capital revalued from $99.90 → ~$113
- Adjusted config for Day 2 limits per assignment (max_trade_usd: 5 → 10, max_loss_per_trade: 2 → 3, max_daily_loss: 10 → 12, trade_size: 88 → 175 CHIP)
- Ran bot ~2h afternoon with min_spread_bps=80 — 0 signals (max real spread observed: +44 bps over 3121 samples)
- Three signals fired evening:
  - 21:01:25 — spread 63.4 bps — **REJECTED_RISK** (trade $10.60 > max_trade_usd $10 because CHIP price made 145 CHIP worth $10.60)
  - 22:12:14 — spread 30.4 bps — **REJECTED is_valid** (Bug #3: hardcoded `expected_net_pnl > 0`)
  - 23:27:41 — spread 58.5 bps — same Bug #3 (bot still running pre-fix code)
- Wired `SignalGeneratedEvent` to Telegram notifier (was only `ExecutionDoneEvent` before) so I'd see all signals, not just executed trades

### Problems Encountered
- Bug #3: `Signal.is_valid()` had hardcoded `expected_net_pnl > 0` check — duplicate of signal generator's `min_profit_usd` filter, but stricter (always 0). Even with `min_profit_usd: -0.20` in config, signals at -$0.10 rejected at executor's validator
- Risk-check failure on 21:01: `trade_size: 145` × CHIP $0.073 = $10.60, breached Day 2 $10 limit → had to reduce trade_size to 130
- Telegram notifications were silent for all rejected signals — discovered `on_signal_generated` handler existed but was never subscribed to bus

### Changes Made
- `src/strategy/signal.py`: parameterized `is_valid(min_pnl=None)` with default 0 (preserves test behavior); threshold passed from config
- `src/executor/engine.py`: added `min_profit_usd: Decimal = Decimal('0')` to `ExecutorConfig`; `Executor.execute()` calls `signal.is_valid(self.config.min_profit_usd)`
- `scripts/arb_bot.py`: thread `min_profit_usd` from `signal_config` into `ExecutorConfig`; subscribed `notifier.on_signal_generated` to `SignalGeneratedEvent` bus
- `configs/chip_observe.yaml`: `min_spread_bps: 30`, `min_profit_usd: -0.20`, `gas_cost_usd_default: 0.10` (down from 0.50 — too pessimistic for Arbitrum L2), `trade_size: 130`, Day-2 risk limits
- Tests: 61/61 still pass after `is_valid` parameterization

### Lessons Learned
- Duplicate filter logic in two places leads to silent inconsistency. Refactor: single canonical filter chain
- Static analysis only finds half: 5 hours of dry-run debugging found 0 issues; one evening of live trading found 1 (will become 4 over Days 3-4)

### Tomorrow's Plan
- Same risk limits ($10/trade) per Day 3 assignment rule
- Continue trading, target 1+ successful round-trip
- Begin advanced strategy analysis (Day 3 lecture deliverable)
- Watch for additional hidden bugs — suspect inventory check has issues since Bug #3 fixed but signals still might fail downstream
