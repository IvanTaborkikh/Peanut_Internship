from decimal import Decimal
from src.pricing.uniswap_v2_pair import UniswapV2Pair
from src.core.types import Address, Token

# ── Test fixtures ─────────────────────────────────────────────────────────────

ADDR_PAIR = Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc")
ADDR_ETH  = Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")  # WETH
ADDR_USDC = Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")

WETH = Token(address=ADDR_ETH,  symbol="WETH", decimals=18)
USDC = Token(address=ADDR_USDC, symbol="USDC", decimals=6)


def make_pair(reserve_eth=1000, reserve_usdc=2_000_000, fee_bps=30) -> UniswapV2Pair:
    """1000 ETH / 2M USDC pool."""
    return UniswapV2Pair(
        address=ADDR_PAIR,
        token0=WETH,
        token1=USDC,
        reserve0=reserve_eth * 10**18,
        reserve1=reserve_usdc * 10**6,
        fee_bps=fee_bps,
    )


# ── get_amount_out ────────────────────────────────────────────────────────────

def test_get_amount_out_basic():
    """Buying ~1 ETH with 2000 USDC should return slightly less than 1 ETH."""
    pair = make_pair()
    usdc_in = 2000 * 10**6
    eth_out = pair.get_amount_out(usdc_in, USDC)

    assert eth_out < 1 * 10**18
    assert eth_out > int(0.99 * 10**18)


def test_get_amount_out_returns_int():
    pair = make_pair()
    out = pair.get_amount_out(1000 * 10**6, USDC)
    assert isinstance(out, int)


def test_get_amount_out_zero_input():
    pair = make_pair()
    assert pair.get_amount_out(0, USDC) == 0


def test_get_amount_out_integer_math_no_precision_loss():
    """Large reserves that would overflow float must work correctly."""
    pair = UniswapV2Pair(
        address=ADDR_PAIR,
        token0=WETH,
        token1=USDC,
        reserve0=10**30,
        reserve1=10**30,
        fee_bps=30,
    )
    out = pair.get_amount_out(10**25, WETH)
    assert isinstance(out, int)
    assert out > 0


def test_get_amount_out_fee_reduces_output():
    """Higher fee should produce less output."""
    pair_30  = make_pair(fee_bps=30)
    pair_100 = make_pair(fee_bps=100)
    amount_in = 1000 * 10**6

    assert pair_30.get_amount_out(amount_in, USDC) > pair_100.get_amount_out(amount_in, USDC)


def test_get_amount_out_both_directions():
    """Swapping in both directions should be consistent with reserves."""
    pair = make_pair()
    usdc_in = 1000 * 10**6
    eth_out = pair.get_amount_out(usdc_in, USDC)
    assert eth_out > 0

    eth_in = 1 * 10**18
    usdc_out = pair.get_amount_out(eth_in, WETH)
    assert usdc_out > 0


# ── get_amount_in ─────────────────────────────────────────────────────────────

def test_get_amount_in_basic():
    """To get exactly 1 ETH out, we should need slightly more than spot."""
    pair = make_pair()
    eth_out = 1 * 10**18
    usdc_in = pair.get_amount_in(eth_out, WETH)

    assert usdc_in > 2000 * 10**6  # more than spot price


def test_get_amount_in_returns_int():
    pair = make_pair()
    result = pair.get_amount_in(1 * 10**18, WETH)
    assert isinstance(result, int)


def test_get_amount_in_out_roundtrip():
    """
    get_amount_in(get_amount_out(x)) should be >= x (due to rounding).
    """
    pair = make_pair()
    amount_in = 1000 * 10**6
    amount_out = pair.get_amount_out(amount_in, USDC)
    required_in = pair.get_amount_in(amount_out, WETH)
    assert required_in >= amount_in


# ── simulate_swap ─────────────────────────────────────────────────────────────

def test_simulate_swap_is_immutable():
    """simulate_swap must not modify original pair reserves."""
    pair = make_pair()
    original_r0 = pair.reserve0
    original_r1 = pair.reserve1

    new_pair = pair.simulate_swap(1000 * 10**6, USDC)

    assert pair.reserve0 == original_r0
    assert pair.reserve1 == original_r1
    assert new_pair.reserve0 != original_r0 or new_pair.reserve1 != original_r1


def test_simulate_swap_returns_new_pair():
    pair = make_pair()
    new_pair = pair.simulate_swap(1000 * 10**6, USDC)
    assert isinstance(new_pair, UniswapV2Pair)
    assert new_pair is not pair


def test_simulate_swap_reserves_update_correctly():
    """After buying ETH with USDC: reserve_usdc increases, reserve_eth decreases."""
    pair = make_pair()
    usdc_in = 2000 * 10**6
    new_pair = pair.simulate_swap(usdc_in, USDC)

    assert new_pair.reserve1 > pair.reserve1   # more USDC in pool
    assert new_pair.reserve0 < pair.reserve0   # less ETH in pool


# ── spot price / execution price / impact ─────────────────────────────────────

def test_get_spot_price_returns_decimal():
    pair = make_pair()
    price = pair.get_spot_price(USDC)
    assert isinstance(price, Decimal)
    assert price > 0


def test_execution_price_worse_than_spot():
    """For non-zero trade, execution price should be worse than spot price."""
    pair = make_pair()
    spot = pair.get_spot_price(USDC)
    exec_price = pair.get_execution_price(10_000 * 10**6, USDC)
    # Buying ETH with USDC: higher USDC cost per ETH = worse for buyer
    assert exec_price > spot


def test_price_impact_positive():
    pair = make_pair()
    impact = pair.get_price_impact(1000 * 10**6, USDC)
    assert impact > Decimal("0")


def test_price_impact_larger_trade_more_impact():
    pair = make_pair()
    small = pair.get_price_impact(1_000 * 10**6, USDC)
    large = pair.get_price_impact(100_000 * 10**6, USDC)
    assert large > small


# ── Solidity match (real on-chain tx) ─────────────────────────────────────────

def test_get_amount_out_matches_solidity():
    """
    Verify our integer math matches the Uniswap V2 contract exactly.

    Real swap — Ethereum mainnet block 12,000,001:
      tx: 0x76403053ee0300411b68fc223b327b51fb4f1a26e1f6cb8667e05ec370e8176e
      Pair: 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc (token0=USDC, token1=WETH)
      Reserves at block 12,000,000 (before swap):
        reserve0 (USDC) = 131_447_219_188_192
        reserve1 (WETH) = 74_491_511_240_158_664_062_533
      Swap: 3.8148 WETH in → 6711.07 USDC out
      On-chain result: 6_711_072_182 USDC (verified on Etherscan)
    """
    USDC_ONCHAIN = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)
    WETH_ONCHAIN = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)

    pair = UniswapV2Pair(
        address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
        token0=USDC_ONCHAIN,   # token0 = USDC on this pair
        token1=WETH_ONCHAIN,   # token1 = WETH on this pair
        reserve0=131_447_219_188_192,          # USDC reserve
        reserve1=74_491_511_240_158_664_062_533,  # WETH reserve
        fee_bps=30,
    )

    amount_weth_in  = 3_814_822_253_806_629_476   # from tx
    expected_usdc_out = 6_711_072_182              # from tx (on-chain result)

    result = pair.get_amount_out(amount_weth_in, WETH_ONCHAIN)

    assert result == expected_usdc_out, (
        f"Our math gave {result}, on-chain was {expected_usdc_out}. "
        f"Difference: {abs(result - expected_usdc_out)}"
    )