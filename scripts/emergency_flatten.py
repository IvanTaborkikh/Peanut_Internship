"""Emergency-flatten CLI — sell every non-stable balance to USDT at market.

Two modes:
    --dry-run (default): print the plan, broadcast nothing
    --confirm:           actually submit market orders

Stables (USDT, USDC, BUSD, TUSD, USD) are skipped. Quantities below the
exchange's per-pair `limits.amount.min` are skipped (orders would be
rejected anyway). The CLI is meant for manual operator use after a kill
switch event — keep it simple, keep it loud.

Usage:
    PYTHONPATH=. python scripts/emergency_flatten.py
    PYTHONPATH=. python scripts/emergency_flatten.py --confirm --testnet
"""
import argparse
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from dotenv import load_dotenv

from src.exchange.client import ExchangeClient


STABLES = {'USDT', 'USDC', 'BUSD', 'TUSD', 'USD', 'DAI'}
QUOTE_FOR_FLATTEN = 'USDT'


@dataclass
class FlattenOrder:
    asset: str
    symbol: str
    side: str           # always 'sell' here, but kept explicit
    amount: Decimal
    skipped_reason: str | None = None


def plan_flatten(balances: dict, markets: dict | None = None,
                 quote: str = QUOTE_FOR_FLATTEN) -> list[FlattenOrder]:
    """Compute the list of market sells needed to flatten non-stable balances.

    `balances` is the dict from ccxt `fetch_balance()` — `{asset: {'free': N, ...}}`.
    `markets` (optional) is `exchange.markets` so we can drop sub-min orders.
    """
    plan: list[FlattenOrder] = []
    for asset, info in balances.items():
        if asset in STABLES:
            continue
        free = Decimal(str(info.get('free', 0) if isinstance(info, dict) else 0))
        if free <= 0:
            continue
        symbol = f"{asset}/{quote}"
        order = FlattenOrder(asset=asset, symbol=symbol, side='sell', amount=free)

        if markets is not None:
            market = markets.get(symbol)
            if market is None:
                order.skipped_reason = f"no {symbol} market on this exchange"
                plan.append(order)
                continue
            min_amount = (market.get('limits') or {}).get('amount', {}).get('min')
            if min_amount is not None and free < Decimal(str(min_amount)):
                order.skipped_reason = f"{free} < min amount {min_amount}"
                plan.append(order)
                continue

        plan.append(order)
    return plan


def render_plan(plan: Iterable[FlattenOrder]) -> str:
    if not plan:
        return "  (no non-stable balances — already flat)"
    lines = []
    for o in plan:
        tag = "SKIP" if o.skipped_reason else "SELL"
        suffix = f"  [{o.skipped_reason}]" if o.skipped_reason else ""
        lines.append(f"  {tag} {o.amount} {o.asset} → market sell on {o.symbol}{suffix}")
    return "\n".join(lines)


def execute_plan(exchange: ExchangeClient, plan: Iterable[FlattenOrder]) -> dict:
    """Submit each non-skipped order. Returns {'submitted': N, 'errors': [...]}.

    Uses ccxt directly to avoid taking a hard dependency on bot helpers.
    """
    submitted = 0
    errors: list[str] = []
    raw = exchange._exchange  # noqa: SLF001 — emergency tool, raw access intentional
    for o in plan:
        if o.skipped_reason:
            continue
        try:
            raw.create_market_order(o.symbol, 'sell', float(o.amount))
            submitted += 1
            logging.warning("FLATTEN submitted: sell %s %s on %s", o.amount, o.asset, o.symbol)
        except Exception as e:
            msg = f"{o.symbol}: {e}"
            errors.append(msg)
            logging.error("FLATTEN failed: %s", msg)
    return {'submitted': submitted, 'errors': errors}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--confirm', action='store_true',
                   help="actually submit market orders (default: dry-run)")
    p.add_argument('--testnet', action='store_true',
                   help="use Binance testnet keys + endpoint")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s | %(levelname)s | %(message)s')
    load_dotenv()

    if args.testnet:
        api_key = os.getenv('BINANCE_TESTNET_API_KEY')
        secret  = os.getenv('BINANCE_TESTNET_SECRET')
        sandbox = True
    else:
        api_key = os.getenv('BINANCE_API_KEY')
        secret  = os.getenv('BINANCE_SECRET')
        sandbox = False
    if not api_key or not secret:
        print("missing API keys — set BINANCE_API_KEY/SECRET (or BINANCE_TESTNET_*)")
        return 1

    print(f"\n{'='*60}\n  EMERGENCY FLATTEN ({'TESTNET' if sandbox else 'PRODUCTION'})\n{'='*60}")

    ex = ExchangeClient({
        'apiKey': api_key, 'secret': secret, 'sandbox': sandbox,
        'options': {'defaultType': 'spot'}, 'enableRateLimit': True,
    })
    balances = ex.fetch_balance()
    markets = getattr(ex._exchange, 'markets', None) or ex._exchange.load_markets()

    plan = plan_flatten(balances, markets)
    print("\nPlan:")
    print(render_plan(plan))

    sellable = [o for o in plan if not o.skipped_reason]
    if not sellable:
        print("\nNothing to flatten.")
        return 0

    if not args.confirm:
        print(f"\nDry run — pass --confirm to actually submit {len(sellable)} order(s).")
        return 0

    print(f"\nSubmitting {len(sellable)} market sell order(s)...")
    result = execute_plan(ex, plan)
    print(f"\nDone. submitted={result['submitted']} errors={len(result['errors'])}")
    if result['errors']:
        for e in result['errors']:
            print(f"  ! {e}")
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
