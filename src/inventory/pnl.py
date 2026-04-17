import csv
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.inventory.tracker import Venue


@dataclass
class TradeLeg:
    """Single execution leg."""
    id: str
    timestamp: datetime
    venue: Venue
    symbol: str        # "ETH/USDT"
    side: str          # "buy" or "sell"
    amount: Decimal    # Base asset qty
    price: Decimal     # Execution price
    fee: Decimal
    fee_asset: str


@dataclass
class ArbRecord:
    """Complete arb trade with both legs."""
    id: str
    timestamp: datetime
    buy_leg: TradeLeg
    sell_leg: TradeLeg
    gas_cost_usd: Decimal = Decimal('0')

    @property
    def notional(self) -> Decimal:
        """Trade size in quote currency (buy side cost before fees)."""
        return self.buy_leg.amount * self.buy_leg.price

    @property
    def gross_pnl(self) -> Decimal:
        """Sell revenue minus buy cost (price difference only)."""
        sell_revenue = self.sell_leg.amount * self.sell_leg.price
        buy_cost     = self.buy_leg.amount  * self.buy_leg.price
        return sell_revenue - buy_cost

    @property
    def total_fees(self) -> Decimal:
        """Sum of both leg fees plus gas cost."""
        return self.buy_leg.fee + self.sell_leg.fee + self.gas_cost_usd

    @property
    def net_pnl(self) -> Decimal:
        """Gross PnL minus all fees."""
        return self.gross_pnl - self.total_fees

    @property
    def net_pnl_bps(self) -> Decimal:
        """Net PnL expressed in basis points of notional."""
        if self.notional == 0:
            return Decimal('0')
        return self.net_pnl / self.notional * Decimal('10000')


class PnLEngine:
    """
    Tracks all arb trades and produces PnL reports.
    """

    def __init__(self):
        self.trades: list[ArbRecord] = []

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def record(self, trade: ArbRecord):
        """Record a completed arb trade."""
        self.trades.append(trade)

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """
        Aggregate PnL summary.

        Returns:
        {
            'total_trades':      int,
            'total_pnl_usd':     Decimal,
            'total_fees_usd':    Decimal,
            'avg_pnl_per_trade': Decimal,
            'avg_pnl_bps':       Decimal,
            'win_rate':          float,
            'best_trade_pnl':    Decimal,
            'worst_trade_pnl':   Decimal,
            'total_notional':    Decimal,
            'sharpe_estimate':   float,
            'pnl_by_hour':       dict,
        }
        """
        if not self.trades:
            return {
                'total_trades':      0,
                'total_pnl_usd':     Decimal('0'),
                'total_fees_usd':    Decimal('0'),
                'avg_pnl_per_trade': Decimal('0'),
                'avg_pnl_bps':       Decimal('0'),
                'win_rate':          0.0,
                'best_trade_pnl':    Decimal('0'),
                'worst_trade_pnl':   Decimal('0'),
                'total_notional':    Decimal('0'),
                'sharpe_estimate':   0.0,
                'pnl_by_hour':       {},
            }

        pnls      = [t.net_pnl for t in self.trades]
        total_pnl = sum(pnls)
        n         = len(self.trades)

        avg_pnl = total_pnl / n
        wins    = sum(1 for p in pnls if p > 0)

        avg_bps = sum(t.net_pnl_bps for t in self.trades) / n

        # Sharpe estimate: mean / stddev of per-trade PnL
        if n > 1:
            mean_f  = float(avg_pnl)
            std     = math.sqrt(sum((float(p) - mean_f) ** 2 for p in pnls) / (n - 1))
            sharpe  = mean_f / std if std > 0 else 0.0
        else:
            sharpe = 0.0

        # PnL grouped by hour
        pnl_by_hour: dict[int, Decimal] = {}
        for t in self.trades:
            h = t.timestamp.hour
            pnl_by_hour[h] = pnl_by_hour.get(h, Decimal('0')) + t.net_pnl

        return {
            'total_trades':      n,
            'total_pnl_usd':     total_pnl,
            'total_fees_usd':    sum(t.total_fees for t in self.trades),
            'avg_pnl_per_trade': avg_pnl,
            'avg_pnl_bps':       avg_bps,
            'win_rate':          wins / n * 100,
            'best_trade_pnl':    max(pnls),
            'worst_trade_pnl':   min(pnls),
            'total_notional':    sum(t.notional for t in self.trades),
            'sharpe_estimate':   sharpe,
            'pnl_by_hour':       pnl_by_hour,
        }

    def recent(self, n: int = 10) -> list[dict]:
        """
        Last N trades as summary dicts for CLI dashboard display.
        """
        return [
            {
                'id':            t.id,
                'timestamp':     t.timestamp.strftime('%H:%M'),
                'symbol':        t.buy_leg.symbol,
                'buy_venue':     t.buy_leg.venue.value,
                'sell_venue':    t.sell_leg.venue.value,
                'net_pnl_usd':   t.net_pnl,
                'net_pnl_bps':   t.net_pnl_bps,
                'profitable':    t.net_pnl > 0,
            }
            for t in self.trades[-n:][::-1]   # newest first
        ]

    def export_csv(self, filepath: str):
        """Export all trades to CSV for analysis."""
        fieldnames = [
            'id', 'timestamp',
            'symbol',
            'buy_venue',  'buy_price',  'buy_amount',  'buy_fee',
            'sell_venue', 'sell_price', 'sell_amount', 'sell_fee',
            'gas_cost_usd',
            'notional', 'gross_pnl', 'total_fees', 'net_pnl', 'net_pnl_bps',
        ]
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in self.trades:
                writer.writerow({
                    'id':            t.id,
                    'timestamp':     t.timestamp.isoformat(),
                    'symbol':        t.buy_leg.symbol,
                    'buy_venue':     t.buy_leg.venue.value,
                    'buy_price':     t.buy_leg.price,
                    'buy_amount':    t.buy_leg.amount,
                    'buy_fee':       t.buy_leg.fee,
                    'sell_venue':    t.sell_leg.venue.value,
                    'sell_price':    t.sell_leg.price,
                    'sell_amount':   t.sell_leg.amount,
                    'sell_fee':      t.sell_leg.fee,
                    'gas_cost_usd':  t.gas_cost_usd,
                    'notional':      t.notional,
                    'gross_pnl':     t.gross_pnl,
                    'total_fees':    t.total_fees,
                    'net_pnl':       t.net_pnl,
                    'net_pnl_bps':   t.net_pnl_bps,
                })


# ---------------------------------------------------------------------------
# CLI  —  python -m src.inventory.pnl [--summary | --recent N]
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    import random
    import logging
    logging.basicConfig(level=logging.WARNING)

    from src.inventory.tracker import Venue

    # Generate realistic demo trades so there's something to display
    random.seed(46)
    engine = PnLEngine()
    base_price = Decimal('2300')

    for i in range(10):
        ts    = datetime(2025, 2, 1, 14 - i // 3, 30 - (i % 3) * 8)
        delta = Decimal(str(random.uniform(-3, 20)))
        buy_p = base_price + Decimal(str(random.uniform(-1, 1)))
        sell_p = buy_p + delta
        fee    = Decimal('2')

        engine.record(ArbRecord(
            id=f'trade-{i+1:03d}',
            timestamp=ts,
            buy_leg=TradeLeg(
                id=f'buy-{i}', timestamp=ts, venue=Venue.WALLET,
                symbol='ETH/USDT', side='buy',
                amount=Decimal('1'), price=buy_p, fee=fee, fee_asset='USDT',
            ),
            sell_leg=TradeLeg(
                id=f'sell-{i}', timestamp=ts, venue=Venue.BINANCE,
                symbol='ETH/USDT', side='sell',
                amount=Decimal('1'), price=sell_p, fee=fee, fee_asset='USDT',
            ),
            gas_cost_usd=Decimal('5'),
        ))

    W = 43
    s = engine.summary()

    if '--recent' in sys.argv:
        n = int(sys.argv[sys.argv.index('--recent') + 1]) if sys.argv[-1] != '--recent' else 5
        print(f'\nRecent Trades (last {n}):')
        print('─' * W)
        for t in engine.recent(n):
            sign   = '+' if t['net_pnl_usd'] > 0 else ''
            flag   = '✓' if t['profitable'] else '✗'
            print(f"  {t['timestamp']}  {t['symbol']}  "
                  f"Buy {t['buy_venue']} / Sell {t['sell_venue']}  "
                  f"{sign}${t['net_pnl_usd']:.2f} ({t['net_pnl_bps']:.1f} bps) {flag}")
    else:  # default: --summary
        print(f'\nPnL Summary (demo — {s["total_trades"]} simulated trades)')
        print('═' * W)
        print(f"  Total Trades:    {s['total_trades']}")
        print(f"  Win Rate:        {s['win_rate']:.1f}%")
        print(f"  Total PnL:       ${s['total_pnl_usd']:.2f}")
        print(f"  Total Fees:      ${s['total_fees_usd']:.2f}")
        print(f"  Avg PnL/Trade:   ${s['avg_pnl_per_trade']:.2f}")
        print(f"  Avg PnL (bps):   {s['avg_pnl_bps']:.1f} bps")
        print(f"  Best Trade:      ${s['best_trade_pnl']:.2f}")
        print(f"  Worst Trade:     ${s['worst_trade_pnl']:.2f}")
        print(f"  Total Notional:  ${s['total_notional']:,.0f}")
        print(f"  Sharpe Est:      {s['sharpe_estimate']:.2f}")

        print('\nAll Trades:')
        for t in engine.recent(s['total_trades']):
            sign = '+' if t['net_pnl_usd'] > 0 else ''
            flag = '✓' if t['profitable'] else '✗'
            print(f"  {t['timestamp']}  ETH  Buy {t['buy_venue']} / Sell {t['sell_venue']}  "
                  f"{sign}${t['net_pnl_usd']:.2f} ({t['net_pnl_bps']:.1f} bps) {flag}")
