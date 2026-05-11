# Advanced Strategy Analysis: Perpetual Funding Rate Arbitrage

**Author**: Ivan Taborskikh
**Date**: 2026-05-09
**Strategy**: Cross-venue perpetual futures funding rate arbitrage

---

## 1. Mechanism

A **perpetual future** is a derivatives contract that mimics a spot position
without expiry. To keep the perp's mark price tethered to spot, exchanges
charge a **funding rate** every 1–8 hours. When the perp trades above spot
(majority long), longs pay shorts; when it trades below spot, shorts pay longs.

**Funding rates differ across venues** because order flow, leverage availability,
and trader composition are venue-specific. Hyperliquid attracts retail leverage
chasing momentum; Binance has more institutional flow and tighter market makers.
The same asset can have +50 bps/8h funding on Hyperliquid while sitting at
-10 bps/8h on Binance.

**The arbitrage trade**: open opposite-direction perps on the two venues so
your net price exposure is zero, then collect the funding-rate differential
every interval.

- **Step 1**: Long ETH-PERP on the venue where you'd be paid (negative funding)
- **Step 2**: Short ETH-PERP, equal notional, on the venue paying out (positive funding)
- **Step 3**: Net delta = 0 (price-neutral). Net funding = (positive − negative) × notional, paid every 8h.
- **Step 4**: Rebalance margin / re-hedge if either side approaches liquidation.

This is a **carry trade**, not a price-direction bet. The edge is structural
(retail leverage premium on Hyperliquid) and persists for weeks-months at a time.

---

## 2. Math example

**Setup**:
- ETH spot: $3,500
- Hyperliquid ETH-PERP funding: **+50 bps / 8h** (longs pay shorts)
- Binance ETH-PERP funding: **−10 bps / 8h** (shorts pay longs)
- Funding spread: 60 bps / 8h = 0.6%
- Trade notional: **$50** per side ($100 total committed margin if 1× leverage,
  much less with leverage — but for safety we assume 1×)

**Per-cycle PnL (every 8h)**:
- Hyperliquid SHORT collects: $50 × 0.005 = **+$0.25**
- Binance LONG receives: $50 × 0.001 = **+$0.05**
- Total funding income: **$0.30 per 8h**, **$0.90 per day**, **~$329 per year**
- On $100 capital: **~329% APR** (theoretical, before fees)

**After realistic costs**:
- Entry fees: 4 perp orders × ~5 bps each = 20 bps × $50 = $0.10 (one-time)
- Hourly micro-rebalances on price drift: assume ~$0.50 / week
- Liquidation insurance buffer: keep 30% margin headroom = $30 idle of $100
- Realistic effective capital efficiency: 70% → **net APR ~150-180%**

**Caveat**: funding spreads of 60 bps / 8h are exceptional and usually decay
within days as arbitrageurs flow in. Sustained "easy" funding spreads on major
pairs (ETH, BTC) are typically 5-20 bps / 8h, giving more realistic APRs of
30-60% on a delta-neutral position. Spreads on small-caps can be 100+ bps but
liquidation risk is much higher.

---

## 3. Risks

**Liquidation risk (most important)**: even though net delta is zero across
venues, each leg can be liquidated independently. If ETH pumps 20% and your
Hyperliquid SHORT margin runs out before the Binance LONG profit covers it,
the SHORT gets force-closed, leaving you naked-long on Binance during the
same pump. **Mitigation**: 30-50% margin headroom, automated re-collateralization
when margin ratio crosses threshold.

**Funding rate inversion**: the spread you opened against can flip while
you're holding. Your "carry +60 bps/8h" becomes "carry −20 bps/8h" overnight.
This is *the* core risk of the strategy. **Mitigation**: monitor funding,
exit if spread narrows below your fee/margin breakeven (typically ~5 bps/8h).

**Exchange insolvency / withdrawal halt**: if Hyperliquid or Binance has an
incident, you can't unwind. FTX 2022 wiped out arbitrageurs holding $1M+
positions split across venues. **Mitigation**: don't oversize relative to
account capital; treat each exchange as a single point of failure. For $100
capital, this risk is bounded — for institutional size, it's existential.

**Hedge ratio drift**: notional changes with price (50 ETH-PERP at $3,500
becomes 50 ETH-PERP at $4,000 — but margin requirements scale linearly).
Each side's $-PnL diverges, requiring rebalance. **Mitigation**: rebalance
trigger at ±5% notional drift.

**Smart contract / oracle risk** (Hyperliquid side): perp DEXes use price
oracles for liquidations. Oracle manipulation or downtime = mistimed
liquidations. Hyperliquid uses its own L1 with multiple validators, but the
risk is non-zero. **Mitigation**: smaller position on perp DEX side initially.

---

## 4. Implementation connection

Direct reuse from the current spot-arb codebase:

| Existing component | Reuse for funding arb? | Notes |
|---|---|---|
| `src/exchange/client.py` (CCXT wrapper) | ✅ as-is | Binance perps via `binance.usdm` exposure |
| `src/safety/killswitch.py` + `RiskManager` | ✅ as-is | Same kill-switch / circuit-breaker logic |
| `src/safety/auto_kill` (capital floor) | ✅ as-is | Floor based on equity, not P&L |
| `src/notifications/telegram_notifier.py` | ✅ as-is | Same alerting paradigm |
| `src/strategy/scorer.py` (signal scoring) | ⚠️ adapt | Score by funding spread depth/persistence, not bps |
| `src/strategy/generator.py` | ⚠️ rewrite | Funding signal logic ≠ spot spread logic |
| `src/executor/engine.py` (CEX leg + DEX leg state machine) | ⚠️ adapt | Two-leg pattern fits, but unwind = close both perps not swap |
| `src/executor/tx_builder.py` (V3 swap calldata) | ❌ unused | Hyperliquid uses its own L1, not Uniswap |
| `src/pricing/uniswap_v3_quoter.py` | ❌ unused | No DEX swap component |

New components required:

1. **HyperliquidConnector** (~200 LOC): REST + WebSocket client for funding rates,
   margin, position open/close. Hyperliquid has decent docs, no CCXT support yet.
2. **FundingSignalGenerator** (~150 LOC): pulls funding rates every 8h from both
   venues, computes spread, ranks pairs by carry × persistence.
3. **MarginManager** (~100 LOC): monitors margin ratio per leg, triggers
   auto-rebalance when ratio < threshold.
4. **PerpExecutor** (~200 LOC): forks current `Executor`. Replaces "CEX limit IOC
   + DEX V3 swap" with "open Binance perp + open Hyperliquid perp simultaneously",
   with their respective unwind paths.

**Estimated effort**: 3-5 days for a working prototype (delta-neutral on one pair,
$100 test capital), 2 weeks for production-grade with multi-pair selection,
auto-rebalance, and proper risk controls.

**Reusable wiring effort**: ~70% of existing code stays. The new pieces all live
under `src/strategy/funding_*` and `src/exchange/hyperliquid.py` — no rewrite of
safety, notification, inventory, or PnL infrastructure.

---

## Summary

Funding rate arb is the natural progression from spot arb: same two-venue
hedged-position pattern, but the edge comes from **structural funding
asymmetry** instead of fleeting price misalignment. Returns are smaller in bps
but accrue continuously (every 8h) instead of needing rare 100+ bps spread
events. For a $100 portfolio, expected annualized returns of 30-60% are
realistic — limited by capital efficiency (margin headroom for liquidation
insurance), not by competition. Perfect next strategy after the current spot-arb
project: same risk discipline, same toolkit, different alpha source.
