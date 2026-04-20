# Inventory Module

The `inventory` module tracks positions across venues, generates rebalancing plans, records trade PnL, and detects arbitrage opportunities end-to-end.

---

## Architecture

```
InventoryTracker    — single source of truth for balances across CEX + wallet
RebalancePlanner    — detects skew and generates transfer plans
PnLEngine           — records arb trades, computes PnL, exports CSV
ArbChecker          — end-to-end opportunity detector (pricing → costs → inventory check)
ArbLogger           — persists every ArbChecker.check() result to CSV
```

---

## InventoryTracker

Tracks balances across `Venue.BINANCE` (CEX) and `Venue.WALLET` (on-chain DEX).

```python
from src.inventory.tracker import InventoryTracker, Venue
from decimal import Decimal

tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])

# Load live CEX balances
tracker.update_from_cex(Venue.BINANCE, client.fetch_balance())

# Load wallet balances (on-chain query result)
tracker.update_from_wallet(Venue.WALLET, {
    'ETH':  Decimal('5'),
    'USDT': Decimal('15000'),
})
```

### snapshot

Full portfolio at current time.

```python
snap = tracker.snapshot()
# {
#     'timestamp': datetime,
#     'venues': {
#         'binance': {'ETH': {'free': ..., 'locked': ..., 'total': ...}, ...},
#         'wallet':  {'ETH': {'free': ..., ...}, ...},
#     },
#     'totals': {'ETH': Decimal('10'), 'USDT': Decimal('35000')},
# }
```

### get_available

Free (non-locked) balance for one asset at one venue.

```python
tracker.get_available(Venue.BINANCE, 'ETH')   # → Decimal('5')
```

### can_execute

Pre-flight check for a two-leg arb: do both venues have enough funds?

```python
result = tracker.can_execute(
    buy_venue=Venue.WALLET,  buy_asset='USDT',  buy_amount=Decimal('2000'),
    sell_venue=Venue.BINANCE, sell_asset='ETH', sell_amount=Decimal('1'),
)
# {
#     'can_execute':          True,
#     'buy_venue_available':  Decimal('15000'),
#     'buy_venue_needed':     Decimal('2000'),
#     'sell_venue_available': Decimal('5'),
#     'sell_venue_needed':    Decimal('1'),
#     'reason':               None,   # human-readable failure message if False
# }
```

### record_trade

Update internal balances after an execution (used by PnLEngine integration).

```python
tracker.record_trade(
    venue=Venue.BINANCE,
    side='sell',
    base_asset='ETH',  quote_asset='USDT',
    base_amount=Decimal('1'), quote_amount=Decimal('2010'),
    fee=Decimal('2.01'), fee_asset='USDT',
)
```

### skew / get_skews

Measure how evenly an asset is distributed across venues.

```python
tracker.skew('ETH')
# {
#     'asset': 'ETH',
#     'total': Decimal('10'),
#     'venues': {
#         'binance': {'amount': Decimal('8'), 'pct': 80.0, 'deviation_pct': 30.0},
#         'wallet':  {'amount': Decimal('2'), 'pct': 20.0, 'deviation_pct': 30.0},
#     },
#     'max_deviation_pct': 30.0,
#     'needs_rebalance':   True,   # > 30% deviation triggers rebalance
# }

tracker.get_skews()   # check all tracked assets at once
```

---

## RebalancePlanner

Generates transfer plans when inventory skew exceeds the threshold (default 30%).
Plans only — does **not** execute transfers.

```python
from src.inventory.rebalancer import RebalancePlanner

planner = RebalancePlanner(tracker, threshold_pct=30.0)
```

### check_all

Quick status for every tracked asset.

```python
planner.check_all()
# [
#     {'asset': 'ETH',  'max_deviation_pct': 42.5, 'needs_rebalance': True},
#     {'asset': 'USDT', 'max_deviation_pct': 15.2, 'needs_rebalance': False},
# ]
```

### plan

Generate a concrete transfer plan for one asset.

```python
plans = planner.plan('ETH')
# [TransferPlan(
#     from_venue=Venue.BINANCE, to_venue=Venue.WALLET,
#     asset='ETH', amount=Decimal('1.5'),
#     estimated_fee=Decimal('0.005'), estimated_time_min=15
# )]

plans[0].net_amount   # amount received after fee
```

Returns `[]` if no rebalance is needed, or if the transfer would fall below minimum operating balance.

Built-in withdrawal fee and minimum balance constants (`TRANSFER_FEES`, `MIN_OPERATING_BALANCE`) cover ETH, USDT, and USDC.

### plan_all / estimate_cost

```python
all_plans = planner.plan_all()   # {'ETH': [TransferPlan], ...}

cost = planner.estimate_cost(list(all_plans.values())[0])
# {
#     'total_transfers': 1,
#     'total_fees_usd':  Decimal('0.005'),
#     'total_time_min':  15,
#     'assets_affected': ['ETH'],
# }
```

### CLI

```bash
PYTHONPATH=. python -m src.inventory.rebalancer           # skew report
PYTHONPATH=. python -m src.inventory.rebalancer --plan ETH
```

---

## PnLEngine

Records completed arb trades and produces performance reports.

```python
from src.inventory.pnl import PnLEngine, ArbRecord, TradeLeg
from src.inventory.tracker import Venue

engine = PnLEngine()

engine.record(ArbRecord(
    id='trade-001',
    timestamp=datetime.now(timezone.utc),
    buy_leg=TradeLeg(
        id='buy-1', timestamp=..., venue=Venue.WALLET,
        symbol='ETH/USDT', side='buy',
        amount=Decimal('1'), price=Decimal('2000'),
        fee=Decimal('2'), fee_asset='USDT',
    ),
    sell_leg=TradeLeg(
        id='sell-1', timestamp=..., venue=Venue.BINANCE,
        symbol='ETH/USDT', side='sell',
        amount=Decimal('1'), price=Decimal('2015'),
        fee=Decimal('2.015'), fee_asset='USDT',
    ),
    gas_cost_usd=Decimal('5'),
))
```

### ArbRecord properties

| Property | Description |
|----------|-------------|
| `notional` | Buy size × buy price (quote currency) |
| `gross_pnl` | Sell revenue − buy cost |
| `total_fees` | Buy fee + sell fee + gas cost |
| `net_pnl` | `gross_pnl − total_fees` |
| `net_pnl_bps` | `net_pnl / notional × 10000` |

### summary

```python
s = engine.summary()
# {
#     'total_trades':      10,
#     'total_pnl_usd':     Decimal('82.50'),
#     'total_fees_usd':    Decimal('90.15'),
#     'avg_pnl_per_trade': Decimal('8.25'),
#     'avg_pnl_bps':       Decimal('3.6'),
#     'win_rate':          80.0,
#     'best_trade_pnl':    Decimal('20.0'),
#     'worst_trade_pnl':   Decimal('-3.0'),
#     'total_notional':    Decimal('23000'),
#     'sharpe_estimate':   1.42,
#     'pnl_by_hour':       {14: Decimal('40.0'), 15: Decimal('42.5')},
# }
```

### recent

```python
engine.recent(n=5)   # last 5 trades, newest first
# [{'id': ..., 'timestamp': '14:30', 'symbol': 'ETH/USDT',
#   'buy_venue': 'wallet', 'sell_venue': 'binance',
#   'net_pnl_usd': Decimal('8.25'), 'net_pnl_bps': Decimal('3.6'),
#   'profitable': True}, ...]
```

### export_csv

```python
engine.export_csv('trades.csv')
```

### CLI

```bash
PYTHONPATH=. python -m src.inventory.pnl             # summary with demo trades
PYTHONPATH=. python -m src.inventory.pnl --recent 5  # last 5 trades
```

---

## ArbChecker

End-to-end opportunity detector. Bridges DEX pricing (or a second CEX via `CexPricingAdapter`) with CEX order book data and inventory checks.

```python
from src.integration.arb_checker import ArbChecker
from decimal import Decimal

checker = ArbChecker(
    pricing_engine=dex_pricer,      # Any object with get_dex_price(pair, size) → dict
    exchange_client=binance_client,
    inventory_tracker=tracker,
    pnl_engine=pnl_engine,
    gas_cost_usd=Decimal('5'),      # Flat gas estimate per arb round-trip
)

result = checker.check('ETH/USDT', size=1.0)
```

### check() flow

1. Fetch CEX order book + trading fees
2. Get DEX price from `pricing_engine` (falls back to CEX mid if `None` or raises)
3. Determine direction: `buy_dex_sell_cex` | `buy_cex_sell_dex` | `None`
4. Calculate raw gap in bps
5. Estimate all costs: DEX fee (30 bps) + DEX price impact + CEX fee + CEX slippage + gas
6. Check inventory via `tracker.can_execute()`
7. Return opportunity assessment

### Return value

```python
{
    'pair':                   'ETH/USDT',
    'timestamp':              datetime,
    'dex_price':              Decimal('2000'),
    'dex_price_is_fallback':  False,        # True if pricing_engine failed/None
    'execution_price':        Decimal('2000'),  # dex_price or cex_ask
    'cex_bid':                Decimal('2010'),
    'cex_ask':                Decimal('2011'),
    'gap_bps':                Decimal('50'),
    'direction':              'buy_dex_sell_cex',
    'estimated_costs_bps':    Decimal('45'),
    'estimated_net_pnl_bps':  Decimal('5'),
    'inventory_ok':           True,
    'executable':             True,         # net_pnl > 0 AND inventory_ok
    'details': {
        'dex_price_impact_bps': Decimal('1'),
        'cex_slippage_bps':     Decimal('0.5'),
        'cex_fee_bps':          Decimal('10'),
        'dex_fee_bps':          Decimal('30'),
        'gas_cost_usd':         Decimal('5'),
    },
}
```

### Inventory logic

| Direction | Needs at buy venue | Needs at sell venue |
|-----------|-------------------|---------------------|
| `buy_dex_sell_cex` | Wallet USDT ≥ size × dex_price | Binance ETH ≥ size |
| `buy_cex_sell_dex` | Binance USDT ≥ size × cex_ask | Wallet ETH ≥ size |

### DEX price fallback

If `pricing_engine=None` or `get_dex_price()` raises, `dex_price` falls back to CEX mid and `dex_price_is_fallback=True`. In this mode direction detection is disabled (prices are always equal → no arb signal).

### Pair validation

```python
checker.check('ETHUSDT')       # ValueError — missing '/'
checker.check('ETH/USDT/BUSD') # ValueError — too many parts
checker.check('/USDT')         # ValueError — empty base
```

### CLI

```bash
PYTHONPATH=. python -m src.integration.arb_checker ETH/USDT --size 1.0
```

---

## ArbLogger

Persists every `ArbChecker.check()` result to a CSV file for historical analysis.

```python
from src.integration.arb_logger import ArbLogger

logger = ArbLogger('arb_log.csv')   # Creates file with header on first run

result = checker.check('ETH/USDT', size=1.0)
logger.log(result, size=1.0)
```

### CSV columns

`timestamp`, `pair`, `size`, `dex_price`, `cex_bid`, `cex_ask`, `gap_bps`, `direction`, `estimated_costs_bps`, `estimated_net_pnl_bps`, `inventory_ok`, `executable`, `dex_price_is_fallback`

### tail

```python
logger.tail(n=10)   # last 10 rows, newest first
```

### summary

```python
logger.summary()
# {
#     'total_checks':    150,
#     'executable':      3,
#     'with_direction':  47,
#     'avg_gap_bps':     Decimal('8.4'),
#     'avg_net_pnl_bps': Decimal('-28.5'),
#     'best_gap_bps':    Decimal('52.1'),
# }
```

### CLI

```bash
PYTHONPATH=. python -m src.integration.arb_logger              # summary + last 20 rows
PYTHONPATH=. python -m src.integration.arb_logger --tail 5
PYTHONPATH=. python -m src.integration.arb_logger --file path/to/log.csv
```
