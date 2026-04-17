from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from enum import Enum


class Venue(str, Enum):
    BINANCE = "binance"
    WALLET = "wallet"    # On-chain wallet (DEX venue)


@dataclass
class Balance:
    venue: Venue
    asset: str
    free: Decimal
    locked: Decimal = Decimal('0')

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class InventoryTracker:
    """
    Tracks positions across CEX and DEX venues.
    Single source of truth for where your money is.
    """

    def __init__(self, venues: list[Venue]):
        """Initialize tracker for given venues."""
        self._venues = venues
        # { venue -> { asset -> Balance } }
        self._balances: dict[Venue, dict[str, Balance]] = {v: {} for v in venues}

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def update_from_cex(self, venue: Venue, balances: dict):
        """
        Update balances from ExchangeClient.fetch_balance().
        Replaces previous snapshot for this venue.

        Args:
            venue:    Which CEX venue
            balances: {asset: {free, locked, total}} from ExchangeClient
        """
        self._balances[venue] = {
            asset: Balance(
                venue=venue,
                asset=asset,
                free=data['free'],
                locked=data['locked'],
            )
            for asset, data in balances.items()
        }

    def update_from_wallet(self, venue: Venue, balances: dict):
        """
        Update balances from on-chain wallet query.

        Args:
            venue:    Wallet venue
            balances: {asset: amount} from chain/ module
        """
        self._balances[venue] = {
            asset: Balance(
                venue=venue,
                asset=asset,
                free=Decimal(str(amount)),
            )
            for asset, amount in balances.items()
            if Decimal(str(amount)) > 0
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """
        Full portfolio snapshot at current time.

        Returns:
        {
            'timestamp': datetime,
            'venues': {
                'binance': {'ETH': {'free': ..., 'locked': ..., 'total': ...}, ...},
                'wallet':  {'ETH': {'free': ..., 'locked': ..., 'total': ...}, ...},
            },
            'totals': {
                'ETH':  Decimal('20.0'),
                'USDT': Decimal('40000.0'),
            },
        }
        Note: 'total_usd' requires a price feed and is omitted here.
        """
        venues_out: dict[str, dict] = {}
        for venue, assets in self._balances.items():
            venues_out[venue.value] = {
                asset: {
                    'free':   b.free,
                    'locked': b.locked,
                    'total':  b.total,
                }
                for asset, b in assets.items()
            }

        # Aggregate totals across all venues
        totals: dict[str, Decimal] = {}
        for assets in self._balances.values():
            for asset, b in assets.items():
                totals[asset] = totals.get(asset, Decimal('0')) + b.total

        return {
            'timestamp': datetime.now(timezone.utc),
            'venues': venues_out,
            'totals': totals,
        }

    def get_available(self, venue: Venue, asset: str) -> Decimal:
        """
        How much of `asset` is available to trade at `venue`.
        Returns free balance only (not locked in orders).
        """
        b = self._balances.get(venue, {}).get(asset)
        return b.free if b else Decimal('0')

    def can_execute(
        self,
        buy_venue: Venue,
        buy_asset: str,
        buy_amount: Decimal,
        sell_venue: Venue,
        sell_asset: str,
        sell_amount: Decimal,
    ) -> dict:
        """
        Pre-flight check: can we execute both legs of an arb?

        Returns:
        {
            'can_execute': bool,
            'buy_venue_available': Decimal,
            'buy_venue_needed': Decimal,
            'sell_venue_available': Decimal,
            'sell_venue_needed': Decimal,
            'reason': str | None,
        }
        """
        buy_available  = self.get_available(buy_venue, buy_asset)
        sell_available = self.get_available(sell_venue, sell_asset)

        reasons = []
        if buy_available < buy_amount:
            reasons.append(
                f"insufficient {buy_asset} at {buy_venue.value}: "
                f"have {buy_available}, need {buy_amount}"
            )
        if sell_available < sell_amount:
            reasons.append(
                f"insufficient {sell_asset} at {sell_venue.value}: "
                f"have {sell_available}, need {sell_amount}"
            )

        return {
            'can_execute':          len(reasons) == 0,
            'buy_venue_available':  buy_available,
            'buy_venue_needed':     buy_amount,
            'sell_venue_available': sell_available,
            'sell_venue_needed':    sell_amount,
            'reason':               '; '.join(reasons) if reasons else None,
        }

    def record_trade(
        self,
        venue: Venue,
        side: str,
        base_asset: str,
        quote_asset: str,
        base_amount: Decimal,
        quote_amount: Decimal,
        fee: Decimal,
        fee_asset: str,
    ):
        """
        Update internal balances after a trade executes.

        buy:  base increases, quote decreases, fee deducted from fee_asset.
        sell: base decreases, quote increases, fee deducted from fee_asset.
        """
        assets = self._balances.setdefault(venue, {})

        def _adjust(asset: str, delta: Decimal):
            if asset not in assets:
                assets[asset] = Balance(venue=venue, asset=asset, free=Decimal('0'))
            assets[asset].free += delta
            # Clamp to zero to avoid floating-point dust going negative
            if assets[asset].free < 0:
                assets[asset].free = Decimal('0')

        if side == 'buy':
            _adjust(base_asset,  +base_amount)
            _adjust(quote_asset, -quote_amount)
        else:  # sell
            _adjust(base_asset,  -base_amount)
            _adjust(quote_asset, +quote_amount)

        _adjust(fee_asset, -fee)

    # ------------------------------------------------------------------
    # Skew
    # ------------------------------------------------------------------

    def skew(self, asset: str) -> dict:
        """
        Calculate distribution skew for an asset across venues.

        Returns:
        {
            'asset': str,
            'total': Decimal,
            'venues': {
                'binance': {'amount': Decimal, 'pct': float, 'deviation_pct': float},
                'wallet':  {'amount': Decimal, 'pct': float, 'deviation_pct': float},
            },
            'max_deviation_pct': float,
            'needs_rebalance': bool,   # True if max_deviation > 30%
        }
        """
        n_venues = len(self._venues)
        target_pct = 100.0 / n_venues if n_venues else 0.0

        amounts = {
            v: self._balances.get(v, {}).get(asset, Balance(v, asset, Decimal('0'))).total
            for v in self._venues
        }
        total = sum(amounts.values())

        venues_out = {}
        max_deviation = 0.0

        for v in self._venues:
            amount = amounts[v]
            pct = float(amount / total * 100) if total else 0.0
            deviation = abs(pct - target_pct)
            max_deviation = max(max_deviation, deviation)
            venues_out[v.value] = {
                'amount':        amount,
                'pct':           pct,
                'deviation_pct': deviation,
            }

        return {
            'asset':            asset,
            'total':            total,
            'venues':           venues_out,
            'max_deviation_pct': max_deviation,
            'needs_rebalance':  max_deviation > 30.0,
        }

    def get_skews(self) -> list[dict]:
        """
        Check skew for ALL tracked assets.
        Returns one skew dict per unique asset found across all venues.
        """
        all_assets: set[str] = set()
        for assets in self._balances.values():
            all_assets.update(assets.keys())

        return [self.skew(asset) for asset in sorted(all_assets)]
