import logging
import time
from decimal import Decimal
from typing import Optional

from src.configs.schema import ChainId
from src.configs.tokens import get_token
from src.strategy.signal import Signal, Direction
from src.strategy.fees import FeeStructure
from src.inventory.tracker import Venue


class SignalGenerator:
    def __init__(self, exchange_client, pricing_module, inventory_tracker,
                 fee_structure: FeeStructure, config: dict,
                 chain_id: ChainId = ChainId.ARBITRUM,
                 dex_quote_override: Optional[dict] = None,
                 stablecoin_converter=None):
        self.exchange = exchange_client
        self.pricing = pricing_module
        self.inventory = inventory_tracker
        self.fees = fee_structure
        self.chain_id = chain_id

        self.min_spread_bps = Decimal(str(config.get('min_spread_bps', 50)))
        self.min_profit_usd = Decimal(str(config.get('min_profit_usd', 5.0)))
        self.max_position_usd = Decimal(str(config.get('max_position_usd', 10_000)))
        self.signal_ttl = config.get('signal_ttl_seconds', 5)
        self.cooldown = config.get('cooldown_seconds', 2)

        # Cross-stablecoin handling: when CEX pair quote (e.g. USDT) differs from
        # DEX-side pool quote (e.g. USDC), translate the symbol via override AND
        # convert prices via the live ratio (StablecoinConverter).
        self.dex_quote_override: dict = dex_quote_override or {}
        self.stablecoin_converter = stablecoin_converter

        self.last_signal_time: dict[str, float] = {}

    def generate(self, pair: str, size: Decimal) -> Optional[Signal]:
        """
        Attempt to generate a signal for the given pair and size.
        Returns Signal if opportunity found and validated, None otherwise.
        """
        if self._in_cooldown(pair):
            return None

        prices = self._fetch_prices(pair, size)
        if prices is None:
            return None

        # Calculate spreads both directions
        spread_a = (prices['dex_sell'] - prices['cex_ask']) / prices['cex_ask'] * 10_000
        spread_b = (prices['cex_bid'] - prices['dex_buy']) / prices['dex_buy'] * 10_000

        logging.info(
            "SPREAD | pair=%s | cex=%.6g/%.6g | dex_sell=%.6g dex_buy=%.6g | "
            "spread_a=%.1fbps spread_b=%.1fbps | min=%.1fbps",
            pair,
            float(prices['cex_bid']), float(prices['cex_ask']),
            float(prices['dex_sell']), float(prices['dex_buy']),
            float(spread_a), float(spread_b), float(self.min_spread_bps),
        )

        # Pick better direction
        if spread_a > spread_b and spread_a >= self.min_spread_bps:
            direction = Direction.BUY_CEX_SELL_DEX
            spread, cex_price, dex_price = spread_a, prices['cex_ask'], prices['dex_sell']
        elif spread_b >= self.min_spread_bps:
            direction = Direction.BUY_DEX_SELL_CEX
            spread, cex_price, dex_price = spread_b, prices['cex_bid'], prices['dex_buy']
        else:
            return None

        # Economics
        trade_value = size * cex_price
        gross_pnl = (spread / 10_000) * trade_value
        fees = (self.fees.total_fee_bps(trade_value) / 10_000) * trade_value
        net_pnl = gross_pnl - fees

        if net_pnl < self.min_profit_usd:
            return None

        # Validation
        inventory_ok = self._check_inventory(pair, direction, size, cex_price)
        within_limits = trade_value <= self.max_position_usd

        signal = Signal.create(
            pair=pair, direction=direction,
            cex_price=cex_price, dex_price=dex_price, spread_bps=spread,
            size=size, expected_gross_pnl=gross_pnl, expected_fees=fees,
            expected_net_pnl=net_pnl, score=0,
            expiry=time.time() + self.signal_ttl,
            inventory_ok=inventory_ok, within_limits=within_limits,
        )

        self.last_signal_time[pair] = time.time()
        return signal

    def _in_cooldown(self, pair: str) -> bool:
        return time.time() - self.last_signal_time.get(pair, 0) < self.cooldown

    def _fetch_prices(self, pair: str, size: Decimal) -> Optional[dict]:
        """
        Fetch CEX order book and DEX price from PricingEngine (Week 2).

        If PricingEngine is not available (e.g., testing without Week 2 module),
        falls back to simulated DEX prices. Mark your fallback clearly in logs.
        """
        try:
            ob = self.exchange.fetch_order_book(pair)
            cex_bid = Decimal(str(ob['bids'][0][0]))
            cex_ask = Decimal(str(ob['asks'][0][0]))

            if self.pricing is not None:
                token_in, token_out = self._pair_to_tokens(pair)
                gas_price = 1  # Arbitrum gas is cheap
                mid = (cex_bid + cex_ask) / 2

                # dex_sell: sell size base tokens → receive quote tokens
                sell_amt = int(size * 10**token_in.decimals)
                sell_quote = self.pricing.get_quote(token_in, token_out, sell_amt, gas_price)
                dex_sell = Decimal(sell_quote.expected_output) / Decimal(10**token_out.decimals) / size

                # dex_buy: spend quote tokens to receive size base tokens (estimate USDT via mid)
                usdt_in = int(size * mid * 10**token_out.decimals)
                buy_quote = self.pricing.get_quote(token_out, token_in, usdt_in, gas_price)
                base_out = Decimal(buy_quote.expected_output) / Decimal(10**token_in.decimals)
                dex_buy = Decimal(usdt_in) / Decimal(10**token_out.decimals) / base_out if base_out else mid

                # Cross-stablecoin conversion: when CEX quote symbol differs from
                # DEX-side symbol (e.g. CEX=USDT, DEX=USDC), translate dex_sell/buy
                # into CEX-quote units using the live exchange rate.
                _, cex_quote_sym = pair.split('/')
                dex_quote_sym = self.dex_quote_override.get(cex_quote_sym, cex_quote_sym)
                if dex_quote_sym != cex_quote_sym and self.stablecoin_converter is not None:
                    try:
                        ratio = self.stablecoin_converter.current_ratio()  # USDC value in USDT
                    except Exception as e:
                        logging.warning("stablecoin ratio fetch failed: %s — using 1:1", e)
                        ratio = Decimal('1')
                    if (cex_quote_sym, dex_quote_sym) == ('USDT', 'USDC'):
                        # DEX prices are in USDC; convert to USDT (multiply by ratio).
                        dex_sell = dex_sell * ratio
                        dex_buy = dex_buy * ratio
                    elif (cex_quote_sym, dex_quote_sym) == ('USDC', 'USDT'):
                        if ratio != 0:
                            dex_sell = dex_sell / ratio
                            dex_buy = dex_buy / ratio
            else:
                # STUB: Simulated DEX prices for testing without Week 2
                mid = (cex_bid + cex_ask) / 2
                dex_buy = mid * Decimal('1.005')
                dex_sell = mid * Decimal('1.008')

            return {
                'cex_bid': cex_bid, 'cex_ask': cex_ask,
                'dex_buy': dex_buy, 'dex_sell': dex_sell,
            }
        except Exception as e:
            logging.warning("_fetch_prices failed for %s: %s", pair, e, exc_info=True)
            return None

    def _pair_to_tokens(self, pair: str) -> tuple:
        base, quote = pair.split('/')
        # When the CEX pair quote (e.g. USDT) doesn't have a DEX pool but a
        # cousin stablecoin does (e.g. USDC), look up the substitute symbol.
        quote = self.dex_quote_override.get(quote, quote)
        return get_token(self.chain_id, base), get_token(self.chain_id, quote)

    def _check_inventory(self, pair, direction, size, price) -> bool:
        base, quote = pair.split('/')
        if direction == Direction.BUY_CEX_SELL_DEX:
            cex_ok = self.inventory.get_available(Venue.BINANCE, quote) >= size * price * Decimal('1.01')
            if self.pricing is None:  # CEX-only mode — no wallet required
                return cex_ok
            return cex_ok and self.inventory.get_available(Venue.WALLET, base) >= size
        else:
            cex_ok = self.inventory.get_available(Venue.BINANCE, base) >= size
            if self.pricing is None:
                return cex_ok
            return cex_ok and self.inventory.get_available(Venue.WALLET, quote) >= size * price * Decimal('1.01')