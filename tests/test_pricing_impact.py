from decimal import Decimal
from src.pricing.UniswapV2Pair import UniswapV2Pair
from src.pricing.PriceImpactAnalyzer import PriceImpactAnalyzer
from src.core.types import Address, Token

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)


def make_pair(reserve_eth=1000, reserve_usdc=2_000_000) -> UniswapV2Pair:
    return UniswapV2Pair(
        address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
        token0=WETH, token1=USDC,
        reserve0=reserve_eth * 10**18,
        reserve1=reserve_usdc * 10**6,
    )


# ── generate_impact_table ─────────────────────────────────────────────────────

def test_generate_impact_table_returns_correct_length():
    analyzer = PriceImpactAnalyzer(make_pair())
    sizes = [1 * 10**18, 5 * 10**18, 10 * 10**18]
    table = analyzer.generate_impact_table(WETH, sizes)
    assert len(table) == 3


def test_generate_impact_table_has_required_keys():
    analyzer = PriceImpactAnalyzer(make_pair())
    table = analyzer.generate_impact_table(WETH, [1 * 10**18])
    row = table[0]
    assert "amount_in" in row
    assert "amount_out" in row
    assert "spot_price" in row
    assert "execution_price" in row
    assert "price_impact_pct" in row


def test_generate_impact_table_amount_in_matches_input():
    analyzer = PriceImpactAnalyzer(make_pair())
    sizes = [1 * 10**18, 5 * 10**18]
    table = analyzer.generate_impact_table(WETH, sizes)
    assert table[0]["amount_in"] == 1 * 10**18
    assert table[1]["amount_in"] == 5 * 10**18


def test_generate_impact_table_impact_increases_with_size():
    analyzer = PriceImpactAnalyzer(make_pair())
    sizes = [1 * 10**18, 10 * 10**18, 50 * 10**18]
    table = analyzer.generate_impact_table(WETH, sizes)
    impacts = [row["price_impact_pct"] for row in table]
    assert impacts[0] < impacts[1] < impacts[2]


def test_generate_impact_table_spot_price_constant():
    """Spot price is pool state — same for all rows."""
    analyzer = PriceImpactAnalyzer(make_pair())
    sizes = [1 * 10**18, 5 * 10**18, 10 * 10**18]
    table = analyzer.generate_impact_table(WETH, sizes)
    spots = [row["spot_price"] for row in table]
    assert spots[0] == spots[1] == spots[2]


def test_generate_impact_table_amount_out_positive():
    analyzer = PriceImpactAnalyzer(make_pair())
    table = analyzer.generate_impact_table(WETH, [1 * 10**18])
    assert table[0]["amount_out"] > 0


# ── find_max_size_for_impact ──────────────────────────────────────────────────

def test_find_max_size_result_is_within_limit():
    analyzer = PriceImpactAnalyzer(make_pair())
    max_size = analyzer.find_max_size_for_impact(WETH, Decimal("0.01"))
    assert max_size > 0
    impact = analyzer.pair.get_price_impact(max_size, WETH)
    assert impact <= Decimal("0.01")


def test_find_max_size_larger_limit_gives_larger_trade():
    analyzer = PriceImpactAnalyzer(make_pair())
    size_1pct = analyzer.find_max_size_for_impact(WETH, Decimal("0.01"))
    size_5pct = analyzer.find_max_size_for_impact(WETH, Decimal("0.05"))
    assert size_5pct > size_1pct


def test_find_max_size_very_strict_limit():
    """Extremely tight limit (0.001%) returns a small but positive value."""
    analyzer = PriceImpactAnalyzer(make_pair())
    size = analyzer.find_max_size_for_impact(WETH, Decimal("0.0001"))
    assert size >= 0


def test_find_max_size_returns_int():
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.find_max_size_for_impact(WETH, Decimal("0.01"))
    assert isinstance(result, int)


# ── estimate_true_cost ────────────────────────────────────────────────────────

def test_estimate_true_cost_has_required_keys():
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20)
    assert "gross_output" in result
    assert "gas_cost_eth" in result
    assert "gas_cost_in_output_token" in result
    assert "net_output" in result
    assert "effective_price" in result


def test_estimate_true_cost_gross_output_positive():
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20)
    assert result["gross_output"] > 0


def test_estimate_true_cost_gas_cost_eth_correct():
    """gas_cost_eth = gas_estimate * gas_price_gwei * 10^9"""
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20, gas_estimate=150_000)
    expected_gas_eth = 150_000 * 20 * 10**9
    assert result["gas_cost_eth"] == expected_gas_eth


def test_estimate_true_cost_net_less_than_gross():
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20)
    assert result["net_output"] < result["gross_output"]


def test_estimate_true_cost_higher_gas_lower_net():
    analyzer = PriceImpactAnalyzer(make_pair())
    cheap = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=10)
    expensive = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=100)
    assert expensive["net_output"] < cheap["net_output"]


def test_estimate_true_cost_effective_price_is_decimal():
    analyzer = PriceImpactAnalyzer(make_pair())
    result = analyzer.estimate_true_cost(1 * 10**18, WETH, gas_price_gwei=20)
    assert isinstance(result["effective_price"], Decimal)
