from dataclasses import dataclass
from decimal import Decimal

from src.chain.client import ChainClient
from src.core.types import Address, Token


@dataclass
class UniswapV2Pair:
    """
    Represents a Uniswap V2 liquidity pair.
    All math uses integers only — no floats anywhere.
    """

    address: Address
    token0: Token
    token1: Token
    reserve0: int
    reserve1: int
    fee_bps: int = 30  # 0.30% = 30 basis points

    def _reserves_for(self, token_in: Token) -> tuple[int, int]:
        """Return (reserve_in, reserve_out) for given input token."""
        if token_in.address == self.token0.address:
            return self.reserve0, self.reserve1
        return self.reserve1, self.reserve0

    def get_amount_out(self, amount_in: int, token_in: Token) -> int:
        """
        Calculate output amount for a given input.
        Matches Solidity exactly:

        amount_in_with_fee = amount_in * (10000 - fee_bps)
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * 10000 + amount_in_with_fee
        amount_out = numerator // denominator
        """
        if amount_in == 0:
            return 0
        reserve_in, reserve_out = self._reserves_for(token_in)
        amount_in_with_fee = amount_in * (10000 - self.fee_bps)
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * 10000 + amount_in_with_fee
        return numerator // denominator

    def get_amount_in(self, amount_out: int, token_out: Token) -> int:
        """
        Calculate required input for desired output.
        Inverse of get_amount_out.
        """
        if amount_out <= 0:
            raise ValueError("amount_out must be positive")

        if token_out.address == self.token0.address:
            reserve_in, reserve_out = self.reserve1, self.reserve0
        else:
            reserve_in, reserve_out = self.reserve0, self.reserve1

        if amount_out >= reserve_out:
            raise ValueError(
                f"amount_out ({amount_out}) exceeds reserve ({reserve_out})"
            )

        numerator = reserve_in * amount_out * 10000
        denominator = (reserve_out - amount_out) * (10000 - self.fee_bps)
        return (numerator + denominator - 1) // denominator

    def get_spot_price(self, token_in: Token) -> Decimal:
        """
        Returns spot price as reserve_in / reserve_out (for display and gas conversion).
        Raises ValueError if reserve_out is zero.
        """
        reserve_in, reserve_out = self._reserves_for(token_in)
        if reserve_out == 0:
            raise ValueError("reserve_out is zero — pool has no liquidity")
        return Decimal(reserve_in) / Decimal(reserve_out)

    def get_execution_price(self, amount_in: int, token_in: Token) -> Decimal:
        """
        Returns actual execution price for given trade size.
        Price in terms of token_in per token_out.
        """
        amount_out = self.get_amount_out(amount_in, token_in)
        if amount_out == 0:
            return Decimal(0)
        return Decimal(amount_in) / Decimal(amount_out)

    def get_price_impact(self, amount_in: int, token_in: Token) -> Decimal:
        """
        Returns price impact as a decimal (0.01 = 1%).
        Impact = (execution_price - spot_price) / spot_price
        """
        spot = self.get_spot_price(token_in)
        exec_price = self.get_execution_price(amount_in, token_in)
        if spot == 0:
            return Decimal(0)
        return (exec_price - spot) / spot

    def simulate_swap(self, amount_in: int, token_in: Token) -> "UniswapV2Pair":
        """
        Returns a NEW pair with updated reserves after the swap.
        Does NOT mutate the current instance.
        """
        amount_out = self.get_amount_out(amount_in, token_in)
        if token_in.address == self.token0.address:
            new_r0 = self.reserve0 + amount_in
            new_r1 = self.reserve1 - amount_out
        else:
            new_r0 = self.reserve0 - amount_out
            new_r1 = self.reserve1 + amount_in
        return UniswapV2Pair(
            address=self.address,
            token0=self.token0,
            token1=self.token1,
            reserve0=new_r0,
            reserve1=new_r1,
            fee_bps=self.fee_bps,
        )

    @classmethod
    def from_chain(cls, address: Address, client: ChainClient) -> "UniswapV2Pair":
        """
        Fetch pair data (reserves, tokens) from on-chain.
        """
        PAIR_ABI = [
            {"name": "getReserves", "type": "function", "inputs": [], "outputs": [
                {"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}
            ], "stateMutability": "view"},
            {"name": "token0", "type": "function", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view"},
            {"name": "token1", "type": "function", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view"},
        ]
        ERC20_ABI = [
            {"name": "symbol", "type": "function", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view"},
            {"name": "decimals", "type": "function", "inputs": [], "outputs": [{"type": "uint8"}], "stateMutability": "view"},
        ]
        w3 = client._web3
        pair_contract = w3.eth.contract(address=address.checksum, abi=PAIR_ABI)
        reserves = pair_contract.functions.getReserves().call()
        t0_addr = pair_contract.functions.token0().call()
        t1_addr = pair_contract.functions.token1().call()

        t0_contract = w3.eth.contract(address=t0_addr, abi=ERC20_ABI)
        t1_contract = w3.eth.contract(address=t1_addr, abi=ERC20_ABI)
        token0 = Token(
            address=Address(t0_addr),
            symbol=t0_contract.functions.symbol().call(),
            decimals=t0_contract.functions.decimals().call(),
        )
        token1 = Token(
            address=Address(t1_addr),
            symbol=t1_contract.functions.symbol().call(),
            decimals=t1_contract.functions.decimals().call(),
        )
        return cls(
            address=address,
            token0=token0,
            token1=token1,
            reserve0=reserves[0],
            reserve1=reserves[1],
        )