"""
ArbChecker: end-to-end opportunity detector.

Bridges Week-2 pricing (DEX) with Week-3 exchange/inventory (CEX).

Expected interface for pricing_engine:
    pricing_engine.get_dex_price(pair: str, size: float)
        -> {'price': Decimal, 'price_impact_bps': Decimal} | None

If pricing_engine is None or raises, DEX price falls back to CEX mid.
"""
from datetime import datetime, timezone
from decimal import Decimal

from src.exchange.orderbook import OrderBookAnalyzer
from src.inventory.tracker import Venue

# Uniswap V2 swap fee (hardcoded — same for all V2 pairs)
_DEX_FEE_BPS = Decimal('30')

# Conservative gas estimate for one arb round-trip (DEX swap + CEX order)
_DEFAULT_GAS_USD = Decimal('5')


class ArbChecker:
    """
    End-to-end arbitrage check: detect → validate → check inventory.
    Does NOT execute — just identifies opportunities.
    """

    def __init__(
        self,
        pricing_engine,
        exchange_client,
        inventory_tracker,
        pnl_engine,
        gas_cost_usd: Decimal = _DEFAULT_GAS_USD,
    ):
        """
        Args:
            pricing_engine:    Object with get_dex_price(pair, size) → dict | None.
                               Pass None to skip DEX price (CEX-only mode).
            exchange_client:   ExchangeClient instance.
            inventory_tracker: InventoryTracker instance.
            pnl_engine:        PnLEngine instance (used for future recording).
            gas_cost_usd:      Flat gas cost estimate per arb in USD.
        """
        self._pricing  = pricing_engine
        self._cex      = exchange_client
        self._tracker  = inventory_tracker
        self._pnl      = pnl_engine
        self._gas_usd  = gas_cost_usd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, pair: str, size: float = 1.0) -> dict:
        """
        Full arb check for a trading pair.

        Flow:
        1. Get CEX order book + fees
        2. Get DEX price from pricing_engine (optional)
        3. Determine direction and calculate gap
        4. Estimate all costs (fees, gas, slippage, price impact)
        5. Check inventory availability via tracker
        6. Return opportunity assessment

        Returns:
        {
            'pair':                   str,
            'timestamp':              datetime,
            'dex_price':              Decimal,
            'cex_bid':                Decimal,
            'cex_ask':                Decimal,
            'gap_bps':                Decimal,
            'direction':              'buy_dex_sell_cex' | 'buy_cex_sell_dex' | None,
            'estimated_costs_bps':    Decimal,
            'estimated_net_pnl_bps':  Decimal,
            'inventory_ok':           bool,
            'executable':             bool,
            'details': {
                'dex_price_impact_bps': Decimal,
                'cex_slippage_bps':     Decimal,
                'cex_fee_bps':          Decimal,
                'dex_fee_bps':          Decimal,
                'gas_cost_usd':         Decimal,
            },
        }
        """
        size_d    = Decimal(str(size))
        timestamp = datetime.now(timezone.utc)

        # ── 1. CEX order book ──────────────────────────────────────────
        ob_data = self._cex.fetch_order_book(pair)
        ob      = OrderBookAnalyzer(ob_data)

        cex_bid = ob_data['best_bid'][0]
        cex_ask = ob_data['best_ask'][0]
        mid     = ob_data['mid_price']

        # CEX fee in bps
        cex_fees    = self._cex.get_trading_fees(pair)
        cex_fee_bps = cex_fees['taker'] * Decimal('10000')

        # CEX slippage from walking the book for `size`
        sell_walk         = ob.walk_the_book('sell', size)
        buy_walk          = ob.walk_the_book('buy',  size)
        cex_sell_slip_bps = sell_walk['slippage_bps']
        cex_buy_slip_bps  = buy_walk['slippage_bps']

        # ── 2. DEX price ───────────────────────────────────────────────
        dex_price            = None
        dex_price_impact_bps = Decimal('0')

        if self._pricing is not None:
            try:
                result = self._pricing.get_dex_price(pair, size)
                if result:
                    dex_price            = Decimal(str(result['price']))
                    dex_price_impact_bps = Decimal(str(result.get('price_impact_bps', 0)))
            except Exception:
                dex_price = None

        # Fallback: no DEX price available → use CEX mid
        dex_price_is_fallback = dex_price is None
        if dex_price_is_fallback:
            dex_price = mid

        # ── 3. Direction & gap ─────────────────────────────────────────
        if dex_price < cex_bid:
            # Buy cheap on DEX, sell high on CEX
            direction        = 'buy_dex_sell_cex'
            gap_bps          = (cex_bid - dex_price) / dex_price * Decimal('10000')
            cex_slippage_bps = cex_sell_slip_bps
            execution_price  = dex_price
        elif cex_ask < dex_price:
            # Buy cheap on CEX, sell high on DEX
            direction        = 'buy_cex_sell_dex'
            gap_bps          = (dex_price - cex_ask) / cex_ask * Decimal('10000')
            cex_slippage_bps = cex_buy_slip_bps
            execution_price  = cex_ask
        else:
            direction        = None
            gap_bps          = Decimal('0')
            cex_slippage_bps = Decimal('0')
            execution_price  = mid

        # ── 4. Cost breakdown ──────────────────────────────────────────
        notional = size_d * execution_price
        gas_bps  = (
            self._gas_usd / notional * Decimal('10000')
            if notional > 0
            else Decimal('0')
        )

        estimated_costs_bps = (
            _DEX_FEE_BPS
            + dex_price_impact_bps
            + cex_fee_bps
            + cex_slippage_bps
            + gas_bps
        )
        estimated_net_pnl_bps = gap_bps - estimated_costs_bps

        # ── 5. Inventory check ─────────────────────────────────────────
        base_asset, quote_asset = pair.split('/')

        if direction == 'buy_dex_sell_cex':
            # Need quote (USDT) in wallet to buy on DEX
            # Need base (ETH) on Binance to sell simultaneously
            quote_needed     = size_d * dex_price
            inventory_result = self._tracker.can_execute(
                buy_venue=Venue.WALLET,  buy_asset=quote_asset, buy_amount=quote_needed,
                sell_venue=Venue.BINANCE, sell_asset=base_asset, sell_amount=size_d,
            )
        elif direction == 'buy_cex_sell_dex':
            # Need quote (USDT) on Binance to buy
            # Need base (ETH) in wallet to sell on DEX
            quote_needed     = size_d * cex_ask
            inventory_result = self._tracker.can_execute(
                buy_venue=Venue.BINANCE, buy_asset=quote_asset, buy_amount=quote_needed,
                sell_venue=Venue.WALLET, sell_asset=base_asset, sell_amount=size_d,
            )
        else:
            inventory_result = {
                'can_execute': False,
                'reason': 'No arbitrage direction — prices are within spread',
            }

        inventory_ok = inventory_result['can_execute']
        executable   = estimated_net_pnl_bps > 0 and inventory_ok

        return {
            'pair':                   pair,
            'timestamp':              timestamp,
            'dex_price':              dex_price,
            'dex_price_is_fallback':  dex_price_is_fallback,
            'execution_price':        execution_price,
            'cex_bid':                cex_bid,
            'cex_ask':                cex_ask,
            'gap_bps':                gap_bps,
            'direction':              direction,
            'estimated_costs_bps':    estimated_costs_bps,
            'estimated_net_pnl_bps':  estimated_net_pnl_bps,
            'inventory_ok':           inventory_ok,
            'executable':             executable,
            'details': {
                'dex_price_impact_bps': dex_price_impact_bps,
                'cex_slippage_bps':     cex_slippage_bps,
                'cex_fee_bps':          cex_fee_bps,
                'dex_fee_bps':          _DEX_FEE_BPS,
                'gas_cost_usd':         self._gas_usd,
            },
        }


# ---------------------------------------------------------------------------
# CLI  —  python -m src.integration.arb_checker PAIR [--size N]
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    import logging
    logging.basicConfig(level=logging.WARNING)

    from src.config import BINANCE_CONFIG
    from src.exchange.client import ExchangeClient
    from src.inventory.tracker import InventoryTracker
    from src.inventory.pnl import PnLEngine

    pair = sys.argv[1] if len(sys.argv) > 1 else 'ETH/USDT'
    size = float(sys.argv[sys.argv.index('--size') + 1]) if '--size' in sys.argv else 1.0

    client  = ExchangeClient(BINANCE_CONFIG)
    tracker = InventoryTracker(venues=[Venue.BINANCE, Venue.WALLET])
    tracker.update_from_cex(Venue.BINANCE, client.fetch_balance())
    tracker.update_from_wallet(Venue.WALLET, {
        pair.split('/')[0]: Decimal('5'),
        pair.split('/')[1]: Decimal('15000'),
    })

    checker = ArbChecker(
        pricing_engine=None,   # No DEX pricer — uses CEX mid as fallback
        exchange_client=client,
        inventory_tracker=tracker,
        pnl_engine=PnLEngine(),
    )

    r      = checker.check(pair, size=size)
    base_asset, quote_asset = pair.split('/')
    size_d = Decimal(str(size))
    W      = 43

    verdict = '✅ PROFITABLE' if r['executable'] else '❌ NOT PROFITABLE'
    reason  = 'Execute!' if r['executable'] else (
        'costs exceed gap' if r['estimated_net_pnl_bps'] <= 0 else 'inventory insufficient'
    )

    print(f'\n{"═" * W}')
    print(f'  ARB CHECK: {pair} (size: {size} {base_asset})')
    print(f'{"═" * W}\n')

    # Show source of DEX price so the operator knows if it is real or an estimate
    dex_label = 'DEX (mid fallback)' if r['dex_price_is_fallback'] else 'Uniswap V2'

    print('Prices:')
    if r['direction'] == 'buy_dex_sell_cex':
        print(f'  {dex_label}:      ${r["dex_price"]:>10,.2f}  (buy {size} {base_asset})')
        print(f'  Binance bid:      ${r["cex_bid"]:>10,.2f}')
    elif r['direction'] == 'buy_cex_sell_dex':
        print(f'  Binance ask:      ${r["cex_ask"]:>10,.2f}  (buy {size} {base_asset})')
        print(f'  {dex_label}:      ${r["dex_price"]:>10,.2f}')
    else:
        print(f'  {dex_label}:      ${r["dex_price"]:>10,.2f}')
        print(f'  Binance bid:      ${r["cex_bid"]:>10,.2f}')
        print(f'  Binance ask:      ${r["cex_ask"]:>10,.2f}')

    gap_usd = abs(r['dex_price'] - r['cex_bid']) if r['direction'] == 'buy_dex_sell_cex' else \
              abs(r['dex_price'] - r['cex_ask']) if r['direction'] == 'buy_cex_sell_dex' else Decimal('0')
    print(f'\nGap: ${gap_usd:.2f} ({r["gap_bps"]:.1f} bps)\n')

    d = r['details']
    # Gas bps uses execution_price (actual cost basis): dex_price when buying on DEX,
    # cex_ask when buying on CEX — matching the notional used in check().
    exec_price = r['execution_price']
    notional   = size_d * exec_price if exec_price > 0 else Decimal('1')
    gas_bps    = d['gas_cost_usd'] / notional * Decimal('10000') if notional > 0 else Decimal('0')
    print('Costs:')
    print(f'  DEX fee:           {d["dex_fee_bps"]:.1f} bps')
    print(f'  DEX price impact:  {d["dex_price_impact_bps"]:.1f} bps')
    print(f'  CEX fee:           {d["cex_fee_bps"]:.1f} bps')
    print(f'  CEX slippage:      {d["cex_slippage_bps"]:.1f} bps')
    print(f'  Gas:               ${d["gas_cost_usd"]:.2f} ({gas_bps:.1f} bps)')
    print(f'  {"─" * 24}')
    print(f'  Total costs:       {r["estimated_costs_bps"]:.1f} bps\n')

    print(f'Net PnL estimate: {r["estimated_net_pnl_bps"]:.1f} bps  {verdict}\n')

    print('Inventory:')
    if r['direction'] == 'buy_dex_sell_cex':
        # Buy on DEX (need quote in wallet), sell on CEX (need base on Binance)
        need_quote   = size_d * r['dex_price']
        wallet_quote = tracker.get_available(Venue.WALLET,  quote_asset)
        binance_base = tracker.get_available(Venue.BINANCE, base_asset)
        print(f'  Wallet {quote_asset}:  {wallet_quote:>10,.0f}  (need ~{need_quote:,.0f})  {"✅" if wallet_quote >= need_quote else "❌"}')
        print(f'  Binance {base_asset}:  {binance_base:>10,.1f}   (need {size_d:.1f})     {"✅" if binance_base >= size_d else "❌"}')
    elif r['direction'] == 'buy_cex_sell_dex':
        # Buy on CEX (need quote on Binance), sell on DEX (need base in wallet)
        need_quote    = size_d * r['cex_ask']
        binance_quote = tracker.get_available(Venue.BINANCE, quote_asset)
        wallet_base   = tracker.get_available(Venue.WALLET,  base_asset)
        print(f'  Binance {quote_asset}:  {binance_quote:>10,.0f}  (need ~{need_quote:,.0f})  {"✅" if binance_quote >= need_quote else "❌"}')
        print(f'  Wallet {base_asset}:  {wallet_base:>10,.1f}   (need {size_d:.1f})     {"✅" if wallet_base >= size_d else "❌"}')
    else:
        print('  N/A — no arb direction')

    print(f'\nVerdict: {"EXECUTE" if r["executable"] else "SKIP"} — {reason}')
    print(f'{"═" * W}')
