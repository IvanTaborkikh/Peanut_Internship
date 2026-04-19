"""
ArbLogger: appends every ArbChecker.check() result to a CSV file.

Usage:
    logger = ArbLogger('arb_log.csv')
    result = checker.check('ETH/USDT', size=1.0)
    logger.log(result, size=1.0)

CLI:
    python -m src.integration.arb_logger [--tail N] [--file PATH]
"""
import csv
from decimal import Decimal
from pathlib import Path

_FIELDS = [
    'timestamp', 'pair', 'size',
    'dex_price', 'cex_bid', 'cex_ask',
    'gap_bps', 'direction',
    'estimated_costs_bps', 'estimated_net_pnl_bps',
    'inventory_ok', 'executable',
    'dex_price_is_fallback',
]

_DEFAULT_PATH = 'arb_log.csv'


class ArbLogger:
    """
    Appends arb opportunity snapshots to a CSV file.
    Each row = one call to ArbChecker.check().
    """

    def __init__(self, filepath: str = _DEFAULT_PATH):
        self._path = Path(filepath)
        if not self._path.exists():
            with open(self._path, 'w', newline='') as f:
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()

    def log(self, result: dict, size: float = 1.0) -> None:
        """Append one check() result as a CSV row."""
        row = {
            'timestamp':              result['timestamp'].isoformat(),
            'pair':                   result['pair'],
            'size':                   size,
            'dex_price':              result['dex_price'],
            'cex_bid':                result['cex_bid'],
            'cex_ask':                result['cex_ask'],
            'gap_bps':                result['gap_bps'],
            'direction':              result['direction'] or '',
            'estimated_costs_bps':    result['estimated_costs_bps'],
            'estimated_net_pnl_bps':  result['estimated_net_pnl_bps'],
            'inventory_ok':           result['inventory_ok'],
            'executable':             result['executable'],
            'dex_price_is_fallback':  result['dex_price_is_fallback'],
        }
        with open(self._path, 'a', newline='') as f:
            csv.DictWriter(f, fieldnames=_FIELDS).writerow(row)

    def read_all(self) -> list[dict]:
        """Return all logged rows as a list of dicts."""
        if not self._path.exists():
            return []
        with open(self._path, newline='') as f:
            return list(csv.DictReader(f))

    def tail(self, n: int = 10) -> list[dict]:
        """Return the last N logged rows, newest first."""
        rows = self.read_all()
        return list(reversed(rows[-n:]))

    def summary(self) -> dict:
        """Aggregate stats over all logged opportunities."""
        rows = self.read_all()
        if not rows:
            return {
                'total_checks':    0,
                'executable':      0,
                'with_direction':  0,
                'avg_gap_bps':     Decimal('0'),
                'avg_net_pnl_bps': Decimal('0'),
                'best_gap_bps':    Decimal('0'),
            }

        total      = len(rows)
        executable = sum(1 for r in rows if r['executable'] == 'True')
        directed   = [r for r in rows if r['direction']]

        gaps    = [Decimal(r['gap_bps'])            for r in directed] if directed else [Decimal('0')]
        net_pnl = [Decimal(r['estimated_net_pnl_bps']) for r in rows]

        return {
            'total_checks':    total,
            'executable':      executable,
            'with_direction':  len(directed),
            'avg_gap_bps':     sum(gaps)    / len(gaps),
            'avg_net_pnl_bps': sum(net_pnl) / len(net_pnl),
            'best_gap_bps':    max(gaps),
        }


# ---------------------------------------------------------------------------
# CLI  —  python -m src.integration.arb_logger [--tail N] [--file PATH]
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    filepath = sys.argv[sys.argv.index('--file') + 1] if '--file' in sys.argv else _DEFAULT_PATH
    n        = int(sys.argv[sys.argv.index('--tail') + 1]) if '--tail' in sys.argv else 20

    logger = ArbLogger(filepath)
    W = 43

    s = logger.summary()
    print(f'\nArb Log: {filepath}')
    print('═' * W)
    print(f"  Total checks:     {s['total_checks']}")
    print(f"  With direction:   {s['with_direction']}")
    print(f"  Executable:       {s['executable']}")
    print(f"  Avg gap (bps):    {s['avg_gap_bps']:.2f}")
    print(f"  Avg net PnL:      {s['avg_net_pnl_bps']:.2f} bps")
    print(f"  Best gap:         {s['best_gap_bps']:.2f} bps")

    rows = logger.tail(n)
    if rows:
        print(f'\nLast {len(rows)} entries:')
        print('─' * W)
        for r in rows:
            ts        = r['timestamp'][11:16]   # HH:MM
            direction = r['direction'] or 'none'
            gap       = Decimal(r['gap_bps'])
            net       = Decimal(r['estimated_net_pnl_bps'])
            flag      = '✅' if r['executable'] == 'True' else '  '
            print(f"  {flag} {ts}  {r['pair']}  {direction:20}  gap={gap:.1f}  net={net:.1f} bps")
    else:
        print('\n  No entries yet.')
    print('═' * W)
