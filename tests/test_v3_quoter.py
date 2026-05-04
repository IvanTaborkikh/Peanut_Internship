"""Unit tests for UniswapV3Quoter and UniswapV3PricingEngine.

Mock-based — no real RPC calls. Verifies ABI encoding, response decoding,
error propagation, and surface-compatibility with the existing V2 PricingEngine.
"""
from unittest.mock import MagicMock

import pytest
from eth_abi import encode as abi_encode

from src.configs.schema import ChainId
from src.configs.tokens import get_token
from src.pricing.pricing_engine import Quote, QuoteError
from src.pricing.uniswap_v3_quoter import (
    QUOTER_V2_ADDRESSES,
    UniswapV3Quoter,
    UniswapV3QuoterError,
)
from src.pricing.v3_pricing_engine import UniswapV3PricingEngine


def _mock_chain_client(call_return: bytes):
    """ChainClient mock whose ._web3.eth.call returns the given raw bytes."""
    client = MagicMock()
    client._web3.eth.call.return_value = call_return
    return client


def _encoded_quote_response(amount_out: int) -> bytes:
    """Encode the QuoterV2 return tuple (amountOut, sqrtPriceAfter, ticks, gas)."""
    return abi_encode(
        ['uint256', 'uint160', 'uint32', 'uint256'],
        [amount_out, 0, 0, 100_000],
    )


# ---------------------------- UniswapV3Quoter ----------------------------

def test_quoter_known_chain_id_uses_canonical_address():
    client = _mock_chain_client(_encoded_quote_response(15_000_000))
    q = UniswapV3Quoter(client, chain_id=42161)
    assert str(q.quoter_address.checksum).lower() == QUOTER_V2_ADDRESSES[42161].lower()


def test_quoter_unknown_chain_id_raises():
    with pytest.raises(ValueError):
        UniswapV3Quoter(MagicMock(), chain_id=999_999)


def test_quote_exact_input_single_decodes_amount_out():
    expected = 15_201_044   # 15.201044 USDC (6 decimals)
    client = _mock_chain_client(_encoded_quote_response(expected))
    q = UniswapV3Quoter(client, chain_id=42161)
    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    out = q.quote_exact_input_single(chip, usdc, 250 * 10**18, fee_tier=100)

    assert out == expected
    # Confirm calldata went to the correct address.
    call_args = client._web3.eth.call.call_args[0][0]
    assert call_args['to'].lower() == QUOTER_V2_ADDRESSES[42161].lower()
    assert call_args['data'].startswith('0x')


def test_quoter_revert_wraps_error():
    client = MagicMock()
    client._web3.eth.call.side_effect = RuntimeError("execution reverted")
    q = UniswapV3Quoter(client, chain_id=42161)
    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    with pytest.raises(UniswapV3QuoterError, match="QuoterV2 call reverted"):
        q.quote_exact_input_single(chip, usdc, 1, fee_tier=100)


def test_quoter_empty_response_raises():
    client = _mock_chain_client(b'')   # Some RPCs return empty on revert
    q = UniswapV3Quoter(client, chain_id=42161)
    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    with pytest.raises(UniswapV3QuoterError, match="empty data"):
        q.quote_exact_input_single(chip, usdc, 1, fee_tier=100)


# ---------------------------- UniswapV3PricingEngine ----------------------------

def test_engine_get_quote_returns_quote_object():
    client = _mock_chain_client(_encoded_quote_response(15_201_044))
    engine = UniswapV3PricingEngine(client, chain_id=42161, fee_tier=100)
    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    q = engine.get_quote(chip, usdc, 250 * 10**18, gas_price_gwei=1)
    assert isinstance(q, Quote)
    assert q.expected_output == 15_201_044
    assert q.simulated_output == q.expected_output   # Quoter IS the simulation
    assert q.is_valid is True


def test_engine_load_pools_is_noop():
    """V3 engine doesn't pre-load pools — should not raise on any input."""
    engine = UniswapV3PricingEngine(MagicMock(), chain_id=42161)
    engine.load_pools([])              # empty list
    engine.load_pools(['0xpool1'])      # arbitrary string
    assert engine.pools == {}


def test_engine_quoter_error_propagates_as_quote_error():
    client = MagicMock()
    client._web3.eth.call.side_effect = RuntimeError("revert: pool not found")
    engine = UniswapV3PricingEngine(client, chain_id=42161, fee_tier=100)
    chip = get_token(ChainId.ARBITRUM, 'CHIP')
    usdc = get_token(ChainId.ARBITRUM, 'USDC')

    with pytest.raises(QuoteError):
        engine.get_quote(chip, usdc, 1, gas_price_gwei=1)


def test_engine_satisfies_v2_interface():
    """Duck-typing check: every method SignalGenerator/ArbBot calls on V2 engine
    must exist on V3 engine too."""
    engine = UniswapV3PricingEngine(MagicMock(), chain_id=42161)
    assert callable(engine.load_pools)
    assert callable(engine.refresh_pool)
    assert callable(engine.get_quote)
    assert callable(engine.start_monitoring)
    assert hasattr(engine, 'pools')
