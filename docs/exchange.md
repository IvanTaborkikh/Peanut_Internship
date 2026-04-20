# Exchange Module

The `exchange` module provides a normalized interface to centralized exchanges (CEX).
It handles rate limiting, response normalization, order book analysis, and a pricing adapter that lets a second CEX act as a DEX price feed for cross-exchange arbitrage.

---

## Architecture

```
ExchangeClient        — ccxt wrapper for Binance; rate limiting, order placement, balances
OrderBookAnalyzer     — book walking, depth, imbalance, effective spread
CexPricingAdapter     — wraps any ExchangeClient to implement the pricing_engine interface
```

---

## ExchangeClient

Wraps `ccxt.binance` with:
- **Rate limiting** — dual-source: local rolling window + `X-MBX-USED-WEIGHT-1M` server header
- **Response normalization** — all prices/quantities returned as `Decimal`
- **Structured error handling** — every method catches and re-raises typed ccxt exceptions

```python
from src.exchange.client import ExchangeClient
from src.config import BINANCE_CONFIG

client = ExchangeClient(BINANCE_CONFIG)
```

On init, `ExchangeClient` calls `/api/v3/exchangeInfo` to load real rate limits (falls back to 1200 weight/min if the call fails), then validates connectivity with a `fetch_time()` ping.

### fetch_order_book

```python
ob = client.fetch_order_book('ETH/USDT', limit=20)
# {
#     'symbol':    'ETH/USDT',
#     'timestamp': 1706000000000,
#     'bids':      [(Decimal('2010'), Decimal('5.2')), ...],  # best → worst
#     'asks':      [(Decimal('2011'), Decimal('3.1')), ...],
#     'best_bid':  (Decimal('2010'), Decimal('5.2')),
#     'best_ask':  (Decimal('2011'), Decimal('3.1')),
#     'mid_price': Decimal('2010.5'),
#     'spread_bps': Decimal('0.50'),
# }
```

Request weight: 2 (limit ≤ 20), 5 (≤ 100), 10 (> 100).

### fetch_balance

```python
balances = client.fetch_balance()
# {
#     'ETH':  {'free': Decimal('5.0'), 'locked': Decimal('0'), 'total': Decimal('5.0')},
#     'USDT': {'free': Decimal('10000'), 'locked': Decimal('0'), 'total': Decimal('10000')},
# }
```

Zero-balance assets are filtered out automatically.

### create_limit_ioc_order

```python
order = client.create_limit_ioc_order('ETH/USDT', side='buy', amount=1.0, price=2000.0)
# {
#     'id':               '12345678',
#     'symbol':           'ETH/USDT',
#     'side':             'buy',
#     'type':             'limit',
#     'time_in_force':    'IOC',
#     'amount_requested': Decimal('1.0'),
#     'amount_filled':    Decimal('1.0'),
#     'avg_fill_price':   Decimal('2000.0'),
#     'fee':              Decimal('2.0'),
#     'fee_asset':        'USDT',
#     'status':           'filled',   # 'filled' | 'partially_filled' | 'expired'
#     'timestamp':        1706000000000,
# }
```

IOC orders fill immediately or cancel — preferred for arbitrage because they never rest in the book.

### create_market_order

Same return format as `create_limit_ioc_order`. Use sparingly — LIMIT IOC is preferred for arb because market orders have unpredictable fill prices.

### cancel_order / fetch_order_status

```python
client.cancel_order('12345678', 'ETH/USDT')
client.fetch_order_status('12345678', 'ETH/USDT')
# Both return the same normalized order dict
```

### get_trading_fees

```python
fees = client.get_trading_fees('ETH/USDT')
# {'maker': Decimal('0.001'), 'taker': Decimal('0.001')}
```

Binance testnet does not support the `sapi` fee endpoint — falls back to standard Binance spot fees (0.1% maker/taker).

### Rate limiting

`ExchangeClient` tracks two weight counters in parallel:

| Source | How it works |
|--------|-------------|
| Local rolling window | Every call logs `(timestamp, weight)` — old entries are dropped after the window expires |
| Server header | `X-MBX-USED-WEIGHT-1M` from the last response is treated as authoritative if received within the current window |

The higher of the two is used. If a call would exceed the limit, `_rate_limit()` sleeps until the oldest entry rolls off.

---

## OrderBookAnalyzer

Accepts the dict returned by `fetch_order_book()` and provides analytical methods.

```python
from src.exchange.orderbook import OrderBookAnalyzer

ob   = client.fetch_order_book('ETH/USDT', limit=30)
anl  = OrderBookAnalyzer(ob)
```

### walk_the_book

Simulates filling `qty` base units against the real order book levels.

```python
result = anl.walk_the_book('buy', qty=2.0)
# {
#     'avg_price':       Decimal('2011.3'),
#     'total_cost':      Decimal('4022.6'),
#     'slippage_bps':    Decimal('0.65'),
#     'levels_consumed': 2,
#     'fully_filled':    True,
#     'fills': [
#         {'price': Decimal('2011'), 'qty': Decimal('1.5'), 'cost': Decimal('3016.5')},
#         {'price': Decimal('2012'), 'qty': Decimal('0.5'), 'cost': Decimal('1006.0')},
#     ],
# }
```

`side='buy'` walks asks; `side='sell'` walks bids. Slippage is measured against the best price at the top of the book.

### depth_at_bps

Total quantity available within N bps of best price.

```python
anl.depth_at_bps('bid', 10)   # total bid qty within 10 bps of best bid
anl.depth_at_bps('ask', 10)   # total ask qty within 10 bps of best ask
```

### imbalance

Order book imbalance ratio in `[-1.0, +1.0]`. `+1.0` = pure buy pressure; `-1.0` = pure sell pressure.

```python
anl.imbalance(levels=10)   # uses top 10 levels on each side
```

### effective_spread

True round-trip cost for a given trade size, in bps.

```python
anl.effective_spread(qty=1.0)
# = (avg_ask_fill - avg_bid_fill) / mid_price * 10000
```

This is more accurate than the quoted spread for large orders — it accounts for walking through multiple price levels.

---

## CexPricingAdapter

Wraps any `ExchangeClient` to implement the `pricing_engine` duck-type interface expected by `ArbChecker`. This enables CEX-vs-CEX arbitrage without needing a DEX pricer.

```python
from src.exchange.cex_pricer_adapter import CexPricingAdapter
from src.exchange.client import ExchangeClient
from src.config import BYBIT_CONFIG

bybit   = ExchangeClient(BYBIT_CONFIG)
adapter = CexPricingAdapter(bybit, name='Bybit')

# Use as pricing_engine in ArbChecker:
checker = ArbChecker(
    pricing_engine=adapter,
    exchange_client=binance,
    ...
)
```

`get_dex_price(pair, size)` returns the mid price of the wrapped exchange's order book with zero price impact (since CEX fills are at quoted prices).

```python
result = adapter.get_dex_price('ETH/USDT', size=1.0)
# {'price': Decimal('2010.5'), 'price_impact_bps': Decimal('0')}
```

**Why zero impact?** A CEX order fills at the quoted price — there is no AMM curve. Slippage from walking the book is computed separately inside `ArbChecker` via `OrderBookAnalyzer.walk_the_book()`.
