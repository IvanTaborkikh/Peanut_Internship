"""
CLI for price impact analysis.

Usage:
    python -m src.pricing.impact_analyzer <pair_address> \\
        --token-in USDC \\
        --sizes 1000,10000,100000,1000000 \\
        --rpc https://mainnet.infura.io/v3/...

    # Without RPC (offline demo with hardcoded reserves):
    python -m src.pricing.impact_analyzer demo
"""

import argparse
import sys
from decimal import Decimal

from src.core.types import Address, Token
from src.pricing.PriceImpactAnalyzer import PriceImpactAnalyzer
from src.pricing.UniswapV2Pair import UniswapV2Pair

# ── Demo pools (no RPC needed) ────────────────────────────────────────────────

_DEMO_TOKENS = {
    "WETH": Token(Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), "WETH", 18),
    "USDC": Token(Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), "USDC", 6),
    "DAI":  Token(Address("0x6B175474E89094C44Da98b954EedeAC495271d0F"), "DAI",  18),
}

_DEMO_PAIRS: dict[str, UniswapV2Pair] = {
    "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc": UniswapV2Pair(
        address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
        token0=_DEMO_TOKENS["WETH"],
        token1=_DEMO_TOKENS["USDC"],
        reserve0=1_000 * 10**18,
        reserve1=2_000_000 * 10**6,
    ),
}


def _fmt_amount(raw: int, decimals: int) -> str:
    human = Decimal(raw) / Decimal(10**decimals)
    if human >= 1_000_000:
        return f"{human:,.0f}"
    if human >= 1_000:
        return f"{human:,.2f}"
    return f"{human:,.4f}"


def _human_exec_price(amount_in: int, amount_out: int, token_in: Token, token_out: Token) -> Decimal:
    """Return execution price in human units: token_out per token_in."""
    if amount_in == 0:
        return Decimal(0)
    human_in  = Decimal(amount_in)  / Decimal(10**token_in.decimals)
    human_out = Decimal(amount_out) / Decimal(10**token_out.decimals)
    if human_in == 0:
        return Decimal(0)
    return human_out / human_in


def _print_table(rows: list[dict], token_in: Token, token_out: Token) -> None:
    col_in   = f"{token_in.symbol} In"
    col_out  = f"{token_out.symbol} Out"
    col_exec = f"{token_out.symbol}/{token_in.symbol}"
    col_imp  = "Impact"

    w0, w1, w2, w3 = 14, 14, 14, 9

    top     = f"┌{'─'*(w0+2)}┬{'─'*(w1+2)}┬{'─'*(w2+2)}┬{'─'*(w3+2)}┐"
    mid_sep = f"├{'─'*(w0+2)}┼{'─'*(w1+2)}┼{'─'*(w2+2)}┼{'─'*(w3+2)}┤"
    bot     = f"└{'─'*(w0+2)}┴{'─'*(w1+2)}┴{'─'*(w2+2)}┴{'─'*(w3+2)}┘"

    def row_str(a, b, c, d):
        return f"│ {a:>{w0}} │ {b:>{w1}} │ {c:>{w2}} │ {d:>{w3}} │"

    print(top)
    print(row_str(col_in, col_out, col_exec, col_imp))
    print(mid_sep)
    for r in rows:
        in_str   = _fmt_amount(r["amount_in"],  token_in.decimals)
        out_str  = _fmt_amount(r["amount_out"], token_out.decimals)
        price    = _human_exec_price(r["amount_in"], r["amount_out"], token_in, token_out)
        exec_str = f"{float(price):,.2f}"
        imp_str  = f"{float(r['price_impact_pct']):.2f}%"
        print(row_str(in_str, out_str, exec_str, imp_str))
    print(bot)


def _run_demo(token_in_sym: str, sizes_raw: list[int]) -> None:
    pair_addr = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    pair = _DEMO_PAIRS[pair_addr]

    token_in = _DEMO_TOKENS.get(token_in_sym.upper())
    if token_in is None:
        print(f"Unknown token: {token_in_sym}. Available: {', '.join(_DEMO_TOKENS)}")
        sys.exit(1)

    token_out = pair.token1 if token_in.address == pair.token0.address else pair.token0

    _print_analysis(pair, token_in, token_out, sizes_raw, pair_addr)


def _run_onchain(pair_addr: str, token_in_sym: str, sizes_raw: list[int], rpc_url: str) -> None:
    from src.chain.client import ChainClient

    client = ChainClient([rpc_url])
    addr = Address(pair_addr)
    pair = UniswapV2Pair.from_chain(addr, client)

    token_in = None
    for sym, t in [("WETH", pair.token0), ("WETH", pair.token1)]:
        if pair.token0.symbol.upper() == token_in_sym.upper():
            token_in = pair.token0
            break
        if pair.token1.symbol.upper() == token_in_sym.upper():
            token_in = pair.token1
            break

    if token_in is None:
        print(f"Token {token_in_sym} not found in pair. Pair has: {pair.token0.symbol}/{pair.token1.symbol}")
        sys.exit(1)

    token_out = pair.token1 if token_in.address == pair.token0.address else pair.token0
    _print_analysis(pair, token_in, token_out, sizes_raw, pair_addr)


def _print_analysis(
    pair: UniswapV2Pair,
    token_in: Token,
    token_out: Token,
    sizes_raw: list[int],
    pair_addr: str,
) -> None:
    analyzer = PriceImpactAnalyzer(pair)

    r0_human = Decimal(pair.reserve0) / Decimal(10**pair.token0.decimals)
    r1_human = Decimal(pair.reserve1) / Decimal(10**pair.token1.decimals)
    # Spot price in human units: token_out per token_in
    reserve_in  = pair.reserve0 if token_in.address == pair.token0.address else pair.reserve1
    reserve_out = pair.reserve1 if token_in.address == pair.token0.address else pair.reserve0
    human_spot = (Decimal(reserve_out) / Decimal(10**token_out.decimals)) / (Decimal(reserve_in) / Decimal(10**token_in.decimals))

    print(f"\nPrice Impact Analysis: {token_in.symbol} → {token_out.symbol}")
    print(f"Pair:        {pair_addr}")
    print(f"Reserves:    {r0_human:,.2f} {pair.token0.symbol} / {r1_human:,.2f} {pair.token1.symbol}")
    print(f"Spot price:  {float(human_spot):,.4f} {token_out.symbol}/{token_in.symbol}")
    print()

    rows = analyzer.generate_impact_table(token_in, sizes_raw)
    _print_table(rows, token_in, token_out)

    max_size = analyzer.find_max_size_for_impact(token_in, Decimal("0.01"))
    print(f"\nMax trade for 1% impact: {_fmt_amount(max_size, token_in.decimals)} {token_in.symbol}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uniswap V2 price impact analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Offline demo (no RPC needed):
  python -m src.pricing.impact_analyzer demo

  # Live pair with RPC:
  python -m src.pricing.impact_analyzer 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc \\
      --token-in WETH --sizes 1e18,5e18,10e18 --rpc https://mainnet.infura.io/v3/KEY
        """,
    )
    parser.add_argument("pair", help='Pair address or "demo"')
    parser.add_argument("--token-in", default="WETH", help="Input token symbol (default: WETH)")
    parser.add_argument(
        "--sizes",
        default=None,
        help="Comma-separated input amounts in token units (e.g. 0.1,1,5,10 for ETH). "
             "Supports scientific notation: 1e18,5e18",
    )
    parser.add_argument("--rpc", default=None, help="RPC URL (required for live pairs)")

    args = parser.parse_args()

    # Parse sizes — human-readable amounts (e.g. 0.1,1,10 for ETH)
    # Automatically multiplied by token decimals (10^18 for WETH, 10^6 for USDC)
    if args.sizes:
        token_sym = args.token_in.upper()
        decimals = 6 if token_sym in ("USDC", "USDT", "USDB") else 18
        try:
            sizes_raw = [int(float(s.strip()) * 10**decimals) for s in args.sizes.split(",")]
        except ValueError:
            print(f"Invalid --sizes value: {args.sizes}")
            sys.exit(1)
    else:
        # Default sizes relative to token_in
        token_sym = args.token_in.upper()
        if token_sym == "WETH":
            sizes_raw = [int(x * 10**18) for x in [0.1, 1, 5, 10, 50, 100]]
        elif token_sym in ("USDC", "USDT"):
            sizes_raw = [int(x * 10**6) for x in [1_000, 10_000, 100_000, 1_000_000]]
        else:
            sizes_raw = [int(x * 10**18) for x in [1, 10, 100, 1_000]]

    if args.pair.lower() == "demo":
        _run_demo(args.token_in, sizes_raw)
    else:
        if not args.rpc:
            print("Error: --rpc required for live pairs. Use 'demo' for offline mode.")
            sys.exit(1)
        _run_onchain(args.pair, args.token_in, sizes_raw, args.rpc)


if __name__ == "__main__":
    main()
