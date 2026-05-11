from dataclasses import dataclass
from decimal import Decimal

from src.inventory.tracker import Venue, InventoryTracker


TRANSFER_FEES: dict[str, dict] = {
    'ETH': {
        'withdrawal_fee':    Decimal('0.005'),
        'min_withdrawal':    Decimal('0.01'),
        'confirmations':     12,
        'estimated_time_min': 15,
    },
    'USDT': {
        'withdrawal_fee':    Decimal('1.0'),
        'min_withdrawal':    Decimal('10.0'),
        'confirmations':     12,
        'estimated_time_min': 15,
    },
    'USDC': {
        'withdrawal_fee':    Decimal('1.0'),
        'min_withdrawal':    Decimal('10.0'),
        'confirmations':     12,
        'estimated_time_min': 15,
    },
}

MIN_OPERATING_BALANCE: dict[str, Decimal] = {
    'ETH':  Decimal('0.5'),
    'USDT': Decimal('500'),
    'USDC': Decimal('500'),
}


@dataclass
class TransferPlan:
    """A planned transfer between venues."""
    from_venue: Venue
    to_venue: Venue
    asset: str
    amount: Decimal
    estimated_fee: Decimal
    estimated_time_min: int

    @property
    def net_amount(self) -> Decimal:
        """Amount received after fees."""
        return self.amount - self.estimated_fee


class RebalancePlanner:
    """
    Generates rebalancing plans when inventory skew exceeds threshold.
    Plans only — does NOT execute transfers.
    """

    def __init__(
        self,
        tracker: InventoryTracker,
        threshold_pct: float = 30.0,
        target_ratio: dict[Venue, float] | None = None,
    ):
        self._tracker = tracker
        self._threshold_pct = threshold_pct

        # Default: equal split across all venues
        venues = tracker._venues
        if target_ratio is None:
            equal = 100.0 / len(venues) if venues else 0.0
            self._target_ratio: dict[Venue, float] = {v: equal for v in venues}
        else:
            self._target_ratio = target_ratio

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(self) -> list[dict]:
        """
        Check all tracked assets for skew.

        Returns:
        [
            {'asset': 'ETH',  'max_deviation_pct': 42.5, 'needs_rebalance': True},
            {'asset': 'USDT', 'max_deviation_pct': 15.2, 'needs_rebalance': False},
        ]
        """
        return [
            {
                'asset':             s['asset'],
                'max_deviation_pct': s['max_deviation_pct'],
                'needs_rebalance':   s['needs_rebalance'],
            }
            for s in self._tracker.get_skews()
        ]

    def plan(self, asset: str) -> list[TransferPlan]:
        """
        Generate transfer plan to rebalance a specific asset.

        Rules:
        - Only generate transfers that reduce skew
        - Respect minimum transfer amounts
        - Account for transfer fees
        - Never plan a transfer that leaves a venue below minimum operating balance

        Returns list of TransferPlan (empty if no rebalance needed).
        """
        skew = self._tracker.skew(asset)

        if not skew['needs_rebalance']:
            return []

        total = skew['total']
        if total == 0:
            return []

        fee_info = TRANSFER_FEES.get(asset)
        min_balance = MIN_OPERATING_BALANCE.get(asset, Decimal('0'))

        # Find from_venue (has excess) and to_venue (is deficient)
        venue_amounts = {
            Venue(v): info['amount']
            for v, info in skew['venues'].items()
        }
        targets = {
            v: total * Decimal(str(self._target_ratio[v])) / Decimal('100')
            for v in venue_amounts
        }

        # Venue with most excess above target → sender
        from_venue = max(venue_amounts, key=lambda v: venue_amounts[v] - targets[v])
        to_venue   = min(venue_amounts, key=lambda v: venue_amounts[v] - targets[v])

        excess = venue_amounts[from_venue] - targets[from_venue]

        # How much can we safely send without dropping below min operating balance
        available_to_send = venue_amounts[from_venue] - min_balance
        if available_to_send <= 0:
            return []

        amount = min(excess, available_to_send)

        # Check minimum withdrawal
        if fee_info:
            min_withdrawal = fee_info['min_withdrawal']
            if amount < min_withdrawal:
                return []

        fee = fee_info['withdrawal_fee'] if fee_info else Decimal('0')
        eta = fee_info['estimated_time_min'] if fee_info else 0

        # Net amount must be positive after fee
        if amount <= fee:
            return []

        return [
            TransferPlan(
                from_venue=from_venue,
                to_venue=to_venue,
                asset=asset,
                amount=amount,
                estimated_fee=fee,
                estimated_time_min=eta,
            )
        ]

    def plan_all(self) -> dict[str, list[TransferPlan]]:
        """
        Generate rebalancing plans for ALL skewed assets.
        Returns {asset: [TransferPlan, ...]}
        """
        result = {}
        for item in self.check_all():
            if item['needs_rebalance']:
                plans = self.plan(item['asset'])
                if plans:
                    result[item['asset']] = plans
        return result

    def estimate_cost(self, plans: list[TransferPlan]) -> dict:
        """
        Estimate total cost of executing rebalance plans.

        Returns:
        {
            'total_transfers': int,
            'total_fees_usd': Decimal,   # Sum of all fees (assumes fee in USD-equiv)
            'total_time_min': int,        # Max time (transfers run in parallel)
            'assets_affected': list[str],
        }
        """
        if not plans:
            return {
                'total_transfers':  0,
                'total_fees_usd':   Decimal('0'),
                'total_time_min':   0,
                'assets_affected':  [],
            }

        return {
            'total_transfers': len(plans),
            'total_fees_usd':  sum(p.estimated_fee for p in plans),
            'total_time_min':  max(p.estimated_time_min for p in plans),
            'assets_affected': list({p.asset for p in plans}),
        }


# ---------------------------------------------------------------------------
# CLI  —  python -m src.inventory.rebalancer [--check | --plan ASSET]
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import os
    import sys
    import logging
    logging.basicConfig(level=logging.WARNING)

    from dotenv import load_dotenv
    load_dotenv()

    from src.config import BINANCE_CONFIG
    from src.exchange.client import ExchangeClient

    client  = ExchangeClient(BINANCE_CONFIG)
    tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])

    # Load real balances from both venues.
    tracker.update_from_cex(Venue.BINANCE, client.fetch_balance())

    # Real on-chain wallet balances via web3 (same approach as
    # scripts/arb_bot.py:_fetch_wallet_balances). Reads PRIVATE_KEY +
    # ARBITRUM_RPC_URL from env; falls back to empty wallet on misconfig.
    wallet_balances: dict = {}
    pk = os.getenv('PRIVATE_KEY')
    rpc = os.getenv('ARBITRUM_RPC_URL')
    if pk and rpc:
        from web3 import Web3
        from eth_account import Account
        from src.configs.tokens import get_token
        from src.configs.schema import ChainId

        erc20_abi = [{
            'name': 'balanceOf', 'type': 'function', 'stateMutability': 'view',
            'inputs': [{'type': 'address', 'name': 'owner'}],
            'outputs': [{'type': 'uint256'}],
        }]
        w3 = Web3(Web3.HTTPProvider(rpc))
        addr = Account.from_key(pk).address
        try:
            wallet_balances['ETH'] = float(
                Decimal(str(w3.eth.get_balance(addr))) / Decimal('1000000000000000000')
            )
        except Exception:
            pass
        for sym in ('USDC', 'CHIP'):
            try:
                token = get_token(ChainId.ARBITRUM, sym)
                contract = w3.eth.contract(address=token.address.checksum, abi=erc20_abi)
                raw = contract.functions.balanceOf(addr).call()
                wallet_balances[sym] = float(
                    Decimal(str(raw)) / Decimal(10 ** token.decimals)
                )
            except Exception:
                pass

    tracker.update_from_wallet(Venue.WALLET, wallet_balances)

    planner = RebalancePlanner(tracker)
    W = 43

    if '--plan' in sys.argv:
        asset = sys.argv[sys.argv.index('--plan') + 1]
        plans = planner.plan(asset)
        print(f'\nRebalance Plan: {asset}')
        print('─' * W)
        if not plans:
            print('  No rebalance needed.')
        for i, p in enumerate(plans, 1):
            print(f'Transfer {i}:')
            print(f'  From:   {p.from_venue.value}')
            print(f'  To:     {p.to_venue.value}')
            print(f'  Amount: {p.amount} {p.asset}')
            print(f'  Fee:    {p.estimated_fee} {p.asset}')
            print(f'  Net:    {p.net_amount} {p.asset}')
            print(f'  ETA:    ~{p.estimated_time_min} min')

            # Projected balances after transfer
            from_bal = tracker.get_available(p.from_venue, p.asset)
            to_bal   = tracker.get_available(p.to_venue,   p.asset)
            proj_from = from_bal - p.amount
            proj_to   = to_bal   + p.net_amount
            proj_total = proj_from + proj_to
            pct_from = proj_from / proj_total * 100 if proj_total else Decimal('0')
            pct_to   = proj_to   / proj_total * 100 if proj_total else Decimal('0')
            print('\n  Result:')
            print(f'    {p.from_venue.value.capitalize():8}: {proj_from:.3f} {p.asset} ({pct_from:.0f}%)')
            print(f'    {p.to_venue.value.capitalize():8}: {proj_to:.3f} {p.asset} ({pct_to:.0f}%)')

        cost = planner.estimate_cost(plans)
        if plans:
            print(f'\nEstimated total fee: {cost["total_fees_usd"]} (max {cost["total_time_min"]} min)')

    else:  # default: --check
        print('\nInventory Skew Report')
        print('═' * W)
        skews = planner.check_all()
        for item in skews:
            s = tracker.skew(item['asset'])
            on_both = all(info['amount'] > 0 for info in s['venues'].values())
            if not on_both:
                continue
            status = '⚠  NEEDS REBALANCE' if item['needs_rebalance'] else '✓  OK'
            print(f'\nAsset: {item["asset"]}')
            for venue, info in s['venues'].items():
                dev = f'{info["deviation_pct"]:+.0f}%'
                print(f'  {venue:8}: {info["amount"]:>10} ({info["pct"]:.0f}%)  ← deviation: {dev}')
            print(f'  Status: {status} (max deviation: {item["max_deviation_pct"]:.1f}%)')
        print('\n' + '═' * W)
