import pytest
import time
from unittest.mock import MagicMock, patch
from src.pricing.pricing_engine import PricingEngine, Quote, QuoteError
from src.pricing.fork_simulator import SimulationResult
from src.pricing.uniswap_v2_pair import UniswapV2Pair
from src.pricing.route import Route
from src.core.types import Address, Token

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)
SHIB = Token(address=Address("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE"), symbol="SHIB", decimals=18)

PAIR = UniswapV2Pair(
    address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
    token0=WETH, token1=USDC,
    reserve0=1_000 * 10**18,
    reserve1=2_000_000 * 10**6,
)


def make_engine() -> PricingEngine:
    """PricingEngine with all external deps mocked."""
    mock_client = MagicMock()
    with patch("src.pricing.pricing_engine.ForkSimulator"), \
         patch("src.pricing.pricing_engine.MempoolMonitor"):
        engine = PricingEngine(
            chain_client=mock_client,
            fork_url="http://localhost:8545",
            ws_url="wss://fake",
        )
    return engine


def engine_with_pool() -> PricingEngine:
    """Engine with WETH/USDC pool pre-loaded (no RPC needed)."""
    engine = make_engine()
    engine.pools[PAIR.address] = PAIR
    from src.pricing.route_finder import RouteFinder
    engine.router = RouteFinder([PAIR])
    return engine


# ── Quote ─────────────────────────────────────────────────────────────────────

def test_quote_is_valid_within_tolerance():
    route = Route(pools=[PAIR], path=[WETH, USDC])
    q = Quote(route=route, amount_in=10**18, expected_output=1000,
              simulated_output=1000, gas_estimate=150_000, timestamp=time.time())
    assert q.is_valid is True


def test_quote_is_valid_just_within_tolerance():
    route = Route(pools=[PAIR], path=[WETH, USDC])
    # 0.09% difference — within 0.1% tolerance
    q = Quote(route=route, amount_in=10**18, expected_output=10000,
              simulated_output=9991, gas_estimate=150_000, timestamp=time.time())
    assert q.is_valid is True


def test_quote_is_invalid_exceeds_tolerance():
    route = Route(pools=[PAIR], path=[WETH, USDC])
    # 0.2% difference — exceeds 0.1%
    q = Quote(route=route, amount_in=10**18, expected_output=10000,
              simulated_output=9979, gas_estimate=150_000, timestamp=time.time())
    assert q.is_valid is False


def test_quote_is_invalid_when_expected_zero():
    route = Route(pools=[PAIR], path=[WETH, USDC])
    q = Quote(route=route, amount_in=10**18, expected_output=0,
              simulated_output=0, gas_estimate=150_000, timestamp=time.time())
    assert q.is_valid is False


def test_quote_has_timestamp():
    route = Route(pools=[PAIR], path=[WETH, USDC])
    before = time.time()
    q = Quote(route=route, amount_in=10**18, expected_output=1000,
              simulated_output=1000, gas_estimate=150_000, timestamp=time.time())
    assert q.timestamp >= before


# ── PricingEngine.get_quote ───────────────────────────────────────────────────

def test_get_quote_raises_without_pools():
    engine = make_engine()
    with pytest.raises(QuoteError, match="No pools loaded"):
        engine.get_quote(WETH, USDC, 10**18, gas_price_gwei=20)


def test_get_quote_raises_when_no_route():
    engine = engine_with_pool()
    engine.simulator.simulate_route.return_value = SimulationResult(
        success=True, amount_out=1_992 * 10**6, gas_used=150_000, error=None
    )
    with pytest.raises(QuoteError):
        engine.get_quote(WETH, SHIB, 10**18, gas_price_gwei=20)


def test_get_quote_returns_quote_object():
    engine = engine_with_pool()
    engine.simulator.simulate_route.return_value = SimulationResult(
        success=True, amount_out=1_992 * 10**6, gas_used=150_000, error=None
    )
    quote = engine.get_quote(WETH, USDC, 10**18, gas_price_gwei=20)
    assert isinstance(quote, Quote)


def test_get_quote_raises_when_simulation_fails():
    engine = engine_with_pool()
    engine.simulator.simulate_route.return_value = SimulationResult(
        success=False, amount_out=0, gas_used=0, error="fork offline"
    )
    with pytest.raises(QuoteError, match="Simulation failed"):
        engine.get_quote(WETH, USDC, 10**18, gas_price_gwei=20)


def test_get_quote_amount_in_preserved():
    engine = engine_with_pool()
    engine.simulator.simulate_route.return_value = SimulationResult(
        success=True, amount_out=1_992 * 10**6, gas_used=150_000, error=None
    )
    quote = engine.get_quote(WETH, USDC, 10**18, gas_price_gwei=20)
    assert quote.amount_in == 10**18


def test_get_quote_gas_estimate_from_simulation():
    engine = engine_with_pool()
    engine.simulator.simulate_route.return_value = SimulationResult(
        success=True, amount_out=1_992 * 10**6, gas_used=142_000, error=None
    )
    quote = engine.get_quote(WETH, USDC, 10**18, gas_price_gwei=20)
    assert quote.gas_estimate == 142_000


# ── PricingEngine.refresh_pool ────────────────────────────────────────────────

def test_refresh_pool_raises_for_unknown_address():
    engine = make_engine()
    unknown = Address("0x1234567890123456789012345678901234567890")
    with pytest.raises(KeyError):
        engine.refresh_pool(unknown)


def test_refresh_pool_updates_pool_and_rebuilds_router():
    engine = engine_with_pool()
    new_pair = UniswapV2Pair(
        address=PAIR.address,
        token0=WETH, token1=USDC,
        reserve0=900 * 10**18,
        reserve1=1_800_000 * 10**6,
    )
    engine.client.get_balance = MagicMock()

    with patch.object(UniswapV2Pair, "from_chain", return_value=new_pair):
        engine.refresh_pool(PAIR.address)

    assert engine.pools[PAIR.address].reserve0 == 900 * 10**18
    assert engine.router is not None


# ── PricingEngine._on_mempool_swap ────────────────────────────────────────────

def test_on_mempool_swap_ignores_swap_with_no_tokens():
    from src.pricing.mempool_monitor import ParsedSwap
    engine = engine_with_pool()

    swap = ParsedSwap(
        tx_hash="0xabc", router="0x", dex="UniswapV2",
        method="swapExactTokensForTokens",
        token_in=None, token_out=None,
        amount_in=0, min_amount_out=0, deadline=0,
        sender=Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"),
        gas_price=0,
    )
    # Should not raise
    engine._on_mempool_swap(swap)


def test_on_mempool_swap_triggers_refresh_for_matching_pool():
    from src.pricing.mempool_monitor import ParsedSwap
    engine = engine_with_pool()

    new_pair = UniswapV2Pair(
        address=PAIR.address, token0=WETH, token1=USDC,
        reserve0=999 * 10**18, reserve1=1_998_000 * 10**6,
    )
    swap = ParsedSwap(
        tx_hash="0xabc", router="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        dex="UniswapV2", method="swapExactTokensForTokens",
        token_in=WETH.address, token_out=USDC.address,
        amount_in=1 * 10**18, min_amount_out=1_900 * 10**6,
        deadline=9999999999,
        sender=Address("0xab5801a7d398351b8be11c439e05c5b3259aec9b"),
        gas_price=20 * 10**9,
    )

    with patch.object(UniswapV2Pair, "from_chain", return_value=new_pair):
        engine._on_mempool_swap(swap)

    assert engine.pools[PAIR.address].reserve0 == 999 * 10**18
