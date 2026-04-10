from unittest.mock import MagicMock, patch
from src.pricing.ForkSimulator import ForkSimulator, SimulationResult
from src.pricing.UniswapV2Pair import UniswapV2Pair
from src.pricing.Route import Route
from src.core.types import Address, Token

WETH = Token(address=Address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"), symbol="WETH", decimals=18)
USDC = Token(address=Address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"), symbol="USDC", decimals=6)
SENDER = Address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

PAIR = UniswapV2Pair(
    address=Address("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"),
    token0=WETH, token1=USDC,
    reserve0=1_000 * 10**18,
    reserve1=2_000_000 * 10**6,
)


def make_simulator() -> ForkSimulator:
    """ForkSimulator with mocked Web3 — no real RPC needed."""
    with patch("src.pricing.ForkSimulator.Web3"):
        sim = ForkSimulator("http://localhost:8545")
    return sim


def mock_get_amounts_out(simulator: ForkSimulator, return_amounts: list) -> None:
    """Configure mock to return given amounts from getAmountsOut."""
    mock_contract = MagicMock()
    mock_contract.functions.getAmountsOut.return_value.call.return_value = return_amounts
    mock_contract.functions.swapExactTokensForTokens.return_value.estimate_gas.side_effect = Exception("no approval")
    simulator.w3.eth.contract.return_value = mock_contract


# ── SimulationResult ──────────────────────────────────────────────────────────

def test_simulation_result_success_fields():
    result = SimulationResult(success=True, amount_out=1000, gas_used=150_000, error=None)
    assert result.success is True
    assert result.amount_out == 1000
    assert result.gas_used == 150_000
    assert result.error is None


def test_simulation_result_failure_fields():
    result = SimulationResult(success=False, amount_out=0, gas_used=0, error="reverted")
    assert result.success is False
    assert result.error == "reverted"


def test_simulation_result_default_logs():
    result = SimulationResult(success=True, amount_out=0, gas_used=0, error=None)
    assert result.logs == []


# ── simulate_swap ─────────────────────────────────────────────────────────────

def test_simulate_swap_returns_amount_from_get_amounts_out():
    sim = make_simulator()
    mock_get_amounts_out(sim, [1_000 * 10**6, 1_992 * 10**6])

    router = Address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
    result = sim.simulate_swap(
        router=router,
        swap_params={"amount_in": 1_000 * 10**6, "path": [USDC.address.checksum, WETH.address.checksum]},
        sender=SENDER,
    )
    assert result.success is True
    assert result.amount_out == 1_992 * 10**6


def test_simulate_swap_fallback_gas_when_estimate_fails():
    """When estimate_gas reverts, falls back to standard 150k gas."""
    sim = make_simulator()
    mock_get_amounts_out(sim, [10**18, 1_992 * 10**6])

    router = Address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
    result = sim.simulate_swap(
        router=router,
        swap_params={"amount_in": 10**18, "path": [WETH.address.checksum, USDC.address.checksum]},
        sender=SENDER,
    )
    assert result.success is True
    assert result.gas_used == 150_000  # fallback value for 2-token path


def test_simulate_swap_returns_failure_on_rpc_error():
    sim = make_simulator()
    mock_contract = MagicMock()
    mock_contract.functions.getAmountsOut.return_value.call.side_effect = Exception("RPC error")
    sim.w3.eth.contract.return_value = mock_contract

    router = Address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
    result = sim.simulate_swap(
        router=router,
        swap_params={"amount_in": 10**18, "path": [WETH.address.checksum, USDC.address.checksum]},
        sender=SENDER,
    )
    assert result.success is False
    assert result.amount_out == 0
    assert "RPC error" in result.error


# ── simulate_route ────────────────────────────────────────────────────────────

def test_simulate_route_builds_path_from_route():
    sim = make_simulator()
    mock_get_amounts_out(sim, [10**18, 1_992 * 10**6])

    route = Route(pools=[PAIR], path=[WETH, USDC])
    result = sim.simulate_route(route, 10**18, SENDER)

    assert result.success is True
    assert result.amount_out == 1_992 * 10**6


def test_simulate_route_multihop_path_length():
    """For 2-hop route, path has 3 tokens."""
    DAI = Token(address=Address("0x6B175474E89094C44Da98b954EedeAC495271d0F"), symbol="DAI", decimals=18)
    PAIR2 = UniswapV2Pair(
        address=Address("0xAE461cA67B15dc8dc81CE7615e0320dA1A9aB8D5"),
        token0=DAI, token1=USDC,
        reserve0=5_000_000 * 10**18,
        reserve1=5_000_000 * 10**6,
    )
    PAIR_ETH_DAI = UniswapV2Pair(
        address=Address("0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"),
        token0=WETH, token1=DAI,
        reserve0=500 * 10**18,
        reserve1=1_000_000 * 10**18,
    )
    sim = make_simulator()
    mock_get_amounts_out(sim, [10**18, 1_990 * 10**18, 1_985 * 10**6])

    route = Route(pools=[PAIR_ETH_DAI, PAIR2], path=[WETH, DAI, USDC])
    sim.simulate_route(route, 10**18, SENDER)

    # Verify path passed to getAmountsOut has 3 tokens
    call_args = sim.w3.eth.contract.return_value.functions.getAmountsOut.call_args
    path_arg = call_args[0][1]
    assert len(path_arg) == 3


# ── compare_simulation_vs_calculation ────────────────────────────────────────

def test_compare_match_when_simulated_equals_calculated():
    sim = make_simulator()
    calculated = PAIR.get_amount_out(10**18, WETH)
    mock_get_amounts_out(sim, [10**18, calculated])

    result = sim.compare_simulation_vs_calculation(PAIR, 10**18, WETH)

    assert result["calculated"] == calculated
    assert result["simulated"] == calculated
    assert result["difference"] == 0
    assert result["match"] is True


def test_compare_mismatch_when_simulated_differs():
    sim = make_simulator()
    calculated = PAIR.get_amount_out(10**18, WETH)
    mock_get_amounts_out(sim, [10**18, calculated + 1])

    result = sim.compare_simulation_vs_calculation(PAIR, 10**18, WETH)

    assert result["match"] is False
    assert result["difference"] == 1


def test_compare_returns_required_keys():
    sim = make_simulator()
    mock_get_amounts_out(sim, [10**18, 1_992 * 10**6])
    result = sim.compare_simulation_vs_calculation(PAIR, 10**18, WETH)

    assert "calculated" in result
    assert "simulated" in result
    assert "difference" in result
    assert "match" in result
