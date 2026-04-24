from dataclasses import dataclass, field
from typing import Optional

from web3 import Web3

from src.core.types import Address, Token
from src.pricing.route import Route
from src.pricing.uniswap_v2_pair import UniswapV2Pair


@dataclass
class SimulationResult:
    """Result of a simulated transaction on a local fork."""

    success: bool
    amount_out: int
    gas_used: int
    error: Optional[str]
    logs: list = field(default_factory=list)


class ForkSimulator:
    """
    Simulates transactions on a local Anvil/Hardhat fork.
    """

    # Uniswap V2 Router02 on mainnet
    ROUTER_ABI = [
        {
            "name": "swapExactTokensForTokens",
            "type": "function",
            "inputs": [
                {"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"},
            ],
            "outputs": [{"name": "amounts", "type": "uint256[]"}],
            "stateMutability": "nonpayable",
        },
        {
            "name": "getAmountsOut",
            "type": "function",
            "inputs": [
                {"name": "amountIn", "type": "uint256"},
                {"name": "path", "type": "address[]"},
            ],
            "outputs": [{"name": "amounts", "type": "uint256[]"}],
            "stateMutability": "view",
        },
    ]

    def __init__(self, fork_url: str):
        """
        fork_url: Local fork RPC (e.g. http://localhost:8545)
        """
        self.w3 = Web3(Web3.HTTPProvider(fork_url))

    def simulate_swap(
        self,
        router: Address,
        swap_params: dict,
        sender: Address,
    ) -> SimulationResult:
        """
        Simulate a single swap via the router contract using eth_call.
        swap_params must contain: amount_in, path (list of address strings), deadline.
        Returns a SimulationResult with output amount, gas used, and logs.
        """
        contract = self.w3.eth.contract(
            address=router.checksum, abi=self.ROUTER_ABI
        )
        path = swap_params["path"]
        amount_in = swap_params["amount_in"]
        deadline = swap_params.get("deadline", 2**32 - 1)
        try:
            amounts = contract.functions.getAmountsOut(amount_in, path).call()
            amount_out = amounts[-1]
        except Exception as e:
            return SimulationResult(
                success=False, amount_out=0, gas_used=0, error=str(e)
            )

        try:
            gas_used = contract.functions.swapExactTokensForTokens(
                amount_in, 0, path, sender.checksum, deadline
            ).estimate_gas({"from": sender.checksum})
        except Exception:
            gas_used = 150_000 + (len(path) - 2) * 100_000  # standard estimate

        return SimulationResult(
            success=True,
            amount_out=amount_out,
            gas_used=gas_used,
            error=None,
        )

    def simulate_route(
        self,
        route: Route,
        amount_in: int,
        sender: Address,
    ) -> SimulationResult:
        """
        Simulate a full multi-hop route by building the token path
        and calling getAmountsOut on the router.
        """
        if not route.pools:
            return SimulationResult(success=False, amount_out=0, gas_used=0, error="Empty route")

        path = [token.address.checksum for token in route.path]

        router_address = Address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")

        swap_params = {
            "amount_in": amount_in,
            "path": path,
            "deadline": 2**32 - 1,
        }
        return self.simulate_swap(router_address, swap_params, sender)

    def compare_simulation_vs_calculation(
        self,
        pair: UniswapV2Pair,
        amount_in: int,
        token_in: Token,
    ) -> dict:
        """
        Compare our AMM integer math against actual fork simulation.
        Returns:
        {
            'calculated': int,
            'simulated': int,
            'difference': int,
            'match': bool,
        }
        """
        calculated = pair.get_amount_out(amount_in, token_in)

        if token_in.address == pair.token0.address:
            token_out = pair.token1
        else:
            token_out = pair.token0

        zero = Address("0x0000000000000000000000000000000000000000")
        route = Route(pools=[pair], path=[token_in, token_out])
        sim_result = self.simulate_route(route, amount_in, zero)

        return {
            "calculated": calculated,
            "simulated": sim_result.amount_out,
            "difference": abs(calculated - sim_result.amount_out),
            "match": calculated == sim_result.amount_out,
        }