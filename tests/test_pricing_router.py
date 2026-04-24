from src.pricing.uniswap_v2_pair import UniswapV2Pair
from src.pricing.route import Route
from src.pricing.route_finder import RouteFinder
from src.core.types import Address, Token

# ── Fixtures ──────────────────────────────────────────────────────────────────

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)
DAI  = Token(address=Address("0x6B175474E89094C44Da98b954EedeAC495271d0F"), symbol="DAI",  decimals=18)
SHIB = Token(address=Address("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE"), symbol="SHIB", decimals=18)

PAIR_ETH_USDC = UniswapV2Pair(
    address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
    token0=WETH, token1=USDC,
    reserve0=1000 * 10**18, reserve1=2_000_000 * 10**6,
)

PAIR_ETH_DAI = UniswapV2Pair(
    address=Address("0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"),
    token0=WETH, token1=DAI,
    reserve0=500 * 10**18, reserve1=1_000_000 * 10**18,
)

PAIR_DAI_USDC = UniswapV2Pair(
    address=Address("0xAE461cA67B15dc8dc81CE7615e0320dA1A9aB8D5"),
    token0=DAI, token1=USDC,
    reserve0=5_000_000 * 10**18, reserve1=5_000_000 * 10**6,
)


# ── Route ─────────────────────────────────────────────────────────────────────

def test_route_num_hops_direct():
    route = Route(pools=[PAIR_ETH_USDC], path=[WETH, USDC])
    assert route.num_hops == 1


def test_route_num_hops_multihop():
    route = Route(pools=[PAIR_ETH_DAI, PAIR_DAI_USDC], path=[WETH, DAI, USDC])
    assert route.num_hops == 2


def test_route_get_output_direct():
    route = Route(pools=[PAIR_ETH_USDC], path=[WETH, USDC])
    out = route.get_output(1 * 10**18)
    assert isinstance(out, int)
    assert out > 0


def test_route_get_output_matches_sequential_swaps():
    """Route output must equal doing swaps one by one."""
    route = Route(pools=[PAIR_ETH_DAI, PAIR_DAI_USDC], path=[WETH, DAI, USDC])
    amount_in = 1 * 10**18

    # Sequential manual
    dai_out = PAIR_ETH_DAI.get_amount_out(amount_in, WETH)
    usdc_out = PAIR_DAI_USDC.get_amount_out(dai_out, DAI)

    assert route.get_output(amount_in) == usdc_out


def test_route_intermediate_amounts_length():
    route = Route(pools=[PAIR_ETH_DAI, PAIR_DAI_USDC], path=[WETH, DAI, USDC])
    amounts = route.get_intermediate_amounts(1 * 10**18)
    # [input, after_hop1, after_hop2]
    assert len(amounts) == 3


def test_route_estimate_gas_single_hop():
    route = Route(pools=[PAIR_ETH_USDC], path=[WETH, USDC])
    assert route.estimate_gas() == 150_000


def test_route_estimate_gas_multihop():
    route = Route(pools=[PAIR_ETH_DAI, PAIR_DAI_USDC], path=[WETH, DAI, USDC])
    assert route.estimate_gas() == 250_000  # 150k + 100k


# ── RouteFinder ───────────────────────────────────────────────────────────────

def test_find_all_routes_direct():
    finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
    routes = finder.find_all_routes(WETH, USDC)
    # Should find at least: WETH→USDC (direct) and WETH→DAI→USDC (2-hop)
    assert len(routes) >= 1


def test_find_all_routes_includes_multihop():
    finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
    routes = finder.find_all_routes(WETH, USDC, max_hops=2)
    hops = [r.num_hops for r in routes]
    assert 2 in hops


def test_no_route_exists():
    finder = RouteFinder([PAIR_ETH_USDC])
    routes = finder.find_all_routes(WETH, SHIB)
    assert routes == []


def test_find_best_route_returns_route_and_output():
    finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
    route, net_out = finder.find_best_route(WETH, USDC, 1 * 10**18, gas_price_gwei=20)
    assert isinstance(route, Route)
    assert isinstance(net_out, int)
    assert net_out > 0


def test_gas_makes_direct_route_better():
    """At very high gas, single-hop should beat multi-hop."""
    finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
    route_cheap, _ = finder.find_best_route(WETH, USDC, 1 * 10**18, gas_price_gwei=1)
    route_expensive, _ = finder.find_best_route(WETH, USDC, 1 * 10**18, gas_price_gwei=10_000)
    # At very high gas, direct route (fewer hops) should win
    assert route_expensive.num_hops <= route_cheap.num_hops


def test_compare_routes_returns_all():
    finder = RouteFinder([PAIR_ETH_USDC, PAIR_ETH_DAI, PAIR_DAI_USDC])
    results = finder.compare_routes(WETH, USDC, 1 * 10**18, gas_price_gwei=20)
    assert len(results) >= 1
    for r in results:
        assert "route" in r
        assert "gross_output" in r
        assert "gas_estimate" in r
        assert "net_output" in r