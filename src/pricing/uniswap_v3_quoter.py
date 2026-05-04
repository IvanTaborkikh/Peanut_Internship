"""Lightweight wrapper around Uniswap V3 QuoterV2 contract.

V3 uses concentrated liquidity + per-tick math; we don't replicate it locally.
Instead, each `quote_exact_input_single` issues a single eth_call to the
QuoterV2 contract, which returns the exact `amountOut` for the trade.

QuoterV2 deployments are at the same address on most chains:
  https://docs.uniswap.org/contracts/v3/reference/deployments

ABI: function quoteExactInputSingle(QuoteExactInputSingleParams calldata params)
     external returns (uint256 amountOut, uint160 sqrtPriceX96After,
                       uint32 initializedTicksCrossed, uint256 gasEstimate)
where struct QuoteExactInputSingleParams {
    address tokenIn; address tokenOut; uint256 amountIn;
    uint24 fee; uint160 sqrtPriceLimitX96;
}
"""
from typing import Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from web3 import Web3

from src.chain.client import ChainClient
from src.core.types import Address, Token


class UniswapV3QuoterError(Exception):
    """Raised when the Quoter contract call reverts or returns no data."""


# Canonical QuoterV2 deployments (same address on most chains).
QUOTER_V2_ADDRESSES: dict[int, str] = {
    1:     '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',  # Ethereum mainnet
    42161: '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',  # Arbitrum
    10:    '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',  # Optimism
    137:   '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',  # Polygon
}

# Function selector: keccak("quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4]
_QUOTE_SELECTOR = bytes.fromhex(
    Web3.keccak(text='quoteExactInputSingle((address,address,uint256,uint24,uint160))').hex()[:8]
)


class UniswapV3Quoter:
    """Quote tokens via Uniswap V3 QuoterV2 contract calls."""

    def __init__(self, chain_client: ChainClient, chain_id: int = 42161,
                 quoter_address: Optional[str] = None):
        self.client = chain_client
        addr = quoter_address or QUOTER_V2_ADDRESSES.get(chain_id)
        if addr is None:
            raise ValueError(
                f"No QuoterV2 address known for chain_id={chain_id}; pass quoter_address explicitly"
            )
        self.quoter_address = Address(addr)

    def quote_exact_input_single(
        self,
        token_in: Token,
        token_out: Token,
        amount_in: int,
        fee_tier: int = 3000,
        sqrt_price_limit_x96: int = 0,
    ) -> int:
        """Returns `amountOut` (in `token_out` wei) for `amount_in` of `token_in`.

        `fee_tier`: 100 (0.01%), 500 (0.05%), 3000 (0.3%), 10000 (1%).
        Raises `UniswapV3QuoterError` if the pool doesn't exist or call reverts.
        """
        encoded_params = abi_encode(
            ['(address,address,uint256,uint24,uint160)'],
            [(
                token_in.address.checksum,
                token_out.address.checksum,
                int(amount_in),
                int(fee_tier),
                int(sqrt_price_limit_x96),
            )],
        )
        calldata = _QUOTE_SELECTOR + encoded_params

        # Build a minimal eth_call request via web3 directly. ChainClient.call()
        # expects a TransactionRequest dataclass; we go around it for simplicity.
        try:
            raw = self.client._web3.eth.call({
                'to':   self.quoter_address.checksum,
                'data': '0x' + calldata.hex(),
            })
        except Exception as e:
            raise UniswapV3QuoterError(
                f"QuoterV2 call reverted ({token_in.symbol}->{token_out.symbol} "
                f"fee={fee_tier}): {e}"
            ) from e

        if not raw or len(raw) < 32:
            raise UniswapV3QuoterError(
                f"QuoterV2 returned empty data ({token_in.symbol}->{token_out.symbol} fee={fee_tier})"
            )

        amount_out, _sqrt_after, _ticks, _gas = abi_decode(
            ['uint256', 'uint160', 'uint32', 'uint256'], raw,
        )
        return amount_out
