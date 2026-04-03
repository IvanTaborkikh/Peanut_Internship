# tests/test_chain_builder.py
import pytest
from unittest.mock import MagicMock
from src.chain.builder import TransactionBuilder
from src.chain import ChainClient, GasPrice
from src.core import Address, TokenAmount
from src.core import WalletManager

TEST_ADDRESS = "0xab5801a7d398351b8be11c439e05c5b3259aec9b"


def _make_builder():
    """Create TransactionBuilder with mocked client and wallet."""
    client = MagicMock(spec=ChainClient)
    client.get_nonce.return_value = 5
    client.estimate_gas.return_value = 21000
    client.get_gas_price.return_value = GasPrice(
        base_fee=10_000_000_000,
        priority_fee_low=1_000_000_000,
        priority_fee_medium=2_000_000_000,
        priority_fee_high=4_000_000_000,
    )

    wallet = MagicMock(spec=WalletManager)
    wallet.address = TEST_ADDRESS

    return TransactionBuilder(client, wallet), client, wallet


# ── Validation tests ──────────────────────────────────────────────────────────
def test_build_requires_to():
    builder, _, _ = _make_builder()
    builder.value(TokenAmount(raw=1000, decimals=18))
    with pytest.raises(ValueError):
        builder.build()


def test_build_requires_value():
    builder, _, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS))
    with pytest.raises(ValueError):
        builder.build()


def test_build_success():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.to.lower == Address(TEST_ADDRESS).lower
    assert tx.value.raw == 1000


def test_build_sets_nonce_automatically():
    builder, client, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.nonce == 5
    client.get_nonce.assert_called_once()


def test_explicit_nonce_skips_rpc():
    builder, client, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .nonce(42)
        .build()
    )
    assert tx.nonce == 42
    client.get_nonce.assert_not_called()


# ── Gas estimation tests ──────────────────────────────────────────────────────
def test_with_gas_estimate_sets_limit():
    builder, client, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_estimate(buffer=1.0)
    tx = builder.build()
    assert tx.gas_limit == 21000


def test_with_gas_estimate_applies_buffer():
    builder, client, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_estimate(buffer=1.5)
    tx = builder.build()
    assert tx.gas_limit == int(21000 * 1.5)


def test_with_gas_price_medium():
    builder, _, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_price("medium")
    tx = builder.build()
    assert tx.max_fee_per_gas is not None
    assert tx.max_priority_fee == 2_000_000_000


def test_with_gas_price_high():
    builder, _, _ = _make_builder()
    builder.to(Address(TEST_ADDRESS)).value(TokenAmount(raw=1000, decimals=18))
    builder.with_gas_price("high")
    tx = builder.build()
    assert tx.max_priority_fee == 4_000_000_000


def test_explicit_gas_limit():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .gas_limit(50000)
        .build()
    )
    assert tx.gas_limit == 50000


# ── Chain ID tests ────────────────────────────────────────────────────────────
def test_default_chain_id_is_mainnet():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.chain_id == 1


def test_chain_id_sepolia():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .chain_id(11155111)
        .build()
    )
    assert tx.chain_id == 11155111


# ── Data tests ────────────────────────────────────────────────────────────────
def test_data_default_empty():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build()
    )
    assert tx.data == b""


def test_data_set():
    builder, _, _ = _make_builder()
    tx = (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .data(b"\x12\x34")
        .build()
    )
    assert tx.data == b"\x12\x34"


# ── Fluent interface tests ────────────────────────────────────────────────────
def test_builder_returns_self_for_chaining():
    builder, _, _ = _make_builder()
    result = builder.to(Address(TEST_ADDRESS))
    assert result is builder


def test_build_and_sign_calls_wallet():
    builder, _, wallet = _make_builder()
    signed_mock = MagicMock()
    wallet.sign_transaction.return_value = signed_mock

    (
        builder.to(Address(TEST_ADDRESS))
        .value(TokenAmount(raw=1000, decimals=18))
        .build_and_sign()
    )

    wallet.sign_transaction.assert_called_once()
