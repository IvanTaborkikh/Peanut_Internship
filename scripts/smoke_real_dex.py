"""Real DEX quote smoke test — proves the pricing module works end-to-end
on actual Uniswap V2 mainnet pools, alongside Binance order book.

Run:    PYTHONPATH=. python scripts/smoke_real_dex.py
Needs:  MAINNET_RPC_URL in .env (Alchemy/Infura/etc).
        Optional: BINANCE_TESTNET_API_KEY/SECRET to also fetch CEX side.

Output: per pair, prints CEX bid/ask, DEX spot price (both directions),
        spread vs each CEX side, and whether a profitable arb signal would
        survive our breakeven_bps threshold.
"""
import logging
import os
from decimal import Decimal

from dotenv import load_dotenv

from src.chain.client import ChainClient
from src.configs.schema import ChainId
from src.configs.tokens import get_token
from src.core.types import Address
from src.pricing.uniswap_v2_pair import UniswapV2Pair


# Known liquid Uniswap V2 pools (mainnet). Arbitrum V2 is thin — Sushi/Camelot
# dominate there. For Arbitrum we'd swap pricing module to a different DEX.
KNOWN_POOLS: dict[tuple[ChainId, str], Address] = {
    (ChainId.ETH_MAINNET, 'WETH/USDT'): Address('0x0d4a11d5EEaaC28EC3F61d100daF4d40471f1852'),
    (ChainId.ETH_MAINNET, 'WETH/USDC'): Address('0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc'),
}


def fetch_dex_quote(client: ChainClient, pool_address: Address,
                    base_sym: str, quote_sym: str, chain_id: ChainId,
                    size_human: Decimal) -> dict:
    """Read pool reserves from chain, compute amount-out for size_human base."""
    pair = UniswapV2Pair.from_chain(pool_address, client)
    base = get_token(chain_id, base_sym)
    quote = get_token(chain_id, quote_sym)

    amount_in_wei = int(size_human * 10 ** base.decimals)

    # base → quote (sell base on DEX)
    out_quote_wei = pair.get_amount_out(amount_in_wei, base)
    dex_sell = Decimal(out_quote_wei) / Decimal(10 ** quote.decimals) / size_human

    # quote → base (buy base on DEX) — use same notional
    quote_in_wei = int(size_human * dex_sell * 10 ** quote.decimals)
    out_base_wei = pair.get_amount_out(quote_in_wei, quote)
    base_out_human = Decimal(out_base_wei) / Decimal(10 ** base.decimals)
    dex_buy = (size_human * dex_sell) / base_out_human if base_out_human else dex_sell

    # Mid from raw reserves
    if pair.token0 == base:
        mid = Decimal(pair.reserve1) / Decimal(10 ** quote.decimals) \
              / (Decimal(pair.reserve0) / Decimal(10 ** base.decimals))
    else:
        mid = Decimal(pair.reserve0) / Decimal(10 ** quote.decimals) \
              / (Decimal(pair.reserve1) / Decimal(10 ** base.decimals))

    return {'dex_buy': dex_buy, 'dex_sell': dex_sell, 'dex_mid': mid}


def fetch_cex_book(pair_str: str) -> dict | None:
    api_key = os.getenv('BINANCE_TESTNET_API_KEY')
    secret = os.getenv('BINANCE_TESTNET_SECRET')
    if not api_key or not secret:
        return None
    try:
        from src.exchange.client import ExchangeClient
        ex = ExchangeClient({
            'apiKey': api_key, 'secret': secret, 'sandbox': True,
            'options': {'defaultType': 'spot'}, 'enableRateLimit': True,
        })
        ob = ex.fetch_order_book(pair_str)
        return {'bid': Decimal(str(ob['bids'][0][0])), 'ask': Decimal(str(ob['asks'][0][0]))}
    except Exception as e:
        return {'error': str(e)}


def report_pair(chain_id: ChainId, pair_str: str, size: Decimal, rpc_url: str):
    base_sym, quote_sym = pair_str.split('/')
    pool = KNOWN_POOLS.get((chain_id, pair_str))
    if pool is None:
        print(f"  ✗ no Uniswap V2 pool registered for {pair_str} on {chain_id.name}")
        return

    client = ChainClient([rpc_url])
    dex = fetch_dex_quote(client, pool, base_sym, quote_sym, chain_id, size)
    cex_pair = pair_str.replace('WETH/', 'ETH/')
    cex = fetch_cex_book(cex_pair)

    print(f"\n  Pool {pool.checksum} (V2)")
    print(f"  DEX  buy = {dex['dex_buy']:.4f}   sell = {dex['dex_sell']:.4f}   mid = {dex['dex_mid']:.4f}")

    if cex is None:
        print("  CEX  (skipped — no Binance testnet keys in .env)")
        return
    if 'error' in cex:
        print(f"  CEX  error: {cex['error']}")
        return

    print(f"  CEX  bid = {cex['bid']:.4f}   ask = {cex['ask']:.4f}")

    # spreads in bps:
    spread_buy_cex_sell_dex = (dex['dex_sell'] - cex['ask']) / cex['ask'] * Decimal(10_000)
    spread_buy_dex_sell_cex = (cex['bid'] - dex['dex_buy']) / dex['dex_buy'] * Decimal(10_000)
    print(f"  Spread (buy CEX → sell DEX): {spread_buy_cex_sell_dex:+.1f} bps")
    print(f"  Spread (buy DEX → sell CEX): {spread_buy_dex_sell_cex:+.1f} bps")

    breakeven = 75  # see docs/strategy_review.md
    best = max(spread_buy_cex_sell_dex, spread_buy_dex_sell_cex)
    verdict = "✓ above breakeven (signal would emit)" if best > breakeven else "✗ below breakeven"
    print(f"  Best spread: {best:+.1f} bps   → {verdict}")


def main():
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()

    mainnet_rpc = os.getenv('MAINNET_RPC_URL')
    if not mainnet_rpc:
        print("MAINNET_RPC_URL not set in .env — cannot fetch real DEX data.")
        return 1

    print("=" * 70)
    print(" Real DEX smoke test — Uniswap V2 mainnet vs Binance testnet")
    print("=" * 70)

    print("\n[Ethereum mainnet]")
    report_pair(ChainId.ETH_MAINNET, 'WETH/USDT', Decimal('1'), mainnet_rpc)
    report_pair(ChainId.ETH_MAINNET, 'WETH/USDC', Decimal('1'), mainnet_rpc)

    arb_rpc = os.getenv('RPC_URL')
    if arb_rpc:
        print("\n[Arbitrum]")
        print("  Skipping — Uniswap V2 has thin liquidity on Arbitrum;")
        print("  Sushi/Camelot dominate. Plug those in via a different pricer.")
    print()


if __name__ == '__main__':
    raise SystemExit(main() or 0)
